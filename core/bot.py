# ./discord-radio-bot-modular/core/bot.py

import discord
from discord.ext import commands
import logging
import aiohttp
import json
import os
import asyncio
from typing import Dict, Any

# Import configuration and constants
import config

logger = logging.getLogger('discord_bot.core')

class RadioBot(commands.Bot):
    """Custom Bot class for the Radio Bot."""

    def __init__(self):
        super().__init__(
            command_prefix=config.COMMAND_PREFIX,
            intents=config.INTENTS,
            help_command=None # We'll use a custom help command in a Cog
        )
        # Shared HTTP session for the bot
        self.http_session: aiohttp.ClientSession | None = None
        # In-memory cache for guild playback states
        self.guild_states: Dict[int, Dict[str, Any]] = {}
        # Flags to ensure setup runs only once
        self.synced_commands = False
        self.loaded_state = False

    async def setup_hook(self):
        """
        Asynchronous setup code that runs before the bot logs in.
        Used for loading extensions (cogs) and initializing resources.
        """
        logger.info("Running setup_hook...")

        # Initialize shared aiohttp session
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession()
            logger.info("Created global aiohttp ClientSession.")

        # Load Cogs from the 'cogs' directory
        cogs_dir = "cogs"
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                cog_name = f"{cogs_dir}.{filename[:-3]}"
                try:
                    await self.load_extension(cog_name)
                    logger.info(f"Successfully loaded Cog: {cog_name}")
                except commands.ExtensionNotFound:
                    logger.error(f"Cog not found: {cog_name}")
                except commands.ExtensionAlreadyLoaded:
                    logger.warning(f"Cog already loaded: {cog_name}")
                except commands.NoEntryPointError:
                    logger.error(f"Cog '{cog_name}' has no setup() function.")
                except commands.ExtensionFailed as e:
                    logger.exception(f"Failed to load Cog '{cog_name}': {e.original}")
                except Exception as e:
                     logger.exception(f"An unexpected error occurred loading Cog '{cog_name}': {e}")

        logger.info("setup_hook finished.")


    async def on_ready(self):
        """Called when the bot is ready after logging in (and reconnects)."""
        logger.info(f"Logged in as {self.user.name} ({self.user.id})")
        logger.info(f"discord.py version: {discord.__version__}")
        logger.info("------")

        # Ensure command tree synced only once
        if not self.synced_commands:
            try:
                # Consider syncing per guild for faster testing:
                # test_guild = discord.Object(id=YOUR_TEST_GUILD_ID)
                # synced = await self.tree.sync(guild=test_guild)
                synced = await self.tree.sync() # Global sync
                logger.info(f"Synced {len(synced)} application (slash) command(s).")
                self.synced_commands = True
            except Exception as e:
                logger.exception(f"Failed to sync slash commands: {e}")
                # Keep synced_commands False to potentially retry on next ready

        # Load persistent state only once
        if not self.loaded_state:
            self.load_state()
            self.loaded_state = True
            logger.info("Attempting auto-resume for saved states...")
            await self.attempt_auto_resume()

        # --- Post-Reconnect Check ---
        # Check guilds where bot *thought* it was playing before disconnect
        # This is important if the bot disconnects/reconnects without restarting the process
        if self.loaded_state and self.synced_commands: # Only run check after initial setup
            playback_cog = self.get_cog('Playback') # Get the playback cog instance
            if playback_cog:
                logger.info("Performing post-reconnect voice state checks...")
                await playback_cog.check_voice_state_after_reconnect()


    async def on_close(self):
        """Called when the bot is shutting down."""
        logger.info("Bot is closing. Saving final state.")
        self.save_state()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            logger.info("Closed aiohttp session.")
        logger.info("Shutdown cleanup complete.")


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
                logger.debug(f"[{guild_id}] Preparing state for saving.")
            else:
                logger.debug(f"[{guild_id}] Skipping save (should_play=False or missing info).")

        try:
            with open(config.STATE_FILE, 'w') as f:
                json.dump(persistent_state, f, indent=4)
            logger.info(f"Saved state for {len(persistent_state)} guild(s) to {config.STATE_FILE}")
        except Exception as e:
            logger.exception(f"Error saving state to {config.STATE_FILE}: {e}")

    def load_state(self):
        """Loads persistent state from state.json."""
        try:
            if os.path.exists(config.STATE_FILE):
                with open(config.STATE_FILE, 'r') as f:
                    loaded_data = json.load(f)
                temp_states = {}
                for guild_id_str, saved_state in loaded_data.items():
                    try:
                        guild_id = int(guild_id_str)
                        temp_states[guild_id] = {
                            'vc': None, 'url': saved_state.get('stream_url'),
                            'stream_name': saved_state.get('stream_name'),
                            'should_play': True, 'retries': 0,
                            'requester_id': saved_state.get('requester_id'),
                            'text_channel_id': saved_state.get('text_channel_id'),
                            'voice_channel_id': saved_state.get('voice_channel_id'),
                            'now_playing_message_id': None, 'current_metadata': None,
                            'is_resuming': True # Flag auto-resume
                        }
                        logger.info(f"[{guild_id}] Loaded saved state for VC {saved_state.get('voice_channel_id')}")
                    except (ValueError, KeyError, TypeError) as e:
                        logger.error(f"Error processing saved state for '{guild_id_str}': {e} - Skipping.")
                self.guild_states = temp_states
                logger.info(f"Loaded state for {len(self.guild_states)} guild(s).")
            else:
                logger.info(f"{config.STATE_FILE} not found, starting empty.")
                self.guild_states = {}
        except Exception as e:
            logger.exception(f"Error loading state: {e}. Starting empty.")
            self.guild_states = {}


    async def attempt_auto_resume(self):
        """Attempts to resume playback based on loaded state."""
        playback_cog = self.get_cog('Playback')
        if not playback_cog:
            logger.error("Cannot auto-resume: Playback Cog not loaded.")
            return

        resumed_count = 0
        for guild_id, state in list(self.guild_states.items()): # Iterate copy
            if state.get('is_resuming'):
                logger.info(f"[{guild_id}] Attempting auto-resume.")
                # Use create_task to avoid blocking on_ready
                # Pass state components to the cog's resume function
                asyncio.create_task(playback_cog.resume_playback(
                    guild_id=guild_id,
                    voice_channel_id=state.get('voice_channel_id'),
                    text_channel_id=state.get('text_channel_id'),
                    stream_url=state.get('url'),
                    stream_name=state.get('stream_name'),
                    requester_id=state.get('requester_id')
                ))
                resumed_count += 1
            # Ensure 'is_resuming' flag is cleared even if task creation fails somehow
            # The task itself should set it to False on successful start
            # state['is_resuming'] = False # Maybe clear later inside the resume task

        if resumed_count > 0:
             logger.info(f"Initiated auto-resume tasks for {resumed_count} guild(s).")
        else:
             logger.info("No states found needing auto-resume.")