import typing
import discord
import subprocess
import sys
import logging
import asyncio
from discord.ext import commands

import stats
from music_interface import cmd_verify
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
        for vc in ctx.bot.voice_clients:
            try:
                vc.stop()
            except:
                pass
        await asyncio.sleep(1)
        stats.save(True)
        await asyncio.sleep(1)
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
        await asyncio.sleep(1)
        stats.save(True)
        await asyncio.sleep(1)
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

    @commands.command()
    @commands.is_owner()
    async def dumpstats(self, ctx: commands.Context):
        try:
            stats.save()
        except Exception as e:
            await ctx.reply(f"Something went wrong {EMOTES.SILLY}")
            log.error(str(e), exc_info=e)
        else:
            await ctx.reply(f"Stats saved successfully {EMOTES.HAPPY}")

    @commands.command()
    @cmd_verify(True)
    async def stats(
        self,
        ctx: commands.Context,
        option: typing.Union[discord.Member, str] = None,
        top_option: str = None,
    ):
        """Param: [None / @Mention / user name / "server" / "top"] displays stats"""
        if option is None:
            option = ctx.author
        if isinstance(option, discord.Member):
            data = stats.users.get_user_data(option.id)
            if data is None:
                await ctx.reply(f"No data for **{option.name}** {EMOTES.SILLY}")
                return
            listening_time = data.get("total_time", 0)
            request_num = data.get("requests", 0)
            songs_listeded_to = data.get("song_count", 0)
            message = (
                f"### {option.mention} stats:\n\n"
                f"Total time listening: `{format_time_string(listening_time)}`\n"
                f"Listened to: `{songs_listeded_to}` songs\n"
                f"Requested: `{request_num}` songs"
            )
            embed = discord.Embed(description=message, color=option.color)
            embed.set_thumbnail(url=option.avatar.url)
            await ctx.reply(embed=embed)
        elif option.lower() == "server":
            data = stats.servers.get_server_data(ctx.guild.id)
            if data is None:
                await ctx.reply(f"No data for this server {EMOTES.SILLY}")
                return
            playing_time = data.get("total_time", 0)
            request_num = data.get("requests", 0)
            songs_played = data.get("song_count", 0)
            message = (
                f"\nTotal time playing: `{format_time_string(playing_time)}`\n"
                f"Played: `{songs_played}` songs\n"
                f"Requests: `{request_num}` songs"
            )
            embed = discord.Embed(title=f"{ctx.guild.name} stats:", description=message)
            embed.set_thumbnail(url=ctx.guild.icon.url)
            await ctx.reply(embed=embed)
        elif option.lower() == "top":
            top_comparison = stats.DataType.Time
            if top_option is None:
                pass
            else:
                top_option = top_option.lower()
                if top_option in ("time", "listen", "listening", "listeners"):
                    top_comparison = stats.DataType.Time
                elif top_option in ("songs", "song", "count", "number", "listened"):
                    top_comparison = stats.DataType.SongCount
                elif top_option in ("requests", "requested", "asked", "queued"):
                    top_comparison = stats.DataType.Request
                else:
                    await ctx.reply(f"Unknown statistic, use: [time, songs, requests]")
                    return


            top_n = 5
            top = stats.users.get_top(top_n, top_comparison)
            leaderboard_text = ""
            for idx in range(top_n):
                users = top.get(idx)
                if not users:
                    continue

                for user_id, score in users:
                    if top_comparison == stats.DataType.Time:
                        score = format_time_string(score)
                    leaderboard_text += f"{idx+1}. <@{user_id}>: {score}\n"

            title = top_comparison.capitalize().replace("_", " ")
            embed = discord.Embed(title=f"🏆 Top by {title} 🏆", description=leaderboard_text)
            await ctx.reply(embed=embed)
        else:
            await ctx.reply(f"Unknown user `{option}` {EMOTES.SIDE_EYE}")

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
