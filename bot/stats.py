from dataclasses import dataclass
import enum
import json
import logging
from player import Song

_stats_filename = "data/stats.json"
_log = logging.getLogger()


class DataType(enum.StrEnum):
    Users = "users"
    Time = "total_time"
    SongCount = "song_count"
    Request = "requests"
    Songs = "songs"


@dataclass
class UserPlayingData:
    time: float = 0
    is_listening: bool = True


@dataclass
class ServerPlayingData:
    song_id: str
    time: float
    song_remaining: float
    song_durration: float
    users_times: dict[int, UserPlayingData]


def _increment(data: dict, uid: int, name: DataType, value=1):
    user_data = data.setdefault(str(uid), {})
    user_data[name] = user_data.get(name, 0) + value


_playing_start: dict[int, ServerPlayingData | None] = {}
_cache_data: dict[str, dict] = {}
# _cache_data structure:
#
# guild_id
# |
# |- total_time: float
# |- song_count: int (how many played)
# |
# |-requests
# |  |
# |  |-song_id: int (how many times requested)
# |
# |-users
#    |
#    |-user_id
#       |
#       |-total_time: float
#       |-songs
#           |
#           |-song_id
#               |
#               |-song_count: int
#               |-requests: int

_final = False


def load():
    try:
        with open(_stats_filename, "r") as f:
            global _cache_data
            _cache_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        _log.warning(f"Could not load stats data: {e}")


def save(final=False):
    global _final
    if _final:
        return
    if final:
        for guild_id in _playing_start:
            cache_song(guild_id, None)
    with open(_stats_filename, "w") as f:
        json.dump(_cache_data, f, indent=4)
    _log.info("Successfully saved stats")
    _final = final


def get_users_cache(guild_id: int) -> dict[str, dict]:
    server_cache = _cache_data.setdefault(str(guild_id), {})
    users_cache = server_cache.setdefault(DataType.Users, {})
    return users_cache


def get_user_current_time(guild_id: int, user_id: int) -> float:
    server_data = _playing_start.get(guild_id)
    if server_data is None:
        return 0
    user_data = server_data.users_times.get(user_id)
    if user_data is None:
        return 0
    return user_data.time if user_data.time is not None else 0


def get_server_current_time(guild_id: int) -> float:
    server_data = _playing_start.get(guild_id)
    if server_data is None:
        return 0
    return server_data.time if server_data.time is not None else 0


def get_songs_cache(guild_id: int) -> dict[str, dict]:
    server_cache = _cache_data.setdefault(str(guild_id), {})
    songs_cache = server_cache.setdefault(DataType.Songs, {})
    return songs_cache


def cache_song(guild_id: int, old_song: Song | None):
    server_data = _playing_start.get(guild_id)
    if server_data is None:
        return
    if old_song is not None:
        old_id = old_song.get_id()
        if old_id is not None and server_data.song_id != old_id:
            _log.error("cache_song called with different song then in the server data")
            return
        update(guild_id, old_song, set())
    _increment(_cache_data, guild_id, DataType.Time, server_data.time)
    if server_data.song_durration is None:
        if old_song:
            dur = old_song.duration
        if dur is None:
            percent = 999
        else:
            percent = dur * 0.75
    else:
        percent = server_data.song_durration * 0.75
    if server_data.time >= percent and server_data.song_id is not None:
        songs_cache = get_songs_cache(guild_id)
        _increment(songs_cache, server_data.song_id, DataType.SongCount)
    users_cache = get_users_cache(guild_id)
    for user_id, data in server_data.users_times.items():
        if data is not None and data.time != 0:
            _increment(users_cache, user_id, DataType.Time, data.time)
            if data.time >= percent:
                _increment(users_cache, user_id, DataType.SongCount)
    _playing_start[guild_id] = None


def update(guild_id: int, song: Song, listeners: set[int]):
    if song is None:
        return
    song_id = song.get_id()
    if song_id is None:
        return
    song_remaining = song.remaining()
    if song_remaining is None:
        _log.warning("update: song remaining is None")
        return
    server_data = _playing_start.get(guild_id)
    if server_data is None:
        _playing_start[guild_id] = ServerPlayingData(
            song_id,
            0,
            song_remaining,
            song.duration,
            {listener: UserPlayingData() for listener in listeners},
        )
        return
    if server_data.song_id != song_id:
        cache_song(guild_id, None)
        _playing_start[guild_id] = ServerPlayingData(
            song_id,
            0,
            song_remaining,
            song.duration,
            {listener: UserPlayingData() for listener in listeners},
        )
    else:
        if server_data.song_remaining is None:
            server_data.song_remaining = song_remaining
            server_data.users_times = {listener: UserPlayingData() for listener in listeners}
            return
        if server_data.song_durration is None:
            if song.duration is None:
                return
            else:
                server_data.song_durration = song.duration
        diff = server_data.song_remaining - song_remaining
        if diff < 0:
            diff = 0
        server_data.song_remaining = song_remaining
        server_data.time += diff
        for user_id in listeners | server_data.users_times.keys():
            in_list = user_id in listeners
            in_dict = user_id in server_data.users_times
            if in_dict:
                user_data = server_data.users_times[user_id]
                if in_list:
                    if user_data is None:
                        server_data.users_times[user_id] = UserPlayingData()
                    else:
                        user_data.is_listening = True
                        user_data.time += diff
                else:
                    if user_data is None:
                        continue
                    if user_data.is_listening:
                        user_data.is_listening = False
                        user_data.time += diff
            else:
                server_data.users_times[user_id] = UserPlayingData()


def song_requested(guild_id: int, user_id: int, song_id: str):
    if song_id is None:
        return
    users_cache = get_users_cache(guild_id)
    user_cache = users_cache.setdefault(str(user_id), {})
    _increment(user_cache, DataType.Request, song_id)
    songs_cache = get_songs_cache(guild_id)
    _increment(songs_cache, song_id, DataType.Request)


def get_top(guild_id: int, top_n: int, comparison: DataType) -> dict[int, list[str, int]]:
    best = [0] * top_n
    users_cache = get_users_cache(guild_id)
    server_start_data = _playing_start.get(guild_id)
    users_start_times = server_start_data.users_times

    def get_comparision_value(user_id: str, rdata: dict):
        if comparison == DataType.Request:
            requests_data = rdata.get(DataType.Request, {})
            cmp_val = sum(requests_data.values())
        elif comparison == DataType.Time:
            user_cur_data = users_start_times.get(int(user_id))
            if user_cur_data:
                cmp_val = user_cur_data.time + rdata.get(comparison, 0)
            else:
                cmp_val = rdata.get(comparison, 0)
        else:
            cmp_val = rdata.get(comparison, 0)
        return cmp_val

    for user_id, data in users_cache.items():
        if not data:
            continue
        cmp = get_comparision_value(user_id, data)
        if cmp == 0:
            continue
        for idx in range(len(best)):
            if cmp == best[idx]:
                break
            if cmp > best[idx]:
                best.insert(idx, cmp)
                best.pop()
                break
    results = {}
    for user_id, data in users_cache.items():
        if not data:
            continue
        cmp = get_comparision_value(user_id, data)
        if cmp == 0:
            continue
        for idx in range(len(best)):
            if cmp == best[idx]:
                place: list = results.setdefault(idx, [])
                place.append((user_id, cmp))
    return results
