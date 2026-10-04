import discord
from discord import ui
from utils import author_check
from .song_lookup import SongLookupView


class FavoritesButton(ui.Button):
    def __init__(self, label: str, style, author_id: int):
        super().__init__(style=style, label=label)
        self.interaction_check = author_check(author_id)

    async def callback(self, interact: discord.Interaction):
        json_result = self.view.json_data
        title = f"{interact.user.mention} favorites"
        playlist_view = SongLookupView(json_result, True, interact.user.id, title)
        if self.style == discord.ButtonStyle.red:
            self.view.stop()
            await interact.response.edit_message(content=None, view=playlist_view)
            playlist_view.message = interact.message
        else:
            await interact.response.send_message(view=playlist_view, ephemeral=True)
            playlist_view.message = await interact.original_response()
            await self.view.on_timeout()
            self.view.stop()


class FavoritesView(ui.View):
    def __init__(self, author_id: int, json_data):
        super().__init__(timeout=60)
        self.json_data = json_data
        self.private_button = FavoritesButton("Private", discord.ButtonStyle.green, author_id)
        self.add_item(self.private_button)
        self.public_button = FavoritesButton("Public", discord.ButtonStyle.red, author_id)
        self.add_item(self.public_button)
        self.msg = None

    async def on_timeout(self):
        self.private_button.disabled = True
        self.public_button.disabled = True
        try:
            await self.msg.edit(view=self)
        except Exception:
            pass
