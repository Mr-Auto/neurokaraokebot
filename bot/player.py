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
from typing import TypeVar
from pedalboard.io import AudioFile
from collections import deque
from itertools import chain, islice
from config import *

# TODO fix deque mutated during iteration // maybe fixed?
# keep in mind the forced feill, probably need to lock it for that
T = TypeVar("T")
log = logging.getLogger()
MODE = 1


def format_song_name(json_data) -> str:
    name = " & ".join(json_data["originalArtists"])
    name += " - " + json_data["title"]
    if len(json_data["coverArtists"]) != 0:
        name += " (" + " & ".join(json_data["coverArtists"]) + ")"
    return name


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
            log.info(f"fetch_json_data: Attempt {i + 1} failed: {e}")
            if i < retries - 1:
                time.sleep(2)
            else:
                log.warning("fetch_json_data: All retry attempts failed.")


class PCMSource(discord.AudioSource):
    SAMPLE_RATE = 48000
    SAMPLES_PER_20MS = int(0.02 * SAMPLE_RATE)
    BYTES_PER_SECOND = SAMPLE_RATE * 2 * (16 // 8)  # 48KHz, 2 channels, 16bit depth
    BYTES_PER_20MS = int(0.02 * BYTES_PER_SECOND)

    def __init__(self):
        self.paused = False
        self.effects_board = pedalboard.Pedalboard()

    def is_opus(self):
        return False

    def set_pause(self, pause: bool):
        if pause and not self.paused:
            log.info("PCMSource: Playback Paused")
        elif not pause and self.paused:
            log.info("PCMSource: Playback Resumed")
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


class LazyPCMSource(PCMSource):
    def __init__(self, url: str, pre_process=False):
        if pre_process:
            log.info(f"LazyPCMSource: Fetching and converting (ffmpeg) song data from '{url}'")
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
            if ffmpeg_log and len(ffmpeg_log) != 0:
                log.error(f"EagerPCMSource: ffmpeg returned: {ffmpeg_log.decode().strip()}")
        else:
            log.info(f"LazyPCMSource: Fetching song data from '{url}'")
            retries = 4
            for i in range(retries):
                try:
                    response = requests.get(url)
                    response.raise_for_status()
                    break
                except (requests.exceptions.RequestException, ValueError) as e:
                    log.warning(f"LazyPCMSource: Attempt {i + 1} failed: {e}")
                    if i < retries - 1:
                        time.sleep(2)
                    else:
                        log.warning("LazyPCMSource: All retry attempts failed.")
                        raise
            file_data = response.content

        self.audio_file = AudioFile(io.BytesIO(file_data))
        self.buffer = self.audio_file.resampled_to(
            self.SAMPLE_RATE, pedalboard.Resample.Quality.ZeroOrderHold
        )
        if self.buffer.num_channels != 2:
            log.warning(f"LazyPCMSource: File number of channels: {self.buffer.num_channels} != 2")
        super().__init__()

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
            log.error(
                f"LazyPCMSource.read: Something went wrong, got more then 20ms of data.\nActual size: {chunk_size} expected: {self.BYTES_PER_20MS} index at {self.buffer.tell()}/{self.buffer.shape[1]}"
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
        return self.buffer.shape[1]

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


class EagerPCMSource(PCMSource):
    def __init__(self, url: str):
        log.info(f"EagerPCMSource: Fetching and converting (ffmpeg) song data from '{url}'")
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
        if ffmpeg_log and len(ffmpeg_log) != 0:
            log.error(f"EagerPCMSource: ffmpeg returned: {ffmpeg_log.decode().strip()}")
        self.buffer = io.BytesIO(raw_pcm_data)
        super().__init__()

    def read(self):
        """Discord calls this every 20ms to get the next chunk of audio."""
        if self.paused:
            return b"\x00" * self.BYTES_PER_20MS
        # Read exactly 20ms of audio
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
            log.error(
                f"EagerPCMSource.read: Something went wrong, got more then 20ms of data.\nActual size: {chunk_size} expected: {self.BYTES_PER_20MS} index at {self.buffer.tell()}/{self.buffer.shape[1]}"
            )
        return chunk

    def seek(self, seconds: float):
        """Move the internal pointer to a specific second."""
        self.buffer.seek(int(seconds * 192000))

    def duration(self) -> int:
        nbytes = self.buffer.getbuffer().nbytes
        return nbytes // self.BYTES_PER_SECOND

    def size(self) -> int:
        return self.buffer.getbuffer().nbytes

    def remaining(self) -> int:
        total_size = self.buffer.getbuffer().nbytes
        current_pos = self.buffer.tell()
        remaining_bytes = total_size - current_pos
        return remaining_bytes // self.BYTES_PER_SECOND

    def close(self):
        self.buffer.close()


class Song:
    def __init__(self, json_data: dict, requested_by: str | None = None):
        if not json_data or not isinstance(json_data, dict):
            raise TypeError(f"Song: trying to create object from wrong data: {json_data}")
        self.playback: PCMSource = None
        self.song_info = json_data
        self.requested_by = requested_by

    def has_playback(self):
        return self.playback is not None

    def song_name(self) -> str:
        return format_song_name(self.song_info)

    def get_id(self) -> str:
        return self.song_info["id"]

    def get_url(self) -> str:
        return SONG_URL + self.song_info["id"]

    def remaning(self) -> int | None:
        return self.playback.remaining() if self.has_playback() else None

    def download(self):
        song_url = STORAGE_URL + self.song_info["absolutePath"].strip("/")
        extension = song_url.rsplit(".", 1)[-1].lower()
        if MODE == 1:
            pre_process = extension not in ["mp3", "flac", "aiff", "ogg", "wav"]
            self.playback = LazyPCMSource(song_url, pre_process)
        else:
            self.playback = EagerPCMSource(song_url)
        if not self.has_playback():
            log.error(f"Song.download: could not load song\n song data: {self.dump_json()}")
            # should probably raise error

    def dump_json(self, indent=4) -> str:
        return json.dumps(self.song_info, indent=indent)

    def get_cover_art(self, download_animated=False) -> str | discord.File | None:
        if self.song_info.get("coverArt") and self.song_info["coverArt"].get("absolutePath"):
            image_url = IMAGES_URL
            image_url += self.song_info["coverArt"]["absolutePath"]
            image_url += "/width=900,height=900,quality=90,fit=crop,gravity=auto"
            if not download_animated or self.song_info["coverArt"]["contentType"] != "image/webp":
                return image_url
            else:
                response = requests.get(image_url)
                if response.status_code != 200:
                    return image_url
                else:
                    with io.BytesIO(response.content) as image_binary:
                        discord_file = discord.File(fp=image_binary, filename="attachment.gif")
                        return discord_file

        return None


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

        for item in to_download:
            item.download()

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

    def request_song(self, song_data: dict, requested_by: str):
        requested_song = Song(song_data, requested_by)
        self.requests_cache.append(requested_song)
        self.refill()
        return len(self.requests_cache), requested_song
