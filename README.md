# Neuro Karaoke Bot

Bot that plays songs from (neuro/evil/twins)karaoke.com in Discord voice channel

Made with discord.py 2.7.1 on python 3.12 (though will probably run on lower versions as well)

The main branch contains the latest version, releases are only used as backups

You can invite the officially hosted bot to your discord server via this link:
https://neurokaraoke.com/discord-bot

---

## 💾 Installation steps for self hosting:

* Create your bot on https://discord.com/developers/home
* Make sure the "Message Content Intent" is enabled (for the text commands)
* Set up your Python environment/docker (if desired, recommended)
* Clone/download repository
* Install dependencies `pip install -r requirements.txt`
* Create `.env` text file and put `BOT_TOKEN=[your bot token here]` inside (optionally token can also be passed in when starting the bot or put in system PATH etc.)
* Start the bot `python main.py`
* Sync slash commands using `!sync`, if you're setting up just one server you can use `local` option (note: using `global` option may take several minutes and should not be used too often or the discord may just ignore the request).
Sync generally needs to be done once per bot. You may need to run it again after making any changes to the slash commands. It is recommended to run `!sync global` first before inviting the bot to multiple servers.
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
  And put your emotes in the discord format `"<(a):NAME:EMOTE_ID>"`, I recommend using application emotes, discord gives you 2k slots for that.
  If you still want to use server emotes, keep in mind that bot can only use emotes from servers it is in and requires permissions to use emotes from different server it is currently in.
  Bot will use those emotes in various response messages
* **(Optional) Setup Custom Progress Bars:** You can also fully customize the song progress bar embed with custom emotes. See the [Custom Progress Bar Guide](progressbar.md) for setup instructions.

---

## Needed permissions:

  * **Connect** - Joining voice channels
  * **Speak** - For being able to play music
  * **Set Voice Channel Status** - Allows setting the status to the current song being played
  * **Send messages**  - For all responses to commands
  * **View Channels** - Can be customized by server owner to allow commands only in specific channels
  * **Embed Links** - For the setlist notification message
  * **Attach Files** - The animated cover arts need to be uploaded to discord due to how the karaoke websites handles them
  * **Bypass Slowmode** - Recommended for any discord bot

---

## Commands:

#### **Slash commands:**
| Command | Params | Description |
|---|---|---|
| `/joinvc` || Invite bot to the voice channel |
| `/commands` || Display all the text commands with descriptions |
| `/randomsong` || Gets random song from the (neuro/evil/twins)karaoke.com |
| `/guesssong` | `"start", song name` | Bot sends short audio fragment and you have limited time to guess its name using the `/guesssong` command |
| `/stats` | `"me", "user", "server", "top", ...` | Check stats of user/server/leaderboards |

#### **Main commands:**
| Command | Params | Description | Global Command\*\* |
|---|---|---|---|
| `!pause` || Pause current song |
| `!resume` || Resume current song |
| `!skip` || Skip current song and go to the next one |
| `!reconnect` || Resets the bot connection to the voice channel and the queue |
| `!song` || Display info about current song |
| `!nextsong` || Display info about next song |
| `!queue` | `page (optional)` | Display current song queue |
| `!songrequest` | `search string` | Will add first matching song to the queue |
| `!findsong` | `search string` | Display search results (max 60) with ability to request them | Yes |
| `!radio` | `"radio21", "swarmFM"` | Request radio playback, works like a single song, use `!skip` to go back to the normal song queue |
| `!updatestatus` | `"yes", "no"` | Enable/disable bot setting voice channel status to the current song name |
| `!playlist` | `url, ID, "lofi"` | display a playlist in the same view as `!findsong`, allowing to request songs from it. Supports normal playlist, artist page playlist, genres/themes/moods playlist |
| `!setlist` || View karaoke setlists, allows requests |
| `!favorites` || View your favorites from (neuro/evil/twins)karaoke.com, allows requests |
| `!issue` || Display common issues list | Yes |
| `!setlistupdates` | `channel, "clear"` \| `ping role (optional` | Sets up new setlist notification (server owner only) | Yes |

\* This command requires `API_KEY` in the .env file to function. Due to privacy reasons, this is kept secret by neurokaraoke owner, if not provided, the command will not work and will not be listed in the commands list

\*\* General use commands that do not require you to be in a voice channel. Note: Behavior may vary slightly if used while connected to voice

#### **Bot owner commands:**
| Command | Params | Description |
|---|---|---|
| `!mode` | `"stream", "download"` | Change audio handling mode. `stream` - will download music in packets aka streaming it (less RAM demanding). `download` - downloads the whole file and stores it in RAM (less CPU-intensive). Use without param to see current mode |
| `!status` || Display list of all servers the bot is connected to with it's status in that server |
| `!emotes` | `group name` | Debug command to display emotes set up in `emotes.json` |
| `!exit` || Terminate bot process |
| `!restart` || Start new process of this bot then close the current one |
| `!latency` || Latency test |
| `!dumpstats` || Dump stats to a file. This command is just for debug/safety. Normally stats are saved when using `!exit` or `!restart` plus automatically every 30min |
| `!setstatus` | `text` | Set the bot activity text |
| `!sync` | `"local", "global"` | Sync slash commands |
| `!unsync` | `"local", "global"` | Unsync slash commands |


---

## 🎧 Usage

1. Make sure the bot is online
2. Join a voice channel in your Discord server
3. Use `/joinvc` to invite the bot
4. Bot will join VC, load random queue and start playing

---

## ⚠️ Notes

* Availability of songs depends on the source website, bot does not cache songs long term
* `config.py` contains additional settings
