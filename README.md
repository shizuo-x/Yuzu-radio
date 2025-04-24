# Modular Discord Radio Bot (Dockerized)

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.x-blue.svg)](https://github.com/Rapptz/discord.py)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)

A flexible and resilient Discord bot focused on playing online radio streams 24/7. Built with Python, `discord.py`, and packaged for easy deployment using Docker. Its modular Cog architecture allows for easy extension and customization.

## Features

*   Plays online radio stream URLs (HTTP/HTTPS).
*   Supports a predefined list of radio streams (easily configurable).
*   Paginated list command (`/list` or `,,list`) with descriptions for easy browsing.
*   Displays currently playing track metadata (song title/artist) if the stream provides it (ICY metadata).
*   Control playback with both Slash Commands (`/`) and Prefix Commands (default: `,,`).
*   Displays a "Now Playing" embed with stream info and metadata.
*   Stop playback using commands or reacting with ⏹️ to the Now Playing message.
*   **Persistent State:** Remembers which stream was playing in which channel.
*   **Auto-Resume:** Automatically attempts to rejoin and resume playback after bot restarts.
*   **Robust Reconnection:** Actively tries to reconnect and resume if voice connection or gateway connection drops unexpectedly.
*   **Modular Cog System:** Functionality is separated into Cogs (`cogs/` directory) for easy maintenance and contribution.
*   **Dockerized:** Simple setup and deployment using Docker and Docker Compose.

## Technology Stack

*   **Language:** Python 3.11+
*   **Library:** `discord.py` (v2.x)
*   **HTTP Requests:** `aiohttp` (for metadata fetching)
*   **Containerization:** Docker & Docker Compose
*   **Audio Processing:** FFmpeg (installed within Docker image)

## Prerequisites

*   **Docker:** Install Docker Desktop (Windows, macOS) or Docker Engine (Linux). Get it from [https://www.docker.com/get-started](https://www.docker.com/get-started).
*   **Docker Compose:** Usually included with Docker Desktop. For Linux Server, you might need to install it separately (follow Docker's official documentation).
*   **Git:** Required to clone this repository.

## Setup Instructions

1.  **Clone the Repository:**
    ```bash
    git clone https://your-repository-url/discord-radio-bot-modular.git
    cd discord-radio-bot-modular
    ```
    *(Replace `your-repository-url` with the actual URL of your Git repo)*

2.  **Create the `.env` File:**
    *   This file stores your secret bot token.
    *   Copy the example file:
        *   **Linux/macOS:** `cp .env.example .env`
        *   **Windows (Command Prompt):** `copy .env.example .env`
        *   **Windows (PowerShell):** `Copy-Item .env.example .env`

3.  **Get Your Discord Bot Token:**
    *   Go to the [Discord Developer Portal](https://discord.com/developers/applications).
    *   Click **"New Application"** (or select an existing one). Give it a name (e.g., "My Radio Bot").
    *   Navigate to the **"Bot"** tab (left menu).
    *   Click **"Add Bot"** and confirm.
    *   Under the bot's username, find the **"Token"** section. Click **"Reset Token"** (or "View Token") and **COPY** the token string. **Treat this token like a password! Do not share it!**
    *   **VERY IMPORTANT:** Scroll down on the Bot page to **"Privileged Gateway Intents"**. You **MUST ENABLE** these intents:
        *   `SERVER MEMBERS INTENT` (Needed for user info)
        *   `MESSAGE CONTENT INTENT` (Needed for prefix commands)
        *   *(Presence Intent is usually not needed)*
    *   Click **"Save Changes"** at the bottom.

4.  **Configure `.env`:**
    *   Open the `.env` file you created in Step 2 with a text editor.
    *   Replace the placeholder `YOUR_BOT_TOKEN_GOES_HERE` with the **actual bot token** you copied.
    *   The line should look like: `DISCORD_TOKEN=AbCdEfGhIjKlMnOpQrStUvWxYz.aBcDeF.abcdefghijklmnopqrstuvwxyz123456` (but with your real token).
    *   Save and close the `.env` file.

5.  **Build and Run the Bot:**
    *   Make sure Docker Desktop or Docker Engine is running.
    *   In your terminal (inside the `discord-radio-bot-modular` folder), run:
    ```bash
    docker-compose up -d
    ```
    *   This command will:
        *   Build the Docker image based on `Dockerfile` (if not already built).
        *   Create and start a container named `discord-radio-bot-modular` based on `docker-compose.yml`.
        *   Run the bot in the background (`-d`).

6.  **Invite Your Bot:**
    *   Go back to the Discord Developer Portal -> Your Application -> **OAuth2 -> URL Generator**.
    *   Select the following **Scopes**:
        *   `bot`
        *   `applications.commands` (Required for Slash Commands)
    *   In the "Bot Permissions" section that appears below, select these permissions:
        *   **General:** `Read Messages/View Channels`, `Send Messages`, `Embed Links`, `Read Message History`, `Add Reactions`
        *   **Voice:** `Connect`, `Speak`
        *   **Recommended:** `Manage Messages` (Allows removing reactions cleanly for pagination).
    *   Copy the **Generated URL** at the bottom.
    *   Paste the URL into your web browser, select the server you want to add the bot to, and click **Authorize**.

## Usage

*   **Default Prefix:** `,,`
*   **Help Command:** `,,help` or `/help`
*   **List Stations:** `,,list` or `/list` (Paginated with reactions)
*   **Play Station:** `,,play <URL or Predefined Name>` or `/play stream:<URL or Predefined Name>`
*   **Stop Playback:** `,,stop` or `/stop` or react with ⏹️ on the Now Playing message.
*   **Show Current Info:** `,,now` or `/now`
*   **Leave Voice Channel:** `,,leave` or `,,dc`

## Configuration

*   **Bot Token:** Set in the `.env` file (see Setup).
*   **Predefined Radio Streams:** Modify the `PREDEFINED_STREAMS` dictionary in `config.py` to add, remove, or change the default radio stations, their URLs, and descriptions.
*   **Other Settings:** Adjust constants like `COMMAND_PREFIX`, `METADATA_FETCH_INTERVAL`, etc., directly in `config.py`. Remember to rebuild the Docker image (`docker-compose build`) if you change `config.py`.

## Running & Managing the Bot

*   **Start:** `docker-compose up -d`
*   **Stop:** `docker-compose down` (Stops and removes the container)
*   **Restart:** `docker-compose restart`
*   **View Logs:** `docker-compose logs -f` (Follow logs in real-time. Press `Ctrl+C` to stop following).
*   **Update Bot Code:**
    1.  Pull the latest changes if using Git (`git pull`).
    2.  Rebuild the image: `docker-compose build`
    3.  Restart the container: `docker-compose up -d` (Compose automatically replaces the old container with the one using the new image).

## Troubleshooting

*   **`Error loading state: Expecting value...`:** This is normal on the first run or if the `state.json` file is empty/invalid. The bot will safely start with an empty state.
*   **Bot Not Coming Online / Login Failed / Privileged Intents Required:** Double-check your `DISCORD_TOKEN` in `.env`. Ensure you enabled **Server Members** and **Message Content** intents in the Discord Developer Portal. Check logs (`docker-compose logs -f`).
*   **Slash Commands Not Appearing:** Allow up to an hour for global commands to register after the bot first starts/syncs. If they persist in not showing, check logs for sync errors during startup.
*   **No Audio / Playback Issues:** Verify the radio stream URLs are correct and working. Check bot logs for FFmpeg errors. Ensure the bot has `Connect` and `Speak` permissions in the voice channel.
*   **Permission Errors (e.g., Cannot Add/Remove Reactions):** Ensure the bot has the required permissions listed in the "Invite Your Bot" section (specifically `Add Reactions` and `Manage Messages`).

## Contributing

Contributions are welcome! Due to the modular Cog structure, adding features is straightforward:

1.  **Create a New Cog:** If adding a distinct set of features (e.g., Search, Favorites), create a new `.py` file in the `cogs/` directory.
2.  **Develop:** Write your Cog class inheriting from `commands.Cog`. Use `self.bot` to access shared resources like the `http_session` or `guild_states`.
3.  **Add Setup Function:** Include the standard `async def setup(bot): await bot.add_cog(YourCogName(bot))` at the end of your Cog file.
4.  **Submit a Pull Request:** Explain your changes and features.

Please report any bugs or suggest features using the GitHub Issues tab.