from enum import IntEnum, StrEnum

# Max songs cached, since we use double cache, with requested songs it will be 4 + one currently playing
# (2 in random queue and 2 in request queue, assuming there is 2 or more songs requested)
MAX_CACHE = 2
# Pause the playback after the bot is left alone in the VC for X minutes
PAUSE_AFTER = 2
# Length of pause between songs (in seconds)
PAUSE_DURATION = 3
# Bitrate for the opus encoder (when applicable)
OPUS_BITRATE = 128


# Url's for linking to the website
SONG_URL = "https://twinskaraoke.com/song/"
PLAYLIST_URL = "https://twinskaraoke.com/playlist/"


class API(StrEnum):
    _API = "https://api.neurokaraoke.com/api"
    SONGS = _API + "/songs"
    RANDOM = SONGS + "/random"
    PLAYLIST = _API + "/playlist"
    ARTIST = _API + "/artist"
    GENRES = _API + "/genres"
    MOODS = _API + "/moods"
    THEMES = _API + "/themes"
    PLAYLISTS = _API + "/playlists"
    COVER_ARTITS = _API + "/filters/cover-artists"


class STORAGE(StrEnum):
    STORAGE = "https://storage.neurokaraoke.com/"
    AUDIO = "https://audio.neurokaraoke.com/"
    IMAGES = "https://images.neurokaraoke.com/"


class RADIO21(StrEnum):
    URL = "https://radio.twinskaraoke.com/public/neuro_21"
    LOGO = "https://x02.me/u/DRAH.gif"
    SONGDATA = "https://radio.twinskaraoke.com/api/nowplaying/neuro_21"


class SWARMFM(StrEnum):
    URL = "https://player.sw.arm.fm"
    STREAM = "https://cast.sw.arm.fm/stream"
    SONGDATA = "https://swarm-fm.boopdev.com/v2/player"
    LOGO = "https://x02.me/u/P9WU3A.png"
    COVER_ART_TWINS = "https://x02.me/u/S4ZRZ.gif"
    COVER_ART_NEURO = "https://x02.me/u/XUWK4K.gif"
    COVER_ART_EVIL = "https://x02.me/u/9BMKG.gif"


# Embed colors
class COLORS(IntEnum):
    QUEUE = 0x237FEB
    EMBED_DEFAULT = 0x237FEB
    NEURO = 0xFBD1A9
    EVIL = 0x8F0A0A
    VEDAL = 0x0A7908
    TWINS = 0xB305AA
