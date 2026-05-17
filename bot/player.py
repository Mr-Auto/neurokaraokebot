import enum
import threading
import weakref
import discord
import io
import subprocess
import numpy
import logging
import asyncio
import requests
import json
import time
import pedalboard
import av
from typing import TypeVar
from pedalboard.io import AudioFile
from collections import deque
from itertools import chain, islice
from config import *

T = TypeVar("T")
log = logging.getLogger()
MODE = 1


def fetch_json_data(url: str, get=None, post=None, retries=3):
    log.info(f"fetch_json_data: Fetching json data from '{url}'")
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
            log.info(f"fetch_json_data: Attempt {i + 1} failed: {e}")
            if i < retries - 1:
                time.sleep(2)
            else:
                log.warning("fetch_json_data: All retry attempts failed.")


class ClassLogger(logging.LoggerAdapter):
    def __init__(self, logger, obj):
        classname = f"{obj.__class__.__name__}: " if obj else ""
        super().__init__(logger, {"classspecific": classname})


class PlaybackSource(discord.AudioSource):
    SAMPLE_RATE = 48000
    SAMPLES_PER_20MS = int(0.02 * SAMPLE_RATE)
    BYTES_PER_SECOND = SAMPLE_RATE * 2 * (16 // 8)  # 48KHz, 2 channels, 16bit depth
    BYTES_PER_20MS = int(0.02 * BYTES_PER_SECOND)

    def __init__(self):
        self.paused = False
        self.effects_board = pedalboard.Pedalboard()
        self.log = ClassLogger(log, self)

    def is_opus(self):
        return False

    def set_pause(self, pause: bool):
        if pause and not self.paused:
            self.log.info("Playback Paused")
        elif not pause and self.paused:
            self.log.info("Playback Resumed")
        self.paused = pause

    def duration(self) -> int:
        raise NotImplementedError

    def size(self) -> int:
        raise NotImplementedError

    def remaining(self) -> int:
        raise NotImplementedError

    def seek(self, seconds: float):
        raise NotImplementedError

    def playback_speed(self, speed: float):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def start(self, func):
        pass


class StreamAudioSource(PlaybackSource):
    SILENCE_FRAME = b"\xf8\xff\xfe"
    BUFFER_SIZE = 200

    def __init__(self, url: str, radio=False):
        super().__init__()
        self.log = ClassLogger(log, self)
        self.url = url
        self.radio = radio
        self.current_pts = 0
        self.buffer = deque()
        self._lock = threading.Lock()
        if radio:
            self.container = None
            self.stream = None
            self.packet_generator = None
        else:
            self.container = av.open(url, timeout=5)
            self.stream = self.container.streams.audio[0]
            self.packet_generator = self.container.demux(self.stream)
            self.buffer.extend(islice(self.packet_generator, self.BUFFER_SIZE))

        self.reset = False
        self.end = False
        self._thread_active = False
        self.next_song = False
        self.update_song_func = None

    def start(self, func):
        if self._thread_active:
            return

        if self.radio:
            self.update_song_func = func
            self.container = av.open(self.url, timeout=5)
            self.stream = self.container.streams.audio[0]
            self.packet_generator = self.container.demux(self.stream)
            self.buffer.extend(islice(self.packet_generator, self.BUFFER_SIZE))

        self._thread_active = True
        self_weak = weakref.ref(self)
        thread = threading.Thread(target=self._run_loop, args=(self_weak,), daemon=True)
        thread.start()

    @staticmethod
    def _run_loop(weak_self: weakref.ReferenceType["StreamAudioSource"]):
        container_ref = weak_self().container
        while True:
            this = weak_self()
            if this is None or this.end or not this.container:
                break

            if this.next_song and this.update_song_func:
                this.update_song_func()
                this.next_song = False

            if this.reset:
                this.buffer.clear()
                this.reset = False
                this._reconnect(True)

            reconnected = False
            if not this.paused:
                try:
                    while len(this.buffer) < this.BUFFER_SIZE:
                        new_packet = next(this.packet_generator)
                        this.buffer.append(new_packet)
                except (av.error.OSError, av.error.TimeoutError):
                    this.log.info("lost connection, attempting reconnect")
                    this._reconnect()
                    container_ref = this.container
                    reconnected = True
                except (StopIteration, av.error.EOFError, av.error.ExitError):
                    if this.radio:
                        this.log.info("lost connection, attempting reconnect")
                        this._reconnect(True)
                        container_ref = this.container
                        reconnected = True
                    else:
                        this.end = True
                except Exception as e:
                    this.log.error(f"Unknown exception trying to read packet: {e}")

            this = None
            if not reconnected:
                time.sleep(0.05)
        if container_ref:
            container_ref.close()

    def _reconnect(self, reset=False):
        with self._lock:
            if self.container:
                self.container.close()
            self.container = None
            seek_to = 0
            if not reset:
                if self.buffer:
                    last_packet = self.buffer[-1]
                    if last_packet.pts:
                        seek_to = last_packet.pts

                if seek_to == 0:
                    seek_to = self.current_pts

            try:
                container = av.open(self.url, timeout=5)
                self.stream = container.streams.audio[0]
                self.packet_generator = container.demux(self.stream)
                if not reset and seek_to != 0:
                    self.log.info("seeking to where we left of")
                    container.seek(seek_to, stream=self.stream)
                for packet in self.packet_generator:
                    if self.end:
                        break
                    if packet.pts is not None and packet.pts > seek_to:
                        self.buffer.append(packet)
                        break

                self.log.info("Connection established/recovered.")
                self.container = container
            except Exception as e:
                self.log.error(f"Failed to re-connect: {e}")
                self.end = True
                if container:
                    container.close()

    def set_pause(self, pause: bool):
        if pause and not self.paused:
            self.log.info("Playback Paused")
        elif not pause and self.paused:
            self.log.info("Playback Resumed")
            if self.radio:
                self.reset = True
        self.paused = pause

    def read(self) -> bytes:
        if self.paused:
            return self.SILENCE_FRAME

        while self.buffer:
            packet = self.buffer.popleft()
            if packet.pts is None:
                self.log.info("dropping packet")
            else:
                if self.radio and packet.pts < self.current_pts:
                    self.next_song = True
                self.current_pts = packet.pts
                return bytes(packet)

        if self.end:
            return b""
        else:
            return self.SILENCE_FRAME

    def is_opus(self) -> bool:
        return True

    def duration(self) -> int:
        if self.radio:
            return None
        with self._lock:
            return int(self.stream.duration * self.stream.time_base)

    def size(self) -> int:
        with self._lock:
            return self.container.size if self.container else None

    def remaining(self) -> int:
        if self.radio:
            return None
        with self._lock:
            if self.stream.time_base is None:
                return None

            total_duration = self.stream.duration
            if total_duration is None:
                return 0

            remaining_units = max(0, total_duration - self.current_pts)
            return int(remaining_units * self.stream.time_base)

    def seek(self, seconds: float):
        if self.radio:
            return
        with self._lock:
            timestamp = int(seconds / self.stream.time_base)
            self.container.seek(timestamp, stream=self.stream)
            self.packet_generator = self.container.demux(self.stream)

    def close(self):
        self.end = True
        self.effects_board.reset()


class LazyPCMSource(PlaybackSource):
    def __init__(self, url: str, pre_process=False):
        super().__init__()
        self.log = ClassLogger(log, self)
        if pre_process:
            self.log.info(f"Fetching and converting (ffmpeg) song data from '{url}'")
            # unsupported file format, we use ffmpeg to convert
            command = [
                "ffmpeg",
                "-i",
                url,
                "-c:a",
                "libvorbis",
                "-q:a",
                "8",
                "-ac",
                "2",  # Channels
                "-ar",
                str(self.SAMPLE_RATE),  # since we're converting anyway, might as well resample it
                "-f",
                "ogg",
                "-loglevel",
                "error",
                "pipe:1",  # Output to stdout
            ]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            file_data, ffmpeg_log = process.communicate()
            if ffmpeg_log:
                ffmpeg_log = ffmpeg_log.decode().strip()
            if process.returncode != 0:
                raise RuntimeError(f"{LazyPCMSource.__name__}: ffmpeg returned: {ffmpeg_log}")
            if ffmpeg_log:
                self.log.error(f"ffmpeg returned: {ffmpeg_log}")
        else:
            self.log.info(f"Fetching song data from '{url}'")
            retries = 4
            for i in range(retries):
                try:
                    response = requests.get(url, timeout=8)
                    response.raise_for_status()
                    break
                except (requests.exceptions.RequestException, ValueError) as e:
                    self.log.warning(f"Attempt {i + 1} failed: {e}")
                    if i < retries - 1:
                        time.sleep(2)
                    else:
                        self.log.warning("All retry attempts failed.")
                        raise
            file_data = response.content

        self.audio_file = AudioFile(io.BytesIO(file_data))
        self.buffer = self.audio_file.resampled_to(
            self.SAMPLE_RATE, pedalboard.Resample.Quality.ZeroOrderHold
        )
        if self.buffer.num_channels != 2:
            self.log.warning(f"File number of channels: {self.buffer.num_channels} != 2")

    def read(self):
        """Discord calls this every 20ms to get the next chunk of audio."""
        if self.paused:
            return b"\x00" * self.BYTES_PER_20MS

        chunk = self.buffer.read(self.SAMPLES_PER_20MS)
        # check if we got empty result (end of file)
        if chunk.shape[1] == 0:
            # ends playback
            return b""

        # if mono double it for stereo
        if chunk.shape[0] == 1:
            chunk = numpy.repeat(chunk, 2, axis=0)

        processed_audio = self.effects_board(chunk, self.SAMPLE_RATE, reset=False)
        processed_audio *= 32767.0
        numpy.clip(processed_audio, -32768, 32767, out=processed_audio)
        pcm_chunk = processed_audio.astype(numpy.int16).T.tobytes()
        chunk_size = len(pcm_chunk)
        if chunk_size < self.BYTES_PER_20MS:
            padding = self.BYTES_PER_20MS - chunk_size
            pcm_chunk += b"\x00" * padding
        elif len(pcm_chunk) > self.BYTES_PER_20MS:
            self.log.error(
                f"Something went wrong, got more then 20ms of data.\nActual size: {chunk_size} expected: {self.BYTES_PER_20MS} index at {self.buffer.tell()}/{self.buffer.frames}"
            )
        return pcm_chunk

    def seek(self, seconds: float):
        """Move the internal pointer to a specific second."""
        target_frame = int(seconds * self.SAMPLE_RATE)
        self.buffer.seek(target_frame)

    def duration(self) -> int:
        """Duration of the song in seconds"""
        return self.buffer.duration

    def size(self) -> int:
        """Numer of samples in the buffer"""
        return self.buffer.frames

    def remaining(self) -> int:
        """Remaining time in seconds"""
        return (self.buffer.frames - self.buffer.tell()) // self.SAMPLE_RATE

    def playback_speed(self, speed: float):
        self.paused = True
        current_pos = self.buffer.tell()
        current_sample_rate = self.buffer.samplerate
        new_sample_rate = self.SAMPLE_RATE * speed
        self.buffer = self.audio_file.resampled_to(
            new_sample_rate, pedalboard.Resample.Quality.ZeroOrderHold
        )
        self.buffer.seek(int(current_pos * (new_sample_rate / current_sample_rate)))
        self.paused = False

    def close(self):
        self.buffer.close()
        self.audio_file = None
        self.effects_board.reset()


class EagerPCMSource(PlaybackSource):
    def __init__(self, url: str):
        super().__init__()
        self.log = ClassLogger(log, self)
        self.log.info(f"Fetching and converting (ffmpeg) song data from '{url}'")
        command = [
            "ffmpeg",
            "-i",
            url,
            "-f",
            "s16le",  # Output format: raw 16-bit PCM
            "-acodec",
            "pcm_s16le",  # Audio codec
            "-ar",
            str(self.SAMPLE_RATE),
            "-ac",
            "2",  # Channels
            "-loglevel",
            "error",
            "pipe:1",  # Output to stdout
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        raw_pcm_data, ffmpeg_log = process.communicate()
        if ffmpeg_log:
            ffmpeg_log = ffmpeg_log.decode().strip()
        if process.returncode != 0:
            raise RuntimeError(f"{EagerPCMSource.__name__}: ffmpeg returned: {ffmpeg_log}")
        if ffmpeg_log:
            self.log.error(f"ffmpeg returned: {ffmpeg_log}")
        self.buffer = io.BytesIO(raw_pcm_data)

    def read(self):
        """Discord calls this every 20ms to get the next chunk of audio."""
        if self.paused:
            return b"\x00" * self.BYTES_PER_20MS

        chunk = self.buffer.read(self.BYTES_PER_20MS)
        if not chunk:
            # ends playback
            return b""

        if self.effects_board:
            audio_data = numpy.frombuffer(chunk, dtype=numpy.int16).reshape(-1, 2).T
            audio_float = audio_data.astype(numpy.float32) / 32768.0
            processed_audio = self.effects_board(audio_float, self.SAMPLE_RATE, reset=False)
            processed_audio *= 32767.0
            numpy.clip(processed_audio, -32768, 32767, out=processed_audio)
            chunk = processed_audio.astype(numpy.int16).T.tobytes()

        chunk_size = len(chunk)
        if chunk_size < self.BYTES_PER_20MS:
            padding = self.BYTES_PER_20MS - chunk_size
            chunk += b"\x00" * padding
        elif chunk_size > self.BYTES_PER_20MS:
            self.log.error(
                f"Something went wrong, got more then 20ms of data.\nActual size: {chunk_size} expected: {self.BYTES_PER_20MS} index at {self.buffer.tell()}/{self.size()}"
            )
        return chunk

    def seek(self, seconds: float):
        """Move the internal pointer to a specific second."""
        self.buffer.seek(int(seconds * 192000))

    def duration(self) -> int:
        return self.size() // self.BYTES_PER_SECOND

    def size(self) -> int:
        """Size of the internal buffer/container, mostly for debug"""
        return self.buffer.getbuffer().nbytes

    def remaining(self) -> int:
        remaining_bytes = self.size() - self.buffer.tell()
        return remaining_bytes // self.BYTES_PER_SECOND

    def close(self):
        self.buffer.close()
        self.effects_board.reset()


class Song:
    def __init__(self, json_data: dict, requested_by: str | None = None):
        if not json_data or not isinstance(json_data, dict):
            raise TypeError(f"Song: trying to create object from wrong data: {json_data}")
        self.playback: PlaybackSource | None = None
        self.song_info = json_data
        self.requested_by = requested_by

    def has_playback(self):
        return self.playback is not None

    def song_name(self) -> str:
        original_by = self.original_artists
        title = self.song_info.get("title", "")
        covered_by = self.cover_artists
        name = ""
        if original_by:
            name = f"{original_by} - "
        name += title
        if covered_by:
            name += f" ({covered_by})"
        return name

    def get_id(self) -> str | None:
        return self.song_info.get("id")

    def get_url(self) -> str | None:
        if self.get_id():
            return SONG_URL + self.get_id()
        else:
            return None

    @property
    def duration(self) -> int | None:
        if self.has_playback():
            try:
                return self.playback.duration()
            except:
                pass

        return self.song_info.get("duration")

    def remaning(self) -> int | None:
        return self.playback.remaining() if self.has_playback() else None

    def download(self):
        opus = self.song_info.get("opus")
        if opus:
            song_url = STORAGE_URL + self.song_info["opus"].strip("/")
        else:
            log.warning(f"Song: '{self.get_id()}' is missing opus!")
            song_url = STORAGE_URL + self.song_info["absolutePath"].strip("/")
        extension = song_url.rsplit(".", 1)[-1].lower()
        try:
            if opus:
                self.playback = StreamAudioSource(song_url)
        except Exception as e:
            log.warning(str(e), exc_info=e)
            opus = None

        if not opus:
            if MODE == 1:
                pre_process = extension not in ["mp3", "flac", "aiff", "ogg", "wav"]
                self.playback = LazyPCMSource(song_url, pre_process)
            else:
                self.playback = EagerPCMSource(song_url)

    def dump_json(self, indent=4) -> str:
        return json.dumps(self.song_info, indent=indent)

    @property
    def cover_artists(self) -> str:
        cover_str = ""
        artists_list = self.song_info.get("coverArtists")
        if artists_list:
            if isinstance(artists_list[0], dict):
                cover_str = " & ".join(artist["name"] for artist in artists_list)
            else:
                cover_str = " & ".join(artists_list)
        return cover_str

    @property
    def original_artists(self) -> str:
        original_str = ""
        artists_list = self.song_info.get("originalArtists")
        if artists_list:
            if isinstance(artists_list[0], dict):
                original_str = " & ".join(artist["name"] for artist in artists_list)
            else:
                original_str = " & ".join(artists_list)
        return original_str

    def get_cover_art(self, download_animated=False) -> str | discord.File | None:
        if self.song_info.get("coverArt") and self.song_info["coverArt"].get("absolutePath"):
            image_url = IMAGES_URL
            image_url += self.song_info["coverArt"]["absolutePath"]
            image_url += "/width=900,height=900,quality=90,fit=crop,gravity=auto"
            if not download_animated or self.song_info["coverArt"]["contentType"] != "image/webp":
                return image_url
            else:
                response = requests.get(image_url, timeout=5)
                if response.status_code != 200:
                    return image_url
                else:
                    with io.BytesIO(response.content) as image_binary:
                        discord_file = discord.File(fp=image_binary, filename="attachment.gif")
                        return discord_file

        return None


class RadioSong(Song):
    def __init__(self, json_data, requested_by=None):
        super().__init__(json_data, requested_by)

    def remaning(self):
        return 0

    def download(self):
        pass

    def get_cover_art(self, download_animated=False):
        art = super().get_cover_art(download_animated)
        if art is None:
            return self.song_info.get("_cover_art")
        return art


class RadioType(enum.Enum):
    Radio21 = enum.auto()
    SwarmFM = enum.auto()


class Radio21(Song):
    def __init__(self, requested_by=None):
        self.playback: StreamAudioSource | None = None
        self.data: dict | None = fetch_json_data(RADIO21_SONGDATA)
        self.fetched_at = time.time()
        self.requested_by = requested_by

    def get_data(self):
        data_age = time.time() - self.fetched_at + 5
        if not self.data or self.data.get("now_playing", {}).get("remaining", -999) < data_age:
            self.data = fetch_json_data(RADIO21_SONGDATA)
            self.fetched_at = time.time()
        return self.data

    def get_song(self, order="now_playing") -> Song | None:
        radio_json = self.get_data()
        if not radio_json:
            return None
        now_playing = radio_json.get(order)
        if not now_playing:
            return None
        song = now_playing.get("song")
        if not song:
            return None
        songId = song.get("custom_fields", {}).get("songId")
        if not songId:
            fake_song_info = {
                "originalArtists": [song.get("artist", "")],
                "duration": now_playing.get("duration", 0),
                "_cover_art": song.get("art"),
                "title": song.get("title"),
            }
            return RadioSong(fake_song_info, "")

        song_info = fetch_json_data(SONG_API + songId)
        if not song_info:
            return None
        else:
            return RadioSong(song_info, "")

    def download(self):
        self.playback = StreamAudioSource(RADIO21_URL, True)

    def has_playback(self):
        return self.playback is not None

    def song_name(self) -> str:
        radio_json = self.get_data()
        return "Radio21: " + radio_json.get("now_playing", {}).get("song", {}).get("text", "")

    def remaning(self) -> int | None:
        radio_json = fetch_json_data(RADIO21_SONGDATA)
        if not radio_json:
            return None
        self.data = radio_json
        self.fetched_at = time.time()
        remaning_time = radio_json.get("now_playing", {}).get("remaining")
        if remaning_time:
            remaning_time += 4
        return remaning_time


class MusicPlayer:
    def __init__(self):
        self.cache = deque()
        self.requests_cache = deque()
        self.effects_board = pedalboard.Pedalboard()
        self.alone_counter = 0
        self.update_status = True
        self.refill_task: asyncio.Future = None
        data = fetch_json_data(RANDOM_API)
        if not isinstance(data, list) or len(data) == 0:
            raise TypeError(
                f"MusicPlayer: Unable to fetch random queue from api.neurokaraoke.com, data: {data}"
            )
        current_song_data = data[0]
        for i in range(1, 50):
            self.cache.append(Song(data[i]))
        self.current_song = Song(current_song_data)
        self.current_song.download()

    def request_queue_duration(self) -> int | None:
        duration = 0
        if isinstance(self.current_song, Radio21):
            return None

        for song in self.requests_cache:
            song_duration = song.song_info.get("duration")
            if song_duration is None:
                return None
            duration += song_duration + PAUSE_DURATION
        duration += PAUSE_DURATION + self.current_song.remaning() or 0
        return duration

    def load_next_song(self):
        if self.current_song.has_playback():
            self.current_song.playback.paused = True
            self.current_song.playback.close()

        if len(self.requests_cache) > 0:
            self.current_song = self.requests_cache.popleft()
        else:
            # unhandled exception if deque empty, but we can't recover anyway
            self.current_song = self.cache.popleft()

    def get_next_song(self) -> Song | None:
        if len(self.requests_cache) > 0:
            return self.requests_cache[0]
        elif len(self.cache) > 0:
            return self.cache[0]
        else:
            return None

    def refill(self, force_wait=False):
        if force_wait:
            log.warning("refill: Forcing refill, expect latency increase")
            self._refill_queue()
            return

        if self.refill_task and not self.refill_task.done():
            log.info("refill: refill already running, skipping")
            return

        loop = asyncio.get_running_loop()
        self.refill_task = loop.run_in_executor(None, self._refill_queue)

    def _refill_queue(self):
        log.info("refill_queue: process starting...")
        to_download = []
        for item in islice(chain(self.requests_cache, self.cache), MAX_CACHE):
            if item.has_playback():
                continue
            to_download.append(item)

        try:
            for item in to_download:
                item.download()
        except Exception as e:
            log.error(f"refill_queue: error during song download: {e}")

        if len(self.cache) < MAX_CACHE + 1:
            data = fetch_json_data(RANDOM_API)
            if not isinstance(data, list) or len(data) == 0:
                log.warning("refill_queue: No data in fetched result")
                return

            for item in data:
                self.cache.append(Song(item))
        log.info("refill_queue: done")

    def pause(self):
        if self.current_song.has_playback():
            self.current_song.playback.set_pause(True)

    def resume(self):
        if self.current_song.has_playback():
            self.current_song.playback.set_pause(False)

    def is_paused(self) -> bool:
        return self.current_song.has_playback() and self.current_song.playback.paused

    def clear_modifiers(self):
        self.effects_board = pedalboard.Pedalboard()
        self.apply_effects_board()

    def get_pedalboard_effect(self, plugin_type: T) -> T | None:
        if plugin_type is None:
            return None

        for effect in self.effects_board:
            if isinstance(effect, plugin_type):
                return effect

        new_effect = plugin_type()
        self.effects_board.append(new_effect)
        self.fix_limiter()
        return new_effect

    def remove_pedalboard_effect(self, plugin_type: T) -> T:
        if plugin_type is None or len(self.effects_board) == 0:
            return
        target_plugin = next((x for x in self.effects_board if isinstance(x, plugin_type)), None)
        if target_plugin is None:
            return
        self.effects_board.remove(target_plugin)
        self.fix_limiter()

    def fix_limiter(self):
        if len(self.effects_board) == 0:
            return
        board = self.effects_board
        if board and isinstance(board[-1], pedalboard.Limiter):
            return
            # if len(board) == 1:
            #     self.effects_board = pedalboard.Pedalboard()
            #     self.apply_effects_board()
            # return

        target_plugin = next((x for x in board if isinstance(x, pedalboard.Limiter)), None)
        if target_plugin is not None:
            board.remove(target_plugin)
            # if len(self.effects_board) != 0:
            board.append(target_plugin)

    def apply_effects_board(self):
        if self.current_song.has_playback():
            self.current_song.playback.effects_board = self.effects_board

    # used for single song request, setlist request have separate logic
    def request_song(self, song_data: dict, requested_by: str):
        requested_song = Song(song_data, requested_by)
        self.requests_cache.append(requested_song)
        self.refill()
        return len(self.requests_cache), requested_song

    def request_radio(self, radio_type: RadioType, requested_by: str):
        match radio_type:
            case RadioType.Radio21:
                self.requests_cache.append(Radio21(requested_by))
            case RadioType.SwarmFM:
                pass

        return len(self.requests_cache)
