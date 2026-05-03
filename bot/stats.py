import json
import time
import logging
from typing import NamedTuple

_stats_filename = "data/stats.json"
_log = logging.getLogger()


def _increment(data: dict, uid: int, name: str):
    user_data = data.setdefault(str(uid), {})
    user_data[name] = user_data.get(name, 0) + 1


def _stop_timer(data: dict, uid: int, playing_start: int):
    guild_data = data.setdefault(str(uid), {})
    elapsed = int(time.time() - playing_start)
    guild_data["total_time"] = guild_data.get("total_time", 0) + elapsed


class _ListeningUserData(NamedTuple):
    time_start: int
    guild_id: int


class _UsersData:
    def __init__(self, main_data: dict):
        self.listening_start: dict[int, _ListeningUserData] = {}
        self.data = main_data["users"]

    def started_listening(self, user_id: int, guild_id: int):
        if self.listening_start.get(user_id) is not None:
            _log.warning(
                "Called UsersData:started_playing while start is not None, overwriting the value"
            )
        self.listening_start[user_id] = _ListeningUserData(time.time(), guild_id)

    def stopped_listening(self, user_id: int):
        data = self.listening_start.get(user_id)
        if data is None:
            _log.error("Called UsersData:stopped_playing while start is None")
            return
        _stop_timer(self.data, user_id, data.time_start)
        self.listening_start[user_id] = None

    def _song_count_increment(self, guild_id: int):
        """Use the server one"""
        for user_id, data in self.listening_start.items():
            if data.guild_id == guild_id:
                _increment(self.data, user_id, "song_count")

    def _server_stopped_playing(self, guild_id: int):
        for user_id, data in self.listening_start.items():
            if data.guild_id == guild_id:
                self.stopped_listening(user_id)

    def cache_listening_time(self, user_id: int):
        data = self.listening_start.get(user_id)
        if data:
            guild_id = data.guild_id
            self.stopped_listening(user_id)
            self.started_listening(user_id, guild_id)

    def get_user_data(self, user_id: int) -> dict | None:
        self.cache_listening_time(user_id)
        return self.data.get(str(user_id))


class _ServersData:
    def __init__(self, main_data: dict):
        self.playing_start: dict[int, int] = {}
        self.data = main_data["servers"]

    def started_playing(self, guild_id: int):
        if self.playing_start.get(guild_id) is not None:
            _log.warning(
                "Called ServersData:started_playing while start is not None, overwriting the value"
            )
        self.playing_start[guild_id] = time.time()

    def stopped_playing(self, guild_id: int):
        start_time = self.playing_start.get(guild_id)
        if start_time is None:
            _log.error("Called ServersData:stopped_playing while start is None")
            return
        _stop_timer(self.data, guild_id, start_time)
        users._server_stopped_playing(guild_id)
        self.playing_start[guild_id] = None

    def song_count_increment(self, guild_id: int):
        _increment(self.data, guild_id, "song_count")
        users._song_count_increment(guild_id)

    def get_server_data(self, guild_id: int) -> dict | None:
        start = self.playing_start.get(guild_id)
        if start is not None:
            # cache the current time
            _stop_timer(self.data, guild_id, start)
            self.playing_start[guild_id] = time.time()
        return self.data.get(str(guild_id))


_data = {"servers": {}, "users": {}}
users = _UsersData(_data)
servers = _ServersData(_data)


def load():
    try:
        with open(_stats_filename, "r") as f:
            global _data, users, servers
            _data = json.load(f)
            users = _UsersData(_data)
            servers = _ServersData(_data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def save():
    for user_id in users.listening_start:
        users.cache_listening_time(user_id)

    for server_id in servers.playing_start:
        servers.stopped_playing(server_id)
        servers.started_playing(server_id)

    with open(_stats_filename, "w") as f:
        json.dump(_data, f, indent=4)


def song_requested(guild_id: int, requested_by_id: int):
    _increment(_data["servers"], guild_id, "requests")
    _increment(_data["users"], requested_by_id, "requests")
