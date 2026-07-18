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
