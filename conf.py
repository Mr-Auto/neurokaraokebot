# Channels that the !randomsong is allowed in
ALLOWED_CHANNELS = (0,)
# Max songs cached, since we use double cache, with requested songs it will be 8
# (4 in random queue and 4 in request queue, assuming there is 4 or more songs requested)
MAX_CACHE = 3
# Pause the playback after the bot is left alone in the VC for X minutes
PAUSE_AFTER = 3

from enum import Enum, IntEnum


# Embed colors
class COLORS(IntEnum):
    QUEUE = 0x237FEB
    EMBED_DEFAULT = 0x237FEB
    NEURO = 0xFBD1A9
    EVIL = 0x8F0A0A
    VEDAL = 0x0A7908
    TWINS = 0xB305AA


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
