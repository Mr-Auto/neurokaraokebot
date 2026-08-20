import json
import random
from dataclasses import dataclass
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


def author_check(owner_id: int):
    async def check_fun(interact: discord.Interaction):
        if check := interact.user.id != owner_id:
            await interact.response.send_message(f"Not your buttons! {EMOTES.SILLY}", ephemeral=True)
        return not check

    return check_fun


class EmotesMetaClass(type):
    @property
    def DINKDONK(cls) -> str:
        return cls._pick(cls.DINKDONK_LIST)

    @property
    def SILLY(cls) -> str:
        return cls._pick(cls.SILLY_LIST)

    @property
    def SWARMFM(cls) -> str:
        return cls._pick(cls.SWARMFM_LIST)

    @property
    def NEUROKARAOKE(cls) -> str:
        return cls._pick(cls.NEUROKARAOKE_LIST)

    @property
    def SAD(cls) -> str:
        return cls._pick(cls.SAD_LIST)

    @property
    def SIDE_EYE(cls) -> str:
        return cls._pick(cls.SIDE_EYE_LIST)

    @property
    def STARE(cls) -> str:
        return cls._pick(cls.STARE_LIST)

    @property
    def HAPPY(cls) -> str:
        return cls._pick(cls.HAPPY_LIST)

    @property
    def PAUSE(cls) -> str:
        return cls._pick(cls.PAUSE_LIST)

    @property
    def LOADING(cls) -> str:
        return cls._pick(cls.LOADING_LIST)

    @property
    def NWELIV(cls) -> str:
        return cls._pick(cls.NWELIV_LIST)

    @property
    def BASED(cls) -> str:
        return cls._pick(cls.BASED_LIST)

    @property
    def NEUROJAM(cls) -> str:
        return cls._pick(cls.NEUROJAM_LIST)

    @property
    def EVILJAM(cls) -> str:
        return cls._pick(cls.EVILJAM_LIST)

    @property
    def OK(cls) -> str:
        return cls._pick(cls.OK_LIST)

    @property
    def WAVE(cls) -> str:
        return cls._pick(cls.WAVE_LIST)

    @property
    def JAM(cls) -> str:
        return cls._pick(cls.NEUROJAM_LIST + cls.EVILJAM_LIST)


class EMOTES(metaclass=EmotesMetaClass):
    SILLY_LIST: list[str] = []
    SAD_LIST: list[str] = []
    SIDE_EYE_LIST: list[str] = []
    STARE_LIST: list[str] = []
    HAPPY_LIST: list[str] = []
    PAUSE_LIST: list[str] = []
    LOADING_LIST: list[str] = []
    NWELIV_LIST: list[str] = []
    BASED_LIST: list[str] = []
    NEUROJAM_LIST: list[str] = []
    EVILJAM_LIST: list[str] = []
    OK_LIST: list[str] = []
    WAVE_LIST: list[str] = []
    SWARMFM_LIST: list[str] = []
    NEUROKARAOKE_LIST: list[str] = []
    DINKDONK_LIST: list[str] = []
    _filename = "data/emotes.json"

    @staticmethod
    def groups() -> list[str]:
        """Return all group names, including specials like JAM"""
        return [
            name for name, value in EmotesMetaClass.__dict__.items() if isinstance(value, property)
        ]

    @staticmethod
    def _pick(source: list[str]) -> str:
        return random.choice(source) if source else ""

    @classmethod
    def has(cls, group_name: str) -> bool:
        """Checks if a group exists (excludes JAM)."""
        attr_name = f"{group_name.upper()}_LIST"
        return hasattr(cls, attr_name)

    @classmethod
    def get_list(cls, group_name: str) -> list[str]:
        """Returns the emote list for a group"""
        group_name = group_name.upper()
        if group_name == "JAM":
            return cls.NEUROJAM_LIST + cls.EVILJAM_LIST
        target = getattr(cls, f"{group_name}_LIST", None)
        if isinstance(target, list):
            return target
        return []

    @classmethod
    def add_emote(cls, group_name: str, emote: str):
        """Adds an emote using a string name (e.g. 'SILLY')."""
        target = getattr(cls, f"{group_name.upper()}_LIST", None)
        if isinstance(target, list):
            if emote not in target:
                target.append(emote)
        else:
            raise ValueError(f"Group '{group_name}' is invalid or read-only.")

    @classmethod
    def load(cls):
        try:
            with open(cls._filename, "r") as f:
                raw = json.load(f).get("EMOTES", {})
                mapped = {f"{k}_LIST": v for k, v in raw.items()}
                for key, value in mapped.items():
                    setattr(cls, key, value)
        except Exception as e:
            print(e)

    @classmethod
    def save(cls):
        """Dumps the emotes to json file"""
        data_to_save = {}
        for key, value in cls.__dict__.items():
            if isinstance(value, list) and not key.startswith("__"):
                data_to_save[key.replace("_LIST", "")] = value

        with open(cls._filename, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=4)
