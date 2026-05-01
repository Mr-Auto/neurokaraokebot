import discord
import logging
import time
import requests
from discord import ui
from enum import Enum, auto
from datetime import datetime
from player import MusicPlayer, Song
from config import EMOTES, PLAYLIST_URL, PLAYLIST_API

log = logging.getLogger()


class RequestButton(ui.Button):
    def __init__(self, song_data: dict, disabled=False):
        super().__init__(label="Request", style=discord.ButtonStyle.primary, disabled=disabled)
        self.song_data = song_data

    async def interaction_check(self, interact: discord.Interaction):
        if not interact.guild.voice_client:
            await interact.response.send_message("Bot not in VC", ephemeral=True)
            return False
        if (
            not interact.user.voice
            or interact.user.voice.channel.id != interact.guild.voice_client.channel.id
        ):
            await interact.response.send_message("You have to be in VC to use this!", ephemeral=True)
            return False

        return True

    async def callback(self, interact: discord.Interaction):
        self.song_data["_requested"] = True
        self.disabled = True
        await interact.response.edit_message(view=self.view)
        cog = interact.client.get_cog("MusicCog")
        mp: MusicPlayer = cog.music_players.get(interact.guild.id)
        playing_in_str = f"`PAUSED` {EMOTES.PAUSE}"
        if not mp.is_paused():
            queue_duration = mp.request_queue_duration()
            if queue_duration is None:
                playing_in_str = f"`in Unknown` {EMOTES.SILLY}"
            else:
                playing_in = int(time.time()) + queue_duration
                playing_in_str = f"<t:{playing_in}:R>"

        position, song = mp.request_song(self.song_data, interact.user.name)
        await interact.channel.send(
            f"{interact.user.mention} requested: `{song.song_name()}`\nAdded to the queue at position {position}, playing {playing_in_str}"
        )


class SongLookupView(ui.LayoutView):
    ITEMS_PER_PAGE = 9

    def __init__(self, data: list, request_allowed: bool, owner_id: int, name: str = None):
        super().__init__(timeout=60)
        self.data = data
        self.current_page = 0
        self.request_allowed = request_allowed
        self.owner_id = owner_id
        self.message = None
        self.name = name
        self.update_view()  # last

    async def on_timeout(self):
        self.request_allowed = False
        self.update_view(True)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

    def update_view(self, no_page_buttons=False):
        self.clear_items()
        start = self.current_page * self.ITEMS_PER_PAGE
        end = min(start + self.ITEMS_PER_PAGE, len(self.data))
        container = ui.Container(accent_color=discord.Color.blue())
        if self.name:
            container.add_item(ui.TextDisplay(f"### {self.name}\n"))
        for idx in range(start, end):
            item = self.process_item(idx)
            if isinstance(item, list):
                for obj in item:
                    container.add_item(obj)
            else:
                container.add_item(item)
            if idx + 1 != end:
                container.add_item(ui.Separator())

        self.add_item(container)
        if no_page_buttons or len(self.data) <= self.ITEMS_PER_PAGE:
            return

        prev_btn = ui.Button(label="Previous", disabled=(self.current_page == 0))
        next_btn = ui.Button(label="Next", disabled=(end == len(self.data)))

        async def prev_callback(interact: discord.Interaction):
            self.current_page -= 1
            self.update_view()
            await interact.response.edit_message(view=self)

        async def next_callback(interact: discord.Interaction):
            self.current_page += 1
            self.update_view()
            await interact.response.edit_message(view=self)

        async def author_check(interact: discord.Interaction) -> bool:
            if interact.user.id != self.owner_id:
                await interact.response.send_message(
                    "Only the owner can use this button!", ephemeral=True
                )
                return False

            return True

        prev_btn.callback = prev_callback
        prev_btn.interaction_check = author_check
        next_btn.callback = next_callback
        next_btn.interaction_check = author_check
        action_row = ui.ActionRow()
        action_row.add_item(prev_btn)
        action_row.add_item(next_btn)
        self.add_item(action_row)

    def process_item(self, idx: int) -> ui.Item | list[ui.Item]:
        item = self.data[idx]
        song = Song(item)
        text_raw = f"{idx +1}. [{song.song_name()}]({song.get_url()})"
        date = item.get("streamDate")
        if date:
            date = datetime.fromisoformat(date).strftime("%B %d, %Y")
            text_raw += f"\n-# {date}\n"
        text = ui.TextDisplay(text_raw)
        if self.request_allowed:
            was_requested = item.get("_requested") or False
            section = ui.Section(text, accessory=RequestButton(item, was_requested))
            return section
        else:
            cover_url = song.get_cover_art()
            if cover_url:
                image = ui.Thumbnail(media=cover_url, description="Cover art")
                return ui.Section(text, accessory=image)

            return text


