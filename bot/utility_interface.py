import asyncio
import logging
import typing
from heapq import nlargest
import discord
from discord.ext import commands
from discord import app_commands, ui, utils

import stats
import player
from config import EMOTES, SONG_API

log = logging.getLogger()


def format_time_string(seconds: float) -> str:
    intervals = [(31536000, "yr"), (86400, "d"), (3600, "h"), (60, "m"), (1, "s")]
    parts = []
    for count, name in intervals:
        value, seconds = divmod(seconds, count)
        if value > 0:
            parts.append(f"{int(value)}{name}")

    return " ".join(parts) if parts else "0s"


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.add_cog(StatsCog())

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


@app_commands.guild_only()
@app_commands.guild_install()
class StatsCog(commands.GroupCog, group_name="stats"):
    top = app_commands.Group(name="top", description="Show top leaderboards")

    def __init__(self):
        self.stats_cooldown = commands.CooldownMapping.from_cooldown(1, 10, commands.BucketType.user)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        class _Dummy:
            def __init__(self, user):
                self.author = user

        bucket = self.stats_cooldown.get_bucket(_Dummy(interaction.user))
        retry_after = bucket.update_rate_limit()
        if retry_after:
            raise app_commands.CommandOnCooldown(bucket, retry_after)
        return True

    @app_commands.command()
    async def me(self, interact: discord.Interaction):
        """Check your own stats"""
        await self.user.callback(self, interact, interact.user)

    @app_commands.command()
    async def user(self, interact: discord.Interaction, user: discord.Member):
        """Check stats of a server member"""
        if user.id == interact.client.user.id:
            await self.server.callback(self, interact)
            return
        await interact.response.defer(thinking=True)
        data = stats.get_users_cache(interact.guild_id).get(str(user.id), {})
        music_cog = interact.client.get_cog("MusicCog")
        music_cog.update_stats(interact.guild_id)
        listening_time = data.get(stats.DataType.Time, 0)
        listening_time += stats.get_user_current_time(interact.guild_id, user.id)
        request = data.get(stats.DataType.Request, {})
        request_num = sum(request.values())
        songs_listened_to = data.get(stats.DataType.SongCount, 0)
        message = (
            f"### {user.mention} stats:\n\n"
            f"Total time listening: `{format_time_string(listening_time)}`\n"
            f"Listened to: `{songs_listened_to}` songs\n"
            f"Requested: `{request_num}` songs"
        )
        stats_embed = discord.Embed(description=message, color=user.color)
        if user.display_avatar:
            stats_embed.set_thumbnail(url=user.display_avatar.url)
        top_song = None
        for song_id, count in request.items():
            if count == 0:
                continue
            if top_song is None or top_song[1] < count:
                top_song = (song_id, count)
        embeds = [stats_embed]
        discord_file = utils.MISSING
        if top_song:
            response = await interact.client.fetch_json_data(SONG_API + top_song[0])
            if response.error or response.status != 200 or not isinstance(response.json_data, dict):
                embeds.append(
                    discord.Embed(
                        description=f"Could not get data for the most requested song {EMOTES.SAD}"
                    )
                )
            else:
                song = player.Song(response.json_data)
                song_embed, discord_file = await music_cog.get_song_embed(
                    song, None, f"Most requested song ({top_song[1]} times)"
                )
                if discord_file is None:
                    discord_file = utils.MISSING
                embeds.append(song_embed)
        await interact.followup.send(embeds=embeds, file=discord_file)

    @app_commands.command()
    async def server(self, interact: discord.Interaction):
        """Check stats of the server"""
        data = stats._cache_data.get(str(interact.guild_id), {})
        interact.client.get_cog("MusicCog").update_stats(interact.guild_id)
        playing_time = data.get(stats.DataType.Time, 0)
        playing_time += stats.get_server_current_time(interact.guild_id)
        songs_cache = stats.get_songs_cache(interact.guild_id)
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
        embed = discord.Embed(title=f"{interact.guild.name} stats:", description=message)
        if interact.guild.icon:
            embed.set_thumbnail(url=interact.guild.icon.url)
        await interact.response.send_message(embed=embed)

    @top.command()
    async def users(
        self,
        interact: discord.Interaction,
        top_by: typing.Literal[
            "time",
            "song count",
            "request count",
        ],
        top_n: app_commands.Range[int, 3, 20] = 5,
    ):
        """Show top members by*"""
        match top_by:
            case "time":
                top_comparison = stats.DataType.Time
            case "song count":
                top_comparison = stats.DataType.SongCount
            case "request count":
                top_comparison = stats.DataType.Request
        if top_comparison != stats.DataType.Request:
            interact.client.get_cog("MusicCog").update_stats(interact.guild_id)
        top = stats.get_top(interact.guild_id, top_n, top_comparison)
        lines = []
        for idx in range(top_n):
            users = top.get(idx)
            if not users:
                continue
            for user_id, score in users:
                if score != 0:
                    if top_comparison == stats.DataType.Time:
                        score = format_time_string(score)
                    lines.append(f"{idx+1}\\. <@{user_id}>: {score}")
        leaderboard_text = "\n".join(lines)
        title = top_comparison.capitalize().replace("_", " ")
        embed = discord.Embed(
            title=f"🏆 Top {top_n} Users by {title} 🏆", description=leaderboard_text
        )
        await interact.response.send_message(embed=embed)

    async def send_song_list(
        self,
        title: str,
        top_list: list,
        top_by: str,
        interact: discord.Interaction,
        top_comparison: str = None,
    ):
        reply = interact.followup.send
        tasks = [interact.client.fetch_json_data(SONG_API + song_id) for song_id, _ in top_list]
        api_results = await asyncio.gather(*tasks)
        for result in api_results:
            if result.error is not None:
                await reply(f"Could not get data for all the songs {EMOTES.SAD}: {result.error}")
                log.warning(
                    f"stats songs: Could not get data from {result.url}, status: `{result.status}` error: {result.error}"
                )
                return
            if result.status != 200:
                await reply(
                    f"Could not get data for all the songs, status code `{result.status}`. {EMOTES.SAD}: {result.error}"
                )
                log.warning(
                    f"stats songs: Could not get data from {result.url}, status: `{result.status}` error: {result.error}"
                )
                return
            if not result.json_data or not isinstance(result.json_data, dict):
                await reply(
                    f"Could not get data for all the songs, Got empty result. {EMOTES.SAD}: {result.error}"
                )
                log.warning(f"stats songs: Got empty result from {result.url}")
                return
            c_song_id = result.json_data["id"]
            for idx in range(len(top_list)):
                if c_song_id == top_list[idx][0]:
                    if top_comparison is None:
                        count = top_list[idx][1]
                    else:
                        count = top_list[idx][1][top_comparison]
                    song = player.Song(result.json_data)
                    top_list[idx] = (count, song)
                    break
        view = ui.LayoutView(timeout=1)
        container = ui.Container(accent_color=discord.Color.blue())
        container.add_item(ui.TextDisplay(title))
        idx = 1
        for score, song in top_list:
            if score != 0:
                text = ui.TextDisplay(
                    f"{idx}. [{song.song_name()}]({song.get_url()})\n" f"-# {top_by} {score} times"
                )
                url = await song.get_cover_art()
                if url is None:
                    container.add_item(text)
                else:
                    image = ui.Thumbnail(media=url, description="Cover art")
                    container.add_item(ui.Section(text, accessory=image))
                # container.add_item(ui.Separator()) # limits the max to 9
                idx += 1
        view.add_item(container)
        await reply(view=view)

    @top.command()
    async def songs(
        self,
        interact: discord.Interaction,
        top_by: typing.Literal["played", "requested"],
        top_n: app_commands.Range[int, 3, 12] = 5,
    ):
        """Top played/requested songs"""
        await interact.response.defer(thinking=True)
        match top_by:
            case "played":
                top_comparison = stats.DataType.SongCount
                title = f"### 🏆 Top {top_n} Songs by Play Count 🏆\n"
            case "requested":
                top_comparison = stats.DataType.Request
                title = f"### 🏆 Top {top_n} Songs by Request Count 🏆\n"
        songs = stats.get_songs_cache(interact.guild_id)
        top_list = nlargest(top_n, songs.items(), key=lambda item: item[1].get(top_comparison, 0))
        await self.send_song_list(title, top_list, top_by, interact, top_comparison)

    @top.command()
    async def my_requests(
        self,
        interact: discord.Interaction,
        top_n: app_commands.Range[int, 3, 12] = 5,
    ):
        """Show your top requested songs"""
        await self.user_requests.callback(self, interact, interact.user, top_n)

    @top.command()
    async def user_requests(
        self,
        interact: discord.Interaction,
        user: discord.Member,
        top_n: app_commands.Range[int, 3, 12] = 5,
    ):
        """Show user top requested songs"""
        data = stats.get_users_cache(interact.guild_id)
        user_cache = data.get(str(user.id))
        if user_cache is None:
            await interact.response.send_message(
                f"No requests for this user {EMOTES.SAD}", ephemeral=True
            )
            return
        requested_songs = user_cache.get(stats.DataType.Request)
        if not requested_songs:
            await interact.response.send_message(
                f"No requests for this user {EMOTES.SAD}", ephemeral=True
            )
            return
        await interact.response.defer(thinking=True)
        top_list = nlargest(top_n, requested_songs.items(), key=lambda item: item[1])
        title = f"### 🏆 Top {top_n} Songs Requested by {user.mention} 🏆\n"
        await self.send_song_list(title, top_list, "requested", interact)
