import enum
import io
import logging
import asyncio
import aiohttp
import requests
import json
import time
import discord
from collections import deque
from itertools import chain, islice
from config import *
from playback_source import *

log = logging.getLogger()
MODE = 1


def fetch_json_data(
    url: str, session: requests.Session, *, get=None, post=None, retries=3
) -> dict | None:
    log.info(f"fetch_json_data: Fetching json data from '{url}'")
    for i in range(retries):
        try:
            if post:
                response = session.post(url, json=post, timeout=8)
            else:
                response = session.get(url, params=get, timeout=8)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.RequestException, ValueError, TimeoutError) as e:
            log.info(f"fetch_json_data: Attempt {i + 1} failed: {e}")
            if i < retries - 1:
                time.sleep(2)
            else:
                log.warning(f"fetch_json_data: All retry attempts failed. {url}")


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

    def download(self, session: requests.Session | None):
        opus = self.song_info.get("opus")
        if opus:
            opus_url = AUDIO_URL + self.song_info["opus"].strip("/")
        else:
            log.warning(f"Song: '{self.get_id()}' is missing opus!")
        song_url = AUDIO_URL + self.song_info["absolutePath"].strip("/")

        try:
            if opus:
                if MODE == 1:
                    self.playback = DirectOpusStream(opus_url)
                else:
                    self.playback = RAMBufferOpusSource(opus_url, session)
        except Exception:
            log.exception("Could not load opus stream, falling back to non opus source")
            opus = None

        if not opus:
            if MODE == 1:
                self.playback = NonOpusStream(song_url)
            else:
                self.playback = RAMBufferNonOpusSource(song_url, session)

    # self.playback = RawPCMSource(song_url)

    def dump_json(self, indent=4) -> str:
        return json.dumps(self.song_info, indent=indent)

    @property
    def cover_artists(self) -> str:
        return self._get_artist("coverArtists")

    @property
    def original_artists(self) -> str:
        return self._get_artist("originalArtists")

    def _get_artist(self, artist_type: str) -> str:
        original_str = ""
        artists_list = self.song_info.get(artist_type)
        if artists_list:
            if isinstance(artists_list[0], dict):
                original_str = " & ".join(artist["name"] for artist in artists_list)
            else:
                original_str = " & ".join(artists_list)
        return original_str

    async def get_cover_art(
        self, download_animated=False, session: aiohttp.ClientSession = None
    ) -> str | discord.File | None:
        coverArt = self.song_info.get("coverArt")
        if not coverArt:
            return None
        absolutePath = coverArt.get("absolutePath")
        if not absolutePath:
            return None
        image_url = IMAGES_URL + absolutePath
        if download_animated and coverArt.get("isAnimated", False):
            try:
                async with session.get(image_url + "/quality=80") as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        with io.BytesIO(data) as image_binary:
                            content_type = resp.headers.get("Content-Type", "image/gif")
                            extension = content_type.split("/")[-1]
                            filename = f"attachment.{extension}"
                            discord_file = discord.File(
                                image_binary, filename, description="Cover Art"
                            )
                            return discord_file
            except Exception:
                log.exception("exception during cover art download")

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

    def download(self, _):
        pass

    async def get_cover_art(
        self, download_animated=False, session: aiohttp.ClientSession = None
    ) -> str | None:
        art = await super().get_cover_art(download_animated, session)
        if art is None:
            return self.song_info.get("_cover_art")
        return art


class RadioType(enum.Enum):
    Radio21 = enum.auto()
    SwarmFM = enum.auto()


class Radio(Song):
    CURRENT = None
    NEXT = None

    @staticmethod
    def name() -> str:
        raise NotImplementedError

    @staticmethod
    def color() -> int:
        raise NotImplementedError

    @staticmethod
    def url() -> str:
        raise NotImplementedError

    @staticmethod
    def logo_url() -> str:
        raise NotImplementedError

    @staticmethod
    def emote() -> str:
        raise NotImplementedError

    @property
    def cover_artists(self) -> str:
        return None

    @property
    def original_artists(self) -> str:
        return None

    def remaining(self) -> None:
        return None

    @property
    def duration(self) -> None:
        return None


g_session = requests.Session()


class Radio21(Radio):
    def __init__(self, requested_by=None):
        self.playback: DirectOpusStream | None = None
        self.fetched_at = 0
        self.requested_by = requested_by
        self.CURRENT = "now_playing"
        self.NEXT = "playing_next"
        self.get_data(True)

    @staticmethod
    def name() -> str:
        return "Radio 21"

    @staticmethod
    def url() -> str:
        return RADIO21.URL

    @staticmethod
    def color() -> int:
        return 0xB554FF

    @staticmethod
    def logo_url() -> str:
        return RADIO21.LOGO

    @staticmethod
    def emote() -> str:
        return EMOTES.NEUROKARAOKE

    def get_data(self, force=False) -> dict | None:
        data_age = time.time() - self.fetched_at + 5
        if (
            force
            or not self.data
            or self.data.get(self.CURRENT, {}).get("remaining", -999) < data_age
        ):
            self.data = fetch_json_data(RADIO21.SONGDATA, g_session)
            self.fetched_at = time.time()
        return self.data

    def get_song(self, order) -> RadioSong | None:
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
            song_info = fetch_json_data(SONG_API + songId, g_session)
        if not song_info:
            return None
        else:
            song = RadioSong(song_info, str(playing.get("is_request", False)))
            time_passed = None
            if order == self.CURRENT:
                time_passed = self.playback.calculate_time_passed()
            song.set_playback_times(playing.get("duration", 0), time_passed)
            return song

    def download(self, session):
        if session is None:
            with requests.Session() as s:
                data = fetch_json_data(RADIO21.SONGDATA, s)
        else:
            data = fetch_json_data(RADIO21.SONGDATA, session)
        log.error(f"after download {data}")
        if data:
            mounts = data.get("station", {}).get("mounts", [])
            for mount in mounts:
                if mount.get("name") == "Opus":
                    self.playback = DirectOpusStream(mount["url"], True)
                    break

    def song_name(self) -> str:
        radio_json = self.get_data()
        return "Radio21: " + radio_json.get(self.CURRENT, {}).get("song", {}).get("text", "")

    def dump_json(self, indent=4) -> str:
        return json.dumps(self.data, indent=indent)


