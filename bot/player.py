import enum
import threading
import weakref
import av.error
import discord
import io
import subprocess
import logging
import asyncio
import requests
import json
import time
import av
from typing import TypeVar
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
    start = None

    def __init__(self):
        self.paused = False
        self.log = log

    def is_opus(self):
        return False

    def set_pause(self, pause: bool):
        if pause and not self.paused:
            self.log.info("Playback Paused")
        elif not pause and self.paused:
            self.log.info("Playback Resumed")
        self.paused = pause

    def duration(self) -> float:
        raise NotImplementedError

    def read(self) -> bytes:
        raise NotImplementedError

    def size(self) -> int:
        raise NotImplementedError

    def remaining(self) -> float:
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class BufferedOpusSource(PlaybackSource):
    SILENCE_FRAME = b"\xf8\xff\xfe"

    def __init__(self, radio: bool):
        super().__init__()
        self.radio = radio
        self.buffer = deque()
        self.current_pts = 0
        self.duration_sec: float = 0
        self.container_size = None
        self.time_base = None
        self.end = False
        self.update_song_func = None

    def read(self) -> bytes:
        if self.paused:
            return self.SILENCE_FRAME

        while self.buffer:
            packet: av.Packet = self.buffer.popleft()
            if packet.pts is None and (len(self.buffer) != 0 or not self.end):
                self.log.warning(f"dropping packet: size: {packet.size} duration: {packet.duration}")
            else:
                if self.radio and self.update_song_func and packet.pts < self.current_pts:
                    self.update_song_func()
                if packet.pts:
                    self.current_pts = packet.pts
                return bytes(packet)

        if self.end:
            log.debug(f"Ending playback, buffer: {len(self.buffer)}")
            return b""
        else:
            self.log.debug("sending silence")
            return self.SILENCE_FRAME

    def is_opus(self) -> bool:
        return True

    def duration(self) -> float | None:
        if self.radio:
            return None
        return self.duration_sec

    def size(self) -> int | None:
        return self.container_size

    def remaining(self) -> float | None:
        if self.duration_sec == 0 or self.time_base is None:
            return None

        time_passed = float(self.current_pts * self.time_base)
        return float(max(0, self.duration_sec - time_passed))

    def close(self):
        self.end = True


