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

# API stuff, no need to edit
RANDOM_API = "https://api.neurokaraoke.com/api/songs/random"
AUDIO_URL = "https://audio.neurokaraoke.com/"
STORAGE_URL = "https://storage.neurokaraoke.com/"
SONG_URL = "https://twinskaraoke.com/song/"
SONG_API = "https://api.neurokaraoke.com/api/songs/"
SEARCH_API = "https://api.neurokaraoke.com/api/songs"
IMAGES_URL = "https://images.neurokaraoke.com"
COVER_ARTITS = "https://api.neurokaraoke.com/api/filters/cover-artists?page=0&pageSize=50"
PLAYLIST_API = "https://api.neurokaraoke.com/api/playlist/"
ARTIST_API = "https://api.neurokaraoke.com/api/artist/"
SETLISTS_API = "https://api.neurokaraoke.com/api/playlists?startIndex=0&pageSize=1000&search=&sortBy=&sortDescending=False&isSetlist=True&year=0"
PLAYLIST_URL = "https://twinskaraoke.com/playlist/"


class RADIO21(StrEnum):
    URL = "https://radio.twinskaraoke.com/public/neuro_21"
    LOGO = "https://files.catbox.moe/e5bshj.gif"
    SONGDATA = "https://radio.twinskaraoke.com/api/nowplaying/neuro_21"


class SWARMFM(StrEnum):
    URL = "https://player.sw.arm.fm"
    STREAM = "https://cast.sw.arm.fm/stream"
    SONGDATA = "https://swarm-fm.boopdev.com/v2/player"
    LOGO = "https://files.catbox.moe/4t19wk.png"
    COVER_ART_TWINS = "https://files.catbox.moe/6osfxs.gif"
    COVER_ART_NEURO = "https://files.catbox.moe/5i1xrb.gif"
    COVER_ART_EVIL = "https://files.catbox.moe/2h9bah.gif"


# Embed colors
class COLORS(IntEnum):
    QUEUE = 0x237FEB
    EMBED_DEFAULT = 0x237FEB
    NEURO = 0xFBD1A9
    EVIL = 0x8F0A0A
    VEDAL = 0x0A7908
    TWINS = 0xB305AA
