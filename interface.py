import discord
from discord.ext import commands, tasks
import logging
import math
import asyncio
import subprocess
import time
import random
import numpy
import sys
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
    SONG_URL,
    IMAGES_URL,
)
from player import MusicPlayer, Song, fetch_json_data
from pedalboard import LowShelfFilter


log = logging.getLogger("interface")


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
        log.error(f"Could not parse cover string {cover_str}")
        raise ValueError("Unknown cover string")


def emote(_emote: EMOTES) -> str:
    return random.choice(_emote.value)


def is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# check if command is allowed in certain situation.
# This also disabled the message event about missing parameter as it needs it satisfy this condition first
def cmd_verify(allowed_channels=False):
    async def predicate(ctx):
        if allowed_channels and ctx.channel.id in ALLOWED_CHANNELS:
            return True
        vc = ctx.voice_client
        mp = ctx.cog.get_music_player(ctx)
        return vc and mp and ctx.channel.id == vc.channel.id

    return commands.check(predicate)


class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_players = {}
        self.check_alone_status.start()

    def cog_unload(self):
        self.check_alone_status.cancel()

    @commands.command(priority=1)
    async def karaokehere(self, ctx):
        """Invite bot to VC"""
        mp = self.get_music_player(ctx)
        if ctx.voice_client or mp:
            return
        if ctx.channel.type != discord.ChannelType.voice:
            await ctx.reply(f"Can't play audio in '{ctx.channel.type}' channel! {emote(EMOTES.SAD)}")
            return
        channel = ctx.channel
        await channel.connect(reconnect=False)
        await ctx.reply(f"Starting Neuro Karaoke Playback in '{channel}' {emote(EMOTES.HAPPY)}")
        await self.start(ctx)

    @commands.command(priority=2)
    @cmd_verify()
    async def pause(self, ctx):
        vc = ctx.voice_client
        if vc.is_playing():
            vc.pause()
            await ctx.reply(f"Paused ⏸️ {emote(EMOTES.PAUSE)}")
            self.get_music_player(ctx).pause()

    @commands.command(priority=2)
    @cmd_verify()
    async def resume(self, ctx):
        vc = ctx.voice_client
        if vc.is_paused():
            vc.resume()
            await ctx.reply(f"Resumed ▶️ {emote(EMOTES.JAM)}")
            self.get_music_player(ctx).resume()

    @commands.command()
    @cmd_verify()
    async def reconnect(self, ctx):
        """Reset the bot and reconnect to this VC (kills the queue)"""
        mp = self.get_music_player(ctx)
        self.music_players[ctx.guild.id] = None
        if mp:
            mp.pause()
        vc = ctx.voice_client
        channel = vc.channel
        await ctx.reply(f"Rebooting voice connection... {emote(EMOTES.LOADING)}")
        await vc.disconnect()
        await asyncio.sleep(2)
        await channel.connect(reconnect=False)
        await self.start(ctx)

    @commands.command(priority=4)
    @cmd_verify()
    async def volume(self, ctx, vol: float):
        """Change the volume"""
        mp = self.get_music_player(ctx)
        vol = numpy.clip(vol, 0, 300.0)
        new_db = 0
        if vol != 100:
            new_db = 20 * math.log10(vol / 100)
        mp.set_volume(new_db)
        await ctx.reply(f"Volume set to {vol}% 🔊")

    @commands.command(priority=4)
    @cmd_verify()
    async def bass(self, ctx, value: str):
        """Change bass [boost, reset, number in db]"""
        if not value:
            return
        mp = self.get_music_player(ctx)
        gain_db = 0.0
        board = mp.effects_board
        if value.lower() == "reset" or value == "0":
            for p in board:
                if isinstance(p, LowShelfFilter):
                    board.remove(p)
                    break
            mp.fix_limiter()
            await ctx.reply(f"Bass reset {emote(EMOTES.NWELIV)}")
            return
        elif value and value.lower() == "boost":
            gain_db = 4.0
        elif is_number(value):
            gain_db = float(value)
        elif value:
            await ctx.reply(f"Wrong parameter. Use [reset, boost or number] {emote(EMOTES.STARE)}")
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
        await ctx.reply(f"Bass adjusted by {gain_db}db {emote(EMOTES.BASED)}")

    @commands.command(priority=8)
    @cmd_verify()
    @commands.cooldown(1, 5)
    async def skip(self, ctx):
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
            await ctx.reply(f"Skipping current song, next: `{next_song.song_name()}`")
        else:
            await ctx.reply("Skipping current song, no more songs in queue")
            log.error(f"skip: no songs in the queue?")

    @commands.command(priority=6)
    @cmd_verify()
    async def song(self, ctx):
        """Check current song"""
        mp = self.get_music_player(ctx)
        requested_by = mp.current_song.requested_by or self.bot.user.name
        song_remaining = mp.current_song.remaning()
        if song_remaining is None:
            log.error("MusicPlayer: No playback for the current song")
            return
        song_end = int(time.time()) + song_remaining
        footer = f'Requested by "{requested_by}"'
        note = f"Ends <t:{song_end}:R>"
        embed = self.get_song_embed(mp.current_song.song_info, note, footer)
        cover_str = " & ".join(mp.current_song.song_info["coverArtists"])
        cover_by = parse_cover_by(cover_str)
        emote_str = emote(EMOTES.JAM)
        match cover_by:
            case CoverBy.Vedal:
                pass
            case CoverBy.Twins:
                emote_str = emote(EMOTES.NEUROJAM) + emote(EMOTES.EVILJAM)
            case CoverBy.Neuro:
                emote_str = emote(EMOTES.NEUROJAM)
            case CoverBy.Evil:
                emote_str = emote(EMOTES.EVILJAM)
        await ctx.reply(content=f"Playing right now {emote_str}", embed=embed)

    @commands.command(priority=6)
    @cmd_verify()
    async def nextsong(self, ctx):
        """Check the next song"""
        next_song = None
        mp = self.get_music_player(ctx)
        next_song = mp.get_next_song()
        if not next_song:
            await ctx.reply(f"No song's in the queue? {emote(EMOTES.SILLY)}")
            log.info(
                f"nextsong: No songs in the queue WTF?! server: {ctx.guild.name}[{ctx.guild.id}]"
            )
            return

        requested_by = next_song.requested_by or self.bot.user.name
        song_remaining = mp.current_song.remaning()
        if song_remaining is None:
            log.error("MusicPlayer: No playback for the current song")
            return
        song_end = int(time.time()) + song_remaining + 2
        footer = f'Requested by "{requested_by}"'
        note = f"Playing in: <t:{song_end}:R>"
        embed = self.get_song_embed(next_song.song_info, note, footer)
        cover_str = " & ".join(next_song.song_info["coverArtists"])
        cover_by = parse_cover_by(cover_str)
        emote_str = emote(EMOTES.JAM)
        match cover_by:
            case CoverBy.Vedal:
                pass
            case CoverBy.Twins:
                emote_str = emote(EMOTES.NEUROJAM) + emote(EMOTES.EVILJAM)
            case CoverBy.Neuro:
                emote_str = emote(EMOTES.NEUROJAM)
            case CoverBy.Evil:
                emote_str = emote(EMOTES.EVILJAM)
        await ctx.reply(content=f"Next song: {emote_str}", embed=embed)

    @commands.command(priority=5)
    @cmd_verify()
    async def queue(self, ctx):
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
    async def sr(self, ctx, *, search):
        """Song request"""
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
        if not response or "items" not in response:
            await ctx.reply(f"Got empty request back {emote(EMOTES.SAD)}")
            return

        result_list = response["items"]
        if len(result_list) == 0:
            await ctx.reply(f"No results for `{search}` {emote(EMOTES.SIDE_EYE)}")
            return

        mp = self.get_music_player(ctx)
        song_remaining = mp.current_song.remaning()
        if song_remaining is None:
            log.error("sr: No playback for the current song")
            song_remaining = 0

        playing_in = int(time.time()) + mp.request_queue_duration() + song_remaining + 2
        requested_song = Song(result_list[0], ctx.author.name)
        mp.requests_cache.append(requested_song)
        song_name = requested_song.song_name()
        await ctx.reply(
            f"Added `{song_name}` at position {len(mp.requests_cache)} in the queue\nPlaying in <t:{playing_in}:R>"
        )
        mp.refill()

    @commands.command()
    @cmd_verify(True)
    async def randomsong(self, ctx):
        """Random song from neurokaraoke.com"""
        data = fetch_json_data(RANDOM_API)
        if not data or not isinstance(data, list) or len(data) == 0:
            await ctx.reply("Unable to fetch data from api.neurokaraoke.com")
            return
        embed = self.get_song_embed(data[0])
        await ctx.reply(embed=embed)

    @commands.command(priority=7)
    @cmd_verify()
    async def updatestatus(self, ctx, update: bool):
        """Disable/enable bot updating VC status with song name"""
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
    @cmd_verify()
    async def resetmodifiers(self, ctx):
        """Reset all song modifiers, like bass, volume etc."""
        mp = self.get_music_player(ctx)
        mp.clear_modifiers()
        await ctx.reply(f"Modifiers reset, volume 100% {emote(EMOTES.OK)}")

    @commands.command(name="commands", hidden=True)
    @cmd_verify(True)
    async def commands_list(self, ctx):
        """List of all commands"""
        embed = discord.Embed(title="Command List", color=discord.Color.orange())
        cmds = [c for c in self.bot.commands if not c.hidden]
        sorted_commands = sorted(
            cmds, key=lambda x: (x.__original_kwargs__.get("priority", 999), x.name)
        )
        for command in sorted_commands:
            embed.add_field(name=f"!{command.name}", value=command.help or "", inline=False)
        await ctx.reply(embed=embed)

    @commands.command(hidden=True)
    @commands.is_owner()
    async def restart(self, ctx):
        await ctx.send(f"Goodbye {emote(EMOTES.SAD)}")
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen([sys.executable] + sys.argv, creationflags=creationflags)
        await self.bot.close()

    @commands.command(hidden=True)
    @commands.is_owner()
    async def exit(self, ctx):
        await ctx.send(f"Goodbye {emote(EMOTES.SAD)}")
        await self.bot.close()

    @commands.command()
    @cmd_verify(True)
    # @commands.is_owner()
    async def emotes(self, ctx, group_name: str):
        group_name = group_name.upper()
        if group_name not in EMOTES.__members__:
            await ctx.reply(f"So such group name {emote(EMOTES.SAD)}")
        else:
            message = ""
            for emote_str in EMOTES[group_name].value:
                message += emote_str
                # just in case send message before we run out of characters
                if len(message) > 2000 - 40:
                    await ctx.reply(message)
                    message = ""
            if message:
                await ctx.reply(message)

    def get_music_player(self, ctx) -> MusicPlayer:
        return self.music_players.get(ctx.guild.id)

    async def start(self, ctx):
        vc = ctx.voice_client
        if not vc:
            return

        if self.music_players.get(ctx.guild.id):
            self.music_players[ctx.guild.id] = None
            log.warning(f"Start: overwriting music player, server: {ctx.guild.name}[{ctx.guild.id}]")
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
        log.info(
            f"Starting karaoke in: {ctx.channel.name}[{ctx.channel.id}] server: {ctx.guild.name}[{ctx.guild.id}]"
        )
        new_mp.refill()

    async def play_current(self, vc):
        mp = self.get_music_player(vc)
        if not mp.current_song.has_playback():
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
            vc.play(mp.current_song.playback, after=lambda e: self.playback_end(vc, e))
        except Exception as e:
            playback_size = (
                len(mp.current_song.playback.buffer) if mp.current_song.has_playback() else None
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

    def playback_end(self, vc, error):
        if error:
            log.error(f"Error during playback: {error}", exc_info=error)
        self.bot.loop.create_task(self.next_song(vc.guild.id))

    async def next_song(self, guild_id: int):
        log.info(f"next_song: attempt (GuildID: {guild_id})")
        vc = self.bot.get_guild(guild_id).voice_client
        mp = self.music_players.get(guild_id)
        # Do not try to load next song if not in vc or no player (probably restarting)
        if not vc or not mp:
            return
        # Force refill if no songs in cache (shouldn't really happen ever)
        if len(mp.requests_cache) == 0 and len(mp.cache) == 0:
            log.warning(f"next_song: forcing refill (GuildID: {guild_id})")
            mp.refill(True)

        mp.refill()
        await asyncio.sleep(2)
        log.info(f"next_song: load and play next song (GuildID: {guild_id})")
        mp.load_next_song()
        await self.play_current(vc)

    def get_song_embed(_, song_info, last_section: str | None = None, footer: str | None = None):
        original_by = " & ".join(song_info["originalArtists"])
        date = datetime.fromisoformat(song_info["streamDate"]).strftime("%B %d, %Y")
        minutes, seconds = divmod(song_info["duration"], 60)
        song_url = SONG_URL + song_info["id"]

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
        song_name = Song(song_info).song_name()
        description = f"Cover by {cover_str}\n\nOriginal by {original_by}\n\nStream date: {date}\n{minutes}:{seconds:02} {play_count} plays"
        if last_section:
            description += f"\n\n{last_section}"
        embed = discord.Embed(title=song_name, description=description, color=color, url=song_url)
        if song_info["coverArt"] and "absolutePath" in song_info["coverArt"]:
            image_url = IMAGES_URL
            image_url += song_info["coverArt"]["absolutePath"]
            image_url += "/width=900,height=900,quality=90,fit=crop,gravity=auto"
            embed.set_thumbnail(url=image_url)
        embed.set_footer(text=footer)
        return embed

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                log.warning(
                    f"Disconnected from voice channel: {before.channel}[{before.channel.id}]"
                )
                guild_id = before.channel.guild.id
                mp = self.music_players.get(guild_id)
                if not mp:
                    return
                mp.pause()
                log.info("Detected active playback, attempting to resume")
                await asyncio.sleep(1)
                vc = await before.channel.connect(reconnect=False)
                # We use play_current that will continue playing the song
                # Even if alone_counter is met, we need to start playback to put it in valid pause state
                await self.play_current(vc)
                if mp.alone_counter > PAUSE_AFTER:
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
                    await after.channel.send(f"🔇 {emote(EMOTES.SAD)}")
                else:
                    await after.channel.send(f"🔊 {emote(EMOTES.HAPPY)}")
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
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            mp = self.music_players.get(guild.id)
            if not mp:
                continue
            vc = guild.voice_client
            if not vc or vc.is_paused():
                continue
            # includes the bot itself
            if len(vc.channel.members) < 2:
                mp.alone_counter += 1
                if mp.alone_counter > PAUSE_AFTER:
                    vc.pause()
                    mp.pause()
                    await vc.channel.send(f"No one around {emote(EMOTES.SAD)}\nPaused ⏸️")
