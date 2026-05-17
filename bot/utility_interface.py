import typing
import discord
import subprocess
import sys
import logging
import asyncio
from discord.ext import commands

import player
from config import EMOTES

log = logging.getLogger()


def format_time_string(seconds: int) -> str:
    intervals = [(31536000, "yr"), (86400, "d"), (3600, "h"), (60, "m"), (1, "s")]
    parts = []
    for count, name in intervals:
        value, seconds = divmod(seconds, count)
        if value > 0:
            parts.append(f"{value}{name}")

    return " ".join(parts) if parts else "0s"


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="commands", hidden=True)
    async def commands_list(self, ctx: commands.Context):
        """List of all commands"""
        embed = discord.Embed(title="Command List", color=discord.Color.orange())
        cmds = [c for c in self.bot.commands if not c.hidden]
        sorted_commands = sorted(
            cmds, key=lambda x: (x.__original_kwargs__.get("priority", 999), x.name)
        )
        for command in sorted_commands:
            name = f"!{command.name}"
            if command.aliases:
                name += " / !"
                name += " / !".join(command.aliases)
            embed.add_field(name=name, value=command.help or "", inline=False)
        await ctx.reply(embed=embed)

    @commands.command(hidden=True)
    @commands.is_owner()
    async def restart(self, ctx: commands.Context):
        await ctx.send(f"Goodbye {EMOTES.SAD}")
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen([sys.executable] + sys.argv, creationflags=creationflags)
        ctx.bot.get_cog("MusicCog").music_players = {}
        for vc in ctx.bot.voice_clients:
            try:
                vc.stop()
            except:
                pass
        await self.bot.close()

    @commands.command(hidden=True)
    @commands.is_owner()
    async def exit(self, ctx: commands.Context):
        await ctx.send(f"Goodbye {EMOTES.SAD}")
        ctx.bot.get_cog("MusicCog").music_players = {}
        for vc in ctx.bot.voice_clients:
            try:
                vc.stop()
            except:
                pass
        await self.bot.close()

    @commands.command(hidden=True)
    @commands.is_owner()
    async def emotes(self, ctx: commands.Context, group_name: str):
        """Debug"""
        all_groups = EMOTES.groups()
        if group_name.upper() not in all_groups:
            all_groups_str = ", ".join(all_groups)
            await ctx.reply(f"No such group name {EMOTES.SAD}\nAvaible groups: [{all_groups_str}]")
        else:
            message = ""
            for emote_str in EMOTES.get_list(group_name):
                if len(message) + len(emote_str) > 2000:
                    await ctx.reply(message)
                    message = ""
                    await asyncio.sleep(0.2)
                message += emote_str
            if message:
                await ctx.reply(message)

    @commands.command(hidden=True)
    @commands.is_owner()
    async def mode(self, ctx: commands.Context, mode: str = None):
        if mode:
            if mode.lower() == "lazy":
                player.MODE = 1
            elif mode.lower() == "eager":
                player.MODE = 2
            else:
                await ctx.reply(f"Wrong option [lazy or eager] {EMOTES.SILLY}")
                return

        if player.MODE == 1:
            await ctx.reply(f"Current mode: `LazyPCMSource(pedalboard)` {EMOTES.LOADING}")
        else:
            await ctx.reply(f"Current mode: `EagerPCMSource(ffmpeg)` {EMOTES.PAUSE}")

    @commands.command(priority=7, aliases=("issues", "problem", "problems"))
    async def issue(self, ctx: commands.Context):
        """Display some info about known issues"""
        await ctx.reply(
            "- Radio playback is pausing/choppy\n"
            "-# Try !pause then !resume after about 4s\n"
            "- Music jittery/glitchy\n"
            "-# Surprisingly, in most cases it's Discord's fault. Try deafen/undeafen or reconnecting to the VC. If it's the bot, try !reconnect\n"
            "- Bot responds that it needs to be in VC, while it sits in VC\n"
            "-# Try !reconnect. If it's not in VC or claims that it is not, try !karaokehere\n"
            "- How do I force the bot out of VC?\n"
            "-# Timeout is the easiest way\n"
            "- No volume on next song?\n"
            "-# Try skipping it using !skip\n"
            "- Other issues with playback\n"
            "-# Try resetting the bot with !reconnect\n"
            "- No sound no matter what\n"
            "-# There is a known Discord issue where audio is just not being delivered to people\n"
            "-# Empty the VC (including the bot via timeout or by moving it to a different VC) and reconnect after a second or so\n"
        )

    @commands.command(hidden=True)
    @commands.is_owner()
    async def status(self, ctx: commands.Context):
        """Check bot status"""
        music_players = ctx.bot.get_cog("MusicCog").music_players
        message = "Status:\n"
        for guild in self.bot.guilds:
            vc = guild.voice_client
            mp = music_players.get(guild.id)
            message += f"**{guild.name}** - MusicPlayer:`{mp is not None}` Connected to voice:`{vc is not None}`"
            if mp:
                message += f" Playback paused:`{mp.is_paused()}`"
            message += "\n"
        await ctx.reply(message)
