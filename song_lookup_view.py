import discord
import logging
import time
from discord import ui
from datetime import datetime
from player import Song
from config import PAUSE_DURATION, EMOTES

log = logging.getLogger()


class RequestButton(ui.Button):
    def __init__(self, song_data: dict, disabled=False):
        super().__init__(label="Request", style=discord.ButtonStyle.primary, disabled=disabled)
        self.song_data = song_data

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            await interaction.response.send_message("Bot not in VC", ephemeral=True)
            return
        if (
            not interaction.user.voice
            or interaction.user.voice.channel.id != interaction.guild.voice_client.channel.id
        ):
            await interaction.response.send_message(
                "You have to be in VC to use this!", ephemeral=True
            )
            return

        self.song_data["_requested"] = True
        self.disabled = True
        await interaction.response.edit_message(view=self.view)
        cog = interaction.client.get_cog("MusicCog")
        mp = cog.music_players.get(interaction.guild.id)
        song_remaining = mp.current_song.remaning() or 0
        playing_in_str = f"`PAUSED` {EMOTES.PAUSE}"
        if not mp.is_paused():
            queue_duration = mp.request_queue_duration()
            if queue_duration is not None:
                playing_in = int(time.time()) + queue_duration + song_remaining + PAUSE_DURATION
                playing_in_str = f"<t:{playing_in}:R>"
            else:
                playing_in_str = f"`in Unknown` {EMOTES.SILLY}"
        requested_song = Song(self.song_data, interaction.user.name)
        mp.requests_cache.append(requested_song)
        await interaction.channel.send(
            f"{interaction.user.mention} requested: `{requested_song.song_name()}`\nAdded to the queue at position {len(mp.requests_cache)}, playing {playing_in_str}"
        )


class SongLookupView(ui.LayoutView):
    def __init__(self, data: list, request_allowed: bool, owner_id: int):
        super().__init__(timeout=60)
        self.data = data
        self.ITEMS_PER_PAGE = 9
        self.current_page = 0
        self.request_allowed = request_allowed
        self.owner_id = owner_id
        self.message = None
        self.update_view()

    async def on_timeout(self):
        self.update_view(True)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

    def update_view(self, no_buttons=False):
        self.clear_items()
        start = self.current_page * self.ITEMS_PER_PAGE
        end = min(start + self.ITEMS_PER_PAGE, len(self.data))
        container = ui.Container(accent_color=discord.Color.blue())
        for idx in range(start, end):
            song = Song(self.data[idx])
            text_raw = f"{idx +1}. [{song.song_name()}]({song.get_url()})"
            date = self.data[idx].get("streamDate")
            if date:
                date = datetime.fromisoformat(date).strftime("%B %d, %Y")
                text_raw += f"\n-# {date}\n"
            text = ui.TextDisplay(text_raw)
            was_requested = self.data[idx].get("_requested") or False
            if self.request_allowed and not no_buttons:
                section = ui.Section(text, accessory=RequestButton(self.data[idx], was_requested))
                container.add_item(section)
            else:
                container.add_item(text)
            if idx + 1 != end:
                container.add_item(ui.Separator())

        self.add_item(container)

        if no_buttons or len(self.data) <= self.ITEMS_PER_PAGE:
            return

        prev_btn = ui.Button(label="Previous", disabled=(self.current_page == 0))
        next_btn = ui.Button(label="Next", disabled=(end == len(self.data)))

        async def prev_callback(interaction: discord.Interaction):
            self.current_page -= 1
            self.update_view()
            await interaction.response.edit_message(view=self)

        async def next_callback(interaction: discord.Interaction):
            self.current_page += 1
            self.update_view()
            await interaction.response.edit_message(view=self)

        async def author_check(interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
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
