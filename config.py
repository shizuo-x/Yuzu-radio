# ./discord-radio-bot-modular/config.py

import os
from dotenv import load_dotenv
import discord
import logging

load_dotenv()

# --- Data Path ---
# All persistent data files (databases, json state) will be stored here.
# This is crucial for data to survive Docker container restarts.
DATA_DIR = "data"
# This line ensures the 'data' directory exists when the bot starts.
os.makedirs(DATA_DIR, exist_ok=True)

# --- Bot Configuration ---
BOT_TOKEN = os.getenv('DISCORD_TOKEN')
COMMAND_PREFIX = ",,"
RECONNECT_DELAY = 5
MAX_RECONNECT_ATTEMPTS = 3
STOP_REACTION = '⏹️'
METADATA_FETCH_INTERVAL = 30
# --- Updated file paths to use the data directory ---
STATE_FILE = os.path.join(DATA_DIR, 'state.json')
PREFIXES_FILE = os.path.join(DATA_DIR, 'prefixes.json')

# --- Translation Configuration ---
LIBRETRANSLATE_API_URL = os.getenv('LIBRETRANSLATE_API_URL', 'https://translate.argosopentech.com')
TRANSLATIONS_DB_FILE = os.path.join(DATA_DIR, 'translations.db')

# --- Confessions Configuration ---
CONFESSIONS_DB_FILE = os.path.join(DATA_DIR, 'confessions.db')

# --- Reminders Configuration ---
REMINDERS_DB_FILE = os.path.join(DATA_DIR, 'reminders.db')
REMINDER_CHECK_INTERVAL = 20.0

# --- AI Assistant Configuration ---
AI_PROVIDER = os.getenv('AI_PROVIDER', 'gemini').lower() # Default to gemini if not set
# Gemini settings
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'models/gemini-1.5-flash-latest')
# DeepSeek settings
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

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
INTENTS = discord.Intents.all()


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