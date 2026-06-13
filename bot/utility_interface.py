import typing
import json
import discord
import subprocess
import sys
import logging
import asyncio
from discord import app_commands
from discord.ext import commands, tasks
import requests

import stats
import player
from config import *

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
        self.setlist_check.start()
        self.setlist_data = None

    def cog_unload(self):
        self.setlist_check.cancel()

    def get_setlist_data(self):
        if self.setlist_data is None:
            try:
                with open("data/setlist.json") as f:
                    self.setlist_data = json.load(f)
            except Exception:
                self.setlist_data = {
                    "last_setlist": "",
                    "temporary": False,
                    "notify": {},  # {"guild_id": "channel_id"}
                }
        return self.setlist_data

    def save_setlist_data(self):
        with open("data/setlist.json", "w") as f:
            json.dump(self.setlist_data, f, indent=4)

    @tasks.loop(minutes=30)
    async def setlist_check(self):
        response = await self.bot.fetch_json_data(SETLISTS_API)
        if response.error or response.status != 200 or len(response.json_data) == 0:
            log.warning("setlist_check: could not reach setlist api")
            return
        self.get_setlist_data()
        recent_setlist = response.json_data[0]
        recent_id = recent_setlist.get("id")
        songs = recent_setlist.get("songListDTOs")
        if songs is None or len(songs) == 0:
            log.warning("setlist_check: no songs in most recent setlist?")
            return
        is_temporary = "(Temporary Stream Audio)" in songs[0].get("title")
        if (
            recent_id != self.setlist_data["last_setlist"]
            or is_temporary != self.setlist_data["temporary"]
        ):
            self.setlist_data["last_setlist"] = recent_id
            self.setlist_data["temporary"] = is_temporary
            temp = " (Temporary Stream Audio)" if is_temporary else ""
            url = PLAYLIST_URL + recent_id
            notif_text = f"{EMOTES.DINKDONK} New setlist uploaded{temp} {EMOTES.DINKDONK}\n{url}"
            for guild_id, channel_id in list(self.setlist_data["notify"].items()):
                channel = self.bot.get_channel(int(channel_id))
                try:
                    if channel is None:
                        channel = await self.bot.fetch_channel(int(channel_id))
                    await channel.send(notif_text)
                except (discord.NotFound, discord.Forbidden):
                    guild = self.bot.get_guild(int(guild_id))
                    if guild == None:
                        log.warning(
                            f"setlist_check: Bot no longer in guild id: ({guild_id}), removing from setlist notification list"
                        )
                        self.setlist_data["notify"].pop(guild_id, None)
                        continue
                    log.warning(
                        f"setlist_check: unable to reach channel: ({channel_id}) guild: ({guild.name})"
                    )
                    if guild.owner is None:
                        try:
                            owner = await guild.fetch_member(guild.owner_id)
                        except Exception:
                            owner = None
                    if owner:
                        try:
                            await owner.send(
                                f"{EMOTES.DINKDONK} Was unable to update setlist, no access to channel id `{channel_id}` in {guild.name}\n"
                                f"Please update my permissions or set different channel for receiving updates {EMOTES.DINKDONK}"
                            )
                        except discord.Forbidden:
                            log.warning(
                                f"setlist_check: Could not DM '{owner.name}'. They have closed DMs?"
                            )
                        except Exception:
                            log.exception(
                                f"setlist_check: exception sending DM to owner '{owner.name}' of guild '{guild.name}'"
                            )
                    else:
                        log.warning(
                            f"setlist_check: could not get owner for the guild '{guild.name}' to inform them"
                        )
                except Exception:
                    log.exception(
                        f"setlist_check: exception sending setlist notification guild id: ({guild_id}) channel id: ({channel_id})"
                    )
                await asyncio.sleep(0.1)
            self.save_setlist_data()

    @app_commands.command(name="commands")
    @app_commands.guild_only()
    @app_commands.guild_install()
    @app_commands.checks.cooldown(1, 1, key=lambda i: i.user.id)
    async def commandslist(self, interact: discord.Interaction):
        """List of all ! commands"""
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
        await interact.response.send_message(embed=embed, ephemeral=True)

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
    async def dumpstats(self, ctx: commands.Context):
        try:
            stats.save()
        except Exception as e:
            await ctx.reply(f"Something went wrong {EMOTES.SILLY}")
            log.error(str(e), exc_info=e)
        else:
            await ctx.reply(f"Stats saved successfully {EMOTES.HAPPY}")

    @commands.command()
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
                    leaderboard_text += f"{idx+1}\\. <@{user_id}>: {score}\n"

            title = top_comparison.capitalize().replace("_", " ")
            embed = discord.Embed(title=f"🏆 Top by {title} 🏆", description=leaderboard_text)
            await ctx.reply(embed=embed)
        else:
            await ctx.reply(f"Unknown user `{option}` {EMOTES.SIDE_EYE}")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def mode(self, ctx: commands.Context, mode: str = None):
        if mode:
            if mode.lower() == "stream":
                player.MODE = 1
            elif mode.lower() == "download":
                player.MODE = 2
            else:
                await ctx.reply(f"Wrong option [stream or download] {EMOTES.SILLY}")
                return

        if player.MODE == 1:
            await ctx.reply(f"Current mode: `Stream` {EMOTES.LOADING}")
        else:
            await ctx.reply(f"Current mode: `Download` {EMOTES.PAUSE}")

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
            "-# Timeout/disconnect the bot from VC, if it doesn't rejoin, use !karaokehere\n"
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

    @commands.command(hidden=True)
    @commands.is_owner()
    async def latency(self, ctx: commands.Context):
        latency = self.bot.latency * 1000
        latency = f"{latency:.2f}ms"
        with requests.Session() as session:
            try:
                response = session.get("https://api.neurokaraoke.com/healthz", timeout=20)
            except:
                neurokaraoke = "failed"
            else:
                response_time = response.elapsed.total_seconds() * 1000
                neurokaraoke = f"`{response_time:.2f}ms` {response.content.decode()}"
            await asyncio.sleep(1)
            try:
                response = session.get(
                    "https://images.neurokaraoke.com/WxURxyML82UkE7gY-PiBKw/031c86f6-e113-405a-ae5b-3ada9bb7b900/quality=95",
                    timeout=20,
                )
            except:
                neurokaraoke_images = "failed"
            else:
                response_time = response.elapsed.total_seconds() * 1000
                neurokaraoke_images = f"`{response_time:.2f}ms`"
            await asyncio.sleep(2)
            try:
                response = session.get(
                    "https://storage.neurokaraoke.com/image/icon/evil_icon.webp", timeout=20
                )
            except:
                neurokaraoke_storage = "failed"
            else:
                response_time = response.elapsed.total_seconds() * 1000
                neurokaraoke_storage = f"`{response_time:.2f}ms`"
            await asyncio.sleep(2)
            try:
                response = session.get(RADIO21.SONGDATA, timeout=20)
            except:
                radio21 = "failed"
            else:
                response_time = response.elapsed.total_seconds() * 1000
                is_online = response.json().get("is_online", "")
                radio21 = f"`{response_time:.2f}ms` is_online: `{is_online}`"
            await asyncio.sleep(2)
            try:
                response = session.get(SWARMFM.SONGDATA, timeout=20)
            except:
                swarmFM = "failed"
            else:
                response_time = response.elapsed.total_seconds() * 1000
                playing = response.json().get("playing", "")
                swarmFM = f"`{response_time:.2f}ms` playing: `{playing}`"

        await ctx.reply(
            f"Bot latency: {latency}\n"
            "## Response times:\n"
            f"- api.neurokaraoke: {neurokaraoke}\n"
            f"- images.neurokaraoke: {neurokaraoke_images}\n"
            f"- storage.neurokaraoke: {neurokaraoke_storage}\n"
            f"- radio21 data: {radio21}\n"
            f"- swarmFM data: {swarmFM}\n"
        )

    @commands.command(hidden=True)
    @commands.is_owner()
    async def setstatus(self, ctx: commands.Context, *, status):
        activity = discord.Activity(
            name="67",
            type=discord.ActivityType.custom,
            state=status,
            status_display_type=discord.StatusDisplayType.state,
        )
        await self.bot.change_presence(activity=activity)
        with open("data/activity_status.txt", "w") as f:
            f.write(status)
        await ctx.reply(f"Updated activity text to: `{status}`, it may take a moment to take effect")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def sync(self, ctx: commands.Context, scope: str):
        try:
            if scope.lower() == "local":
                ctx.bot.tree.copy_global_to(guild=ctx.guild)
                synced_commands = await ctx.bot.tree.sync(guild=ctx.guild)
            elif scope.lower() == "global":
                synced_commands = await ctx.bot.tree.sync()
            else:
                await ctx.repl(f"Use !sync [local, global]")
                return
        except Exception as e:
            await ctx.reply(f"Got an error: ({e})")
        else:
            commands_text = ", ".join(cmd.name for cmd in synced_commands)
            await ctx.reply(f"Synced commands {scope}y\n`{commands_text}`")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def unsync(self, ctx: commands.Context, scope: str):
        try:
            if scope.lower() == "local":
                self.bot.tree.clear_commands(guild=ctx.guild)
                await self.bot.tree.sync(guild=ctx.guild)
            elif scope.lower() == "global":
                await self.bot.tree._http.bulk_upsert_global_commands(ctx.bot.application_id, [])
            else:
                await ctx.repl(f"Use !unsync [local, global]")
                return
        except Exception as e:
            await ctx.reply(f"Got an error: ({e})")
        else:
            await ctx.reply(f"Unsynced commands {scope}y")

    @commands.command()
    async def setlistupdates(
        self, ctx: commands.Context, channel: typing.Union[discord.TextChannel, str] = None
    ):
        """Set/Change channel receiving Setlist updates (server owner only)"""
        if ctx.guild.owner_id == ctx.author.id:
            self.get_setlist_data()
            if isinstance(channel, str) and channel.lower() == "clear":
                self.setlist_data["notify"].pop(str(ctx.guild.id), None)
                await ctx.reply(f"Removed setlist notification for this server")
                self.save_setlist_data()
                return
            channel_id = self.setlist_data["notify"].get(str(ctx.guild.id))
            if channel is None or (isinstance(channel, str) and channel.lower() != "clear"):
                channel_info = ""
                if channel_id:
                    channel_info = f"\nCurrently set to channel <#{channel_id}>"
                await ctx.reply(f"Usage `!setlistupdates #channel_name / clear`{channel_info}")
                return
            bot_member = ctx.guild.get_member(ctx.bot.user.id)
            if bot_member is None:
                await ctx.reply(f"Something went wrong, bot not member of the guild? {EMOTES.SILLY}")
                return
            if channel.guild.id != ctx.guild.id:
                await ctx.reply(f"Channel must belong to this guild {EMOTES.SIDE_EYE}")
                return
            # must be discord.TextChannel, discord.Thread, or discord.VoiceChannel
            permissions = channel.permissions_for(bot_member)
            if not permissions.send_messages:
                await ctx.reply(
                    f"I don't have permissions to send messages in that channel {EMOTES.SAD}"
                )
                return
            if not permissions.view_channel:
                await ctx.reply(f"I don't have permissions to view this channel {EMOTES.SAD}")
                return
            self.setlist_data["notify"][str(ctx.guild.id)] = str(channel.id)
            if channel_id is None:
                await ctx.reply(f"Succesfully set {channel.mention} for receiving setlist updates")
            else:
                await ctx.reply(
                    f"Succesfully changed setlist updates from <#{channel_id}> to {channel.mention} "
                )
            self.save_setlist_data()
