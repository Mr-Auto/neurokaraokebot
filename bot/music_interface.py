import json
import discord
from discord.ext import commands, tasks
import logging
import math
import asyncio
import requests
import time
import numpy
import typing
from enum import Enum
from datetime import datetime
from itertools import chain, islice
from config import (
    EMOTES,
    COLORS,
    ALLOWED_CHANNELS,
    PAUSE_AFTER,
    SEARCH_API,
    RANDOM_API,
    IMAGES_URL,
    PAUSE_DURATION,
    PLAYLIST_API,
)
from player import MusicPlayer, Song, fetch_json_data
import player
from pedalboard import LowShelfFilter
from song_lookup_view import SongLookupView


log = logging.getLogger()


class CoverBy(Enum):
    Vedal = 1
    Twins = 2
    Neuro = 3
    Evil = 4


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
        log.warning(f"parse_cover_by: error during parsing string - '{cover_str}'")
        # default to some color
        return CoverBy.Twins


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
def cmd_verify(allowed_channels=False):
    async def predicate(ctx: commands.Context):
        extra = ""
        if allowed_channels:
            extra = " or allowed channels"
            if ctx.channel.id in ALLOWED_CHANNELS:
                return True
        vc = ctx.voice_client
        mp = ctx.bot.get_cog("MusicCog").get_music_player(ctx)
        if not vc or not mp:
            raise NotAllowedError(
                f"Bot not running, use !karaokehere to invite it to VC. Command allowed only in VC{extra}"
            )

        if (
            ctx.channel.id != vc.channel.id
            or not ctx.author.voice
            or ctx.author.voice.channel.id != vc.channel.id
        ):
            raise NotAllowedError(f"You can only use this command in VC with the bot{extra}")

        return True

    return commands.check(predicate)


