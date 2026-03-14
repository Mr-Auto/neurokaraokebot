import discord
from discord.ext import commands
import logging
import io
import math
import asyncio
import subprocess
import requests
import time
import random
import numpy
import sys
import os
from dotenv import load_dotenv
from enum import Enum
from collections import deque
from datetime import datetime
from itertools import chain, islice
from pedalboard import (
    Pedalboard,
    LowpassFilter,
    HighpassFilter,
    Reverb,
    Compressor,
    Gain,
    Limiter,
    LowShelfFilter,
    Bitcrush,
)
import conf
from conf import EMOTES, COLORS

# Known issues:
# - when request queue is empty and you request song at the exact time the current playing song ended
# bot will try to play that song immediately, but the playback is not ready, so it will be skipped

MAX_CACHE = 4
RANDOM_API = "https://api.neurokaraoke.com/api/songs/random"
STORAGE_URL = "https://storage.neurokaraoke.com/"
SONG_URL = "https://www.evilkaraoke.com/song/"
SEARCH_API = "https://api.neurokaraoke.com/api/songs"
IMAGES_URL = "https://images.neurokaraoke.com"
# COVER_ARTITS = (
#     "https://api.neurokaraoke.com/api/filters/cover-artists?page=0&pageSize=50"
# )
log = logging.getLogger("discord")


def emote(emote: EMOTES) -> str:
    return random.choice(emote.value)


def clamp(val, minv, maxv):
    return minv if val < minv else maxv if val > maxv else val


def is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def fetch_json_data(url: str, get=None, post=None, retries=3):
    log.info(f"featch_json_data: Fetching json data from '{url}'")
    for i in range(retries):
        try:
            if post:
                response = requests.post(url, json=post, timeout=8)
            elif get:
                response = requests.get(url, json=get, timeout=8)
            else:
                response = requests.get(url, timeout=8)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            log.info(f"Attempt {i+1} failed: {e}")
            if i < retries - 1:
                asyncio.sleep(2)
            else:
                log.info("All retry attempts failed.")


def format_song_name(json_data) -> str:
    name = " & ".join(json_data["originalArtists"])
    name += " - " + json_data["title"]
    name += " (" + " & ".join(json_data["coverArtists"]) + ")"
    return name


class PCMSource(discord.AudioSource):
    def __init__(self, url: str):
        command = [
            "ffmpeg",
            "-i",
            url,
            "-f",
            "s16le",  # Output format: raw 16-bit PCM
            "-acodec",
            "pcm_s16le",  # Audio codec
            "-ar",
            "48000",  # Sample rate
            "-ac",
            "2",  # Channels
            "-loglevel",
            "quiet",  # Keep the console clean
            "pipe:1",  # Output to stdout
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE)
        raw_pcm_data, _ = process.communicate()
        self.buffer = io.BytesIO(raw_pcm_data)
        self.paused = False
        self.effects_board = Pedalboard([])
        self.BYTES_PER_SECOND = (
            192000  # 48000 * 2 * (16 // 8) # 48KHz, 2 channels, 16bit depth
        )

    def read(self):
        """Discord calls this every 20ms to get the next chunk of audio."""
        if self.paused:
            return b"\x00" * 3840
        # Read exactly 20ms of audio
        chunk = self.buffer.read(3840)
        if not chunk:
            # ends playback
            return b""

        if self.effects_board:
            audio_data = numpy.frombuffer(chunk, dtype=numpy.int16).reshape(-1, 2).T
            audio_float = audio_data.astype(numpy.float32) / 32768.0
            processed_float = self.effects_board(audio_float, sample_rate=48000)
            final_pcm = (processed_float * 32767.0).astype(numpy.int16)
            chunk = final_pcm.T.tobytes()

        if len(chunk) < 3840:
            padding = 3840 - len(chunk)
            chunk += b"\x00" * padding
        return chunk

    def is_opus(self):
        return False

    def seek(self, seconds: float):
        """Move the internal pointer to a specific second."""
        self.buffer.seek(int(seconds * 192000))

    def set_pause(self, pause: bool):
        if pause and not self.paused:
            log.info("PCMSource: Playback Paused")
        elif not pause and self.paused:
            log.info("PCMSource: Playback Resumed")
        self.paused = pause

    def duration(self) -> int:
        nbytes = self.buffer.getbuffer().nbytes
        return nbytes // self.BYTES_PER_SECOND

    def remaning(self) -> int:
        total_size = self.buffer.getbuffer().nbytes
        current_pos = self.buffer.tell()
        remaining_bytes = total_size - current_pos
        return remaining_bytes // self.BYTES_PER_SECOND


