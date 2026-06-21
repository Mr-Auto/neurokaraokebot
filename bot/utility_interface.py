import typing
import discord
from discord.ext import commands
from discord import app_commands

import stats
from config import EMOTES


def format_time_string(seconds: float) -> str:
    intervals = [(31536000, "yr"), (86400, "d"), (3600, "h"), (60, "m"), (1, "s")]
    parts = []
    for count, name in intervals:
        value, seconds = divmod(seconds, count)
        if value > 0:
            parts.append(f"{int(value)}{name}")

    return " ".join(parts) if parts else "0s"


class UtilityCog(commands.Cog):
    def __init__(self):
        self.stats_cooldown = commands.CooldownMapping.from_cooldown(1, 10, commands.BucketType.user)

    @app_commands.command(name="commands")
    @app_commands.guild_only()
    @app_commands.guild_install()
    @app_commands.checks.cooldown(1, 1, key=lambda i: i.user.id)
    async def commandslist(self, interact: discord.Interaction):
        """List of all ! commands"""
        embed = discord.Embed(title="Command List", color=discord.Color.orange())
        cmds = [c for c in interact.client.commands if not c.hidden]
        sorted_commands = sorted(
            cmds, key=lambda x: (x.__original_kwargs__.get("priority", 999), x.name)
        )
        for command in sorted_commands:
            name = f"!{command.name}"
            if command.aliases:
                name += " / !"
                name += " / !".join(command.aliases)
            embed.add_field(name=name, value=command.help or "", inline=False)
        await interact.response.send_message(embed=embed, ephemeral=True)

    @commands.command(priority=7, aliases=("issues", "problem", "problems"))
    async def issue(self, ctx: commands.Context):
        """Display some info about known issues"""
        await ctx.reply(
            "- Radio playback is pausing/choppy\n"
            "-# Try !pause then !resume after about 4s\n"
            "- Music jittery/glitchy\n"
            "-# Surprisingly, in most cases it's Discord's fault. Try deafen/undeafen or reconnecting to the VC. If it's definitely the bot, try !reconnect\n"
            "- Bot plays fine, then disconnects, connects back and the audio is broken (fast/glitchy/robotic)\n"
            "-# Try !pause then after some time !reconnect. Discord issue, may need to timeout the bot and wait few minutes before inviting it again, you may also try to ask everyone to leave VC\n"
            "- Bot responds that it needs to be in VC, while it sits in VC\n"
            "-# Timeout/disconnect the bot from VC, if it doesn't rejoin use /joinvc\n"
            "- How do I force the bot out of VC?\n"
            "-# Timeout is the easiest way\n"
            "- No volume on next/current song?\n"
            "-# Try skipping it using !skip\n"
            "- Other issues with playback\n"
            "-# Try resetting the bot with !reconnect\n"
            "- No sound no matter what\n"
            "-# There is a known Discord issue where audio is just not being delivered to people\n"
            "-# Empty the VC (including the bot via timeout or by moving it to a different VC) and reconnect after a second or so\n"
        )

    @commands.group(invoke_without_command=True)
    async def stats(
        self,
        ctx: commands.Context,
        *,
        member: typing.Union[discord.Member, str] = None,
    ):
        """Param: [None / @Mention / user name / "server" / "top"] displays stats"""
        if member is None:
            member = ctx.author
        if isinstance(member, discord.Member):
            if member.id == ctx.bot.user.id:
                await self.server(ctx)
                return
            data = stats.get_users_cache(ctx.guild.id).get(str(member.id))
            if data is None:
                data = {}
            ctx.bot.get_cog("MusicCog").update_stats(ctx.guild.id)
            listening_time = data.get(stats.DataType.Time, 0)
            listening_time += stats.get_user_current_time(ctx.guild.id, member.id)
            request = data.get(stats.DataType.Request, {})
            request_num = sum(request.values())
            songs_listeded_to = data.get(stats.DataType.SongCount, 0)
            message = (
                f"### {member.mention} stats:\n\n"
                f"Total time listening: `{format_time_string(listening_time)}`\n"
                f"Listened to: `{songs_listeded_to}` songs\n"
                f"Requested: `{request_num}` songs"
            )
            embed = discord.Embed(description=message, color=member.color)
            embed.set_thumbnail(url=member.avatar.url)
            await ctx.reply(embed=embed)
        else:
            char_limit = 20
            if len(member) > char_limit:
                truncated = member[:char_limit] + "..."
            else:
                truncated = member
            await ctx.reply(f"Unknown member `{truncated}` {EMOTES.SIDE_EYE}")

    @stats.before_invoke
    async def check_stats_cooldown(self, ctx):
        bucket = self.stats_cooldown.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            raise commands.CommandOnCooldown(bucket, retry_after, commands.BucketType.user)

    @stats.command()
    async def server(self, ctx: commands.Context):
        data = stats._cache_data.get(str(ctx.guild.id))
        if data is None:
            data = {}
        ctx.bot.get_cog("MusicCog").update_stats(ctx.guild.id)
        playing_time = data.get(stats.DataType.Time, 0)
        playing_time += stats.get_server_current_time(ctx.guild.id)
        songs_cache = stats.get_songs_cache(ctx.guild.id)
        request_num = 0
        songs_played = 0
        for song_data in songs_cache.values():
            request_num += song_data.get(stats.DataType.Request, 0)
            songs_played += song_data.get(stats.DataType.SongCount, 0)
        message = (
            f"\nTotal time playing: `{format_time_string(playing_time)}`\n"
            f"Played: `{songs_played}` songs\n"
            f"Requests: `{request_num}` songs"
        )
        embed = discord.Embed(title=f"{ctx.guild.name} stats:", description=message)
        embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.reply(embed=embed)

    @stats.command()
    async def top(self, ctx: commands.Context, top_option: str = None, top_n: int = 5):
        if top_n < 2 or top_n > 20:
            await ctx.reply(f"Allowed range for the top 2-20 {EMOTES.SILLY}")
            return
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
        ctx.bot.get_cog("MusicCog").update_stats(ctx.guild.id)
        top = stats.get_top(ctx.guild.id, top_n, top_comparison)
        leaderboard_text = ""
        for idx in range(top_n):
            users = top.get(idx)
            if not users:
                continue
            for user_id, score in users:
                if score != 0:
                    if top_comparison == stats.DataType.Time:
                        score = format_time_string(score)
                    leaderboard_text += f"{idx+1}\\. <@{user_id}>: {score}\n"

        title = top_comparison.capitalize().replace("_", " ")
        embed = discord.Embed(title=f"🏆 Top {top_n} by {title} 🏆", description=leaderboard_text)
        await ctx.reply(embed=embed)
