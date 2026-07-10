from collections import namedtuple
import dataclasses
import enum
import inspect
import re
import random
import time
import logging
import asyncio
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands, ui, utils

import player
import stats
from config import EMOTES, RANDOM_API, STORAGE_URL

log = logging.getLogger()


async def verify_message(channel: discord.abc.Messageable, message_id: int):
    try:
        msg = await channel.fetch_message(message_id)
        return bool(msg.id)
    except discord.NotFound:
        return False
    except (discord.Forbidden, discord.HTTPException):
        return None


SongName = namedtuple("SongName", ["choice", "lower"])


@dataclasses.dataclass
class GuessSongData:
    message: discord.Message
    last_action: float
    audio: player.SeekableOpusSource
    options: dict[str, SongName]
    correct_song: player.Song
    state: int = 0


class GuessSongCog(commands.Cog, group_name="guesssong"):
    TIMEOUTS = [60, 70, 80, 100, 120]
    TIMES = [3, 6, 15, 30, 60]
    REWARDS = [1000, 500, 200, 100, 50]
    NUM_OF_CHOICES = 20  # should not be more then 40, just to be safe
    DEFAULT_LIST = [app_commands.Choice(name="start", value="start")]

    async def song_autocomplete(
        self, interact: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        game = self.guesssong_data.get(interact.user.id)
        if game is None:
            return self.DEFAULT_LIST
        if not current:
            return [data.choice for data in game.options.values()]
        matches = []
        current = current.lower()
        for data in game.options.values():
            if current in data.lower:
                matches.append(data.choice)
        return matches

    @app_commands.command()
    @app_commands.guild_only()
    @app_commands.guild_install()
    @app_commands.autocomplete(song_name=song_autocomplete)
    async def guesssong(self, interact: discord.Interaction, song_name: str):
        """Bandle minigame, Guess the name of the song"""
        if song_name == "start":
            await self.start(interact)
        else:
            await self.answer(interact, song_name)

    class GameResult(enum.Enum):
        PENDING = enum.auto()
        WIN = enum.auto()
        LOSS = enum.auto()
        TIMEOUT = enum.auto()

    def __init__(self, bot):
        self.guesssong_data: dict[int, GuessSongData] = {}
        self.bot: commands.Bot = bot

    async def game_timeout(self, sleep_for: int, user_id: int):
        await asyncio.sleep(sleep_for + 0.1)
        game = self.guesssong_data.get(user_id)
        if game and (game.last_action + self.TIMEOUTS[game.state]) <= time.time():
            await self.update_game_message(game.message.edit, user_id, self.GameResult.TIMEOUT)
            await self.end_game(user_id, self.GameResult.TIMEOUT)

    def scheduled_task_done(self, fut):
        try:
            fut.result()
        except Exception:
            log.exception("Task failed:")

    async def update_game_message(
        self, method, user_id: int, result: GameResult = GameResult.PENDING
    ):
        game = self.guesssong_data.get(user_id)
        if game is None:
            return
        if result is self.GameResult.TIMEOUT:
            result = self.GameResult.LOSS
        discord_file = None
        if result is self.GameResult.PENDING:
            audio_lenght = self.TIMES[game.state]
            random_position = random.uniform(10.0, game.audio.duration() - 10 - audio_lenght)
            game.audio.seek(random_position)
            discord_file = discord.File(game.audio.sub_audio(audio_lenght), "sample.ogg")
        view = ui.View()
        for i in range(len(self.TIMES)):
            if i < game.state:
                color = discord.ButtonStyle.red
                button = ui.Button(label=f"{self.TIMES[i]}s", style=color, disabled=True)
            if i > game.state:
                button = ui.Button(label=f"{self.TIMES[i]}s", disabled=True)
            elif i == game.state:
                label = f"{self.TIMES[i]}s"
                disabled = True
                match result:
                    case self.GameResult.PENDING:
                        if len(self.TIMES) - 1 == game.state:
                            color = discord.ButtonStyle.gray
                        else:
                            label = f"+{self.TIMES[i+1]-self.TIMES[i]}s"
                            color = discord.ButtonStyle.blurple
                            disabled = False
                        emoji = EMOTES.SIDE_EYE
                    case self.GameResult.WIN:
                        color = discord.ButtonStyle.green
                        emoji = EMOTES.HAPPY
                    case self.GameResult.LOSS:
                        color = discord.ButtonStyle.red
                        emoji = EMOTES.SAD

                if not emoji:  # in case there are no EMOTES defined
                    emoji = None
                button = ui.Button(label=label, style=color, disabled=disabled, emoji=emoji)
                button.callback = self.button_get_more_time
            view.add_item(button)
        try:
            ins = inspect.signature(method)
            msg = f"Guess this song using `/guesssong answer [name]`\nTimeout <t:{int(time.time()+self.TIMEOUTS[game.state])}:R>"
            if "wait" in ins.parameters:
                return await method(msg, file=discord_file, view=view, wait=True)
            else:  # edit
                attachments = utils.MISSING
                if discord_file:
                    attachments = [discord_file]
                return await method(content=msg, attachments=attachments, view=view)
        except Exception:
            raise
        finally:
            if discord_file:
                discord_file.close()

    async def button_get_more_time(self, interact: discord.Interaction):
        game = self.guesssong_data.get(interact.user.id)
        reply = interact.response.send_message
        if game is None or game.message is None:
            await reply(f"Error: no active game {EMOTES.SILLY}", ephemeral=True)
            return
        if game.state == len(self.TIMES) - 1:
            await reply(f"Something went wrong {EMOTES.SILLY}", ephemeral=True)
            return
        if interact.message.id != game.message.id:
            await reply(f"That's not yours {EMOTES.SILLY}", ephemeral=True)
            return
        await interact.response.defer()
        game.state += 1
        game.last_action = time.time()
        task = interact.client.loop.create_task(
            self.game_timeout(self.TIMEOUTS[game.state], interact.user.id)
        )
        task.add_done_callback(self.scheduled_task_done)
        await self.update_game_message(interact.message.edit, interact.user.id)

    async def end_game(self, user_id, result: GameResult):
        game = self.guesssong_data.pop(user_id)
        try:
            embed_file = None
            music_file = None
            guild_id = game.message.guild.id
            match result:
                case self.GameResult.WIN:
                    reward = self.REWARDS[game.state]
                    stats.give_points(guild_id, user_id, reward, game.state)
                    msg = f"{EMOTES.HAPPY} That's right <@{user_id}> `+{reward}` points"
                case self.GameResult.LOSS:
                    msg = f"{EMOTES.SILLY} Wrong <@{user_id}>\nThe answer was:"
                case self.GameResult.TIMEOUT:
                    msg = f"Times up <@{user_id}>{EMOTES.SIDE_EYE}\nThe answer was:"
            music_cog = self.bot.get_cog("MusicCog")
            embed, embed_file = await music_cog.get_song_embed(guild_id, game.correct_song)
            game.audio.file_buffer.seek(0)
            filename = re.sub(
                r"[^\w\-_ .!,`~'@#$;%^&+=(){}\[\]]", " ", game.correct_song.song_name()
            ).replace("  ", " ")
            filename += ".ogg"
            music_file = discord.File(game.audio.file_buffer, filename)
            files = [music_file]
            if embed_file is not None:
                files.append(embed_file)
            await game.message.reply(msg, embed=embed, files=files)
        except Exception:
            log.exception("end_game: ")
        finally:
            game.message = None
            if embed_file is not None:
                embed_file.close()
            if music_file is not None:
                music_file.close()
            game.audio.close()

    async def start(self, interact: discord.Interaction):
        current_game = self.guesssong_data.get(interact.user.id)
        if current_game is not None:
            message_link = current_game.message.jump_url if current_game.message else None
            await interact.response.send_message(
                f"You still have active game {message_link} {EMOTES.SIDE_EYE}",
                ephemeral=True,
            )
            return
        await interact.response.defer(thinking=True)
        reply = interact.followup.send
        response = await interact.client.fetch_json_data(RANDOM_API)
        if response.error:
            await reply(
                f"Could not get data from neurokaraoke.com {EMOTES.SAD}\n(`{response.error}`)",
                ephemeral=True,
            )
            return
        if response.status != 200:
            await reply(
                f"Could not get data from neurokaraoke.com {EMOTES.SAD}\nHTML status code (`{response.status}`)",
                ephemeral=True,
            )
            return
        if not isinstance(response.json_data, list) or len(response.json_data) == 0:
            await reply(
                f"Could not get data from neurokaraoke.com {EMOTES.SAD}\n`Got empty result from random API`",
                ephemeral=True,
            )
            return
        songs_list: dict[str, SongName] = {}
        for _ in range(1000):
            selected_song_data = random.choice(response.json_data)
            opus_path = selected_song_data.get("opus")
            if opus_path:
                break
        if not opus_path:
            raise RuntimeError("Could not get song with valid opus!")
        selected_song = player.Song(selected_song_data)
        song_name = selected_song.song_name()
        songs_list[song_name] = SongName(
            app_commands.Choice(name=song_name, value=song_name), song_name.lower()
        )
        for song_data in response.json_data:
            song_name = player.Song(song_data).song_name()
            songs_list[song_name] = SongName(
                app_commands.Choice(name=song_name, value=song_name), song_name.lower()
            )
            if len(songs_list) >= self.NUM_OF_CHOICES:
                break
        if len(songs_list) < self.NUM_OF_CHOICES:
            await reply(
                f"Couldn't get enough songs from neurokaraoke.com {EMOTES.SILLY}",
                ephemeral=True,
            )
            return
        list_to_shuffle = list(songs_list.items())
        random.shuffle(list_to_shuffle)
        songs_list = dict(list_to_shuffle)
        session: aiohttp.ClientSession = interact.client.session
        try:
            audio_url = STORAGE_URL + opus_path.strip("/")
            async with session.get(audio_url) as resp:
                resp.raise_for_status()
                song_data = await resp.read()
                if len(song_data) == 0:
                    raise RuntimeError(f"Could not download audio from `{audio_url}`")
                audio_source = player.SeekableOpusSource(song_data)
        except Exception as e:
            await reply(
                f"Could not download audio from neurokaraoke.com {EMOTES.SAD}\n`{e}`",
                ephemeral=True,
            )
            return
        new_game = self.guesssong_data.setdefault(
            interact.user.id,
            GuessSongData(None, time.time(), audio_source, songs_list, selected_song),
        )
        message = await self.update_game_message(reply, interact.user.id)
        new_game.last_action = time.time()
        task = interact.client.loop.create_task(
            self.game_timeout(self.TIMEOUTS[0], interact.user.id)
        )
        task.add_done_callback(self.scheduled_task_done)
        if message is not None:
            message.guild = interact.guild
            new_game.message = message

    async def answer(self, interact: discord.Interaction, song_name: str):
        current_game = self.guesssong_data.get(interact.user.id)
        if current_game is None:
            await interact.response.send_message(
                f"Game not running {EMOTES.SILLY} use `/guesssong start` first", ephemeral=True
            )
            return
        if current_game.message.channel.id != interact.channel_id:
            channel_mention = current_game.message.channel.mention if current_game.message else None
            await interact.response.send_message(
                f"Current game open in {channel_mention} {EMOTES.SIDE_EYE}",
                ephemeral=True,
            )
            return
        await interact.response.defer(ephemeral=True)
        checker = await verify_message(current_game.message.channel, current_game.message.id)
        if checker is None:
            await interact.followup.send(
                f"Something went wrong, try again {EMOTES.SILLY}",
                ephemeral=True,
            )
            return
        if not checker:
            current_game.audio.close()
            self.guesssong_data.pop(interact.user.id)
            await interact.followup.send(
                f"Oryginal message not found, resting {EMOTES.SIDE_EYE}",
                ephemeral=True,
            )
            return
        current_game.last_action = time.time()
        if song_name in current_game.options:  # shortcut
            first_match = song_name
        else:
            song_name_lower = song_name.lower()
            items = current_game.options.items()
            first_match = next((name for name, data in items if song_name_lower in data.lower), None)
        current_game.options.pop(first_match, None)
        if first_match is None:
            first_match = ""
        edit = current_game.message.edit
        if current_game.correct_song.song_name() == first_match:
            await self.update_game_message(edit, interact.user.id, self.GameResult.WIN)
            await self.end_game(interact.user.id, self.GameResult.WIN)
            await interact.delete_original_response()
        else:
            if current_game.state >= len(self.TIMES) - 1:
                await self.update_game_message(edit, interact.user.id, self.GameResult.LOSS)
                await self.end_game(interact.user.id, self.GameResult.LOSS)
                await interact.delete_original_response()
            else:
                current_game.state += 1
                task = interact.client.loop.create_task(
                    self.game_timeout(self.TIMEOUTS[current_game.state], interact.user.id)
                )
                task.add_done_callback(self.scheduled_task_done)
                await self.update_game_message(edit, interact.user.id)
                await interact.followup.send(f"Wrong {EMOTES.SILLY}, try again", ephemeral=True)
