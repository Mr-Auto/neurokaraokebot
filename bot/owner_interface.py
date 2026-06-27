import typing
import json
import discord
import subprocess
import sys
import logging
import asyncio
from discord.ext import commands, tasks
import requests

import stats
import player
from config import *

log = logging.getLogger()


class OwnerCog(commands.Cog):
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
        stats.save()
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

    @commands.command(hidden=True)
    @commands.is_owner()
    async def restart(self, ctx: commands.Context):
        await ctx.send(f"Goodbye {EMOTES.SAD}")
        ctx.bot.get_cog("MusicCog").music_players = {}
        for vc in ctx.bot.voice_clients:
            try:
                vc.stop()
            except:
                pass
        stats.save(True)
        await asyncio.sleep(1)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen([sys.executable] + sys.argv, creationflags=creationflags)
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
        message = await ctx.reply(f"Processing {EMOTES.LOADING}")
        latency = self.bot.latency * 1000
        latency = f"{latency:.2f}ms"
        vc_latency = None
        if ctx.guild.voice_client:
            vc_latency = ctx.guild.voice_client.latency * 1000
            vc_a_latency = ctx.guild.voice_client.average_latency * 1000
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
        if vc_latency is None:
            voice_latency = "`Not connected`"
        else:
            voice_latency = f"c: `{vc_latency:.2f}ms` a: `{vc_a_latency:.2f}ms`"
        await message.edit(
            content=f"Bot latency: {latency}\n"
            f"Voice latency (this server): {voice_latency}\n"
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
