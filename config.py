# ./discord-radio-bot-modular/config.py

import os
from dotenv import load_dotenv
import discord
import logging

load_dotenv()

# --- Bot Configuration ---
BOT_TOKEN = os.getenv('DISCORD_TOKEN')
COMMAND_PREFIX = ",,"
RECONNECT_DELAY = 5
MAX_RECONNECT_ATTEMPTS = 3
STOP_REACTION = '⏹️'
STATE_FILE = 'state.json'
METADATA_FETCH_INTERVAL = 30

# --- Predefined Radio Streams (NEW STRUCTURE) ---
# Format: "Display Name": {"url": "stream_url", "desc": "Short description"}
PREDEFINED_STREAMS = {
    "radio-one": {
        "url": "link to station",
        "desc": "station description"
    },
}

# --- Logging ---
LOG_LEVEL = logging.INFO

# --- Intents ---
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True
INTENTS.guilds = True
INTENTS.reactions = True

# --- Other Constants ---
DEFAULT_EMBED_COLOR = discord.Color.blue()