class DirectOpusStream(BufferedOpusSource):
    BUFFER_SIZE = 200

    def __init__(self, url: str, radio=False):
        super().__init__(radio)
        self.log = ClassLogger(log, self)
        self.url = url
        self.reset = False
        self._thread_active = False

        if not radio:
            self._thread_active = True
            self_weak = weakref.ref(self)
            thread = threading.Thread(target=self._run_loop, args=(self_weak,), daemon=True)
            thread.start()

    def set_pause(self, pause: bool):
        if pause and not self.paused:
            self.log.info("Playback Paused")
        elif not pause and self.paused:
            self.log.info("Playback Resumed")
            if self.radio:
                self.reset = True
        self.paused = pause

    def start(self, func):
        if self._thread_active:
            return
        self._thread_active = True
        self.update_song_func = func
        self_weak = weakref.ref(self)
        thread = threading.Thread(target=self._run_loop, args=(self_weak,), daemon=True)
        thread.start()

    @staticmethod
    def _run_loop(weak_self: weakref.ReferenceType["DirectOpusStream"]):
        initialised = False
        seek_to = None
        error_count = 0
        while True:
            this = weak_self()
            if this is None or this.end:
                return
            if initialised:  # only for reconnecting
                if not this.radio:
                    try:
                        last_packet = this.buffer[-1]
                        seek_to = last_packet.pts
                    except IndexError:
                        pass
                    if seek_to is None:
                        seek_to = this.current_pts

            try:
                with av.open(
                    this.url,
                    timeout=(4, 4),
                    options={
                        "reconnect": "1",
                        "reconnect_streamed": "1",
                        "reconnect_delay_max": "4",
                    },
                ) as container:
                    audio_stream = container.streams.audio[0]
                    packet_generator = container.demux(audio_stream)
                    if seek_to:
                        container.seek(seek_to, stream=audio_stream)
                    this.time_base = audio_stream.time_base
                    if not this.radio:
                        this.container_size = container.size
                        if audio_stream.duration and this.time_base:
                            this.duration_sec = float(audio_stream.duration * this.time_base)

                    initialised = True
                    while True:
                        this = weak_self()
                        if this is None or this.end:
                            return

                        if this.reset:  # radio only
                            this.buffer.clear()
                            this.reset = False
                            break

                        if not (this.paused and this.radio):
                            while len(this.buffer) < this.BUFFER_SIZE:
                                new_packet = next(packet_generator)
                                if seek_to and new_packet.pts < seek_to:
                                    continue
                                seek_to = None
                                this.buffer.append(new_packet)
                                error_count = 0

                        this = None
                        time.sleep(0.02)

            except (
                av.HTTPUnauthorizedError,
                av.HTTPForbiddenError,
                av.ProtocolNotFoundError,
                av.DecoderNotFoundError,
                av.HTTPBadRequestError,
                av.HTTPOtherClientError,
            ) as e:
                this.log.error(f"{e.strerror}: {this.url}")
                this.end = True
            except av.HTTPNotFoundError:
                if error_count > 2:
                    this.log.error(f"HTTPError not found, giving up: {this.url}")
                    this.end = True
                this.log.warning("HTTPError not found, retrying")
            except (av.HTTPClientError, av.HTTPServerError) as e:
                this.log.warning(f"HTTPError ({e.errno}: {e.strerror}): {this.url}")
            except (av.EOFError, av.ExitError) as e:
                this.log.info(f"error, reconnecting: {e}")
            except StopIteration as e:
                if this.radio:
                    this.log.info("lost connection, attempting reconnect (StopIteration)")
                else:
                    this.end = True
            except av.InvalidDataError as e:
                if this.radio:
                    this.log.info("lost connection, attempting reconnect (InvalidDataError)")
                else:
                    this.log.error("InvalidDataError: ending playback")
                    this.end = True
            except av.OSError as e:
                # handles av.TimeoutError, av.ConnectionResetError av.BrokenPipeError and more
                this.log.info(f"({e.strerror}) lost connection, attempting reconnect")
            except av.FileNotFoundError as e:
                this.log.error(str(e))
                this.end = True
            except Exception as e:
                this.log.exception(f"Unknown exception trying to read packet:")
            finally:
                error_count += 1
                if error_count > 5:
                    this.end = True
                else:
                    this = None
                    time.sleep(0.3)

    def calculate_time_passed(self) -> float | None:
        if self.time_base is None:
            return None
        return float(self.current_pts * self.time_base)


class RAMBufferOpusSource(PlaybackSource):
    SILENCE_FRAME = b"\xf8\xff\xfe"

    def __init__(self, url: str):
        super().__init__()
        self.log = ClassLogger(log, self)
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        self.file_buffer = io.BytesIO(response.content)
        if self.file_buffer.getbuffer().nbytes < 10000:
            self.file_buffer.close()
            raise RuntimeError(f"{RAMBufferOpusSource.__name__}: Got less then 10KB")

        self.container = av.open(self.file_buffer)
        audio_stream = self.container.streams.audio[0]
        self.time_base = audio_stream.time_base
        self.duration_sec = float(audio_stream.duration * self.time_base)
        self.packet_generator = self.container.demux(audio_stream)
        self.current_pts = 0

    def is_opus(self):
        return True

    def read(self) -> bytes:
        if not self.container:
            return b""
        if self.paused:
            return self.SILENCE_FRAME

        try:
            packet = next(self.packet_generator)
            if packet.pts is not None:
                self.current_pts = packet.pts
            return bytes(packet)
        except (StopIteration, av.error.EOFError, av.error.ExitError):
            return b""

    def duration(self) -> float | None:
        return self.duration_sec

    def size(self) -> int | None:
        return self.container.size

    def remaining(self) -> float | None:
        if not self.duration_sec or self.time_base is None:
            return None
        time_passed = float(self.current_pts * self.time_base)
        return float(max(0, self.duration_sec - time_passed))

    def close(self):
        self.container.close()
        self.file_buffer.close()


