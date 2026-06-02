# Neuro Karaoke Bot 🎤

Bot that plays songs from neurokaraoke.com in discord VC

Made with discord.py 2.7.1 on python 3.11, though will probably run on lower versions as well

Main branch is the latest version, releases are only used as backups

---

## 💾 Installation

* Create your bot on https://discord.com/developers/home
* Setup your python environment/docker (if desired)
* Clone repository
* Install dependencies `pip install -r requirements.txt`
* Create `.env` text file and put `BOT_TOKEN=[your bot token here]` inside (optionally token can also be feed in when starting the bot or put in system PATH etc.)
* (optional) Setup emotes for the bot to use: create `emotes.json` with the structure
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
        "WAVE": []
    }
  }
  ```
  And put your emotes in the discord format `"<(a):NAME:EMOTES_ID>"`, I recommend using application emotes, discord gives you 2k slots for that.
  If you still want to use server emotes, keep in mind that bot can only use emotes from servers it is in and requires permissions to use emotes from different server it is currently in.
  Bot will use those emotes in various response messages
* Start the bot `python main.py`

---

## Commands:
* **Invite bot to a VC** `!karaokehere` 
* **Main commands** (work only in VC with the bot):
  *  **Song Requests** `!songrequest [search string]` will add first matching song to the queue, alternatively `!findsong [search string]` to display results before requesting (max 60 results)
  *  **Radio21** can be requested with `!radio`, radio playback works like a single song, will play until skipped using `!skip`
  *  **Song title as VC status** can be disabled with `!updatestatus on/off`
  *  **View playlist** `!playlist [url/ID]`, will display the playlist in the same view as `!findsong`, allowing to request songs from it
  *  **View setlist** `!setlist`, will display form with all the setlists, allowing you to request whole setlist or open it as playlist
  *  **Reset bot** `!reconnect` will make the bot reconnect and full reset for this server (clear queue etc.)
  *  **Other self explanatory** `!commands`, `!pause`, `!resume`, `!song`, `!nextsong`, `!skip`, `!queue`
* **Additional** (work everywhere):
  *  **User stats** can be seen using `!stats` (how long did user listened to karaoke, how many songs have they requested etc.), command also accepts user name or mention as parameter to check other people stats. Also special string `server` to see the whole server stats
  *  **Random song** `!randomsong` will get and display random song from the website
  *  **Song lookup** `!findsong [search string]` displays view of the lookup result (max 99 results)
  *  **Command list** `!commands` same as the main one, list of all commands
  *  **Issues list** can be seen using `!issue` command, mostly advices on what to do when bot is not working correctly
* **Admin** (only bot's owner)
  * **Check/change current playback mode** `!mode [None/eager/lazy]`, the bot has two modes, `eager` and `lazy`, you may choose depending on your setup (default set in the `player.py`), the `eager` one will convert the song to a raw bytes, consuming more RAM but using very little processing power, `lazy` will keep songs in the original format and convert them on the fly (note: songs uploaded to neurokaraoke.com by users are kept in m4a format, not supported by the `lazy` backend pedalboard and will need additional conversion step before storing)
  *  **Bot status** `!status` will display all the servers the bot is in plus additional information (has valid MusicPlayer, is in VC, is paused)
  *  **Debug emotes** `!emotes [group name]` will make the bot send all the emotes from given group, useful for overview and check if all the emotes are working/are in correct format
  *  **Stop the bot** `!exit` will kill the bot process completely (can be used as restart on a hosting platform that supports auto restart)
  *  **Restart** `!restart` will start new process of this bot and close the current one
  *  **Latency test** with `!latency` (takes few seconds)
  *  **Dump stats** `!dumpstats` will save all the stats to file. This is just emergency safety feature, stats are saved if bot is stopped using `!exit` or `!restart`

---

## 🎧 Usage

1. Make sure the bot is online
2. Join a voice channel in your Discord server
3. In the text potion of the voice channel type `!karaokehere`
4. Bot will join VC, load random queue and start playing

---

## ⚠️ Notes

* This project is not affiliated with neurokaraoke.com
* Availability of songs depends on the source website, bot does not cache songs longterm
* `config.py` contains additional settings if needed
* Make sure your bot has permission to:

  * Join voice channels
  * Speak
  * Set Voice Channel Status
  * Send messages
    
* Also in discord developer portal, make sure "Message Content Intent" is enabled
