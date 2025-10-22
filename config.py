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

# --- Translation Configuration ---
LIBRETRANSLATE_API_URL = os.getenv('LIBRETRANSLATE_API_URL', 'https://translate.argosopentech.com')
TRANSLATIONS_DB_FILE = 'translations.db'

# --- Confessions Configuration ---
CONFESSIONS_DB_FILE = 'confessions.db'

# --- Predefined Radio Streams ---
PREDEFINED_STREAMS = {
    "name1": {
        "url": "link to station",
        "desc": "short description"
    },
}

# --- Logging ---
LOG_LEVEL = logging.INFO

# --- Intents ---
# --- FIX: Use discord.Intents.all() to enable all privileged intents ---
# This ensures that if they are enabled in the Dev Portal, the bot will use them.
INTENTS = discord.Intents.all()
# If you want to be more specific instead of .all():
# INTENTS = discord.Intents.default()
# INTENTS.message_content = True # Required for prefix commands
# INTENTS.voice_states = True
# INTENTS.guilds = True
# INTENTS.reactions = True
# INTENTS.members = True # Explicitly enable the members intent
# INTENTS.presences = True # Often useful with the members intent


# --- Permissions ---
PERMISSIONS = discord.Permissions()
PERMISSIONS.read_messages = True
PERMISSIONS.send_messages = True
PERMISSIONS.embed_links = True
PERMISSIONS.read_message_history = True
PERMISSIONS.manage_messages = True
PERMISSIONS.add_reactions = True
PERMISSIONS.use_external_emojis = True
PERMISSIONS.connect = True
PERMISSIONS.speak = True
PERMISSIONS.manage_expressions = True
PERMISSIONS.use_application_commands = True

# --- Other Constants ---
DEFAULT_EMBED_COLOR = discord.Color.blue()