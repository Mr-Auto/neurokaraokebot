import asyncio
from dataclasses import dataclass
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from discord import Intents, Activity, ActivityType, StatusDisplayType, Interaction
from discord import app_commands
from discord.ext import commands

import stats
from music_interface import MusicCog, NotAllowedError
from owner_interface import OwnerCog
from utility_interface import UtilityCog
from config import EMOTES

log = logging.getLogger()


@dataclass
class CustomResponse:
    json_data: str | None
    status: int | None
    error: str | None


class MyBot(commands.Bot):
    def __init__(self):
        intents = Intents(guilds=True, message_content=True, voice_states=True, guild_messages=True)
        status = "Playing songs 🎵"
        try:
            with open("data/activity_status.txt") as f:
                status = f.read()
            status = status.strip()
        except:
            pass
        activity = Activity(
            name="67",
            type=ActivityType.custom,
            state=status,
            status_display_type=StatusDisplayType.state,
        )
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            help_command=None,
            activity=activity,
        )
        self.session: aiohttp.ClientSession = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(15, 5))
        await self.add_cog(MusicCog(self))
        await self.add_cog(OwnerCog(self))
        await self.add_cog(UtilityCog())
        self.before_invoke(self.before_command_invoke)
        self.tree.on_error = self.on_app_command_error

    async def close(self):
        await super().close()
        if self.session:
            await self.session.close()

    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} servers:")
        for guild in self.guilds:
            print(f"- {guild.name:<12} ({guild.id})")
        print("\n")

    async def on_guild_join(_, guild):
        log.info(f"I have been added to a new server: {guild.name}[{guild.id}]")
        print(f"I have been added to a new server: {guild.name}[{guild.id}]")
        for channel in guild.text_channels:
            if "general" in channel.name.lower():
                await channel.send(EMOTES.WAVE)
                break

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return

        if isinstance(error, commands.CommandOnCooldown):
            time_left = round(error.retry_after, 1)
            await ctx.reply(f"⏳ Command under cooldown. {time_left}s")
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Missing argument: {error.param.name} {EMOTES.SIDE_EYE}")
            return

        if isinstance(error, commands.BadArgument):
            await ctx.reply(f"{error} {EMOTES.SIDE_EYE}")
            return

        if isinstance(error, NotAllowedError):
            await ctx.reply(f"❌ {error}", delete_after=10)
            return

        log.error(f"on_command_error: '!{ctx.command}': ", exc_info=error)

    async def on_app_command_error(self, interact: Interaction, error: app_commands.AppCommandError):
        if interact.response.is_done():
            repl = interact.followup.send
        else:
            repl = interact.response.send_message
        if isinstance(error, app_commands.CommandOnCooldown):
            await repl(
                f"⏳ Command under cooldown.  {error.retry_after:.1f} seconds.", ephemeral=True
            )
        else:
            await repl(f"Something went wrong {EMOTES.SAD}", ephemeral=True)
            log.exception(f"Unhandled tree error: {error}")

    async def before_command_invoke(self, ctx):
        log.info(
            f"Command: '!{ctx.command}' used by: {ctx.author}[{ctx.author.id}] in: ({ctx.guild} / {ctx.channel})"
        )

    async def fetch_json_data(
        self, url: str, *, get=None, post=None, headers=None
    ) -> CustomResponse:
        method = self.session.post if post is not None else self.session.get
        kwargs = {"json": post} if post is not None else {"params": get}
        for i in range(2):
            try:
                async with method(url, headers=headers, **kwargs) as resp:
                    resp.raise_for_status()
                    json_data = None
                    if resp.status == 200:
                        json_data = await resp.json()
                    return CustomResponse(json_data, resp.status, None)
            except (
                aiohttp.ClientConnectionError,
                aiohttp.ClientOSError,
                aiohttp.ClientPayloadError,
            ) as e:
                if i > 0:
                    return CustomResponse(None, None, str(e))
            except web.HTTPServerError as e:
                if i > 0:
                    return CustomResponse(None, e.status, f"Server Error({e.status})")
            except web.HTTPClientError as e:
                if i > 0 or e.status not in (408, 409, 421, 424, 429):
                    return CustomResponse(None, e.status, f"HTTP Error({e.status})")
            except web.HTTPError as e:
                return CustomResponse(None, e.status, f"Unknown HTTP Error({e.status})")
            except TimeoutError:
                return CustomResponse(None, None, "Timeout Error")
            except Exception:
                log.exception("exception in fetch_json_data")
                return CustomResponse(None, None, "Unknown Error")
            finally:
                await asyncio.sleep(0.5)


def my_namer(default_name: str) -> str:
    date_part = default_name.split(".")[-1]
    return f"logs/{date_part}.log"


os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)
stats.load()
handler = TimedRotatingFileHandler("logs/current.log", "midnight", 1, 30, "utf-8")
handler.namer = my_namer
formatter = logging.Formatter(
    "[{asctime}] [{levelname:<8} {module:>15}] {classspecific}{message}",
    style="{",
    defaults={"classspecific": ""},
)
handler.setFormatter(formatter)
bot = MyBot()
print("Starting up")
load_dotenv()
bot.run(os.getenv("BOT_TOKEN"), log_handler=handler, log_formatter=formatter, root_logger=True)
stats.save(True)
print("Shutting down")
log.info("Shutting down\n\n")
