# Neuro Karaoke Bot

Bot that plays songs from neurokaraoke.com in discord VC

Made with discord.py 2.7.1 on python 3.12, though will probably run on lower versions as well

Main branch is the latest version, releases are only used as backups

---

## 💾 Installation

* Create your bot on https://discord.com/developers/home
* Make sure the "Message Content Intent" is enabled
* Setup your python environment/docker (if desired)
* Clone repository
* Install dependencies `pip install -r requirements.txt`
* Create `.env` text file and put `BOT_TOKEN=[your bot token here]` inside (optionally token can also be feed in when starting the bot or put in system PATH etc.)
* (optional) Setup emotes for the bot to use: create `data/emotes.json` with the structure
  ```json
  {
    "EMOTES": {
        "SILLY": [],
        "SAD": [],
        "SIDE_EYE": [],
        "STARE": [],
        "HAPPY": [],
        "PAUSE": [],
        "LOADING": [],
        "NWELIV": [],
        "BASED": [],
        "NEUROJAM": [],
        "EVILJAM": [],
        "OK": [],
        "WAVE": [],
        "SWARMFM": [],
        "NEUROKARAOKE": [],
        "DINKDONK": []
    }
  }
  ```
  And put your emotes in the discord format `"<(a):NAME:EMOTES_ID>"`, I recommend using application emotes, discord gives you 2k slots for that.
  If you still want to use server emotes, keep in mind that bot can only use emotes from servers it is in and requires permissions to use emotes from different server it is currently in.
  Bot will use those emotes in various response messages
* Start the bot `python main.py`
* Sync slash commands using `!sync`, if you're setting up just one server you can use `local` option (note: using `global` option may take serval minutes and should not be used too often or the discord may just ignore the request).
Sync generally needs to be done once per bot. You may need to run it again after updating any of the slash commands

---

## Commands:
* **Slash commands**
  *  **Invite bot to a VC** `/joinvc`
  *  **Command list** `/commands` displays text commands list
  *  **Random song** `/randomsong` will get and display random song from the neurokaraoke
  *  **Guess Song game** `/guesssong [start/song name]`, the bot sends short audio fragment and you have limited time to guess it's name using the `/guesssong` command
  *  **User stats** (how long did user listened to karaoke, how many songs have they requested etc.):
     * `/stats me/user` - check your own stats or any member in this server. When checking your own stats, you can also select `global` to see combined stats from all the servers you used the bot.
     * `/stats server` - same as above, but for the whole server
     * `/stats guesssong [user]` - check user guess song game stats
     * `/stats top users [top_by]` - check a leaderboard by total time listened to karaoke, song listened to, song requested etc.
     * `/stats top songs [top_by]` - leaderboard of top songs by play count or request count
     * `/stats top my_requests/user_requests` - check your or other member top requested songs
 
* **Main commands** (work only in VC with the bot):
  *  **Song Requests** `!songrequest [search string]` will add first matching song to the queue, alternatively `!findsong [search string]` to display results before requesting (max 60 results)
  *  **Radio21 or SwarmFM** can be requested with `!radio` command, radio playback works like a single song, will play until skipped using `!skip`
  *  **Song title as VC status** can be disabled with `!updatestatus on/off`
  *  **View playlist** `!playlist [url/ID/"lofi"]`, will display the playlist in the same view as `!findsong`, allowing to request songs from it
  *  **View setlist** `!setlist`, will display form with all the available setlists, allowing you to request whole setlist or open it as playlist
  *  **Reset bot** `!reconnect` will make the bot reconnect and full reset for this server (clear queue etc.)
  *  **Other self explanatory** `!pause`, `!resume`, `!song`, `!nextsong`, `!skip`, `!queue`
* **Additional**:
  *  **Song lookup** `!findsong [search string]` displays view of the lookup result (max 99 results)
  *  **Issues list** can be seen using `!issue` command, mostly advices on what to do when bot is not working correctly
* **Setlist updates** only server owner, use `!setlistupdates [channel/clear] [ping role (optional)]` to set/clear channel receiving updates when new karaoke setlist is uploaded

* **Admin** (only bot's owner)
  * **Check/change current playback mode** `!mode [None/stream/download]`, the bot has two modes, you may choose depending on your setup (default set in the `player.py`), the `stream` mode will download music in packets aka streaming it, 
`download` mode downloads the whole file and stores it in RAM, the second option has less complexity and should generally be less CPU demanding but uses more RAM
  *  **Bot status** `!status` will display all the servers the bot is in, plus additional information (has valid MusicPlayer, is in VC, is paused)
  *  **Debug emotes** `!emotes [group name]` will make the bot send all the emotes from given group, useful for overview and check if all the emotes are working/are in correct format
  *  **Stop the bot** `!exit` will kill the bot process completely (can be used as restart on a hosting platform that supports auto restart)
  *  **Restart** `!restart` will start new process of this bot and close the current one
  *  **Latency test** with `!latency` (can take up to few minutes)
  *  **Dump stats** `!dumpstats` will save all the stats to file. This is just emergency safety feature, stats are saved every 30 minutes, or if bot is stopped using `!exit` or `!restart`
  *  **Discord Activity** text for the bot can be set using `!setstatus [text]` command (the text on the user list / in user profile) 
  *  **Sync/Unsync** slash commands with `!sync/!unsync [local/global]`, this needs to be done once after installing the bot

---

## 🎧 Usage

1. Make sure the bot is online
2. Join a voice channel in your Discord server
3. In the text portion of the voice channel type `/joinvc`
4. Bot will join VC, load random queue and start playing

---

## ⚠️ Notes

* This project is not affiliated with neurokaraoke.com
* Availability of songs depends on the source website, bot does not cache songs long term
* `config.py` contains additional settings if needed
* Make sure your bot has permission to:

  * Join voice channels
  * Speak
  * Set Voice Channel Status
  * Send messages
