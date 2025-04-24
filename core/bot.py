# ./discord-radio-bot-modular/core/bot.py

import discord
from discord.ext import commands
import logging
import aiohttp
import json
import os
import asyncio
from typing import Dict, Any, List, Union

# Import configuration and constants
import config

logger = logging.getLogger('discord_bot.core')

# --- Prefix Handling ---
# Default prefixes that will always work
DEFAULT_PREFIXES = [config.COMMAND_PREFIX]

# This function will determine the prefix(es) for a given message
async def get_prefix(bot_instance: 'RadioBot', message: discord.Message) -> Union[List[str], str]:
    """Dynamically determines the command prefix based on the guild."""
    # Allow mentions as prefixes always
    base_prefixes = commands.when_mentioned

    if not message.guild:
        # Use default prefix in DMs
        # Return list including mention prefix
        return base_prefixes(bot_instance, message) + DEFAULT_PREFIXES

    # Check stored prefixes for the guild
    guild_id_str = str(message.guild.id) # Use string key for JSON compatibility
    custom_prefix = bot_instance.guild_prefixes.get(guild_id_str)

    if custom_prefix:
        # If custom prefix exists, allow it *and* mentions
        return base_prefixes(bot_instance, message) + [custom_prefix]
    else:
        # Otherwise, use default prefix *and* mentions
        return base_prefixes(bot_instance, message) + DEFAULT_PREFIXES