class Song:
    def __init__(self, playback: PCMSource, json_data, requested_by: str = None):
        self.playback = playback
        self.song_info = json_data
        self.requested_by = requested_by

    def set_volume(self, db_gain: float):
        if self.playback:
            board = self.playback.effects_board
            gain = None
            for p in board:
                if isinstance(p, Gain):
                    gain = p
                    break

            if db_gain == 0:
                if gain:
                    board.remove(gain)
                self.fix_limiter()
            else:
                if gain:
                    gain.gain_db = db_gain
                else:
                    board.append(Gain(gain_db=db_gain))
                    self.fix_limiter()

    def fix_limiter(self):
        if self.playback:
            board = self.playback.effects_board
            for p in board:
                if isinstance(p, Limiter):
                    board.remove(p)
                    break

            if len(board) > 0:
                board.append(Limiter(threshold_db=-0.1))

    def song_name(self) -> str:
        return format_song_name(self.song_info)

    def get_id(self) -> int:
        return self.song_info["id"]

    def remaning(self) -> int:
        return self.playback.remaning() if self.playback else None

    def clear_modifiers(self):
        self.playback.effects_board = Pedalboard([])


class MusicPlayer:
    def __init__(self):
        self.cache = deque()
        self.requests_cache = deque()
        self.update_status = True
        self.refill_task: asyncio.Future = None
        data = fetch_json_data(RANDOM_API)
        if not isinstance(data, list) or len(data) == 0:
            log.error(
                "MusicPlayer: Unable to fetch random queue from api.neurokaraoke.com"
            )
            return
        current_song_data = data[0]
        for i in range(1, 50):
            self.cache.append(Song(None, data[i]))
        song_url = STORAGE_URL + current_song_data["absolutePath"]
        playback = PCMSource(song_url)
        if not playback:
            log.error("MusicPlayer: Unable to construct playback")
            return
        self.current_song = Song(playback, current_song_data)

    def request_queue_duration(self) -> int:
        duration = 0
        for song in self.requests_cache:
            duration += song.song_info["duration"] + 2

        return duration

    def load_next_song(self):
        effects_board = (
            self.current_song.playback.effects_board
            if self.current_song.playback
            else Pedalboard([])
        )
        if len(self.requests_cache) > 0:
            self.current_song = self.requests_cache.popleft()
        else:
            self.current_song = self.cache.popleft()
        if self.current_song.playback:
            self.current_song.playback.effects_board = effects_board

    def get_next_song(self) -> Song:
        if len(self.requests_cache) > 0:
            return self.requests_cache[0]
        elif len(self.cache) > 0:
            return self.cache[0]

    def refill(self, force_await=False):
        if force_await:
            self._refill_queue()
            return

        if self.refill_task and not self.refill_task.done():
            log.info("refill_queue: refill already running, skipping")
            return

        loop = asyncio.get_running_loop()
        self.refill_task = loop.run_in_executor(None, self._refill_queue)

    def _refill_queue(self):
        for item in islice(chain(self.requests_cache, self.cache), MAX_CACHE):
            if item.playback:
                continue
            song_url = STORAGE_URL + item.song_info["absolutePath"]
            item.playback = PCMSource(song_url)
            if not item.playback:
                log.warning(f"refill_queue: Unable to load song url: {song_url}")
                continue

        if len(self.cache) < MAX_CACHE + 1:
            data = fetch_json_data(RANDOM_API)
            if not isinstance(data, list) or len(data) == 0:
                log.warning("refill_queue: No data in fetched result")
                return

            for item in data:
                self.cache.append(Song(None, item))


