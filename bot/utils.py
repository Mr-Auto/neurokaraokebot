import json
import random
from dataclasses import dataclass, field, asdict
from logging.handlers import TimedRotatingFileHandler
from logging import Formatter, LoggerAdapter
import discord


class MyTimedRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(
        self,
        filename,
        when="h",
        interval=1,
        backupCount=0,
        encoding=None,
        delay=False,
        utc=False,
        atTime=None,
        errors=None,
    ):
        super().__init__(filename, when, interval, backupCount, encoding, delay, utc, atTime, errors)
        formatter = Formatter(
            "[{asctime}] [{levelname:<8} {module:>15}] {classspecific}{message}",
            style="{",
            defaults={"classspecific": ""},
        )
        self.setFormatter(formatter)

    def namer(self, default_name: str) -> str:
        date_part = default_name.split(".")[-1]
        return f"logs/{date_part}.log"


class ClassLogger(LoggerAdapter):
    def __init__(self, logger, obj):
        classname = f"{obj.__class__.__name__}: " if obj else ""
        super().__init__(logger, {"classspecific": classname})


@dataclass
class CustomResponse:
    json_data: str | None
    status: int | None
    error: str | None
    url: str


async def verify_message(channel: discord.abc.Messageable, message_id: int):
    try:
        msg = await channel.fetch_message(message_id)
        return bool(msg.id)
    except discord.NotFound:
        return False
    except (discord.Forbidden, discord.HTTPException):
        return None


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
    SWARMFM_LIST: list[str] = field(default_factory=list)
    NEUROKARAOKE_LIST: list[str] = field(default_factory=list)
    DINKDONK_LIST: list[str] = field(default_factory=list)
    _filename = "data/emotes.json"

    @property
    def DINKDONK(self) -> str:
        return self._pick(self.DINKDONK_LIST)

    @property
    def SILLY(self) -> str:
        return self._pick(self.SILLY_LIST)

    @property
    def SWARMFM(self) -> str:
        return self._pick(self.SWARMFM_LIST)

    @property
    def NEUROKARAOKE(self) -> str:
        return self._pick(self.NEUROKARAOKE_LIST)

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
    def _load(cls):
        try:
            with open(cls._filename, "r") as f:
                raw = json.load(f).get("EMOTES", {})
                mapped = {f"{k}_LIST": v for k, v in raw.items()}
                return cls(**mapped)
        except Exception as e:
            print(e)
            return cls()

    def save(self):
        """Dumps the emotes to json file"""
        raw_dict = asdict(self)
        clean_dict = {k.replace("_LIST", ""): v for k, v in raw_dict.items()}
        with open(self._filename, "w") as f:
            json.dump({"EMOTES": clean_dict}, f, indent=4)


EMOTES: _EmoteCollection = _EmoteCollection._load()
