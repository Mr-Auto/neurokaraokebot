# Channels that the !randomsong is allowed in
ALLOWED_CHANNELS = (0,)

from enum import Enum, IntEnum


# Embed colors
class COLORS(IntEnum):
    QUEUE = 0x237FEB
    EMBED_DEFAULT = 0x237FEB
    NEURO = 0xFDE7D3
    EVIL = 0x8F0A0A
    VEDAL = 0x0A7908


# Emotes to be used in various messages
class EMOTES(Enum):
    SILLY = ("",)
    SAD = ("",)
    SIDE_EYE = ("",)
    STARE = ("",)
    HAPPY = ("",)
    PAUSE = ("",)
    LOADING = ("",)
    NWELIV = ("",)
    BASED = ("",)
    NEUROJAM = ("",)
    EVILJAM = ("",)
    JAM = NEUROJAM + EVILJAM
    OK = ("",)
    WAVE = ("",)