class SwarmFM(Radio):
    def __init__(self, requested_by=None):
        self.playback: NonOpusStream | None = None
        self.fetched_at = 0
        self.requested_by = requested_by
        self.CURRENT = "current"
        self.NEXT = "next"
        self.get_data(True)
        self.song_update_running = False

    @staticmethod
    def name():
        return "SwarmFM"

    @staticmethod
    def url() -> str:
        return SWARMFM.URL

    @staticmethod
    def color() -> int:
        return 0xCCCCCC

    @staticmethod
    def logo_url() -> str:
        # logo by SNT10
        return SWARMFM.LOGO

    @staticmethod
    def emote() -> str:
        return EMOTES.SWARMFM

    def get_data(self, force=False):
        data_age = time.time() - self.fetched_at - 1
        if force or not self.data or self.data.get("position", {}) < data_age:
            self.data = fetch_json_data(SWARMFM.SONGDATA, g_session)
            self.fetched_at = time.time()
        return self.data

    def get_song(self, order) -> RadioSong | None:
        radio_json = self.get_data(True)
        if not radio_json:
            return None
        playing = radio_json.get(order)
        if not playing:
            return None
        cap_cover_artists = [item.capitalize() for item in playing.get("singer", [])]
        if "Neuro" in cap_cover_artists:
            if "Evil" in cap_cover_artists:
                cover_art = SWARMFM.COVER_ART_TWINS
            else:
                cover_art = SWARMFM.COVER_ART_NEURO
        elif "Evil" in cap_cover_artists:
            cover_art = SWARMFM.COVER_ART_EVIL
        else:
            cover_art = None

        fake_song_info = {
            "originalArtists": [playing.get("artist", "")],
            "coverArtists": cap_cover_artists,
            "duration": playing.get("duration", 0),
            "_cover_art": str(cover_art) if cover_art is not None else None,
            "title": playing.get("name"),
            "songId": playing.get("id"),
        }
        song = RadioSong(fake_song_info)
        time_passed = None
        if order == self.CURRENT:
            time_passed = radio_json.get("position", 0)
        song.set_playback_times(playing.get("duration", 0), time_passed)

        return song

    def download(self, _):
        self.playback = NonOpusStream(SWARMFM.STREAM, True)

    def song_name(self) -> str:
        radio_json = self.get_data()
        current = radio_json.get(self.CURRENT, {})
        artist = current.get("artist", "")
        song_name = current.get("name", "")
        cap_cover_artists = [item.capitalize() for item in current.get("singer", [])]
        cover_by = " & ".join(cap_cover_artists)
        full_name = f"SwarmFM: {artist} - {song_name}"
        if cover_by:
            full_name += f" ({cover_by})"
        return full_name

    def dump_json(self, indent=4) -> str:
        return json.dumps(self.data, indent=indent)


class MusicPlayer:
    def __init__(self, data: list):
        self.cache = deque()
        self.requests_cache = deque()
        self.alone_counter = 0
        self.update_status = True
        self.refill_task: asyncio.Future = None
        self.cache.extend(Song(item) for item in data)
        self.current_song: Song | Radio = self.cache.popleft()
        self.refill_session = requests.Session()
        self.current_song.download(self.refill_session)

    def request_queue_duration(self) -> int | None:
        duration = 0
        if isinstance(self.current_song, Radio):
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
        self.refill_task = asyncio.create_task(asyncio.to_thread(self._refill_queue))

    def _refill_queue(self):
        log.info("refill_queue: process starting...")
        to_download = []
        # known issue: this first loop can throw 'deque mutated during iteration' if we load next song at a perfect time
        # but it's extremely improbable, also this runs in separate thread so we can ignore it, will download on next refill
        for item in islice(chain(self.requests_cache, self.cache), MAX_CACHE):
            if item.has_playback():
                continue
            to_download.append(item)
        try:
            for item in to_download:
                item.download(self.refill_session)
        except Exception:
            log.exception(f"refill_queue: error during song download:")

        if len(self.cache) < MAX_CACHE + 1:
            response = self.refill_session.get(RANDOM_API, timeout=8)
            if response.status_code != 200:
                log.error(f"refill_queue: Random API returned {response.status_code}")
                return
            data = response.json()
            if not isinstance(data, list) or len(data) == 0:
                log.warning("refill_queue: No data in fetched result from the random api")
                return
            self.cache.extend(Song(item) for item in data)
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
                self.requests_cache.append(SwarmFM(requested_by))
        self.refill()
        return len(self.requests_cache)
