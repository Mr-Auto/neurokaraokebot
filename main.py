import logging
import os
from discord import Intents, Activity, ActivityType, StatusDisplayType
from discord.ext import commands
from interface import MusicCog, emote, NotAllowedError
from config import EMOTES
from dotenv import load_dotenv
from datetime import datetime

log = logging.getLogger("main")


class MyBot(commands.Bot):
    def __init__(self):
        intents = Intents(guilds=True, message_content=True, voice_states=True, guild_messages=True)
        activity = Activity(
            name="Playing songs 🎵",
            type=ActivityType.custom,
            state="Playing songs 🎵",
            status_display_type=StatusDisplayType.state,
        )
        super().__init__(command_prefix="!", intents=intents, help_command=None, activity=activity)

    async def setup_hook(self):
        await self.add_cog(MusicCog(self))

    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} servers:")
        for guild in self.guilds:
            print(f"- {guild.name} (ID: {guild.id})")
        print("\n")

    async def on_guild_join(_, guild):
        log.info(f"I have been added to a new server: {guild.name}[{guild.id}]")
        for channel in guild.text_channels:
            if "general" in channel.name.lower():
                await channel.send(emote(EMOTES.WAVE))
                break

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(
            error, (commands.CommandNotFound, commands.CheckFailure, commands.CommandOnCooldown)
        ):
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Missing argument: {error.param.name} {emote(EMOTES.SIDE_EYE)}")
            return

        if isinstance(error, NotAllowedError):
            await ctx.reply(f"❌ {error}", delete_after=5)
            return

        log.error(f"Error in command '{ctx.command}':", exc_info=error)

    async def on_command(self, ctx: commands.Context):
        log.info(
            f"Command '!{ctx.command}' used by: {ctx.author}[{ctx.author.id}] in channel: {ctx.channel}[{ctx.channel.id}] server: {ctx.guild}[{ctx.guild.id}]"
        )


timestamp = datetime.now().strftime("%y%m%d-%H%M%S")
log_filename = f"neurokaraoke_{timestamp}.log"
handler = logging.FileHandler(filename=log_filename, encoding="utf-8", mode="w")
formatter = logging.Formatter("[{asctime}] [{levelname:<8}] {name}: {message}", style="{")
handler.setFormatter(formatter)
bot = MyBot()
print("Starting up")
load_dotenv()
bot.run(os.getenv("BOT_TOKEN"), log_handler=handler, log_formatter=formatter, root_logger=True)
print("Shutting down")
