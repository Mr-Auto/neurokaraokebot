import weakref
import discord
from discord import utils
from discord.ext import commands, tasks
from discord import app_commands
import discord.ui
import logging
import asyncio
import time
import typing
import enum
import datetime
from itertools import chain, islice

import player
import stats
from config import *
from song_lookup_view import SongLookupView, RequestButton, SetlistsView

log = logging.getLogger()


class CoverBy(enum.Enum):
    Vedal = enum.auto()
    Twins = enum.auto()
    Neuro = enum.auto()
    Evil = enum.auto()
    Unknown = enum.auto()


def parse_cover_by(cover_str: str) -> CoverBy:
    if "Vedal" in cover_str:
        return CoverBy.Vedal
    elif "Neuro" in cover_str and "Evil" in cover_str:
        return CoverBy.Twins
    elif "Neuro" in cover_str:
        return CoverBy.Neuro
    elif "Evil" in cover_str:
        return CoverBy.Evil
    else:
        if cover_str:
            log.warning(f"parse_cover_by: error during parsing string - '{cover_str}'")
        return CoverBy.Unknown


def is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


class NotAllowedError(commands.CommandError):
    pass


# check if command is allowed in certain situation.
# This also disabled the message event about missing parameter as it needs to satisfy this condition first
def cmd_verify():
    async def predicate(ctx: commands.Context):
        vc = ctx.voice_client
        mp = ctx.bot.get_cog("MusicCog").get_music_player(ctx)
        if not vc or not mp:
            raise NotAllowedError(
                "Bot not running, use !karaokehere to invite it to VC. Command allowed only in VC"
            )
        if (
            ctx.channel.id != vc.channel.id
            or not ctx.author.voice
            or ctx.author.voice.channel.id != vc.channel.id
        ):
            raise NotAllowedError("You can only use this command in VC with the bot")
        return True

    return commands.check(predicate)