class RAMBufferSource(BufferedOpusSource):
    BUFFER_SIZE = 50

    def __init__(self, url: str):
        super().__init__(False)
        self.log = ClassLogger(log, self)
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        file_buffer = io.BytesIO(response.content)
        if file_buffer.getbuffer().nbytes < 10000:
            file_buffer.close()
            raise RuntimeError(f"{RAMBufferSource.__name__}: Got less then 10KB")

        self_weak = weakref.ref(self)
        thread = threading.Thread(target=self._run_loop, args=(self_weak, file_buffer), daemon=True)
        thread.start()

    @staticmethod
    def _run_loop(weak_self: weakref.ReferenceType["RAMBufferSource"], file_buffer: io.BytesIO):
        this = weak_self()
        try:
            with av.open(file_buffer) as container:
                this.container_size = container.size
                audio_stream = container.streams.audio[0]
                packet_generator = container.demux(audio_stream)
                if audio_stream.duration:
                    this.duration_sec = float(audio_stream.duration * audio_stream.time_base)
                else:
                    this.duration_sec = 0
                audio_fifo = av.AudioFifo()
                resampler = av.AudioResampler(format="s16", layout="stereo", rate=this.SAMPLE_RATE)
                encoder = av.CodecContext.create("libopus", "w")
                encoder.sample_rate = this.SAMPLE_RATE
                encoder.layout = "stereo"
                encoder.format = "s16"
                encoder.open()
                this.time_base = encoder.time_base

                while True:
                    this = weak_self()
                    if this is None or this.end:
                        break

                    while len(this.buffer) < this.BUFFER_SIZE:
                        packet = next(packet_generator)
                        for frame in packet.decode():
                            resampled_frames = resampler.resample(frame)
                            for r_frame in resampled_frames:
                                r_frame.pts = None
                                audio_fifo.write(r_frame)

                        while audio_fifo.samples >= this.SAMPLES_PER_20MS:
                            audio_block = audio_fifo.read(this.SAMPLES_PER_20MS)
                            packets = encoder.encode(audio_block)
                            this.buffer.extend(packets)

                    this = None
                    time.sleep(0.2)

        except (StopIteration, av.error.EOFError, av.error.ExitError):
            n_samples_left = audio_fifo.samples
            if n_samples_left > 0:
                this.log.debug("adding padding")
                padding = av.AudioFrame(
                    samples=this.SAMPLES_PER_20MS - n_samples_left, format="s16", layout="stereo"
                )
                padding.sample_rate = this.SAMPLE_RATE
                for plane in padding.planes:
                    plane.update(b"\x00" * plane.buffer_size)

                audio_fifo.write(padding)
                audio_block = audio_fifo.read()
                packets = encoder.encode(audio_block)
                this.buffer.extend(packets)

            this.buffer.extend(encoder.encode())

            this.end = True
        except Exception as e:
            this.end = True
            this.log.exception(f"Unknown exception trying to read packet:")
        finally:
            file_buffer.close()


class RawPCMSource(PlaybackSource):
    def __init__(self, url: str):
        super().__init__()
        self.log = ClassLogger(log, self)
        self.log.info(f"Fetching and converting (ffmpeg) song data to raw from '{url}'")
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
            raise RuntimeError(f"{RawPCMSource.__name__}: ffmpeg returned: {ffmpeg_log}")
        if ffmpeg_log:
            self.log.error(f"ffmpeg returned: {ffmpeg_log}")
        self.buffer = io.BytesIO(raw_pcm_data)
        if self.buffer.getbuffer().nbytes < 10000:
            raise RuntimeError(f"{RawPCMSource.__name__}: Got less then 10KB")

    def read(self):
        """Discord calls this every 20ms to get the next chunk of audio."""
        if self.paused:
            return b"\x00" * self.BYTES_PER_20MS

        chunk = self.buffer.read(self.BYTES_PER_20MS)
        if not chunk:
            # ends playback
            return b""

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
        self.buffer.seek(int(seconds * self.BYTES_PER_SECOND))

    def duration(self) -> float:
        return self.size() / self.BYTES_PER_SECOND

    def size(self) -> int:
        """Size of the internal buffer/container, mostly for debug"""
        return self.buffer.getbuffer().nbytes

    def remaining(self) -> float:
        remaining_bytes = self.size() - self.buffer.tell()
        return remaining_bytes / self.BYTES_PER_SECOND

    def close(self):
        self.buffer.close()


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
    def duration(self) -> float | None:
        if self.has_playback():
            try:
                return self.playback.duration()
            except:
                pass

        return self.song_info.get("duration")

    def remaining(self) -> float | None:
        return self.playback.remaining() if self.has_playback() else None

    def download(self):
        opus = self.song_info.get("opus")
        if opus:
            opus_url = STORAGE_URL + self.song_info["opus"].strip("/")
        else:
            log.warning(f"Song: '{self.get_id()}' is missing opus!")
            song_url = STORAGE_URL + self.song_info["absolutePath"].strip("/")
        try:
            if opus:
                log.info(f"opus url: {opus_url}")
                self.playback = DirectOpusStream(opus_url)
        except Exception:
            log.exception("Could not load opus stream, falling back to default source")
            opus = None

        if not opus:
            if MODE == 1:
                self.playback = RAMBufferSource(song_url)
            else:
                self.playback = RawPCMSource(song_url)

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
        coverArt = self.song_info.get("coverArt")
        if not coverArt:
            return None
        absolutePath = coverArt.get("absolutePath")
        if not absolutePath:
            return None
        image_url = IMAGES_URL + absolutePath
        if download_animated and coverArt.get("isAnimated", False):
            image_url += "/quality=80"
            response = requests.get(image_url, timeout=8)
            if response.status_code == 200:
                with io.BytesIO(response.content) as image_binary:
                    discord_file = discord.File(fp=image_binary, filename="attachment.gif")
                    return discord_file

        image_url += "/quality=90"
        return image_url


