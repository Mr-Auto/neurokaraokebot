# Channels that the commands unrelated to VC are allowed in
ALLOWED_CHANNELS = (0,)
# Max songs cached, since we use double cache, with requested songs it will be 6
# (3 in random queue and 3 in request queue, assuming there is 3 or more songs requested)
MAX_CACHE = 3
# Pause the playback after the bot is left alone in the VC for X minutes
PAUSE_AFTER = 2
# Length of pause between songs (in seconds)
PAUSE_DURATION = 3

# API stuff, no need to edit
RANDOM_API = "https://api.neurokaraoke.com/api/songs/random"
STORAGE_URL = "https://storage.neurokaraoke.com/"
SONG_URL = "https://www.evilkaraoke.com/song/"
SEARCH_API = "https://api.neurokaraoke.com/api/songs"
IMAGES_URL = "https://images.neurokaraoke.com"
COVER_ARTITS = "https://api.neurokaraoke.com/api/filters/cover-artists?page=0&pageSize=50"
PLAYLIST_API = "https://api.neurokaraoke.com/api/playlist/"


from enum import IntEnum
import json
import random
from dataclasses import dataclass, field, asdict


# Embed colors
class COLORS(IntEnum):
    QUEUE = 0x237FEB
    EMBED_DEFAULT = 0x237FEB
    NEURO = 0xFBD1A9
    EVIL = 0x8F0A0A
    VEDAL = 0x0A7908
    TWINS = 0xB305AA


@dataclass
class _EmoteCollection:
    SILLY_LIST: list[str] = field(default_factory=list)
    SAD_LIST: list[str] = field(default_factory=list)
    SIDE_EYE_LIST: list[str] = field(default_factory=list)
    STARE_LIST: list[str] = field(default_factory=list)
    HAPPY_LIST: list[str] = field(default_factory=list)
    PAUSE_LIST: list[str] = field(default_factory=list)
    LOADING_LIST: list[str] = field(default_factory=list)
    NWELIV_LIST: list[str] = field(default_factory=list)
    BASED_LIST: list[str] = field(default_factory=list)
    NEUROJAM_LIST: list[str] = field(default_factory=list)
    EVILJAM_LIST: list[str] = field(default_factory=list)
    OK_LIST: list[str] = field(default_factory=list)
    WAVE_LIST: list[str] = field(default_factory=list)

    @property
    def SILLY(self) -> str:
        return self._pick(self.SILLY_LIST)

    @property
    def SAD(self) -> str:
        return self._pick(self.SAD_LIST)

    @property
    def SIDE_EYE(self) -> str:
        return self._pick(self.SIDE_EYE_LIST)

    @property
    def STARE(self) -> str:
        return self._pick(self.STARE_LIST)

    @property
    def HAPPY(self) -> str:
        return self._pick(self.HAPPY_LIST)

    @property
    def PAUSE(self) -> str:
        return self._pick(self.PAUSE_LIST)

    @property
    def LOADING(self) -> str:
        return self._pick(self.LOADING_LIST)

    @property
    def NWELIV(self) -> str:
        return self._pick(self.NWELIV_LIST)

    @property
    def BASED(self) -> str:
        return self._pick(self.BASED_LIST)

    @property
    def NEUROJAM(self) -> str:
        return self._pick(self.NEUROJAM_LIST)

    @property
    def EVILJAM(self) -> str:
        return self._pick(self.EVILJAM_LIST)

    @property
    def OK(self) -> str:
        return self._pick(self.OK_LIST)

    @property
    def WAVE(self) -> str:
        return self._pick(self.WAVE_LIST)

    @property
    def JAM(self) -> str:
        return self._pick(self.NEUROJAM_LIST + self.EVILJAM_LIST)

    def groups(self) -> list[str]:
        """Return all group names, including specials like JAM"""
        return [name for name, value in type(self).__dict__.items() if isinstance(value, property)]

    def _pick(self, source: list[str]) -> str:
        return random.choice(source) if source else ""

    def has(self, group_name: str) -> bool:
        """Checks if a group exists (excludes JAM)."""
        attr_name = f"{group_name.upper()}_LIST"
        return hasattr(self, attr_name)

    def get_list(self, group_name: str) -> list[str]:
        """Returns the emote list for a group"""
        group_name = group_name.upper()
        if group_name == "JAM":
            return self.NEUROJAM_LIST + self.EVILJAM_LIST
        target = getattr(self, f"{group_name}_LIST", None)
        if isinstance(target, list):
            return target
        return []

    def add_emote(self, group_name: str, emote: str):
        """Adds an emote using a string name (e.g. 'SILLY')."""
        target = getattr(self, f"{group_name.upper()}_LIST", None)
        if isinstance(target, list):
            if emote not in target:
                target.append(emote)
        else:
            raise ValueError(f"Group '{group_name}' is invalid or read-only.")

    @classmethod
    def _load(cls, filename="emotes.json"):
        try:
            with open(filename, "r") as f:
                raw = json.load(f).get("EMOTES", {})
                mapped = {f"{k}_LIST": v for k, v in raw.items()}
                return cls(**mapped)
        except Exception as e:
            print(e)
            return cls()

    def save(self, filename="emotes.json"):
        """Dumps the emotes to json file"""
        raw_dict = asdict(self)
        clean_dict = {k.replace("_LIST", ""): v for k, v in raw_dict.items()}
        with open(filename, "w") as f:
            json.dump({"EMOTES": clean_dict}, f, indent=4)


EMOTES: _EmoteCollection = _EmoteCollection._load()