def song_search(**kwargs) -> dict:
    """
    sort by available:
    Title PlayCount KaraokeDate Duration

    all available keys and example data:
    {"search":"text","page": 1,"pageSize": 10,"sortBy":"KaraokeDate","sortDesc": True,"sortDesc":false,"genreIds":null,"themeIds":null,"moodIds":null,"artistIds":null,
    "coverArtistIds":null,"languageIds":null,"energyLevel":null,"tempo":null,"key":null,"karaokeStart":null,"karaokeEnd":null}
    """
    return kwargs


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.music_players: dict[int, player.MusicPlayer] = {}
        self.check_alone_status.start()
        self.voice_statuses = {}
        self.error_time = {}

    async def cog_unload(self):
        self.check_alone_status.cancel()
        self.music_players = {}

    @app_commands.command()
    @app_commands.guild_only()
    @app_commands.guild_install()
    @app_commands.checks.cooldown(1, 12, key=lambda i: i.guild_id)
    async def joinvc(self, interact: discord.Interaction):
        """Invite bot to VC"""
        repl = interact.response.send_message
        if interact.guild.voice_client:
            await repl(f"Bot already in VC {EMOTES.SILLY}", ephemeral=True)
            return
        if interact.channel.type != discord.ChannelType.voice:
            await repl(
                f"Can't play audio in '{interact.channel.type}' channel! {EMOTES.SAD}",
                ephemeral=True,
            )
            return
        last_error = self.error_time.get(interact.guild_id, 0) + 10
        if last_error > time.time():
            diff = last_error - time.time()
            await repl(
                f"There has been an error {EMOTES.SAD}, try again in {diff:.1f}s", ephemeral=True
            )
            return
        channel = interact.channel
        try:
            await channel.connect(reconnect=False, timeout=10)
            await repl(f"Starting Neuro Karaoke Playback in '{channel}' {EMOTES.HAPPY}")
            await self.start(channel)
        except TimeoutError:
            await repl(
                f"Connection timeout {EMOTES.SAD}, try again in a minute or two", ephemeral=True
            )
        except Exception:
            await repl(f"Something went wrong {EMOTES.SILLY}", ephemeral=True)
            raise

    @commands.command(priority=2, aliases=("⏸️",))
    @cmd_verify()
    @commands.cooldown(1, 2, commands.BucketType.guild)
    async def pause(self, ctx: commands.Context):
        vc = ctx.voice_client
        mp = self.get_music_player(ctx)
        mp.pause()
        stats.update(ctx.guild.id, mp.current_song, self.get_members_listening(ctx.channel))
        if vc.is_playing():
            vc.pause()
            await ctx.reply(f"Paused ⏸️ {EMOTES.PAUSE}")
            if mp.update_status:
                await self.set_voice_status(vc.channel, mp.current_song.song_name(), True)

    @commands.command(priority=2, aliases=("▶️",))
    @cmd_verify()
    @commands.cooldown(1, 2, commands.BucketType.guild)
    async def resume(self, ctx: commands.Context):
        vc = ctx.voice_client
        mp = self.get_music_player(ctx)
        mp.resume()
        if vc.is_paused():
            vc.resume()
            await ctx.reply(f"Resumed ▶️ {EMOTES.JAM}")
            if mp.update_status:
                await self.set_voice_status(vc.channel, mp.current_song.song_name(), False)

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def reconnect(self, ctx: commands.Context):
        """Reset the bot and reconnect to this VC (kills the queue)"""
        if not ctx.voice_client:
            await ctx.reply(
                "Bot not running, use !karaokehere to invite it to VC. Command allowed only in VC",
                delete_after=5,
            )
            return
        if ctx.channel.id != ctx.voice_client.channel.id:
            await ctx.reply("You can only use this command in VC with the bot", delete_after=5)
            return
        mp = self.get_music_player(ctx)
        self.music_players[ctx.guild.id] = None
        if mp:
            mp.pause()
        self.error_time[ctx.guild.id] = time.time()
        vc = ctx.voice_client
        channel = vc.channel
        stats.cache_song(channel.guild.id, mp.current_song)
        vc.stop()
        await ctx.reply(f"Rebooting voice connection... {EMOTES.LOADING}")
        await vc.disconnect()
        await asyncio.sleep(2)
        await channel.connect(reconnect=False)
        await self.start(ctx.channel)

    @commands.command(priority=8)
    @cmd_verify()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def skip(self, ctx: commands.Context):
        """Skip current song"""
        next_song = self.get_music_player(ctx).get_next_song()
        vc = ctx.voice_client
        bucket = self.song._buckets.get_bucket(ctx.message)
        bucket.reset()
        bucket = self.nextsong._buckets.get_bucket(ctx.message)
        bucket.reset()
        if not vc.is_playing() and not vc.is_paused():
            log.warning("Skip: no current playback?")
            mp = self.get_music_player(ctx)
            song = mp.current_song if mp else None
            stats.cache_song(ctx.guild.id, song)
            mp.load_next_song()
            await self.play_current(vc)
            await asyncio.sleep(0.1)
            stats.update(ctx.guild.id, mp.current_song, self.get_members_listening(ctx.channel))
        else:
            ctx.voice_client.stop()

        if next_song is not None:
            await ctx.reply(f"Skipping current song, next: `{next_song.song_name()}` {EMOTES.JAM}")
        else:
            await ctx.reply(f"Skipping current song, no more songs in queue {EMOTES.SILLY}")
            log.error(f"skip: no songs in the queue?")

    @commands.command(priority=6, aliases=("current", "currentsong"))
    @cmd_verify()
    @commands.cooldown(1, 20, commands.BucketType.guild)
    async def song(self, ctx: commands.Context):
        """Check current song"""
        mp = self.get_music_player(ctx)
        current_song = mp.current_song
        radio_name = None
        if isinstance(current_song, player.Radio):
            current_song = mp.current_song.get_song(mp.current_song.CURRENT)
            radio_name = mp.current_song.name()
        song_remaining = current_song.remaining()
        if song_remaining is None and radio_name is None:
            await ctx.reply(f"Something went wrong {EMOTES.SILLY}")
            log.error(f"song command: No playback for the current song! {radio_name or ''}")
            return
        if song_remaining is not None:
            bucket = ctx.command._buckets.get_bucket(ctx.message)
            bucket.per = song_remaining / 2
            song_end = int(time.time() + song_remaining)
            note = f"Ends <t:{song_end}:R>"
        else:
            note = f"Ends `Unknown` {EMOTES.SILLY}"
        requested_by = current_song.requested_by
        if radio_name:
            if requested_by == "True":
                requested_by = "Yes"
            else:
                requested_by = "No"
            footer = radio_name
            if radio_name == player.Radio21.name():
                footer += f"         Requested: {requested_by}"
        else:
            requested_by = requested_by or self.bot.user.name
            footer = f'Requested by "{requested_by}"'
        if mp.is_paused():
            note = f"Ends `PAUSED` {EMOTES.PAUSE}"
        embed, discord_file = await self.get_song_embed(current_song, note, footer, song_remaining)
        cover_str = current_song.cover_artists
        cover_by = parse_cover_by(cover_str)
        emote_str = EMOTES.JAM
        match cover_by:
            case CoverBy.Vedal:
                pass
            case CoverBy.Twins:
                emote_str = EMOTES.NEUROJAM + EMOTES.EVILJAM
            case CoverBy.Neuro:
                emote_str = EMOTES.NEUROJAM
            case CoverBy.Evil:
                emote_str = EMOTES.EVILJAM
        try:
            msg = await ctx.reply(f"Playing right now {emote_str}", embed=embed, file=discord_file)
        except discord.errors.HTTPException as e:
            if e.code == 40005:
                msg = await ctx.reply(f"Playing right now {emote_str}", embed=embed)
            else:
                raise
        if song_remaining is None:
            return
        symbol = embed.description.rfind("🔘")
        if symbol == -1:
            return
        if radio_name is None:
            song_ref = weakref.ref(current_song)
        else:
            song_ref = lambda: current_song
        self.bot.loop.create_task(self.update_embed(song_ref, msg, embed, symbol))

    async def update_embed(
        self,
        song_ref: weakref.ReferenceType[player.Song],
        msg: discord.Message,
        embed: discord.Embed,
        symbol: int,
    ):
        line_start = embed.description.rfind("\n", 0, symbol)
        if line_start == -1:
            return
        line_end = embed.description.rfind("▬")
        if line_end == -1:
            return
        if symbol > line_end:
            line_end = symbol
        description_end = embed.description[line_end + 1 :]
        duration = song_ref().duration
        counter = 0
        while True:
            await asyncio.sleep(1.6)
            if (song := song_ref()) is not None:
                remaining = song.remaining()
                song = None
            else:
                return

            if remaining is None:
                return
            pminutes, pseconds = divmod(round(duration - remaining), 60)
            seg = int((remaining * 10) / duration)
            embed.description = f"{embed.description[:line_start]}\n`{pminutes}:{pseconds:02} {'▬'*(10-seg)}🔘{'▬'*seg}{description_end}"
            try:
                await msg.edit(embed=embed)
            except discord.NotFound:
                return
            if remaining <= 0:
                return
            counter += 1
            if counter > 1000:
                return

    @commands.command(priority=6, aliases=("ns",))
    @cmd_verify()
    @commands.cooldown(1, 20, commands.BucketType.guild)
    async def nextsong(self, ctx: commands.Context):
        """Check the next song"""
        next_song = None
        mp = self.get_music_player(ctx)
        radio_name = None
        if isinstance(mp.current_song, player.Radio):
            next_song = mp.current_song.get_song(mp.current_song.NEXT)
            radio_name = mp.current_song.name()
            song_remaining = mp.current_song.get_song(mp.current_song.CURRENT).remaining()
        else:
            next_song = mp.get_next_song()
            song_remaining = mp.current_song.remaining()
        if not next_song:
            await ctx.reply(f"No song's in the queue? {EMOTES.SILLY}")
            log.error(f"nextsong: No songs in the queue WTF?!")
            return
        if song_remaining is None and radio_name is None:
            await ctx.reply(f"Something went wrong {EMOTES.SILLY}")
            log.error("MusicPlayer: No playback for the current song")
            return
        if song_remaining:
            bucket = ctx.command._buckets.get_bucket(ctx.message)
            bucket.per = song_remaining / 2
            song_end = int(time.time() + song_remaining) + PAUSE_DURATION
            note = f"Playing <t:{song_end}:R>"
        else:
            note = f"Ends `Unknown` {EMOTES.SILLY}"
        requested_by = next_song.requested_by
        if radio_name:
            if requested_by == "True":
                requested_by = "Yes"
            else:
                requested_by = "No"
            footer = radio_name
            if radio_name == player.Radio21.name():
                footer += f"         Requested: {requested_by}"
        else:
            requested_by = requested_by or self.bot.user.name
            footer = f'Requested by "{requested_by}"'
        if mp.is_paused():
            note = f"Playing `PAUSED` {EMOTES.PAUSE}"
        if isinstance(next_song, player.Radio):
            embed = self.get_radio_embed(next_song, note, footer)
            emote_str = next_song.emote()
            discord_file = None
        else:
            embed, discord_file = await self.get_song_embed(next_song, note, footer)
            cover_by = parse_cover_by(next_song.cover_artists)
            emote_str = EMOTES.JAM
            match cover_by:
                case CoverBy.Vedal:
                    pass
                case CoverBy.Twins:
                    emote_str = EMOTES.NEUROJAM + EMOTES.EVILJAM
                case CoverBy.Neuro:
                    emote_str = EMOTES.NEUROJAM
                case CoverBy.Evil:
                    emote_str = EMOTES.EVILJAM
        try:
            await ctx.reply(f"Next song: {emote_str}", embed=embed, file=discord_file)
        except discord.errors.HTTPException as e:
            if e.code == 40005:
                await ctx.reply(f"Next song: {emote_str}", embed=embed)
            else:
                raise

    @commands.command(priority=5)
    @cmd_verify()
    async def queue(self, ctx: commands.Context, page: int = 1):
        """Current queue (next 10 songs)"""
        mp = self.get_music_player(ctx)
        queue_size = len(mp.requests_cache) + len(mp.cache)
        offset = page - 1
        if offset < 0 or offset * 10 >= queue_size:
            await ctx.reply(f"No [1-{(queue_size + 9) // 10}] {EMOTES.STARE}")
            return

        description = ""
        if page == 1:
            description = f" - ▶️ __{mp.current_song.song_name()}__ 🎵\n-# (playing right now)\n"
        # Show max 10 in a queue
        for song in islice(chain(mp.requests_cache, mp.cache), offset * 10, offset * 10 + 10):
            if song in mp.requests_cache:
                description += f"- **{song.song_name()}**\n"
            else:
                description += f"- {song.song_name()}\n"

        embed = discord.Embed(title=f"📜 Queue", description=description, color=COLORS.QUEUE)
        embed.set_footer(text=f"(page {page}/{(queue_size + 9) // 10})")
        await ctx.reply(embed=embed)

    @commands.command(priority=8, aliases=("sr", "songrequests"))
    @cmd_verify()
    async def songrequest(
        self, ctx: commands.Context, *, search_string: typing.Union[discord.PartialEmoji, str]
    ):
        """Song request"""
        if isinstance(search_string, discord.PartialEmoji):
            search_string = search_string.name
        post_data = song_search(
            search=search_string, page=1, pageSize=1, sortBy="KaraokeDate", sortDesc=True
        )
        response = await self.bot.fetch_json_data(SEARCH_API, post=post_data)
        if response.error:
            await ctx.reply(f"Got {response.error} {EMOTES.SILLY}")
            return
        if response.status != 200:
            await ctx.reply(f"Something went wrong, status code: `{response.status}` {EMOTES.SILLY}")
            return
        if not response.json_data or "items" not in response.json_data:
            log.warning(f"term: '{search_string}' resulted in empty response")
            await ctx.reply(f"Got empty request back {EMOTES.SAD}")
            return

        result_list = response.json_data["items"]
        if len(result_list) == 0:
            char_limit = 20
            if len(search_string) > char_limit:
                truncated = search_string[:char_limit] + "..."
            else:
                truncated = search_string
            await ctx.reply(f"No results for `{truncated}` {EMOTES.SIDE_EYE}")
            return

        mp = self.get_music_player(ctx)
        queue_duration = mp.request_queue_duration()
        playing_in_str = f"`PAUSED` {EMOTES.PAUSE}"
        if not mp.is_paused():
            if queue_duration is not None:
                playing_in = int(time.time()) + queue_duration
                playing_in_str = f"<t:{playing_in}:R>"
            else:
                playing_in_str = f"`Unknown` {EMOTES.SILLY}"

        position, song = mp.request_song(result_list[0], ctx.author.name)
        stats.song_requested(ctx.guild.id, ctx.author.id, song.get_id())
        await ctx.reply(
            f"Added `{song.song_name()}` at position {position} in the queue\nPlaying {playing_in_str}"
        )
        mp.refill()

    @app_commands.command()
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def randomsong(self, interact: discord.Interaction):
        """Random song from neurokaraoke.com"""
        await interact.response.defer(ephemeral=False)
        repl = interact.followup.send
        response = await self.bot.fetch_json_data(RANDOM_API)
        if response.error:
            await repl(f"Got {response.error} {EMOTES.SILLY}", ephemeral=True)
            return
        if response.status != 200:
            await repl(
                f"Something went wrong, status code: `{response.status}` {EMOTES.SILLY}",
                ephemeral=True,
            )
            return
        data = response.json_data
        if not data or not isinstance(data, list) or len(data) == 0:
            await repl(
                f"Unable to fetch data from api.neurokaraoke.com {EMOTES.SAD}", ephemeral=True
            )
            return
        embed, discord_file = await self.get_song_embed(player.Song(data[0]))
        vc = interact.guild.voice_client
        view = utils.MISSING
        if vc and self.get_music_player(interact) and interact.channel.id == vc.channel.id:
            view = discord.ui.View(timeout=60)
            view.add_item(RequestButton(data[0]))

            async def on_view_timeout():
                if view.message:
                    try:
                        await view.message.edit(embed=embed, view=None)
                    except discord.NotFound:
                        pass

            view.on_timeout = on_view_timeout
        try:
            if discord_file is None:
                discord_file = utils.MISSING
            await repl(embed=embed, view=view, file=discord_file)
        except discord.errors.HTTPException as e:
            if e.code == 40005:
                await repl(embed=embed, view=view)
            else:
                raise
        if view:
            view.message = await interact.original_response()

    @commands.command(priority=7)
    @cmd_verify()
    async def updatestatus(self, ctx: commands.Context, update: bool):
        """Disable/enable bot updating VC status with song name"""
        mp = self.get_music_player(ctx)
        if mp.update_status != update:
            if update:
                await ctx.reply(f"Status updates back ON {EMOTES.OK}")
                await self.set_voice_status(ctx.channel, mp.current_song.song_name(), mp.is_paused())
            else:
                await ctx.reply(f"Status updates OFF {EMOTES.NWELIV}")
        mp.update_status = update

    @commands.command(aliases=("fs",))
    async def findsong(self, ctx: commands.Context, *, search_string: str):
        """Lookup for specific song, allows request from the list if used in VC"""
        # we pull max 60 songs since the view shows up to 6 songs at once
        post_data = song_search(
            search=search_string, page=1, pageSize=60, sortBy="KaraokeDate", sortDesc=True
        )
        response = await self.bot.fetch_json_data(SEARCH_API, post=post_data)
        if response.error:
            await ctx.reply(f"Got {response.error} {EMOTES.SILLY}")
            return
        if response.status != 200:
            await ctx.reply(f"Something went wrong, status code: `{response.status}` {EMOTES.SILLY}")
            return
        if not response.json_data or "items" not in response.json_data:
            await ctx.reply(f"Got empty request back {EMOTES.SAD}")
            return
        result_list = response.json_data["items"]
        if len(result_list) == 0:
            char_limit = 20
            if len(search_string) > char_limit:
                truncated = search_string[:char_limit] + "..."
            else:
                truncated = search_string
            await ctx.reply(f"No results for `{truncated}` {EMOTES.SIDE_EYE}")
            return

        request_allowed = ctx.voice_client and ctx.voice_client.channel.id == ctx.channel.id
        view = SongLookupView(result_list, request_allowed, ctx.author.id)
        view.message = await ctx.reply(view=view)

    @commands.command(aliases=("playlists", "pl"))
    @cmd_verify()
    async def playlist(self, ctx: commands.Context, url: str):
        """Open playlist from neurokaraoke (full url, just id or "lofi"), allowing you to request songs from it"""
        artist_playlist = False
        if url.lower() == "lofi":
            playlist_id = "c33f0038-3abc-4343-9ab9-f597581ce279"
        else:
            playlist_id = url.strip("<>").strip("/").rsplit("/", 1)[-1]
            if len(playlist_id) != 36:
                await ctx.reply(f"Invalid playlist link or id {EMOTES.SILLY}")
                return
        artist_playlist = "/artist/" in url
        if not artist_playlist:
            response = await self.bot.fetch_json_data(
                PLAYLIST_API + playlist_id, headers={"x-guest-id": "67"}
            )
            if response.error:
                await ctx.reply(f"Got {response.error} {EMOTES.SILLY}")
                return
            if response.status == 204 and len(url) < 40:
                artist_playlist = True
            elif response.status != 200:
                await ctx.reply(
                    f"Something went wrong, status code: `{response.status}` {EMOTES.SILLY}"
                )
                return
            else:
                json_result = response.json_data
        if artist_playlist:
            response = await self.bot.fetch_json_data(ARTIST_API + playlist_id)
            if response.error:
                await ctx.reply(f"Got {response.error} {EMOTES.SILLY}")
                return
            if response.status != 200:
                await ctx.reply(
                    f"Something went wrong, status code: `{response.status}` {EMOTES.SILLY}"
                )
                return
            json_result = response.json_data
        if "songListDTOs" not in json_result or len(json_result["songListDTOs"]) == 0:
            await ctx.reply(f"Didn't get playlist back {EMOTES.SAD}")
            return
        view = SongLookupView(
            json_result["songListDTOs"], True, ctx.author.id, json_result.get("name")
        )
        view.message = await ctx.reply(view=view)

    @commands.command(aliases=("setlists",))
    @cmd_verify()
    async def setlist(self, ctx: commands.Context):
        """Show all avaible karaoke setlists, allows opening them and songs request"""
        response = await self.bot.fetch_json_data(SETLISTS_API)
        if response.error:
            await ctx.reply(f"Got {response.error} {EMOTES.SILLY}")
            return
        if response.status != 200:
            await ctx.reply(f"Something went wrong, status code: `{response.status}` {EMOTES.SILLY}")
            return
        json_result = response.json_data
        if not json_result or len(json_result) == 0:
            await ctx.reply(f"Didn't get playlist back {EMOTES.SILLY}")
            return
        view = SetlistsView(json_result, ctx.author.id)
        view.message = await ctx.reply(view=view)

    @commands.command()
    @cmd_verify()
    async def radio(self, ctx: commands.Context, *, radio: str):
        """Request radio playback, avaible options: [Radio21, SwarmFM]"""
        mp = self.get_music_player(ctx)
        if radio.lower() in ("21", "radio21", "neuro_21", "radio 21", "radio-21"):
            radio_type = player.RadioType.Radio21
        elif radio.lower() in ("swarmfm", "swfm", "sw.fm", "sw-fm", "swarm-fm", "swarm fm", "swarm"):
            radio_type = player.RadioType.SwarmFM
        else:
            await ctx.reply(f"Unknown radio, avaible options: [Radio21, SwarmFM]")
            return
        queue_duration = mp.request_queue_duration()
        playing_in_str = f"`PAUSED` {EMOTES.PAUSE}"
        if not mp.is_paused():
            if queue_duration is not None:
                playing_in = int(time.time()) + queue_duration
                playing_in_str = f"<t:{playing_in}:R>"
            else:
                playing_in_str = f"`Unknown` {EMOTES.SILLY}"
        position = mp.request_radio(radio_type, ctx.author.name)
        if radio_type == player.RadioType.Radio21:
            radio_name = player.Radio21.name()
        elif radio_type == player.RadioType.SwarmFM:
            radio_name = player.SwarmFM.name()
        await ctx.reply(
            f"Added `{radio_name}` at position {position} in the queue\nPlaying {playing_in_str}"
        )

    def get_music_player(self, ctx: commands.Context) -> player.MusicPlayer:
        return self.music_players.get(ctx.guild.id)

    async def set_voice_status(self, channel: discord.VoiceChannel, text: str, is_paused: bool):
        try:
            self.voice_statuses[channel.guild.id] = text
            if is_paused:
                new_voice_status = f"{EMOTES.PAUSE} {text}"
            else:
                new_voice_status = text
            await channel.edit(status=new_voice_status)
        except:
            log.exception("set_voice_status exception:")

    async def radio_update_status(self, guild_id: int):
        mp = self.music_players.get(guild_id)
        if not mp or not mp.update_status:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        retry_count = 0
        await asyncio.sleep(1)
        try:
            while True:
                mp.current_song.get_data(True)
                song_name = mp.current_song.song_name()
                if self.voice_statuses.get(guild_id, "") != song_name:
                    voice_channel = guild.voice_client.channel
                    await self.set_voice_status(voice_channel, song_name, mp.is_paused())
                    return
                retry_count += 1
                if retry_count > 5:
                    log.debug("radio_update_status: was unable to update vc status for the radio")
                    return
                await asyncio.sleep(3)
        except:
            log.exception("radio_update_status: exception during status update")

    async def swarmfm_song_update(self, ref: weakref.ReferenceType[player.SwarmFM], guild_id: int):
        while True:
            swarmfm = ref()
            if swarmfm is None:
                return
            await self.radio_update_status(guild_id)
            data = swarmfm.get_data()
            position = data.get("position", 0)
            duration = data.get("current", {}).get("duration", 30)
            swarmfm = None
            data = None
            await asyncio.sleep(duration - position)

    async def start(self, channel: discord.VoiceChannel):
        vc = channel.guild.voice_client
        if not vc:
            return

        if self.music_players.get(channel.guild.id):
            self.music_players[channel.guild.id] = None
            log.warning(
                f"start: Overwriting music player, server: {channel.guild.name}[{channel.guild.id}]"
            )
        if vc.is_playing():
            self.error_time[channel.guild.id] = time.time()
            vc.stop()
        start_wait = time.perf_counter()
        response = await self.bot.fetch_json_data(RANDOM_API)
        if response.error:
            raise TypeError(
                f"MusicPlayer: Unable to fetch random queue from api.neurokaraoke.com, {response.error}"
            )
        if response.status != 200:
            raise TypeError(
                f"MusicPlayer: Unable to fetch random queue from api.neurokaraoke.com, status code: {response.status}"
            )
        data = response.json_data
        if not isinstance(data, list) or len(data) == 0:
            raise TypeError(
                f"MusicPlayer: Unable to fetch random queue from api.neurokaraoke.com, data: {data}"
            )
        new_mp = player.MusicPlayer(data)
        song_name = new_mp.current_song.song_name()
        self.music_players[channel.guild.id] = new_mp
        # sleep for about 3s before starting, include the download and processing in the wait
        remaining = max(0, 3 - (time.perf_counter() - start_wait))
        await asyncio.sleep(remaining)
        await self.play_current(vc)
        await channel.send(f"Now playing `{song_name}` {EMOTES.JAM}")
        log.info(f"start: Starting karaoke in: ({channel.guild.name} / {channel.name})")
        new_mp.refill()

    async def play_current(self, vc: discord.VoiceClient, start_paused=False):
        mp = self.get_music_player(vc)
        if not mp.current_song.has_playback():
            await vc.channel.send(EMOTES.LOADING)
            if mp.refill_task is not None:
                await mp.refill_task
            if not mp.current_song.has_playback():
                log.warning(
                    f"play_current: no playback for current song. Requested ({mp.current_song.requested_by is not None}) Attempting to download again"
                )
                try:
                    mp.current_song.download(None)
                except Exception:
                    log.exception("play_current: error during download")
                if not mp.current_song.has_playback():
                    log.error(
                        f"play_current: could not download the song: {mp.current_song.dump_json()}"
                    )
                    self.playback_end(vc, None)
                    return

        try:
            log.info(
                f"play_current: Starting playback '{mp.current_song.song_name()}' in {vc.guild.name}/{vc.channel.name}"
            )
            if mp.current_song.playback.start:
                if (
                    isinstance(mp.current_song, player.SwarmFM)
                    and not mp.current_song.song_update_running
                ):
                    ref = weakref.ref(mp.current_song)
                    task = self.bot.loop.create_task(self.swarmfm_song_update(ref, vc.guild.id))
                    task.add_done_callback(self.scheduled_task_done)
                    mp.current_song.song_update_running = True
                    mp.current_song.playback.start(None)
                else:
                    set_status_lambda = lambda: asyncio.run_coroutine_threadsafe(
                        self.radio_update_status(vc.guild.id), self.bot.loop
                    )
                    mp.current_song.playback.start(set_status_lambda)

            vc.play(
                mp.current_song.playback,
                bitrate=OPUS_BITRATE,
                signal_type="music",
                after=lambda e: self.playback_end(vc, e),
            )
            if start_paused:
                vc.pause()
            stats.update(vc.guild.id, mp.current_song, self.get_members_listening(vc.channel))
        except discord.ClientException as e:
            if "Not connected to voice" in str(e):
                log.error("play_current: Bot not connected to VC?")
                self.error_time[vc.guild.id] = time.time()
                vc.stop()
                await vc.guild.voice_client.disconnect(force=True)
                return
            elif "Already playing audio" in str(e):
                log.error("play_current: Already playing?")
                self.error_time[vc.guild.id] = time.time()
                vc.stop()
                await vc.guild.voice_client.disconnect(force=True)
                return
        except Exception:
            cs = mp.current_song
            size = cs.playback.size() if cs and cs.has_playback() else None
            log.exception(f"play_current: could not start the playback. Playback size: {size}")
            if cs:
                log.error(f"Song data:{cs.dump_json()}")
            self.playback_end(vc, None)
        else:
            if mp.update_status:
                song_name = mp.current_song.song_name()
                await self.set_voice_status(vc.channel, song_name, start_paused)

    def playback_end(self, vc: discord.VoiceClient, error):
        if error:
            log.error(f"Error during playback: {error}, server: {vc.guild.name}")
        if (self.error_time.get(vc.guild.id, 0) + 5) > time.time():
            return
        future = asyncio.run_coroutine_threadsafe(self.next_song(vc.guild.id), self.bot.loop)
        future.add_done_callback(self.scheduled_task_done)

    def scheduled_task_done(self, fut):
        try:
            fut.result()
        except Exception:
            log.exception("Playing next song failed:")

    async def next_song(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.error(f"next_song: could not get guild (ID: {guild_id})")
            return
        log.info(f"next_song: attempt, server: {guild.name}[{guild_id}]")
        vc = guild.voice_client
        mp = self.music_players.get(guild_id)
        # Do not try to load next song if not in vc or no player (probably restarting)
        song = mp.current_song if mp else None
        if not vc or not mp:
            log.warning(
                f"next_song: STOP Voice:{vc is not None}, MusicPlayer:{mp is not None}, server: {guild.name}[{guild_id}]"
            )
            return
        # Force refill if no songs in cache (shouldn't really happen ever)
        if len(mp.requests_cache) == 0 and len(mp.cache) == 0:
            log.warning(f"next_song: forcing refill, server: {guild.name}[{guild_id}]")
            if mp.refill_task is not None:
                await mp.refill_task
            mp.refill(True)
        else:
            mp.refill()
        await asyncio.sleep(PAUSE_DURATION)
        log.info(f"next_song: load and play next song, server: {guild.name}[{guild_id}]")
        stats.cache_song(guild_id, song)
        mp.load_next_song()
        await self.play_current(vc)

    async def get_song_embed(
        self,
        song: player.Song,
        last_section: str | None = None,
        footer: str | None = None,
        remaining: int = None,
    ):
        original_by = song.original_artists
        date = song.song_info.get("streamDate")
        if not date:
            date = song.song_info.get("karaokeDate")
        if date:
            date = datetime.datetime.fromisoformat(date).strftime("%B %d, %Y")
        duration = song.duration or 0
        minutes, seconds = divmod(round(duration), 60)
        song_url = song.get_url()
        cover_str = song.cover_artists
        cover_by = parse_cover_by(cover_str)

        color = COLORS.EMBED_DEFAULT
        match cover_by:
            case CoverBy.Vedal:
                color = COLORS.VEDAL
            case CoverBy.Twins:
                color = COLORS.TWINS
            case CoverBy.Neuro:
                color = COLORS.NEURO
            case CoverBy.Evil:
                color = COLORS.EVIL

        play_count = song.song_info.get("playCount")
        song_name = song.song_name()
        description = ""
        if cover_str:
            description = f"Cover by {cover_str}\n\n"
        description += f"Original by {original_by}\n\n"
        if date:
            description += f"Stream date: {date}"
        if remaining and duration != 0:
            pminutes, pseconds = divmod(round(duration - remaining), 60)
            seg = int((remaining * 10) / duration)
            description += (
                f"\n`{pminutes}:{pseconds:02} {'▬'*(10-seg)}🔘{'▬'*seg} {minutes}:{seconds:02}`"
            )
            if play_count:
                description += f"\n{play_count} plays"
        else:
            description += f"\n{minutes}:{seconds:02}"
            if play_count:
                description += f"  {play_count} plays"
        if last_section:
            description += f"\n\n{last_section}"
        embed = discord.Embed(title=song_name, description=description, color=color, url=song_url)
        discord_file = None
        image_data = await song.get_cover_art(True, self.bot.session)
        if image_data:
            if type(image_data) is str:
                embed.set_thumbnail(url=image_data)
            elif type(image_data) is discord.File:
                embed.set_thumbnail(url=image_data.uri)
                discord_file = image_data
            else:
                log.error(
                    f"get_song_embed: got unknown data type for song cover: {type(image_data)}"
                )

        embed.set_footer(text=footer)
        return embed, discord_file

    @staticmethod
    def get_radio_embed(
        radio: player.Radio,
        last_section: str | None = None,
        footer: str | None = None,
    ):
        embed = discord.Embed(
            title=radio.name(), description=last_section, color=radio.color(), url=radio.get_url()
        )
        embed.set_thumbnail(url=radio.logo_url())
        embed.set_footer(text=footer)
        return embed

    def get_members_listening(self, channel: discord.VoiceChannel) -> set[int]:
        return {
            member.id
            for member in channel.members
            if not (member.bot or member.voice.deaf or member.voice.self_deaf)
        }

    def update_stats(self, guild_id: int):
        # helper used from utility_interface
        mp = self.music_players.get(guild_id)
        song = mp.current_song if mp else None
        listening = set()
        guild = self.bot.get_guild(guild_id)
        if guild:
            vc = guild.voice_client
            if vc:
                listening = self.get_members_listening(vc.channel)
        stats.update(guild_id, song, listening)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                log.warning(
                    f"Disconnected from voice channel: `{before.channel}` in guild `{before.channel.guild.name}`"
                )
                guild_id = before.channel.guild.id
                mp = self.music_players.get(guild_id)
                if not mp:
                    return
                was_paused = mp.is_paused()
                mp.pause()
                log.warning("Detected active playback, attempting to resume")
                await asyncio.sleep(1)
                if member.guild.voice_client:
                    log.warning("Already connected to voice?")
                vc = await before.channel.connect(reconnect=False)
                # We use play_current so it will continue playing the song
                # Even if alone_counter is met, we need to start playback to put it in valid vc state
                # since the MusicPlayer is paused, it will send silence anyway
                await self.play_current(vc, was_paused)
                if mp.alone_counter > PAUSE_AFTER:
                    pass
                else:
                    # wait a little before resuming
                    await asyncio.sleep(0.2)
                    mp.resume()
            elif before.channel is None and after.channel is not None:
                log.info(
                    f"Connected to voice channel: `{after.channel}` in guild `{after.channel.guild.name}`"
                )
            elif before.channel != after.channel:
                log.info(
                    f"Bot changed channels from `{before.channel.name}` to `{after.channel.name}` in guild: `{member.guild.name}`"
                )
                mp = self.get_music_player(member)
                if not mp:
                    return
                listening = self.get_members_listening(after.channel)
                stats.update(member.guild.id, mp.current_song, listening)
                if mp.update_status:
                    self.set_voice_status(after.channel, mp.current_song.song_name(), mp.is_paused())
            elif before.mute != after.mute:
                guild_id = before.channel.guild.id
                mp = self.music_players.get(guild_id)
                if not mp:
                    return
                if after.mute:
                    mp.pause()
                    listening = self.get_members_listening(before.channel)
                    stats.update(member.guild.id, mp.current_song, listening)
                    await after.channel.send(f"🔇 {EMOTES.SAD}")
                else:
                    await after.channel.send(f"🔊 {EMOTES.HAPPY}")
                    mp.resume()
        else:
            # everyone except this bot:
            vc = member.guild.voice_client
            mp = self.music_players.get(member.guild.id)
            if not vc or not mp:
                # bot no in vc
                return
            stats.update(member.guild.id, mp.current_song, self.get_members_listening(vc.channel))
            if after.channel is not None:
                if vc.channel.id == after.channel.id:
                    mp.alone_counter = 0

    @tasks.loop(minutes=1.0)
    async def check_alone_status(self):
        for guild in self.bot.guilds:
            mp = self.music_players.get(guild.id)
            if not mp:
                continue
            vc = guild.voice_client
            if not vc:
                log.warning(f"Bot has MusicPlayer but it's not in VC rn {guild.name}[{guild.id}]")
                mp.pause()
                continue
            stats.update(guild.id, mp.current_song, self.get_members_listening(vc.channel))
            if mp.is_paused() or vc.is_paused():
                if len(vc.channel.members) == 1:
                    mp.alone_counter += 1
                if mp.alone_counter > PAUSE_AFTER + 2:
                    self.music_players[guild.id] = None
                    await vc.disconnect()
                continue
            # includes the bot itself
            undeafened_members = [
                m for m in vc.channel.members if not (m.voice.self_deaf or m.voice.deaf)
            ]
            if len(undeafened_members) < 2:
                mp.alone_counter += 1
                if mp.alone_counter > PAUSE_AFTER:
                    vc.pause()
                    mp.pause()
                    if mp.update_status:
                        await self.set_voice_status(vc.channel, mp.current_song.song_name(), True)
                    await vc.channel.send(f"No one's listening {EMOTES.SAD}\nPaused ⏸️")

    @check_alone_status.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