# --- Custom Bot Class ---
class RadioBot(commands.Bot):
    """Custom Bot class for the Radio Bot with dynamic prefixes."""

    def __init__(self):
        super().__init__(
            # --- Use the get_prefix function ---
            command_prefix=get_prefix,
            intents=config.INTENTS,
            help_command=None
        )
        self.http_session: aiohttp.ClientSession | None = None
        self.guild_states: Dict[int, Dict[str, Any]] = {}
        # --- Store custom prefixes here ---
        self.guild_prefixes: Dict[str, str] = {} # {guild_id_str: prefix}
        self.synced_commands = False
        self.loaded_state = False
        self.loaded_prefixes = False # Flag for prefix loading

        self.load_prefixes() # Load prefixes during initialization

    async def setup_hook(self):
        """Asynchronous setup code before the bot logs in."""
        logger.info("Running setup_hook...")
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession()
            logger.info("Created global aiohttp ClientSession.")

        # --- Load Cogs ---
        cogs_dir = "cogs"
        loaded_cogs = []
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                cog_name = f"{cogs_dir}.{filename[:-3]}"
                try:
                    await self.load_extension(cog_name)
                    logger.info(f"Successfully loaded Cog: {cog_name}")
                    loaded_cogs.append(cog_name)
                except Exception as e:
                     logger.exception(f"Failed to load Cog '{cog_name}': {e}")
        logger.info(f"Loaded {len(loaded_cogs)} cogs: {', '.join(loaded_cogs)}")
        logger.info("setup_hook finished.")


    async def on_ready(self):
        """Called when the bot is ready after logging in (and reconnects)."""
        logger.info(f"Logged in as {self.user.name} ({self.user.id})")
        logger.info(f"discord.py version: {discord.__version__}")
        logger.info(f"Default Prefix: {config.COMMAND_PREFIX}") # Log default

        # --- Generate and Log Invite Link ---
        if self.user:
            invite_url = discord.utils.oauth_url(client_id=self.user.id, permissions=config.PERMISSIONS, scopes=('bot', 'applications.commands'))
            logger.info("----------------------------------------------------")
            logger.info(f"Invite Link (Default Prefix: {config.COMMAND_PREFIX}):")
            logger.info(invite_url)
            logger.info("----------------------------------------------------")
        else: logger.warning("Could not generate invite link: Bot user ID not available.")

        logger.info("------")

        # Ensure command tree synced only once per run
        if not self.synced_commands:
            try:
                synced = await self.tree.sync()
                logger.info(f"Synced {len(synced)} application (slash) command(s).")
                self.synced_commands = True
            except Exception as e: logger.exception(f"Failed to sync slash commands: {e}")

        # Load persistent state only once per run
        if not self.loaded_state:
            self.load_state()
            self.loaded_state = True
            logger.info("Attempting auto-resume for saved states...")
            await self.attempt_auto_resume()

        # Post-Reconnect Check
        if self.loaded_state and self.synced_commands:
            playback_cog = self.get_cog('Playback')
            if playback_cog:
                logger.info("Performing post-reconnect voice state checks...")
                await playback_cog.check_voice_state_after_reconnect()


    async def on_close(self):
        """Called when the bot is shutting down."""
        logger.info("Bot is closing. Saving final states.")
        self.save_state()
        self.save_prefixes() # Save prefixes on close too
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            logger.info("Closed aiohttp session.")
        logger.info("Shutdown cleanup complete.")

    # --- Prefix Persistence Methods ---
    def load_prefixes(self):
        """Loads custom prefixes from prefixes.json."""
        try:
            if os.path.exists(config.PREFIXES_FILE):
                with open(config.PREFIXES_FILE, 'r') as f:
                    # Ensure loaded data is treated as dict[str, str]
                    loaded_data: Dict[str, str] = json.load(f)
                    # Basic validation might be good here (ensure keys are digits, values are strings)
                    valid_prefixes = {k: v for k, v in loaded_data.items() if isinstance(k, str) and k.isdigit() and isinstance(v, str) and v}
                    self.guild_prefixes = valid_prefixes
                logger.info(f"Loaded {len(self.guild_prefixes)} custom prefixes from {config.PREFIXES_FILE}.")
            else:
                logger.info(f"{config.PREFIXES_FILE} not found, using default prefixes.")
                self.guild_prefixes = {}
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Error loading prefixes from {config.PREFIXES_FILE}: {e}. Using default prefixes.")
            self.guild_prefixes = {}
        except Exception as e:
             logger.exception(f"Unexpected error loading prefixes: {e}. Using default prefixes.")
             self.guild_prefixes = {}
        self.loaded_prefixes = True

    def save_prefixes(self):
        """Saves the current custom prefixes to prefixes.json."""
        try:
            with open(config.PREFIXES_FILE, 'w') as f:
                json.dump(self.guild_prefixes, f, indent=4)
            logger.info(f"Saved {len(self.guild_prefixes)} custom prefixes to {config.PREFIXES_FILE}.")
        except IOError as e:
            logger.error(f"Error saving prefixes to {config.PREFIXES_FILE}: {e}")
        except Exception as e:
             logger.exception(f"Unexpected error saving prefixes: {e}")

    # --- State Persistence Methods (Unchanged) ---
    def save_state(self):
        """Saves the relevant parts of guild_states to state.json."""
        persistent_state = {}
        for guild_id, state in self.guild_states.items():
            if state.get('should_play') and state.get('voice_channel_id') and state.get('url'):
                persistent_state[str(guild_id)] = {
                    'voice_channel_id': state['voice_channel_id'],
                    'text_channel_id': state.get('text_channel_id'),
                    'stream_url': state['url'],
                    'stream_name': state.get('stream_name', state['url']),
                    'requester_id': state.get('requester_id'),
                }
        try:
            with open(config.STATE_FILE, 'w') as f: json.dump(persistent_state, f, indent=4)
            logger.info(f"Saved playback state for {len(persistent_state)} guild(s).")
        except Exception as e: logger.exception(f"Error saving playback state: {e}")

    def load_state(self):
        """Loads persistent playback state from state.json."""
        try:
            if os.path.exists(config.STATE_FILE):
                with open(config.STATE_FILE, 'r') as f: loaded_data = json.load(f)
                temp_states = {}
                for guild_id_str, saved_state in loaded_data.items():
                    try:
                        guild_id = int(guild_id_str)
                        temp_states[guild_id] = {
                            'vc': None, 'url': saved_state.get('stream_url'), 'stream_name': saved_state.get('stream_name'),
                            'should_play': True, 'retries': 0, 'requester_id': saved_state.get('requester_id'),
                            'text_channel_id': saved_state.get('text_channel_id'), 'voice_channel_id': saved_state.get('voice_channel_id'),
                            'now_playing_message_id': None, 'current_metadata': None, 'is_resuming': True
                        }
                    except Exception as e: logger.error(f"Error processing saved state for '{guild_id_str}': {e} - Skipping.")
                self.guild_states = temp_states
                logger.info(f"Loaded playback state for {len(self.guild_states)} guild(s).")
            else: logger.info(f"{config.STATE_FILE} not found, starting empty playback state."); self.guild_states = {}
        except Exception as e: logger.exception(f"Error loading playback state: {e}. Starting empty."); self.guild_states = {}

    async def attempt_auto_resume(self):
        """Attempts to resume playback based on loaded state."""
        playback_cog = self.get_cog('Playback')
        if not playback_cog: logger.error("Cannot auto-resume: Playback Cog not loaded."); return
        resumed_count = 0
        for guild_id, state in list(self.guild_states.items()):
            if state.get('is_resuming'):
                logger.info(f"[{guild_id}] Attempting auto-resume.")
                asyncio.create_task(playback_cog.resume_playback(
                    guild_id=guild_id, voice_channel_id=state.get('voice_channel_id'),
                    text_channel_id=state.get('text_channel_id'), stream_url=state.get('url'),
                    stream_name=state.get('stream_name'), requester_id=state.get('requester_id') ))
                resumed_count += 1
        logger.info(f"Initiated auto-resume tasks for {resumed_count} guild(s).")