class RadioSong(Song):
    def __init__(self, json_data, requested_by=None):
        super().__init__(json_data, requested_by)
        self.time_remaining = None
        self.time_calc = None
        self.duration_sec = None

    def set_playback_times(self, duration: float, time_passed: float = None):
        if time_passed is not None:
            self.time_remaining = duration - time_passed
            self.time_calc = time.time()
        self.duration_sec = duration

    def remaining(self) -> float | None:
        if self.time_remaining is None:
            return None
        time_diff = time.time() - self.time_calc
        remaining_sec = self.time_remaining - time_diff
        if remaining_sec < 0:
            remaining_sec = 0
        return remaining_sec

    @property
    def duration(self) -> float | None:
        return self.duration_sec

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
        self.playback: DirectOpusStream | None = None
        self.data: dict | None = fetch_json_data(RADIO21_SONGDATA)
        self.fetched_at = time.time()
        self.requested_by = requested_by

    def get_data(self, force=False):
        data_age = time.time() - self.fetched_at + 5
        if (
            force
            or not self.data
            or self.data.get("now_playing", {}).get("remaining", -999) < data_age
        ):
            self.data = fetch_json_data(RADIO21_SONGDATA)
            self.fetched_at = time.time()
        return self.data

    def get_song(self, order="now_playing") -> RadioSong | None:
        radio_json = self.get_data()
        if not radio_json:
            return None
        playing = radio_json.get(order)
        if not playing:
            return None
        song = playing.get("song")
        if not song:
            return None
        songId = song.get("custom_fields", {}).get("songId")
        if not songId:
            fake_song_info = {
                "originalArtists": [song.get("artist", "")],
                "duration": playing.get("duration", 0),
                "_cover_art": song.get("art"),
                "title": song.get("title"),
            }
            song_info = fake_song_info
        else:
            song_info = fetch_json_data(SONG_API + songId)
        if not song_info:
            return None
        else:
            song = RadioSong(song_info, str(playing.get("is_request", False)))
            time_passed = None
            if order == "now_playing":
                time_passed = self.playback.calculate_time_passed()
            song.set_playback_times(playing.get("duration", 0), time_passed)
            return song

    def download(self):
        self.playback = DirectOpusStream(RADIO21_URL, True)

    def has_playback(self):
        return self.playback is not None

    def song_name(self) -> str:
        radio_json = self.get_data()
        return "Radio21: " + radio_json.get("now_playing", {}).get("song", {}).get("text", "")

    def remaining(self) -> None:
        return None


class MusicPlayer:
    def __init__(self):
        self.cache = deque()
        self.requests_cache = deque()
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
            song_duration = song.duration
            if song_duration is None:
                return None
            duration += song_duration + PAUSE_DURATION
        duration += PAUSE_DURATION + (self.current_song.remaining() or 0)
        return round(duration)

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