def song_search(**kwargs) -> list | None:
    """
    sort by available:
    Title PlayCount KaraokeDate Duration

    all available keys and example data:
    {"search":"text","page": 1,"pageSize": 10,"sortBy":"KaraokeDate","sortDesc": True,"sortDesc":false,"genreIds":null,"themeIds":null,"moodIds":null,"artistIds":null,
    "coverArtistIds":null,"languageIds":null,"energyLevel":null,"tempo":null,"key":null,"karaokeStart":null,"karaokeEnd":null}
    """
    return fetch_json_data(SEARCH_API, post=kwargs)


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.music_players: dict[int, MusicPlayer] = {}
        self.check_alone_status.start()

    def cog_unload(self):
        self.check_alone_status.cancel()
        self.music_players = {}

    @commands.command(priority=1)
    async def karaokehere(self, ctx: commands.Context):
        """Invite bot to VC"""
        mp = self.get_music_player(ctx)
        if ctx.voice_client or mp:
            return
        if ctx.channel.type != discord.ChannelType.voice:
            await ctx.reply(f"Can't play audio in '{ctx.channel.type}' channel! {EMOTES.SAD}")
            return
        channel = ctx.channel
        await channel.connect(reconnect=False)
        await ctx.reply(f"Starting Neuro Karaoke Playback in '{channel}' {EMOTES.HAPPY}")
        try:
            await self.start(ctx)
        except:
            await ctx.reply(f"Something went wrong {EMOTES.SILLY}")
            raise

    @commands.command(priority=2)
    @cmd_verify()
    async def pause(self, ctx: commands.Context):
        vc = ctx.voice_client
        self.get_music_player(ctx).pause()
        if vc.is_playing():
            vc.pause()
            await ctx.reply(f"Paused ⏸️ {EMOTES.PAUSE}")

    @commands.command(priority=2)
    @cmd_verify()
    async def resume(self, ctx: commands.Context):
        vc = ctx.voice_client
        self.get_music_player(ctx).resume()
        if vc.is_paused():
            vc.resume()
            await ctx.reply(f"Resumed ▶️ {EMOTES.JAM}")

    @commands.command()
    @cmd_verify()
    async def reconnect(self, ctx: commands.Context):
        """Reset the bot and reconnect to this VC (kills the queue)"""
        mp = self.get_music_player(ctx)
        self.music_players[ctx.guild.id] = None
        if mp:
            mp.pause()
        vc = ctx.voice_client
        channel = vc.channel
        await ctx.reply(f"Rebooting voice connection... {EMOTES.LOADING}")
        await vc.disconnect()
        await asyncio.sleep(2)
        await channel.connect(reconnect=False)
        await self.start(ctx)

    @commands.command(name="volume", priority=4)
    @cmd_verify()
    async def volume_short(self, ctx: commands.Context, vol: float):
        """Short for !modifiers volume"""
        await ctx.invoke(self.volume, vol=vol)

    @commands.command(name="bass", priority=4)
    @cmd_verify()
    async def bass_short(self, ctx: commands.Context, value: str):
        """Short for !modifiers bass"""
        await ctx.invoke(self.bass, value=value)

    @commands.command(priority=8)
    @cmd_verify()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def skip(self, ctx: commands.Context):
        """Skip current song"""
        next_song = self.get_music_player(ctx).get_next_song()
        vc = ctx.voice_client
        if not vc.is_playing() and not vc.is_paused():
            log.warning("Skip: no current playback?")
            mp = self.get_music_player(ctx)
            mp.load_next_song()
            await self.play_current(vc)
        else:
            ctx.voice_client.stop()
        if next_song is not None:
            await ctx.reply(f"Skipping current song, next: `{next_song.song_name()}` {EMOTES.JAM}")
        else:
            await ctx.reply(f"Skipping current song, no more songs in queue {EMOTES.SILLY}")
            log.error(f"skip: no songs in the queue?")

    @commands.command(priority=6)
    @cmd_verify()
    async def song(self, ctx: commands.Context):
        """Check current song"""
        mp = self.get_music_player(ctx)
        requested_by = mp.current_song.requested_by or self.bot.user.name
        song_remaining = mp.current_song.remaning()
        if song_remaining is None:
            await ctx.reply(f"Something went wrong {EMOTES.SILLY}")
            log.error(f"No playback for the current song!")
            return
        song_end = int(time.time()) + song_remaining
        footer = f'Requested by "{requested_by}"'
        note = f"Ends <t:{song_end}:R>"
        if mp.is_paused():
            note = f"Ends `PAUSED` {EMOTES.PAUSE}"
        embed = self.get_song_embed(mp.current_song.song_info, note, footer)
        cover_str = " & ".join(mp.current_song.song_info["coverArtists"])
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
        await ctx.reply(f"Playing right now {emote_str}", embed=embed)

    @commands.command(priority=6)
    @cmd_verify()
    async def nextsong(self, ctx: commands.Context):
        """Check the next song"""
        next_song = None
        mp = self.get_music_player(ctx)
        next_song = mp.get_next_song()
        if not next_song:
            await ctx.reply(f"No song's in the queue? {EMOTES.SILLY}")
            log.info(f"nextsong: No songs in the queue WTF?!")
            return

        requested_by = next_song.requested_by or self.bot.user.name
        song_remaining = mp.current_song.remaning()
        if song_remaining is None:
            await ctx.reply(f"Something went wrong {EMOTES.SILLY}")
            log.error("MusicPlayer: No playback for the current song")
            return
        song_end = int(time.time()) + song_remaining + PAUSE_DURATION
        footer = f'Requested by "{requested_by}"'
        note = f"Playing <t:{song_end}:R>"
        if mp.is_paused():
            note = f"Playing `PAUSED` {EMOTES.PAUSE}"
        embed = self.get_song_embed(next_song.song_info, note, footer)
        cover_str = " & ".join(next_song.song_info["coverArtists"])
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
        await ctx.reply(f"Next song: {emote_str}", embed=embed)

    @commands.command(priority=5)
    @cmd_verify()
    async def queue(self, ctx: commands.Context):
        """Current queue (next 10 songs)"""
        mp = self.get_music_player(ctx)
        description = ""
        # Show max 10 in a queue
        for song in islice(chain(mp.requests_cache, mp.cache), 10):
            description += f"- {song.song_name()}\n"

        embed = discord.Embed(title="Current queue:", description=description, color=COLORS.QUEUE)
        await ctx.reply(embed=embed)

    @commands.command(priority=8)
    @cmd_verify()
    async def sr(
        self, ctx: commands.Context, *, search_string: typing.Union[discord.PartialEmoji, str]
    ):
        """Song request"""
        if isinstance(search_string, discord.PartialEmoji):
            search_string = search_string.name

        response = song_search(
            search=search_string, page=1, pageSize=1, sortBy="KaraokeDate", sortDesc=True
        )
        if not response or "items" not in response:
            log.warning(f"term: '{search_string}' resulted in empty response")
            await ctx.reply(f"Got empty request back {EMOTES.SAD}")
            return

        result_list = response["items"]
        if len(result_list) == 0:
            char_limit = 20
            if len(search_string) > char_limit:
                truncated = search_string[:char_limit] + "..."
            else:
                truncated = search_string
            await ctx.reply(f"No results for `{truncated}` {EMOTES.SIDE_EYE}")
            return

        mp = self.get_music_player(ctx)
        song_remaining = mp.current_song.remaning() or 0
        queue_duration = mp.request_queue_duration()
        playing_in_str = f"`PAUSED` {EMOTES.PAUSE}"
        if not mp.is_paused():
            if queue_duration is not None:
                playing_in = int(time.time()) + queue_duration + song_remaining + PAUSE_DURATION
                playing_in_str = f"<t:{playing_in}:R>"
            else:
                playing_in_str = f"`Unknown` {EMOTES.SILLY}"

        requested_song = Song(result_list[0], ctx.author.name)
        mp.requests_cache.append(requested_song)
        song_name = requested_song.song_name()
        await ctx.reply(
            f"Added `{song_name}` at position {len(mp.requests_cache)} in the queue\nPlaying {playing_in_str}"
        )
        mp.refill()

    @commands.command()
    @cmd_verify(True)
    async def randomsong(self, ctx: commands.Context):
        """Random song from neurokaraoke.com"""
        data = fetch_json_data(RANDOM_API)
        if not data or not isinstance(data, list) or len(data) == 0:
            await ctx.reply("Unable to fetch data from api.neurokaraoke.com")
            return
        embed = self.get_song_embed(data[0])
        await ctx.reply(embed=embed)

    @commands.command(priority=7)
    @cmd_verify()
    async def updatestatus(self, ctx: commands.Context, update: bool):
        """Disable/enable bot updating VC status with song name"""
        if self.updatestatus != update:
            if update:
                await ctx.reply(f"Status updates back ON {EMOTES.OK}")
                mp = self.get_music_player(ctx)
                song_name = mp.current_song.song_name()
                await ctx.channel.edit(status=song_name)
            else:
                await ctx.reply(f"Status updates OFF {EMOTES.NWELIV}")
        self.updatestatus = update

    @commands.group(priority=3, invoke_without_command=True)
    @cmd_verify()
    async def modifiers(self, ctx: commands.Context):
        """Edit/Reset modifiers"""
        command_names = ", ".join(c.name for c in self.modifiers.commands)
        mp = self.get_music_player(ctx)
        plugin_names = ", ".join(type(plugin).__name__ for plugin in mp.effects_board)
        await ctx.reply(f"Available options: [{command_names}]\nActive plugins: [{plugin_names}]")

    @modifiers.command()
    async def help(self, ctx: commands.Context):
        embed = discord.Embed(title="Modifiers help:", color=discord.Color.orange())
        embed.description = "Placeholder text"
        for command in self.modifiers.commands:
            if command.name == "help":
                continue
            field_name = f"!{command.name}"
            for alias in command.aliases:
                field_name += f"  !{alias}"
            embed.add_field(name=field_name, value=command.help or "", inline=False)
        await ctx.reply(embed=embed)

    @modifiers.command()
    async def reset(self, ctx: commands.Context):
        """Reset all modifiers, like bass, volume etc."""
        mp = self.get_music_player(ctx)
        mp.clear_modifiers()
        if mp.current_song.has_playback():
            mp.current_song.playback.playback_speed(1)
        await ctx.reply(f"Modifiers reset, volume 100% {EMOTES.OK}")

    @modifiers.command()
    async def speed(self, ctx: commands.Context, speed: float):
        """Change playback speed, special non pedalboard modifier, only applied to the current song"""
        if player.MODE != 1:
            await ctx.reply(f"Not supported in the `eager` mode {EMOTES.SILLY}")
            return
        if speed < 0.3 or speed > 3:
            await ctx.reply(f"Value `{speed}` not allowed {EMOTES.SILLY}")
            return
        mp = self.get_music_player(ctx)
        if mp.current_song.has_playback():
            mp.current_song.playback.playback_speed(1 / speed)

    @modifiers.command(aliases=("LowShelfFilter",))
    @cmd_verify()
    async def bass(self, ctx: commands.Context, value: str):
        """Change bass [boost, reset, number in db]"""
        mp = self.get_music_player(ctx)
        gain_db = 0.0
        board = mp.effects_board
        if value.lower() == "reset" or value == "0":
            for p in board:
                if isinstance(p, LowShelfFilter):
                    board.remove(p)
                    break
            mp.fix_limiter()
            await ctx.reply(f"Bass reset {EMOTES.NWELIV}")
            return
        elif value.lower() == "boost":
            gain_db = 4.0
        elif is_number(value):
            gain_db = float(value)
        else:
            await ctx.reply(f"Wrong parameter. Use [reset, boost or number] {EMOTES.STARE}")
            return
        gain_db = numpy.clip(gain_db, -100.0, 20.0)
        low_shelf = None
        for p in board:
            if isinstance(p, LowShelfFilter):
                low_shelf = p
                break

        if low_shelf:
            low_shelf.gain_db = gain_db
        else:
            low_shelf = LowShelfFilter(cutoff_frequency_hz=200, gain_db=gain_db)
            board.insert(0, low_shelf)
            mp.fix_limiter()

        # TODO use based emote only if the bass is positive value
        await ctx.reply(f"Bass adjusted by {gain_db}db {EMOTES.BASED}")

    @modifiers.command(aliases=("Gain",))
    @cmd_verify()
    async def volume(self, ctx: commands.Context, vol: float):
        """Change the volume, values in %"""
        mp = self.get_music_player(ctx)
        vol = numpy.clip(vol, 0, 300.0)
        new_db = 0
        if vol != 100:
            new_db = 20 * math.log10(vol / 100)
        mp.set_volume(new_db)
        await ctx.reply(f"Volume set to {vol}% 🔊")

    @commands.command()
    @cmd_verify(True)
    async def findsong(self, ctx: commands.Context, *, search_string: str):
        """Lookup for specific song, allows request from the list if used in VC"""
        # we pull max 99 songs since the view shows up to 9 songs at once
        # it thorws error at us if we try to show 10
        response = song_search(
            search=search_string, page=1, pageSize=99, sortBy="KaraokeDate", sortDesc=True
        )
        if not response or "items" not in response:
            await ctx.reply(f"Got empty request back {EMOTES.SAD}")
            return

        result_list = response["items"]
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

    @commands.command()
    @cmd_verify()
    async def playlist(self, ctx: commands.Context, url: str):
        """Open playlist from neurokaraoke (full url or just id), allowing you to request songs from it"""
        playlist_id = url.strip("/").rsplit("/", 1)[-1]
        if len(playlist_id) != 36:
            await ctx.reply(f"Invalid playlist link or id {EMOTES.SILLY}")
            return

        response = requests.get(PLAYLIST_API + playlist_id, headers={"x-guest-id": "67"})
        if response.status_code != 200:
            await ctx.reply(
                f"Something went wrong, status code: `{response.status_code}` {EMOTES.SILLY}"
            )
            return

        json_result = response.json()
        if "songListDTOs" not in json_result or len(json_result["songListDTOs"]) == 0:
            await ctx.reply(f"Didn't get playlist back {EMOTES.SILLY}")
            return

        view = SongLookupView(json_result["songListDTOs"], True, ctx.author.id)
        view.message = await ctx.reply(view=view)

    def get_music_player(self, ctx: commands.Context) -> MusicPlayer:
        return self.music_players.get(ctx.guild.id)

    async def start(self, ctx: commands.Context):
        vc = ctx.voice_client
        if not vc:
            return

        if self.music_players.get(ctx.guild.id):
            self.music_players[ctx.guild.id] = None
            log.warning(f"start: Overwriting music player, server: {ctx.guild.name}[{ctx.guild.id}]")
        vc.stop()
        start_wait = time.perf_counter()

        new_mp = MusicPlayer()
        song_name = new_mp.current_song.song_name()
        self.music_players[ctx.guild.id] = new_mp

        # sleep for about 3s before starting, include the download and processing in the wait
        remaining = max(0, 3 - (time.perf_counter() - start_wait))
        await asyncio.sleep(remaining)
        await self.play_current(vc)
        await ctx.send(f"Now playing `{song_name}` {EMOTES.JAM}")
        log.info(f"start: Starting karaoke in: ({ctx.guild.name} / {ctx.channel.name})")
        new_mp.refill()

    async def play_current(self, vc: discord.VoiceClient):
        mp = self.get_music_player(vc)
        if not mp.current_song.has_playback():
            await vc.channel.send(EMOTES.LOADING)
            log.warning(
                f"play_current: no playback for current song. Requested ({mp.current_song.requested_by is not None}) Attempting to download again"
            )
            mp.current_song.download()
            if not mp.current_song.has_playback():
                log.error(
                    f"play_current: could not download the song: {mp.current_song.dump_json()}"
                )
                self.playback_end(vc, None)
                return

        mp.apply_effects_board()
        try:
            log.info(f"play_current: Starting playback '{mp.current_song.song_name()}'")
            vc.play(
                mp.current_song.playback,
                bitrate=192,
                signal_type="music",
                after=lambda e: self.playback_end(vc, e),
            )
        except Exception as e:
            playback_size = (
                mp.current_song.playback.size() if mp.current_song.has_playback() else None
            )
            log.error(
                f"play_current: could not start the playback error: ({e}) Playback size: {playback_size} Song data:"
            )
            log.error(mp.current_song.dump_json())
            self.playback_end(vc, None)
        else:
            if self.updatestatus:
                song_name = mp.current_song.song_name()
                await vc.channel.edit(status=song_name)

    def playback_end(self, vc: discord.VoiceClient, error):
        if error:
            log.error(f"Error during playback: {error}", exc_info=error)

        fut = asyncio.run_coroutine_threadsafe(self.next_song(vc.guild.id), self.bot.loop)
        fut.result()

    async def next_song(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.error(f"next_song: could not get guild (ID: {guild_id})")
            return
        log.info(f"next_song: attempt, server: {guild.name}[{guild_id}]")
        vc = guild.voice_client
        mp = self.music_players.get(guild_id)
        # Do not try to load next song if not in vc or no player (probably restarting)
        if not vc or not mp:
            log.warning(
                f"next_song: STOP Voice:{vc is not None}, MusicPlayer:{mp is not None}, server: {guild.name}[{guild_id}]"
            )
            return
        # Force refill if no songs in cache (shouldn't really happen ever)
        if len(mp.requests_cache) == 0 and len(mp.cache) == 0:
            log.warning(f"next_song: forcing refill, server: {guild.name}[{guild_id}]")
            mp.refill(True)
        else:
            mp.refill()

        await asyncio.sleep(PAUSE_DURATION)
        log.info(f"next_song: load and play next song, server: {guild.name}[{guild_id}]")
        mp.load_next_song()
        await self.play_current(vc)

    def get_song_embed(
        _, song_info: dict, last_section: str | None = None, footer: str | None = None
    ):
        original_by = " & ".join(song_info["originalArtists"])
        date = song_info.get("streamDate")
        if date:
            date = datetime.fromisoformat(date).strftime("%B %d, %Y")
        minutes, seconds = divmod(song_info["duration"] or 0, 60)
        song = Song(song_info)
        song_url = song.get_url()
        cover_str = " & ".join(song_info["coverArtists"])
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

        play_count = song_info["playCount"]
        song_name = song.song_name()
        description = f"Cover by {cover_str}\n\nOriginal by {original_by}\n\nStream date: {date}\n{minutes}:{seconds:02} {play_count} plays"
        if last_section:
            description += f"\n\n{last_section}"
        embed = discord.Embed(title=song_name, description=description, color=color, url=song_url)
        if song_info.get("coverArt") and song_info["coverArt"].get("absolutePath"):
            image_url = IMAGES_URL
            image_url += song_info["coverArt"]["absolutePath"]
            image_url += "/width=900,height=900,quality=90,fit=crop,gravity=auto"
            embed.set_thumbnail(url=image_url)
        embed.set_footer(text=footer)
        return embed

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                log.warning(
                    f"Disconnected from voice channel: {before.channel}[{before.channel.id}]"
                )
                guild_id = before.channel.guild.id
                mp = self.music_players.get(guild_id)
                if not mp:
                    return
                was_paused = mp.is_paused()
                mp.pause()
                log.info("Detected active playback, attempting to resume")
                await asyncio.sleep(1)
                vc = await before.channel.connect(reconnect=False)
                # We use play_current that will continue playing the song
                # Even if alone_counter is met, we need to start playback to put it in valid pause state
                # since the MusicPlayer is paused, it will send silence anyway
                await self.play_current(vc)
                if mp.alone_counter > PAUSE_AFTER or was_paused:
                    vc.pause()
                else:
                    # wait a little before resuming
                    await asyncio.sleep(0.2)
                    mp.resume()
            elif before.channel is None and after.channel is not None:
                log.info(f"Connected to voice channel: {after.channel}[{after.channel.id}]")
            elif before.mute != after.mute:
                guild_id = before.channel.guild.id
                mp = self.music_players.get(guild_id)
                if not mp:
                    return
                if after.mute:
                    mp.pause()
                    await after.channel.send(f"🔇 {EMOTES.SAD}")
                else:
                    await after.channel.send(f"🔊 {EMOTES.HAPPY}")
                    mp.resume()
        else:
            if after.channel is not None:
                vc = member.guild.voice_client
                if not vc:
                    return
                mp = self.music_players.get(member.guild.id)
                if mp and vc.channel.id == after.channel.id:
                    mp.alone_counter = 0

    @tasks.loop(minutes=1.0)
    async def check_alone_status(self):
        for guild in self.bot.guilds:
            mp = self.music_players.get(guild.id)
            if not mp:
                continue
            vc = guild.voice_client
            if not vc or mp.is_paused() or vc.is_paused():
                continue
            # includes the bot itself
            if len(vc.channel.members) < 2:
                mp.alone_counter += 1
                if mp.alone_counter > PAUSE_AFTER:
                    vc.pause()
                    mp.pause()
                    await vc.channel.send(f"No one's around {EMOTES.SAD}\nPaused ⏸️")

    @check_alone_status.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
