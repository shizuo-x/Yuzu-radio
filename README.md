# Yuzu Radio & Utility Bot

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.x-blue.svg)](https://github.com/Rapptz/discord.py)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Yuzu** is a flexible and feature-rich Discord bot built with a powerful modular architecture. Originally a 24/7 radio bot, it has expanded to include advanced server utilities like high-quality GIF-to-emoji conversion. It's designed for easy self-hosting and extensibility using Docker.

## ✨ Core Features

### 📻 24/7 Radio Playback
*   **Persistent & Resilient:** Remembers what stream was playing and automatically resumes after restarts or disconnects.
*   **Live Metadata:** Displays the currently playing song title from streams that support it.
*   **High Quality Audio:** Uses FFmpeg for reliable and high-quality audio streaming.
*   **Configurable Stations:** Easily add your own favorite radio stations to a central config file.
*   **Paginated List:** Browse the station list with descriptions using a clean, paginated embed.

### 🖼️ High-Quality Emoji Converter
*   **GIF to Emoji:** Convert any GIF from a URL into a high-quality server emoji with one command.
*   **Quality-First Cascade:** The bot first attempts to upload the original GIF to let Discord's powerful servers handle processing. Only if that fails does it attempt its own smart resizing, prioritizing visual quality and transparency.
*   **Animated & Static:** Works perfectly with both animated and static images.

### ⚙️ General Features
*   **Hybrid Commands:** Use modern Slash Commands (`/`) or a classic Prefix (default: `,,`).
*   **Dynamic Prefix:** Server admins can change the bot's prefix at any time.
*   **Modular Cogs:** All features are neatly organized into plugins, making the code easy to maintain and extend.
*   **Dockerized:** Simple, one-command setup for anyone with Docker.

---

## 🚀 Getting Started

### Prerequisites
*   **Docker & Docker Compose:** Required to run the bot. Install from the [official Docker website](https://www.docker.com/get-started).
*   **Git:** Required to clone the repository.
### 🛠️ Setup Instructions

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/shizuo-x/Yuzu-radio.git
    cd Yuzu-radio
    ```

2.  **Create the `.env` File:**
    This file stores your secret credentials. Copy the provided template:
    ```bash
    cp .env.example .env
    ```

3.  **Configure Your Bot on Discord:**
    *   Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a **New Application**.
    *   Go to the **"Bot"** tab and click **"Add Bot"**.
    *   **Copy the Token:** Click **"Reset Token"** to reveal and copy your bot's token.
    *   **Enable Intents:** Scroll down to **"Privileged Gateway Intents"** and enable **ALL THREE**:
        *   `PRESENCE INTENT`
        *   `SERVER MEMBERS INTENT`
        *   `MESSAGE CONTENT INTENT`
    *   Click **"Save Changes"**.

4.  **Edit the `.env` File:**
    *   Open the `.env` file with a text editor.
    *   Paste your bot token: `DISCORD_TOKEN=YOUR_BOT_TOKEN_GOES_HERE`
    5.  **Build and Run the Bot:**
    *   Make sure Docker is running on your machine.
    *   From your terminal, inside the project folder, run:
    ```bash
    docker-compose up -d
    ```
    This single command builds the Docker image and starts the bot in the background.

6.  **Invite The Bot to Your Server:**
    *   When the bot starts, it will print a pre-configured invite link in the logs. Run `docker-compose logs` to find it. This is the **recommended** way to invite the bot as it includes all necessary permissions.
    *   Alternatively, create one manually in the Developer Portal under **OAuth2 -> URL Generator**. Select scopes `bot` and `applications.commands` and grant the permissions listed in the bot's startup logs.

---

## 🤖 Command Usage

The bot supports both slash commands and prefix commands (default prefix is `,,`). Use `/help` or `,,help` for a full, paginated list.

### Radio Commands (`/play`, `/stop`, etc.)
*   `/play stream:<Name or URL>`: Plays a radio station.
*   `/stop`: Stops playback.
*   `/list`: Shows the paginated list of predefined radio stations.
*   `/now`: Re-sends the "Now Playing" embed.

### Utility Commands
*   `/convert link:<URL> name:<Name>`: Converts a GIF into a server emoji.
*   `/help`: Displays the paginated help menu.
*   `/ping`: Checks the bot's responsiveness.
*   `/setprefix new_prefix:<Prefix>`: (Admin Only) Changes the bot's command prefix for the server.

---

## 🔧 Management & Customization

### Managing the Container
*   **Start:** `docker-compose up -d`
*   **Stop:** `docker-compose down`
*   **Restart:** `docker-compose restart`
*   **View Logs:** `docker-compose logs -f` (Press `Ctrl+C` to exit)
*   **Update Bot Code:** After pulling new changes with `git pull`, simply run `docker-compose up -d --build`. Docker will rebuild the image and restart the container with the new code.

### Customization
*   **Radio Stations:** Edit the `PREDEFINED_STREAMS` dictionary in `config.py`. Add your station names, URLs, and descriptions.
*   **Default Prefix:** Change the `COMMAND_PREFIX` variable in `config.py`.
*   **Remember to rebuild your Docker image (`docker-compose build`) after changing any files.**

## 🤝 Contributing

This project is built to be modular. If you want to add a new feature:
1.  Create a new Python file in the `cogs/` directory.
2.  Build your feature as a `commands.Cog` class.
3.  Add the `async def setup(bot):` function at the bottom of the file.
4.  The bot will automatically load your new cog on the next restart.
5.  Submit a Pull Request with your changes!