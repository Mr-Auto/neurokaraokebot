from functools import partial
import discord
import player
from discord import ui
from config import COLORS
from player import CoverArtistFlag


class FilterView(ui.LayoutView):
    cover_filter = (
        ("Evil", CoverArtistFlag.Evil),
        ("Neuro", CoverArtistFlag.Neuro),
        ("Neuro v1", CoverArtistFlag.NeuroV1),
        ("Neuro v2", CoverArtistFlag.NeuroV2),
        ("Neuro & Evil (duet)", CoverArtistFlag.NeuroAndEvil),
    )

    def __init__(self, music_player: player.MusicPlayer):
        super().__init__(timeout=100)
        self.mp = music_player
        text = ui.TextDisplay("Cover Artists filter:")
        container = ui.Container(text, accent_color=COLORS.EMBED_DEFAULT)
        self.action_row = discord.ui.ActionRow()
        for name, flag in self.cover_filter:
            color = (
                discord.ButtonStyle.red
                if self.mp.cover_filters & flag
                else discord.ButtonStyle.green
            )
            button = discord.ui.Button(label=name, style=color)
            button.callback = partial(self.button_press, button=button, flag=flag)
            self.action_row.add_item(button)
        container.add_item(self.action_row)
        self.submit_button = discord.ui.Button(label="Submit", style=discord.ButtonStyle.blurple)
        self.submit_button.callback = self.submit
        container.add_item(discord.ui.ActionRow(self.submit_button))
        container.add_item(
            discord.ui.TextDisplay(
                "-# Submitting resets the queue, even without it, the filter are applied and will take effect next queue refill"
            )
        )
        self.add_item(container)
        self.msg = None

    async def button_press(self, interact: discord.Interaction, button: discord.ui.Button, flag):
        self.mp.cover_filters ^= flag
        color = (
            discord.ButtonStyle.red if self.mp.cover_filters & flag else discord.ButtonStyle.green
        )
        button.style = color
        await interact.response.edit_message(view=self)

    async def submit(self, interact: discord.Interaction):
        for button in self.action_row.children:
            button.disabled = True
        self.submit_button.disabled = True
        self.submit_button.style = discord.ButtonStyle.gray
        await interact.response.edit_message(view=self)
        self.mp.cache.clear()
        self.mp.refill()

    async def on_timeout(self):
        for button in self.action_row.children:
            button.disabled = True
        self.submit_button.style = discord.ButtonStyle.gray
        self.submit_button.disabled = True
        await self.msg.edit(view=self)
