import discord
import io
import numpy
import logging
import asyncio
import requests
import json
import time
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
from pedalboard.io import AudioFile
from collections import deque
from itertools import chain, islice
from config import MAX_CACHE, STORAGE_URL, RANDOM_API, SONG_URL

# TODO fix deque mutated during iteration
# keep in mind the forced feill, probably need to lock it for that

log = logging.getLogger("player")


def format_song_name(json_data) -> str:
    name = " & ".join(json_data["originalArtists"])
    name += " - " + json_data["title"]
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
            log.info(f"Attempt {i + 1} failed: {e}")
            if i < retries - 1:
                time.sleep(2)
            else:
                log.warning("All retry attempts failed.")


class PCMSource(discord.AudioSource):
    SAMPLE_RATE = 48000
    SAMPLES_PER_20MS = int(0.02 * SAMPLE_RATE)
    BYTES_PER_SECOND = SAMPLE_RATE * 2 * (16 // 8)  # 48KHz, 2 channels, 16bit depth
    BYTES_PER_20MS = int(0.02 * BYTES_PER_SECOND)

    def __init__(self, url: str):
        self.paused = False
        self.effects_board = Pedalboard()
        log.info(f"PCMSource: Fetching song data from '{url}'")
        retries = 4
        for i in range(retries):
            try:
                response = requests.get(url)
                response.raise_for_status()
                break
            except (requests.exceptions.RequestException, ValueError) as e:
                log.warning(f"Attempt {i + 1} failed: {e}")
                if i < retries - 1:
                    time.sleep(2)
                else:
                    log.warning("All retry attempts failed.")
                    raise

        self.buffer = AudioFile(io.BytesIO(response.content)).resampled_to(self.SAMPLE_RATE)
        if self.buffer.num_channels != 2:
            log.warning(f"File number of channels: {self.buffer.num_channels} != 2")

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

        processed_audio = self.effects_board(chunk, self.SAMPLE_RATE)
        pcm_16 = (processed_audio * 32767.0).astype(numpy.int16)
        pcm_chunk = pcm_16.T.tobytes()
        chunk_size = len(pcm_chunk)
        if chunk_size < self.BYTES_PER_20MS:
            padding = self.BYTES_PER_20MS - chunk_size
            pcm_chunk += b"\x00" * padding
        elif len(pcm_chunk) > self.BYTES_PER_20MS:
            log.error(
                f"PCMSource.read: Something went wrong, got more then 20ms of data.\nActual size: {chunk_size} expected: {self.BYTES_PER_20MS} index at {self.buffer.tell()}/{self.buffer.shape[1]}"
            )
        return pcm_chunk

    def is_opus(self):
        return False

    def seek(self, seconds: float):
        """Move the internal pointer to a specific second."""
        target_frame = int(seconds * self.SAMPLE_RATE)
        self.buffer.seek(target_frame)

    def set_pause(self, pause: bool):
        if pause and not self.paused:
            log.info("PCMSource: Playback Paused")
        elif not pause and self.paused:
            log.info("PCMSource: Playback Resumed")
        self.paused = pause

    def duration(self) -> int:
        return self.buffer.duration

    def size(self) -> int:
        return len(self.buffer)

    def remaining(self) -> int:
        return (self.buffer.frames - self.buffer.tell()) // self.SAMPLE_RATE


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
        song_url = STORAGE_URL + self.song_info["absolutePath"]
        self.playback = PCMSource(song_url)
        if not self.has_playback():
            log.error(f"Song.download: could not load song\n song data: {self.song_info}")
            # should probably raise error

    def dump_json(self, indent=4) -> str:
        return json.dump(self.song_info, indent=indent)


class MusicPlayer:
    def __init__(self):
        self.cache = deque()
        self.requests_cache = deque()
        self.effects_board = Pedalboard()
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

    def request_queue_duration(self) -> int:
        duration = 0
        for song in self.requests_cache:
            duration += song.song_info["duration"] + 2
        return duration

    def load_next_song(self):
        if len(self.requests_cache) > 0:
            self.current_song = self.requests_cache.popleft()
        else:
            # unhandled exception, but we can't recover anyway
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
            log.info("refill_queue: refill already running, skipping")
            return

        loop = asyncio.get_running_loop()
        self.refill_task = loop.run_in_executor(None, self._refill_queue)

    def _refill_queue(self):
        log.info("reffil process starting...")
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
        log.info("reffil done")

    def pause(self):
        if self.current_song.has_playback():
            self.current_song.playback.set_pause(True)

    def resume(self):
        if self.current_song.has_playback():
            self.current_song.playback.set_pause(False)

    def is_paused(self) -> bool:
        return self.current_song.has_playback() and self.current_song.playback.paused

    def clear_modifiers(self):
        self.effects_board = Pedalboard()

    def set_volume(self, db_gain: float):
        if self.current_song.has_playback():
            board = self.effects_board
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
        if self.current_song.has_playback():
            board = self.effects_board
            for p in board:
                if isinstance(p, Limiter):
                    board.remove(p)
                    break
            if len(board) > 0:
                board.append(Limiter(threshold_db=-0.1))

    def apply_effects_board(self):
        if self.current_song.has_playback():
            self.current_song.playback.effects_board = self.effects_board
