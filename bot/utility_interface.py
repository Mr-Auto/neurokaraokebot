import discord
import subprocess
import sys
import logging
import asyncio
from discord.ext import commands
from music_interface import cmd_verify
from config import EMOTES

log = logging.getLogger()


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="commands", hidden=True)
    @cmd_verify(True)
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
        await self.bot.close()

    @commands.command(hidden=True)
    @commands.is_owner()
    async def exit(self, ctx: commands.Context):
        await ctx.send(f"Goodbye {EMOTES.SAD}")
        ctx.bot.get_cog("MusicCog").music_players = {}
        await self.bot.close()

    @commands.command(hidden=True)
    @cmd_verify(True)
    # @commands.is_owner()
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
    async def status(self, ctx: commands.Context):
        """Check bot status"""
        music_players = ctx.bot.get_cog("MusicCog").music_players
        message = "Status:\n"
        for guild in self.bot.guilds:
            vc = guild.voice_client
            mp = music_players.get(guild.id)
            message += f"**{guild.name}** - MusicPlayer:`{mp is not None}` Connected to voice:`{vc is not None}`"
            if mp:
                message += f" Playback:`{not mp.is_paused()}`"
            message += "\n"
        await ctx.reply(message)
