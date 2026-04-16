import discord
from discord.ext import commands
import player
import pedalboard
import numpy
import math
import json
from config import EMOTES
from music_interface import cmd_verify, is_number

# pitchshift is too heavy

PRESETS_FILE = "presets.json"


class ModifiersCog(commands.Cog):
    @commands.command(name="volume", priority=4)
    @cmd_verify()
    async def volume_short(self, ctx: commands.Context, vol: float):
        """Short for !modifiers volume"""
        await ctx.invoke(self.volume, vol=vol)

    @commands.command(name="bass", priority=4)
    @cmd_verify()
    async def bass_short(self, ctx: commands.Context, value: str):
        """Short for !modifiers bass"""
        await ctx.invoke(self.bass, value=value)

    @commands.group(priority=3, invoke_without_command=True)
    @cmd_verify()
    async def modifiers(self, ctx: commands.Context):
        """Edit/Reset modifiers"""
        command_names = ", ".join(c.name for c in self.modifiers.commands)
        mp = self.get_music_player(ctx)
        plugin_names = ", ".join(type(plugin).__name__ for plugin in mp.effects_board)
        await ctx.reply(f"Available options: [{command_names}]\nActive plugins: [{plugin_names}]")

    @modifiers.command()
    async def help(self, ctx: commands.Context):
        embed = discord.Embed(title="Modifiers help:", color=discord.Color.orange())
        embed.description = "Effects come from python pedalboard and require at least one parameter (rest will be set to default)"
        for command in self.modifiers.commands:
            if command.name == "help":
                continue
            field_name = f"{command.name}"
            for alias in command.aliases:
                field_name += f" / {alias}"
            embed.add_field(name=field_name, value=command.help or "", inline=False)
        await ctx.reply(embed=embed)

    @modifiers.command()
    async def reset(self, ctx: commands.Context):
        """Reset all modifiers"""
        mp = self.get_music_player(ctx)
        mp.clear_modifiers()
        if mp.current_song.has_playback():
            mp.current_song.playback.playback_speed(1)
        await ctx.reply(f"Modifiers reset, volume 100% {EMOTES.OK}")

    @modifiers.command()
    async def speed(self, ctx: commands.Context, speed: float):
        """Change playback speed, special non pedalboard modifier, only applied to the current song"""
        if player.MODE != 1:
            await ctx.reply(f"Not supported in the `eager` mode {EMOTES.SILLY}")
            return
        if speed < 0.3 or speed > 3:
            await ctx.reply(f"Value `{speed}` not allowed {EMOTES.SILLY}")
            return
        mp = self.get_music_player(ctx)
        if mp.current_song.has_playback():
            mp.current_song.playback.playback_speed(1 / speed)

    @modifiers.command(aliases=("Gain",))
    async def volume(self, ctx: commands.Context, vol: float):
        """Change the volume, values in %, params: [vol]"""
        mp = self.get_music_player(ctx)
        vol = numpy.clip(vol, 0, 300.0)
        new_db = 0
        if vol != 100:
            new_db = 20 * math.log10(vol / 100)

        if new_db == 0:
            mp.remove_pedalboard_effect(pedalboard.Gain)
        else:
            gain = mp.get_pedalboard_effect(pedalboard.Gain)
            gain.gain_db = new_db
        await ctx.reply(f"Volume set to {vol}% 🔊")

    @modifiers.command(aliases=("LowShelfFilter",))
    async def bass(self, ctx: commands.Context, value: str, cutoff_freq: float = 200):
        """Change bass, params: ["boost" / "reset" / gain_db] [cutoff_freq=200]"""
        if value.lower() == "boost":
            value = "4"
        await self.handle_effect(
            ctx, pedalboard.LowShelfFilter, "gain_db", value, cutoff_frequency_hz=cutoff_freq
        )

    @modifiers.command(aliases=("Limiter",))
    async def limiter(self, ctx: commands.Context, value: str, release_ms: float = None):
        """Limiter, params: ["reset" / threshold] [release_ms]"""
        await self.handle_effect(
            ctx, pedalboard.limiter, "threshold_db", value, release_ms=release_ms
        )

    @modifiers.command(aliases=("Reverb",))
    async def reverb(
        self,
        ctx: commands.Context,
        value: str,
        damping: float = None,
        wet_level: float = None,
        dry_level: float = None,
        width: float = None,
        freeze_mode: float = None,
    ):
        """Reverb, params: ["reset" / room_size] [damping] [wet_level] [dry_level] [width] [freeze_mode]"""
        await self.handle_effect(
            ctx,
            pedalboard.Reverb,
            "room_size",
            value,
            damping=damping,
            wet_level=wet_level,
            dry_level=dry_level,
            width=width,
            freeze_mode=freeze_mode,
        )

    @modifiers.command(aliases=("Compressor",))
    async def compressor(
        self,
        ctx: commands.Context,
        value: str,
        ratio: float = None,
        attack_ms: float = None,
        release_ms: float = None,
    ):
        """Compressor, params: ["reset" / threshold] [ratio] [attack_ms] [release_ms]"""
        await self.handle_effect(
            ctx,
            pedalboard.Compressor,
            "threshold_db",
            value,
            ratio=ratio,
            attack_ms=attack_ms,
            release_ms=release_ms,
        )

    @modifiers.command(aliases=("HighShelfFilter",))
    async def treble(self, ctx: commands.Context, value: str, cutoff_freq: float = 8000):
        """HighShelfFilter, params: ["reset" / gain_db] [cutoff_freq=8k]"""
        await self.handle_effect(
            ctx, pedalboard.HighShelfFilter, "gain_db", value, cutoff_frequency_hz=cutoff_freq
        )

    @modifiers.command(aliases=("Chorus",))
    async def chorus(
        self,
        ctx: commands.Context,
        value: str,
        rate_hz: float = None,
        centre_delay_ms: float = None,
        feedback: float = None,
        mix: float = None,
    ):
        """Chorus, params: ["reset" / depth] [rate_hz] [centre_delay_ms] [feedback] [mix]"""
        await self.handle_effect(
            ctx,
            pedalboard.Chorus,
            "depth",
            value,
            rate_hz=rate_hz,
            centre_delay_ms=centre_delay_ms,
            feedback=feedback,
            mix=mix,
        )

    @modifiers.command(aliases=("Delay",))
    async def delay(
        self, ctx: commands.Context, value: str, feedback: float = None, mix: float = None
    ):
        """Delay, params: ["reset" / delay(s)] [feedback] [mix]"""
        await self.handle_effect(
            ctx, pedalboard.Delay, "delay_seconds", value, feedback=feedback, mix=mix
        )

    @modifiers.command(aliases=("LowpassFilter",))
    async def lowpass(self, ctx: commands.Context, value: str):
        """LowpassFilter, params: ["reset" / cutoff_freq]"""
        await self.handle_effect(ctx, pedalboard.LowpassFilter, "cutoff_frequency_hz", value)

    @modifiers.command(aliases=("HighpassFilter",))
    async def highpass(self, ctx: commands.Context, value: str):
        """HighpassFilter, params: ["reset" / cutoff_freq]"""
        await self.handle_effect(ctx, pedalboard.HighpassFilter, "cutoff_frequency_hz", value)

    @modifiers.command(aliases=("Clipping",))
    async def clipping(self, ctx: commands.Context, value: str):
        """Clipping, params: ["reset" / threshold]"""
        await self.handle_effect(ctx, pedalboard.Clipping, "threshold_db", value)

    @modifiers.command(aliases=("Bitcrush",))
    async def bitcrush(self, ctx: commands.Context, value: str):
        """Bitcrush, params: ["reset" / bit_depth]"""
        await self.handle_effect(ctx, pedalboard.Bitcrush, "bit_depth", value)

    @modifiers.command()
    async def save(self, ctx: commands.Context, *, preset_name: str):
        """Save current setup as preset"""
        if len(preset_name) > 20:
            ctx.reply(f"Preset name max 20 characters {EMOTES.SILLY}")
            return
        mp = self.get_music_player(ctx)
        if len(mp.effects_board) == 0:
            await ctx.reply(f"No active effects to save {EMOTES.SILLY}")
            return
        try:
            with open(PRESETS_FILE, "r") as f:
                all_presets = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            all_presets = {}

        if preset_name in all_presets:
            await ctx.reply(f"Preset `{preset_name}` already exists {EMOTES.SILLY}")
            return
        all_presets[preset_name] = {}
        for plugin in mp.effects_board:
            params = {
                name: getattr(plugin, name)
                for name, value in type(plugin).__dict__.items()
                if isinstance(value, property)
            }
            all_presets[preset_name][type(plugin).__name__] = params

        with open(PRESETS_FILE, "w") as f:
            json.dump(all_presets, f, indent=4)

        await ctx.reply(f"Preset `{preset_name}` Saved {EMOTES.HAPPY}")

    @modifiers.command()
    async def load(self, ctx: commands.Context, *, preset_name: str = None):
        """Load saved preset, omit the name to list all available"""
        try:
            with open(PRESETS_FILE, "r") as f:
                all_presets = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            all_presets = {}
        if preset_name is None:
            all_preset_names = ", ".join(all_presets)
            await ctx.reply(f"Avaible presets: [{all_preset_names}]")
            return

        if preset_name not in all_presets:
            char_limit = 20
            if len(preset_name) > char_limit:
                truncated = preset_name[:char_limit] + "..."
            else:
                truncated = preset_name
            await ctx.reply(f"No such preset found `{truncated}` {EMOTES.SILLY}")
            return

        plugins = []
        for name, params in all_presets[preset_name].items():
            plugin_class = getattr(pedalboard, name)
            plugins.append(plugin_class(**params))

        mp = self.get_music_player(ctx)
        mp.effects_board = pedalboard.Pedalboard(plugins)
        mp.apply_effects_board()
        await ctx.reply(f"Loaded preset `{preset_name}` {EMOTES.HAPPY}")

    async def handle_effect(
        self, ctx: commands.Context, plugin_class, main_param_name, value, **kwargs
    ):
        mp = self.get_music_player(ctx)
        effect_name = plugin_class.__name__

        if value.lower() == "reset":
            mp.remove_pedalboard_effect(plugin_class)
            return await ctx.reply(f"{effect_name} removed {EMOTES.OK}")

        if not is_number(value):
            return await ctx.reply(
                f'Wrong parameter. Use ["reset" / {main_param_name}] {EMOTES.STARE}'
            )

        effect = mp.get_pedalboard_effect(plugin_class)
        try:
            setattr(effect, main_param_name, float(value))
            for attr, val in kwargs.items():
                if val is not None:
                    setattr(effect, attr, val)
        except ValueError as e:
            return await ctx.reply(str(e))

        attrs = [main_param_name] + [k for k, v in kwargs.items() if v is not None]
        status = ", ".join([f"{a}: {getattr(effect, a)}" for a in attrs])
        await ctx.reply(f"{effect_name} set {status}")

    def get_music_player(self, ctx: commands.Context) -> player.MusicPlayer:
        return ctx.bot.get_cog("MusicCog").music_players.get(ctx.guild.id)