class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_players = {}

    @commands.command(priority=1)
    async def karaokehere(self, ctx):
        """Invite bot to VC"""
        mp = self.get_music_player(ctx)
        if ctx.voice_client or mp:
            return
        if ctx.channel.type != discord.ChannelType.voice:
            await ctx.reply(
                f"Can't play audio in '{ctx.channel.type}' channel! {emote(EMOTES.SAD)}"
            )
            return
        channel = ctx.channel
        await channel.connect(reconnect=False)
        await ctx.reply(
            f"Starting Neuro Karaoke Playback in '{channel}' {emote(EMOTES.HAPPY)}"
        )
        await self.start(ctx)

    @commands.command(priority=2)
    async def pause(self, ctx):
        if not self.cmd_verify(ctx):
            return
        vc = ctx.voice_client
        if vc.is_playing():
            vc.pause()
            await ctx.reply(f"Paused ⏸️ {emote(EMOTES.PAUSE)}")

    @commands.command(priority=2)
    async def resume(self, ctx):
        if not self.cmd_verify(ctx):
            return
        vc = ctx.voice_client
        if vc.is_paused():
            vc.resume()
            await ctx.reply(f"Resumed ▶️ {emote(EMOTES.JAM)}")

    @commands.command()
    async def reconnect(self, ctx):
        """Reset the bot and reconnect to this VC (kills the queue)"""
        if not self.cmd_verify(ctx):
            return
        mp = self.get_music_player(ctx)
        self.music_players[ctx.guild.id] = None
        if mp and mp.current_song.playback:
            mp.current_song.playback.set_pause(True)

        vc = ctx.voice_client
        channel = vc.channel
        await ctx.reply(f"Rebooting voice connection... {emote(EMOTES.LOADING)}")
        await vc.disconnect()
        await asyncio.sleep(2)
        await channel.connect(reconnect=False)
        await self.start(ctx)

    @commands.command(priority=4)
    async def volume(self, ctx, vol: float):
        """Change the volume"""
        if not self.cmd_verify(ctx):
            return
        mp = self.get_music_player(ctx)
        vol = clamp(vol, 0, 300.0)
        new_db = 0
        if vol != 100:
            new_db = 20 * math.log10(vol / 100)
        mp.current_song.set_volume(new_db)
        await ctx.reply(f"Volume set to {vol}% 🔊")

    @commands.command(priority=4)
    async def bass(self, ctx, value: str):
        """Change bass [boost, reset, value in db]"""
        if not self.cmd_verify(ctx):
            return
        mp = self.get_music_player(ctx)
        gain_db = 0.0
        board = mp.current_song.playback.effects_board
        if value and (value.lower() == "reset" or value == "0"):
            for p in board:
                if isinstance(p, LowShelfFilter):
                    board.remove(p)
                    break
            mp.current_song.fix_limiter()
            await ctx.reply(f"Bass reset {emote(EMOTES.NWELIV)}")
            return
        elif value and value.lower() == "boost":
            gain_db = 4.0
        elif is_number(value):
            gain_db = float(value)
        elif value:
            await ctx.reply(
                f"Wrong parameter. Use [reset, boost or number] {emote(EMOTES.STARE)}"
            )
            return

        gain_db = clamp(gain_db, -100.0, 20.0)
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
            mp.current_song.fix_limiter()

        await ctx.reply(f"Bass adjusted by {gain_db}db {emote(EMOTES.BASED)}")

    @commands.command(priority=8)
    @commands.cooldown(1, 5)
    async def skip(self, ctx):
        """Skip current song"""
        if not self.cmd_verify(ctx):
            return
        next_song = self.get_music_player(ctx).get_next_song()
        ctx.voice_client.stop()

        await ctx.reply(f"Skipping current song, next: `{next_song.song_name()}`")

    @commands.command(priority=6)
    async def song(self, ctx):
        """Check current song"""
        if not self.cmd_verify(ctx):
            return
        mp = self.get_music_player(ctx)
        requested_by = mp.current_song.requested_by or self.bot.user.name
        song_end = int(time.time()) + mp.current_song.remaning()
        footer = f'Requested by "{requested_by}"'
        note = f"Ends <t:{song_end}:R>"
        embed = self.get_song_embed(mp.current_song.song_info, note, footer)
        cover_by = " & ".join(mp.current_song.song_info["coverArtists"])
        emot = EMOTES.JAM
        if cover_by == "Evil":
            emot = EMOTES.EVILJAM
        elif cover_by == "Neuro" or cover_by == "Neuro v1" or cover_by == "Neuro v2":
            emot = EMOTES.NEUROJAM
        await ctx.reply(content=f"Playing right now {emote(emot)}", embed=embed)

    @commands.command(priority=6)
    async def nextsong(self, ctx):
        """Check the next song"""
        if not self.cmd_verify(ctx):
            return
        next_song = None
        mp = self.get_music_player(ctx)
        next_song = mp.get_next_song()
        if not next_song:
            await ctx.reply(f"No song's in the queue? {emote(EMOTES.SILLY)}")
            log.info(f"nextsong: No songs in the queue WTF?! (GuildID: {ctx.guild.id})")
            return

        requested_by = next_song.requested_by or self.bot.user.name
        song_end = int(time.time()) + mp.current_song.remaning() + 2
        footer = f'Requested by "{requested_by}"'
        note = f"Playing in: <t:{song_end}:R>"
        embed = self.get_song_embed(next_song.song_info, note, footer)
        cover_by = " & ".join(next_song.song_info["coverArtists"])
        emot = EMOTES.JAM
        if cover_by == "Evil":
            emot = EMOTES.EVILJAM
        elif cover_by == "Neuro" or cover_by == "Neuro v1" or cover_by == "Neuro v2":
            emot = EMOTES.NEUROJAM
        await ctx.reply(content=f"Next song: {emote(emot)}", embed=embed)

    @commands.command(priority=5)
    async def queue(self, ctx):
        """Current queue (nest 10 songs)"""
        if not self.cmd_verify(ctx):
            return
        mp = self.get_music_player(ctx)

        description = ""
        # Show max 10 in a queue
        for song in islice(chain(mp.requests_cache, mp.cache), 10):
            description += f"- {song.song_name()}\n"

        embed = discord.Embed(
            title="Current queue:", description=description, color=COLORS.QUEUE
        )
        await ctx.reply(embed=embed)

    @commands.command(priority=8)
    async def sr(self, ctx, *, search):
        """Song request"""
        if not self.cmd_verify(ctx):
            return
        data = {
            "search": search,
            "page": 1,
            "pageSize": 1,
            "sortBy": "KaraokeDate",
            "sortDesc": True,
        }
        # sort by available:
        # Title PlayCount KaraokeDate Duration
        # other available keys:
        # {"sortDesc":false,"genreIds":null,"themeIds":null,"moodIds":null,"artistIds":null,
        # "coverArtistIds":null,"languageIds":null,"energyLevel":null,"tempo":null,"key":null,"karaokeStart":null,"karaokeEnd":null}
        response = fetch_json_data(SEARCH_API, post=data)
        if not response or not "items" in response:
            await ctx.reply(f"Got empty request back {emote(EMOTES.SAD)}")
            return

        result_list = response["items"]
        if len(result_list) == 0:
            await ctx.reply(f"No results for `{search}` {emote(EMOTES.SIDE_EYE)}")
            return

        mp = self.get_music_player(ctx)
        playing_in = (
            int(time.time())
            + mp.request_queue_duration()
            + mp.current_song.remaning()
            + 2
        )
        mp.requests_cache.append(Song(None, result_list[0], ctx.author.name))
        song_name = format_song_name(result_list[0])
        await ctx.reply(
            f"Added `{song_name}` at position {len(mp.requests_cache)} in the queue\nPlaying in <t:{playing_in}:R>"
        )
        mp.refill()

    @commands.command()
    async def randomsong(self, ctx):
        """Random song from neurokaraoke.com"""
        if self.cmd_verify(ctx) or ctx.channel.id in conf.ALLOWED_CHANNELS:
            data = fetch_json_data(RANDOM_API)
            if not data or not isinstance(data, list) or len(data) == 0:
                await ctx.reply("Unable to fetch data from api.neurokaraoke.com")
                return
            embed = self.get_song_embed(data[0])
            await ctx.reply(embed=embed)

    @commands.command(priority=7)
    async def updatestatus(self, ctx, update: bool):
        """Disable/enable bot updating VC status with song name"""
        if not self.cmd_verify(ctx):
            return
        if self.updatestatus != update:
            if update:
                await ctx.reply(f"Status updates back ON {emote(EMOTES.OK)}")
                mp = self.get_music_player(ctx)
                song_name = mp.current_song.song_name()
                await ctx.channel.edit(status=song_name)
            else:
                await ctx.reply(f"Status updates OFF {emote(EMOTES.NWELIV)}")
        self.updatestatus = update

    @commands.command(priority=3)
    async def resetmodifiers(self, ctx):
        """Reset all song modifiers, like bass, volume etc."""
        if not self.cmd_verify(ctx):
            return
        mp = self.get_music_player(ctx)
        mp.current_song.clear_modifiers()
        await ctx.reply(f"Modifiers reset, volume 100% {emote(EMOTES.OK)}")

    @commands.command(name="commands", hidden=True)
    async def commands_list(self, ctx):
        """List of all commands"""
        if not self.cmd_verify(ctx):
            return
        embed = discord.Embed(title="Command List", color=discord.Color.orange())
        cmds = [c for c in self.bot.commands if not c.hidden]
        sorted_commands = sorted(
            cmds, key=lambda x: (x.__original_kwargs__.get("priority", 999), x.name)
        )
        for command in sorted_commands:
            embed.add_field(
                name=f"!{command.name}",
                value=command.help or "",
                inline=False,
            )
        await ctx.reply(embed=embed)

    @commands.command(hidden=True)
    async def restart(_, ctx):
        if ctx.author.id != conf.OWNER_ID:
            return
        await ctx.send(f"Goodbye {emote(EMOTES.SAD)}")
        subprocess.Popen(
            [sys.executable] + sys.argv, creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        await bot.close()

    def cmd_verify(self, ctx):
        vc = ctx.voice_client
        mp = self.get_music_player(ctx)
        return vc and mp and ctx.channel.id == vc.channel.id

    def get_music_player(self, ctx) -> MusicPlayer:
        return self.music_players.get(ctx.guild.id)

    async def start(self, ctx):
        vc = ctx.voice_client
        if not vc:
            return

        if self.music_players.get(ctx.guild.id):
            self.music_players[ctx.guild.id] = None
            log.info(f"MusicPlayer: reset (GuildID: {ctx.guild.id})")
        vc.stop()
        start_wait = time.perf_counter()

        new_mp = MusicPlayer()
        song_name = new_mp.current_song.song_name()
        self.music_players[ctx.guild.id] = new_mp

        # sleep for about 3s before starting, include the download and processing in the wait
        remaining = max(0, 3 - (time.perf_counter() - start_wait))
        await asyncio.sleep(remaining)
        await self.play_current(vc)
        await ctx.send(f"Now playing `{song_name}` {emote(EMOTES.JAM)}")
        new_mp.refill()

    async def play_current(self, vc):
        mp = self.get_music_player(vc)
        vc.play(
            mp.current_song.playback,
            after=lambda e: log.error(
                f"Voice playback error: {e}\n(GuildID: {vc.guild.id})", exc_info=e
            )
            if e
            else self.bot.loop.create_task(self.next_song(vc.guild.id)),
        )
        if self.updatestatus:
            song_name = mp.current_song.song_name()
            await vc.channel.edit(status=song_name)

    async def next_song(self, guild_id: int):
        log.info(f"next_song: attempt (GuildID: {guild_id})")
        vc = self.bot.get_guild(guild_id).voice_client
        mp = self.music_players.get(guild_id)
        # Do not try to load next song if not in vc or no player (probably restarting)
        if not vc or not mp:
            return
        log.info(f"next_song: playing next song (GuildID: {guild_id})")
        if len(mp.requests_cache) == 0 and len(mp.cache) == 0:
            mp.refill(True)

        await asyncio.sleep(2)
        mp.load_next_song()
        await self.play_current(vc)
        mp.refill()

    def get_song_embed(_, song_info, last_section: str = None, footer: str = None):
        cover_by = " & ".join(song_info["coverArtists"])
        original_by = " & ".join(song_info["originalArtists"])
        date = datetime.fromisoformat(song_info["streamDate"]).strftime("%B %d, %Y")
        minutes, seconds = divmod(song_info["duration"], 60)
        song_url = SONG_URL + song_info["id"]
        color = COLORS.EMBED_DEFAULT
        if cover_by == "Evil":
            color = COLORS.EVIL
        elif cover_by == "Neuro" or cover_by == "Neuro v1" or cover_by == "Neuro v2":
            color = COLORS.NEURO
        elif "Vedal" in cover_by:
            color = COLORS.VEDAL

        play_count = song_info["playCount"]
        song_name = format_song_name(song_info)
        description = f"Cover by {cover_by}\n\nOriginal by {original_by}\n\nStream date: {date}\n{minutes}:{seconds:02} {play_count} plays"
        if last_section:
            description += f"\n\n{last_section}"
        embed = discord.Embed(
            title=song_name, description=description, color=color, url=song_url
        )
        if song_info["coverArt"] and "absolutePath" in song_info["coverArt"]:
            image_url = IMAGES_URL
            image_url += song_info["coverArt"]["absolutePath"]
            image_url += "/width=900,height=900,quality=90,fit=crop,gravity=auto"
            embed.set_thumbnail(url=image_url)
        embed.set_footer(text=footer)
        return embed


class MyBot(commands.Bot):
    def __init__(self):
        self.music_cog: MusicCog = None
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        self.music_cog = MusicCog(self)
        await self.add_cog(self.music_cog)

    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} servers:")
        for guild in self.guilds:
            print(f"- {guild.name} (ID: {guild.id})")
        print("\n")

    async def on_guild_join(_, guild):
        print(f"\nI have been added to a new server: {guild.name} (ID: {guild.id})\n")
        for channel in guild.text_channels:
            if "general" in channel.name.lower():
                await channel.send(emote(EMOTES.WAVE))
                break

    async def on_voice_state_update(self, member, before, after):
        if member.id == self.user.id:
            if before.channel is not None and after.channel is None:
                log.info(f"Disconnected from voice channel '{before.channel}'")
                guild_id = before.channel.guild.id
                mp = self.music_cog.music_players.get(guild_id)
                if mp and mp.current_song.playback:
                    log.info("Detected active playback, attempting to resume")
                    mp.current_song.playback.set_pause(True)
                    await asyncio.sleep(1)
                    vc = await before.channel.connect(reconnect=False)
                    await self.music_cog.play_current(vc)
                    mp.current_song.playback.set_pause(False)
            elif before.channel is None and after.channel is not None:
                log.info(f"Connected to voice channel '{after.channel}'")
            elif before.mute != after.mute:
                guild_id = before.channel.guild.id
                mp = self.music_cog.music_players.get(guild_id)
                if not mp:
                    return
                if after.mute:
                    await after.channel.send(f"🔇 {emote(EMOTES.SAD)}")
                    mp.current_song.set_pause(True)
                else:
                    await after.channel.send(f"🔊 {emote(EMOTES.HAPPY)}")
                    mp.current_song.set_pause(False)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, commands.MissingRequiredArgument):
            if not ctx.voice_client or ctx.voice_client.channel.id != ctx.channel.id:
                return
            await ctx.reply(
                f"Missing argument: {error.param.name} {emote(EMOTES.SIDE_EYE)}"
            )
            return

        log.error(f"Error in command '{ctx.command}':", exc_info=error)


timestamp = datetime.now().strftime("%y%m%d-%H%M%S")
log_filename = f"neurokaraoke_{timestamp}.log"
handler = logging.FileHandler(filename=log_filename, encoding="utf-8", mode="w")
bot = MyBot()
print("Starting up")
load_dotenv()
bot.run(os.getenv("BOT_TOKEN"), log_handler=handler, log_level=logging.DEBUG)
print("Shutting down")