class ButtonType(Enum):
    REQUEST = auto()
    OPEN = auto()


class SetlistButton(ui.Button):
    def __init__(self, label: str, data: list, owner_id, button_function: ButtonType):
        style = (
            discord.ButtonStyle.primary
            if button_function == ButtonType.REQUEST
            else discord.ButtonStyle.green
        )
        super().__init__(label=label, style=style)
        self.data = data
        self.owner_id = owner_id
        self.button_function = button_function

    async def interaction_check(self, interact: discord.Interaction):
        if not interact.guild.voice_client:
            await interact.response.send_message("Bot not in VC", ephemeral=True)
            return False
        if (
            not interact.user.voice
            or interact.user.voice.channel.id != interact.guild.voice_client.channel.id
        ):
            await interact.response.send_message("You have to be in VC to use this!", ephemeral=True)
            return False
        if interact.user.id != self.owner_id:
            await interact.response.send_message(
                "Only the owner can use this button!", ephemeral=True
            )
            return False
        return True

    async def callback(self, interact: discord.Interaction):
        response = requests.get(PLAYLIST_API + self.data["id"], headers={"x-guest-id": "69"})
        if response.status_code != 200:
            await interact.response.send_message(
                f"Something went wrong, status code: `{response.status_code}` {EMOTES.SILLY}",
                ephemeral=True,
            )
            return

        json_result = response.json()
        if "songListDTOs" not in json_result or len(json_result["songListDTOs"]) == 0:
            await interact.response.send_message(
                f"Didn't get playlist back {EMOTES.SILLY}", ephemeral=True
            )
            return

        plylist_data = json_result["songListDTOs"]
        if self.button_function == ButtonType.REQUEST:
            # don't like this child accessing parent, no better idea for now
            the_view: SetlistsView = self.view
            the_view.request_allowed = False
            the_view.update_view()
            await interact.response.edit_message(view=the_view)
            cog = interact.client.get_cog("MusicCog")
            mp: MusicPlayer = cog.music_players.get(interact.guild.id)
            mp.requests_cache.extend(map(lambda d: Song(d, interact.user.name), plylist_data))
            mp.refill()
            song_nr = len(plylist_data)
            await interact.channel.send(
                f"{interact.user.mention} requested: `{song_nr} songs`\nFrom {self.data['name']}"
            )
        else:
            new_view = SongLookupView(plylist_data, True, self.owner_id, plylist_data.get("name"))
            new_view.message = interact.message
            await interact.response.edit_message(view=new_view)


class SetlistsView(SongLookupView):
    ITEMS_PER_PAGE = 7

    def __init__(self, data: list, owner_id: int):
        super().__init__(data, True, owner_id)

    def process_item(self, idx: int) -> ui.Item | list[ui.Item]:
        setlist = self.data[idx]
        text_raw = f"{idx +1}. [{setlist['name']}]({PLAYLIST_URL}{setlist['id']})"
        date = setlist.get("setListDate")
        if date:
            date = datetime.fromisoformat(date).strftime("%B %d, %Y")
            text_raw += f"\n-# {date} {setlist['songCount']} songs\n"
        text = ui.TextDisplay(text_raw)
        if self.request_allowed:
            row = ui.ActionRow()
            request_button = SetlistButton("Request", setlist, self.owner_id, ButtonType.REQUEST)
            row.add_item(request_button)
            open_button = SetlistButton("Open", setlist, self.owner_id, ButtonType.OPEN)
            row.add_item(open_button)
            return [text, row]
        else:
            return text
