from functools import partial
import discord
import player
import utils
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
        super().__init__(timeout=60)
        self.mp = music_player
        text = ui.TextDisplay("Cover Artists filter:")
        container = ui.Container(text, accent_color=COLORS.EMBED_DEFAULT)
        self.action_row = ui.ActionRow()
        for name, flag in self.cover_filter:
            color = (
                discord.ButtonStyle.red
                if self.mp.cover_filters & flag
                else discord.ButtonStyle.green
            )
            button = ui.Button(label=name, style=color)
            button.callback = partial(self.button_press, button=button, flag=flag)
            button.interaction_check = utils.vc_check
            self.action_row.add_item(button)
        container.add_item(self.action_row)
        color = discord.ButtonStyle.red if self.mp.christmas_filter else discord.ButtonStyle.green
        self.christmas_button = ui.Button(label="Christmas", emoji="🎄", style=color)
        self.christmas_button.callback = self.christmas_button_callback
        self.christmas_button.interaction_check = utils.vc_check
        extra_filters = ui.ActionRow(self.christmas_button)
        container.add_item(extra_filters)
        self.submit_button = ui.Button(label="Submit", style=discord.ButtonStyle.blurple)
        self.submit_button.callback = self.submit
        self.submit_button.interaction_check = utils.vc_check
        container.add_item(ui.ActionRow(self.submit_button))
        note = "-# Submitting resets the queue, even without it, the filter are applied and will take effect after clearing the currect queue"
        container.add_item(ui.TextDisplay(note))
        self.add_item(container)
        self.msg = None

    async def button_press(self, interact: discord.Interaction, button: ui.Button, flag):
        self.mp.cover_filters ^= flag
        color = (
            discord.ButtonStyle.red if self.mp.cover_filters & flag else discord.ButtonStyle.green
        )
        button.style = color
        await interact.response.edit_message(view=self)

    async def christmas_button_callback(self, interact: discord.Interaction):
        self.mp.christmas_filter = not self.mp.christmas_filter
        color = discord.ButtonStyle.red if self.mp.christmas_filter else discord.ButtonStyle.green
        self.christmas_button.style = color
        await interact.response.edit_message(view=self)

    async def submit(self, interact: discord.Interaction):
        for button in self.action_row.children:
            button.disabled = True
        self.submit_button.disabled = True
        self.christmas_button.disabled = True
        self.submit_button.style = discord.ButtonStyle.gray
        await interact.response.edit_message(view=self)
        self.mp.cache.clear()
        self.mp.refill()

    async def on_timeout(self):
        for button in self.action_row.children:
            button.disabled = True
        self.submit_button.style = discord.ButtonStyle.gray
        self.submit_button.disabled = True
        self.christmas_button.disabled = True
        await self.msg.edit(view=self)
