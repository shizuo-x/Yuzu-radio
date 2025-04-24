# ./discord-radio-bot-modular/cogs/playback.py

import discord
from discord.ext import commands, tasks
import asyncio
import functools
import logging
import re
import datetime
from typing import Dict, Any, Optional
import aiohttp

# Import configuration and the main bot class type hint
import config
from core.bot import RadioBot # Used for type hinting self.bot

logger = logging.getLogger('discord_bot.cogs.playback')

class Playback(commands.Cog):
    """Handles voice connection, radio playback, related commands and tasks."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        self.metadata_loop.start()
        logger.info("Playback Cog initialized.")

    def cog_unload(self):
        self.metadata_loop.cancel()
        logger.info("Playback Cog unloaded, metadata loop cancelled.")

    # --- Helper Methods ---
    # (cleanup_now_playing_message, send_or_edit_now_playing_embed, _play_internal,
    #  after_playback_handler, reconnect_after_delay, ensure_voice_and_play,
    #  resume_playback remain unchanged from the previous version)
    async def cleanup_now_playing_message(self, guild_id: int):
        """Safely deletes the existing 'Now Playing' message."""
        state = self.bot.guild_states.get(guild_id)
        if not state: return

        message_id = state.get('now_playing_message_id')
        channel_id = state.get('text_channel_id')
        state['now_playing_message_id'] = None # Clear ID immediately

        if message_id and channel_id:
            try:
                guild = self.bot.get_guild(guild_id)
                if not guild: return
                channel = guild.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.TextChannel): return
                message = await channel.fetch_message(message_id)
                await message.delete()
                logger.info(f"[{guild_id}] Deleted Now Playing message {message_id}.")
            except discord.NotFound: logger.debug(f"[{guild_id}] Now Playing message {message_id} not found.")
            except discord.Forbidden: logger.warning(f"[{guild_id}] No permission to delete message {message_id}.")
            except Exception as e: logger.error(f"[{guild_id}] Error deleting message {message_id}: {e}", exc_info=True)

    async def send_or_edit_now_playing_embed(self, guild_id: int, force_new: bool = False):
        """Creates/sends or edits the 'Now Playing' embed."""
        state = self.bot.guild_states.get(guild_id)
        if not state or not state.get('should_play'):
            logger.debug(f"[{guild_id}] Send/Edit embed skipped: should_play=False.")
            await self.cleanup_now_playing_message(guild_id)
            return

        guild = self.bot.get_guild(guild_id)
        channel_id = state.get('text_channel_id')
        if not guild or not channel_id: logger.error(f"[{guild_id}] Embed fail: Guild/Channel ID missing."); return
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel): logger.warning(f"[{guild_id}] Embed fail: Channel {channel_id} invalid."); return

        embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        stream_name = state.get('stream_name', 'Unknown Stream')
        requester_id = state.get('requester_id')
        requester = await self.bot.fetch_user(requester_id) if requester_id else None
        requester_mention = requester.mention if requester else "Unknown"
        metadata = state.get('current_metadata')

        embed.add_field(name="Stream", value=f"`{stream_name}`", inline=False)
        if metadata: embed.add_field(name="Current Track", value=f"```{metadata}```", inline=False)
        embed.add_field(name="Requested By", value=requester_mention, inline=False)
        embed.add_field(name="Playback Position", value="🔵 **LIVE**", inline=False)
        try: embed.set_footer(text=f"{self.bot.user.name} Radio", icon_url=self.bot.user.display_avatar.url)
        except: embed.set_footer(text=f"{self.bot.user.name} Radio")

        message_id = state.get('now_playing_message_id')
        message = None

        if not force_new and message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed)
                logger.debug(f"[{guild_id}] Edited embed {message_id}.")
                return # Edit successful
            except discord.NotFound: logger.info(f"[{guild_id}] Message {message_id} not found, sending new."); message_id = None; state['now_playing_message_id'] = None
            except discord.Forbidden: logger.warning(f"[{guild_id}] No permission edit message {message_id}."); # Try sending new below
            except Exception as e: logger.error(f"[{guild_id}] Error editing message {message_id}: {e}"); # Try sending new below

        if not message_id or force_new:
            await self.cleanup_now_playing_message(guild_id) # Clean old one first
            try:
                new_message = await channel.send(embed=embed)
                state['now_playing_message_id'] = new_message.id
                logger.info(f"[{guild_id}] Sent new embed {new_message.id}")
                try: await new_message.add_reaction(config.STOP_REACTION)
                except Exception as react_error: logger.warning(f"[{guild_id}] Failed adding reaction: {react_error}")
            except discord.Forbidden: logger.warning(f"[{guild_id}] No permission send embed/add reaction in {channel.id}."); state['now_playing_message_id'] = None
            except Exception as e: logger.error(f"[{guild_id}] Error sending new embed: {e}", exc_info=True); state['now_playing_message_id'] = None


    async def _play_internal(self, guild_id: int, voice_client: discord.VoiceClient):
        """Internal logic to start FFmpeg playback."""
        state = self.bot.guild_states.get(guild_id)
        if not state or not state.get('should_play') or not state.get('url'):
            logger.warning(f"[{guild_id}] _play_internal skipped: invalid state or URL.")
            if state: state['should_play'] = False; self.bot.save_state() # Ensure state is saved if stopping
            return

        stream_url = state['url']
        stream_name = state.get('stream_name', 'Unknown Stream')
        logger.info(f"[{guild_id}] Attempting FFmpeg playback for: {stream_name}")

        try:
            if voice_client.is_playing() or voice_client.is_paused(): voice_client.stop(); await asyncio.sleep(0.5)
            ffmpeg_options = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -analyzeduration 5000000 -probesize 5000000', 'options': '-vn -loglevel warning'}
            audio_source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_options)
            after_callback = functools.partial(self.after_playback_handler, guild_id) # Pass self for method call
            voice_client.play(audio_source, after=after_callback)

            logger.info(f"[{guild_id}] FFmpeg playback started for {stream_name}")
            state['retries'] = 0
            state['is_resuming'] = False # Clear resuming flag
            self.bot.save_state() # Save state after successful start

            await self.send_or_edit_now_playing_embed(guild_id, force_new=True)

        except Exception as e:
            logger.error(f"[{guild_id}] Error starting FFmpeg playback for '{stream_name}': {e}", exc_info=True)
            state['should_play'] = False
            self.bot.save_state()
            # Optionally notify text channel here

    def after_playback_handler(self, guild_id: int, error: Optional[Exception]):
        """Callback after playback ends or errors. Handles state and reconnection."""
        state = self.bot.guild_states.get(guild_id)
        if not state:
            logger.warning(f"[{guild_id}] after_playback_handler called but no state found.")
            return

        should_play = state.get('should_play', False) # Check intent *before* modifying state
        logger.info(f"[{guild_id}] Playback finished/stopped in 'after' handler. Error: {error}, should_play flag was: {should_play}")

        # Use run_coroutine_threadsafe to schedule async cleanup
        cleanup_future = asyncio.run_coroutine_threadsafe(self.cleanup_now_playing_message(guild_id), self.bot.loop)
        try:
            cleanup_future.result(timeout=5) # Optional: wait briefly for task submission
        except TimeoutError:
            logger.warning(f"[{guild_id}] Timeout waiting for cleanup task submission in 'after' handler.")
        except Exception as e:
             logger.error(f"[{guild_id}] Error submitting cleanup task in 'after' handler: {e}")


        if error:
            logger.error(f"[{guild_id}] Playback Error reported in 'after' handler: {error}")
            if should_play:
                state['retries'] = state.get('retries', 0) + 1
                if state['retries'] <= config.MAX_RECONNECT_ATTEMPTS:
                    logger.warning(f"[{guild_id}] Playback error while should_play=True. Attempting reconnect {state['retries']}/{config.MAX_RECONNECT_ATTEMPTS} in {config.RECONNECT_DELAY}s.")
                    # Use run_coroutine_threadsafe for reconnect scheduling
                    reconnect_future = asyncio.run_coroutine_threadsafe(self.reconnect_after_delay(guild_id), self.bot.loop)
                    try: reconnect_future.result(timeout=5)
                    except TimeoutError: logger.warning(f"[{guild_id}] Timeout waiting for reconnect task submission.")
                    except Exception as e: logger.error(f"[{guild_id}] Error submitting reconnect task: {e}")
                else:
                    logger.error(f"[{guild_id}] Max reconnect attempts ({config.MAX_RECONNECT_ATTEMPTS}) reached after error. Stopping playback permanently.")
                    state['should_play'] = False
                    self.bot.save_state() # Save the stopped state
            else:
                logger.info(f"[{guild_id}] Playback error occurred, but should_play=False (manual stop during error?). Not attempting reconnect.")
                state['should_play'] = False; state['retries'] = 0; self.bot.save_state()
        else:
            # Playback finished without error (manual stop, or potentially stream ending cleanly)
            logger.info(f"[{guild_id}] Playback ended without error in 'after' handler. Assuming manual stop or natural end.")
            state['should_play'] = False; state['retries'] = 0; self.bot.save_state()


    async def reconnect_after_delay(self, guild_id: int):
        """Waits and then attempts to reconnect and play."""
        await asyncio.sleep(config.RECONNECT_DELAY)
        state = self.bot.guild_states.get(guild_id)
        if not state or not state.get('should_play'):
            logger.info(f"[{guild_id}] Reconnect cancelled after delay (state changed).")
            return

        logger.info(f"[{guild_id}] Executing reconnect attempt {state.get('retries', '?')}")
        vc_id = state.get('voice_channel_id')
        txt_id = state.get('text_channel_id')
        url = state.get('url')
        name = state.get('stream_name')
        req_id = state.get('requester_id')

        if not all([vc_id, url, name]):
            logger.error(f"[{guild_id}] Reconnect failed: Missing state info. Stopping.")
            state['should_play'] = False; self.bot.save_state(); return

        await self.ensure_voice_and_play(guild_id, vc_id, txt_id, url, name, req_id, is_manual_play=False)


    async def ensure_voice_and_play(self, guild_id: int, voice_channel_id: Optional[int], text_channel_id: Optional[int], stream_url: str, stream_name: str, requester_id: Optional[int], is_manual_play: bool = False):
        """Connects/moves to VC and initiates playback. Main entry point for playing."""
        guild = self.bot.get_guild(guild_id)
        if not guild: logger.error(f"[{guild_id}] EnsureVP: Guild not found."); return "Error: Guild not found."
        if not voice_channel_id: logger.error(f"[{guild_id}] EnsureVP: Voice channel ID missing."); return "Error: Voice channel ID missing."

        voice_channel = guild.get_channel(voice_channel_id)
        if not voice_channel or not isinstance(voice_channel, discord.VoiceChannel):
            logger.error(f"[{guild_id}] EnsureVP: VC {voice_channel_id} not found/invalid."); return "Error: Voice channel not found."

        # --- Update state ---
        if guild_id not in self.bot.guild_states: self.bot.guild_states[guild_id] = {}
        self.bot.guild_states[guild_id].update({
            'url': stream_url, 'stream_name': stream_name, 'requester_id': requester_id,
            'text_channel_id': text_channel_id, 'voice_channel_id': voice_channel_id,
            'should_play': True, 'retries': self.bot.guild_states[guild_id].get('retries', 0),
            'vc': guild.voice_client, 'now_playing_message_id': self.bot.guild_states[guild_id].get('now_playing_message_id'),
            'current_metadata': None, 'is_resuming': not is_manual_play,
        })
        logger.info(f"[{guild_id}] Updating state: should_play=True, VC={voice_channel_id}, URL={stream_url}")

        voice_client = guild.voice_client
        try:
            if voice_client and voice_client.is_connected():
                if voice_client.channel.id != voice_channel_id:
                    logger.info(f"[{guild_id}] Moving to VC: {voice_channel.name}")
                    await voice_client.move_to(voice_channel)
                else: logger.info(f"[{guild_id}] Already in correct VC: {voice_channel.name}")
            else:
                logger.info(f"[{guild_id}] Connecting to VC: {voice_channel.name}")
                voice_client = await voice_channel.connect(timeout=60.0, reconnect=True)
                self.bot.guild_states[guild_id]['vc'] = voice_client # Store new VC

            if not voice_client or not voice_client.is_connected(): raise Exception("VC connection failed.")

            await self._play_internal(guild_id, voice_client) # Start playback
            return f"▶️ Now playing: `{stream_name}`"

        except Exception as e:
            logger.error(f"[{guild_id}] Error in ensure_voice_and_play for '{stream_name}': {e}", exc_info=True)
            self.bot.guild_states[guild_id]['should_play'] = False; self.bot.save_state()
            return f"An error occurred connecting or playing: {e}"


    async def resume_playback(self, guild_id: int, voice_channel_id: Optional[int], text_channel_id: Optional[int], stream_url: Optional[str], stream_name: Optional[str], requester_id: Optional[int]):
        """Called by on_ready to handle auto-resume for a specific guild."""
        if not all([voice_channel_id, stream_url, stream_name]):
            logger.warning(f"[{guild_id}] Cannot auto-resume: Missing state info.")
            if guild_id in self.bot.guild_states:
                self.bot.guild_states[guild_id]['should_play'] = False
                self.bot.guild_states[guild_id]['is_resuming'] = False
            self.bot.save_state() # Ensure non-playable state is saved
            return

        logger.info(f"[{guild_id}] Attempting resume: VC={voice_channel_id}, Stream={stream_name}")
        await self.ensure_voice_and_play(guild_id, voice_channel_id, text_channel_id, stream_url, stream_name, requester_id, is_manual_play=False)


    # --- Listeners ---
    # (on_voice_state_update, on_reaction_add remain unchanged)
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state changes, especially for the bot itself."""
        if member.id != self.bot.user.id: return # Only care about the bot's state

        guild_id = member.guild.id
        state = self.bot.guild_states.get(guild_id)

        if before.channel and not after.channel: # Bot disconnected
            logger.info(f"[{guild_id}] Bot disconnected from VC '{before.channel.name}'.")
            if state:
                state['vc'] = None
                if state.get('should_play'):
                    logger.warning(f"[{guild_id}] Bot disconnected unexpectedly while should_play=True! Attempting reconnect.")
                    state['retries'] = 0 # Reset retries
                    # Use create_task as this is already in async context
                    asyncio.create_task(self.reconnect_after_delay(guild_id))
                else:
                    logger.info(f"[{guild_id}] Bot disconnect expected. Resetting state.")
                    state['retries'] = 0; self.bot.save_state()
                    await self.cleanup_now_playing_message(guild_id)

        elif not before.channel and after.channel: # Bot connected
            logger.info(f"[{guild_id}] Bot connected to VC '{after.channel.name}'.")
            if state: state['vc'] = member.guild.voice_client

        elif before.channel != after.channel: # Bot moved
             logger.info(f"[{guild_id}] Bot moved from '{before.channel.name}' to '{after.channel.name}'.")
             if state: state['vc'] = member.guild.voice_client

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User | discord.Member):
        """Handle stop reaction."""
        if user.bot or not reaction.message.guild: return
        guild_id = reaction.message.guild.id
        state = self.bot.guild_states.get(guild_id)

        if state and str(reaction.emoji) == config.STOP_REACTION and reaction.message.id == state.get('now_playing_message_id'):
            logger.info(f"[{guild_id}] Stop reaction from {user.name}.")
            vc = reaction.message.guild.voice_client
            if vc and vc.is_connected():
                state['should_play'] = False # Set intent *before* stopping
                logger.info(f"[{guild_id}] Stopping playback via reaction.")
                if vc.is_playing() or vc.is_paused(): vc.stop() # Triggers after_handler
                else: self.bot.save_state(); await self.cleanup_now_playing_message(guild_id) # Cleanup if stopped but connected
                try: await reaction.remove(user)
                except: pass # Ignore permission errors removing reaction
                try: await reaction.message.channel.send(f"⏹️ Playback stopped by {user.mention}.", delete_after=10)
                except: pass
            else: # Bot not connected, but reaction exists
                logger.info(f"[{guild_id}] Stop reaction but bot not connected. Cleaning state/message.")
                state['should_play'] = False; self.bot.save_state(); await self.cleanup_now_playing_message(guild_id)

    # --- Tasks ---
    # (metadata_loop and its before_loop remain unchanged)
    @tasks.loop(seconds=config.METADATA_FETCH_INTERVAL)
    async def metadata_loop(self):
        """Fetches and updates ICY metadata for active streams."""
        if not self.bot.http_session or self.bot.http_session.closed:
             logger.warning("Metadata loop: HTTP session closed/missing."); return

        active_guild_ids = list(self.bot.guild_states.keys())
        for guild_id in active_guild_ids:
            state = self.bot.guild_states.get(guild_id)
            if state and state.get('should_play') and state.get('url') and state.get('vc') and state['vc'].is_playing():
                stream_url = state['url']
                logger.debug(f"[{guild_id}] Fetching metadata for: {stream_url}")
                metadata = None
                try:
                    headers = {'Icy-Metadata': '1'}
                    async with self.bot.http_session.get(stream_url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if 200 <= response.status < 300 and 'icy-metaint' in response.headers:
                            metaint = int(response.headers['icy-metaint'])
                            await response.content.readexactly(metaint) # Skip audio data
                            length_byte = await response.content.readexactly(1)
                            metadata_length = length_byte[0] * 16
                            if metadata_length > 0:
                                metadata_bytes = await response.content.readexactly(metadata_length)
                                metadata_text = metadata_bytes.decode('utf-8', errors='ignore').strip()
                                match = re.search(r"StreamTitle='([^;]*)';", metadata_text)
                                if match: metadata = match.group(1).strip()
                                else: logger.debug(f"[{guild_id}] No StreamTitle in metadata: {metadata_text}")
                        # else: logger.debug(f"[{guild_id}] No metaint header or bad status {response.status}")

                    current_meta = state.get('current_metadata')
                    if metadata != current_meta: # Check if metadata actually changed or appeared/disappeared
                        logger.info(f"[{guild_id}] Updating metadata: '{metadata}' (was: '{current_meta}')")
                        state['current_metadata'] = metadata
                        await self.send_or_edit_now_playing_embed(guild_id) # Edit the existing embed

                except (asyncio.TimeoutError, aiohttp.ClientError, asyncio.IncompleteReadError) as e: logger.debug(f"[{guild_id}] Network/Timeout error fetching metadata: {e}")
                except Exception as e: logger.exception(f"[{guild_id}] Unexpected error fetching metadata for {stream_url}: {e}")

    @metadata_loop.before_loop
    async def before_metadata_loop(self):
        await self.bot.wait_until_ready() # Wait for bot connection


    # --- Commands ---

    @commands.hybrid_command(name="play", aliases=['p', 'stream'], description="Plays a radio stream URL or predefined name.")
    @discord.app_commands.describe(stream="The URL or predefined name of the stream (see /list)")
    async def play(self, ctx: commands.Context, *, stream: str):
        """Plays a live radio stream from URL or predefined name."""
        if not ctx.author.voice:
            await ctx.send("You need to be in a voice channel.", ephemeral=True)
            return
        if not ctx.guild:
             await ctx.send("This command only works in a server.", ephemeral=True)
             return

        if ctx.interaction: await ctx.defer(ephemeral=False)

        result = await self._play_command_logic(
            ctx.guild.id,
            ctx.author,
            ctx.channel.id,
            ctx.author.voice.channel, # Already checked ctx.author.voice exists
            stream # Pass the raw input here
        )
        await ctx.send(result)

    # --- MODIFIED _play_command_logic ---
    async def _play_command_logic(self, guild_id, user, text_channel_id, voice_channel, stream_input):
        """Shared logic called by hybrid command handler."""
        if not voice_channel: return "You need to be in a voice channel."
        if not text_channel_id: logger.error(f"[{guild_id}] Play fail: No text channel."); return "Error: Missing text channel."

        stream_lookup_key = stream_input.strip('<>') # Clean input for dictionary lookup
        stream_url = stream_lookup_key # Assume it's a URL initially
        stream_name = stream_lookup_key # Display name defaults to input

        # --- MODIFY DICTIONARY LOOKUP ---
        matched_key = next((key for key in config.PREDEFINED_STREAMS if key.lower() == stream_lookup_key.lower()), None)

        if matched_key:
            stream_data = config.PREDEFINED_STREAMS[matched_key]
            stream_url = stream_data.get("url") # Get URL from inner dict
            stream_name = matched_key # Use the canonical key as the name
            if not stream_url:
                 logger.error(f"[{guild_id}] Predefined stream '{stream_name}' is missing 'url' in config.")
                 return f"Error: Configuration for stream '{stream_name}' is invalid."
            logger.info(f"[{guild_id}] Matched predefined stream: {stream_name}")
        elif not stream_url.startswith(('http://', 'https')):
            return f"Input is not valid URL or predefined name. See `{config.COMMAND_PREFIX}list`."
        # --- END MODIFY DICTIONARY LOOKUP ---

        result = await self.ensure_voice_and_play(guild_id, voice_channel.id, text_channel_id, stream_url, stream_name, user.id, is_manual_play=True)
        return result
    # --- END MODIFIED _play_command_logic ---

    # (stop, leave, now, list commands remain unchanged from previous version)
    @commands.hybrid_command(name="stop", description="Stops the current audio stream.")
    async def stop(self, ctx: commands.Context):
        """Stops the current audio stream."""
        if not ctx.guild: await ctx.send("Not usable outside servers.", ephemeral=True); return
        if ctx.interaction: await ctx.defer(ephemeral=False) # Defer public response

        result = await self._stop_command_logic(ctx.guild.id)
        await ctx.send(result)

    async def _stop_command_logic(self, guild_id):
        """Shared logic for stopping playback."""
        state = self.bot.guild_states.get(guild_id)
        guild = self.bot.get_guild(guild_id)
        vc = guild.voice_client if guild else None

        if state: state['should_play'] = False; logger.info(f"[{guild_id}] Stop command: should_play=False.")

        if vc and vc.is_connected():
            if vc.is_playing() or vc.is_paused(): vc.stop(); return "⏹️ Playback stopped." # after_handler saves state and cleans embed
            else: # Connected but not playing
                if state: self.bot.save_state(); await self.cleanup_now_playing_message(guild_id) # Explicit cleanup
                return "Nothing was playing, but I am connected."
        else: # Not connected
            if state: self.bot.save_state(); await self.cleanup_now_playing_message(guild_id) # Ensure cleanup if message exists
            return "Not currently connected to voice."


    @commands.hybrid_command(name="leave", aliases=['dc'], description="Disconnects the bot from the voice channel.")
    async def leave(self, ctx: commands.Context):
        """Disconnects the bot from the voice channel."""
        if not ctx.guild: await ctx.send("Not usable outside servers.", ephemeral=True); return
        if ctx.interaction: await ctx.defer(ephemeral=False)

        guild_id = ctx.guild.id
        state = self.bot.guild_states.get(guild_id)
        vc = ctx.guild.voice_client

        if state:
            state['should_play'] = False; logger.info(f"[{guild_id}] Leave command: should_play=False."); self.bot.save_state()
            await self.cleanup_now_playing_message(guild_id) # Explicit cleanup

        if vc and vc.is_connected():
            channel_name = vc.channel.name
            logger.info(f"[{guild_id}] Disconnecting from '{channel_name}' via command.")
            await vc.disconnect(force=False) # Triggers on_voice_state_update
            await ctx.send(f"Left `{channel_name}`.")
        else:
            await ctx.send("Not connected.")


    @commands.hybrid_command(name="now", aliases=['np'], description="Shows the currently playing stream.")
    async def now(self, ctx: commands.Context):
        """Shows the currently playing stream."""
        if not ctx.guild: await ctx.send("Not usable outside servers.", ephemeral=True); return
        # Now command should be fast, defer might not be needed unless embed creation is slow
        # if ctx.interaction: await ctx.defer(ephemeral=True) # Ephemeral for 'now' seems reasonable

        state = self.bot.guild_states.get(ctx.guild.id)
        vc = ctx.guild.voice_client
        # Check vc status as well
        if state and state.get('should_play') and vc and vc.is_playing():
            logger.info(f"[{ctx.guild.id}] Resending Now Playing embed via command.")
            # Force recreation of embed to show latest metadata immediately
            await self.send_or_edit_now_playing_embed(ctx.guild.id, force_new=True)
            # If interaction, send ephemeral confirmation, otherwise maybe delete prefix message
            if ctx.interaction:
                try:
                    # Need to check if response already sent if we didn't defer
                    if not ctx.interaction.response.is_done():
                        await ctx.interaction.response.send_message("Showing current stream info.", ephemeral=True)
                    else:
                        await ctx.interaction.followup.send("Showing current stream info.", ephemeral=True)
                except discord.errors.NotFound: pass # Interaction might expire quickly
            elif ctx.command: # Check if it was triggered by a prefix command
                 try: await ctx.message.delete()
                 except: pass
        else:
            # If interaction, respond accordingly
            if ctx.interaction:
                try:
                    if not ctx.interaction.response.is_done():
                         await ctx.interaction.response.send_message("Not currently playing anything.", ephemeral=True)
                    else:
                         await ctx.interaction.followup.send("Not currently playing anything.", ephemeral=True)
                except discord.errors.NotFound: pass
            else: # Prefix command response
                 await ctx.send("Not currently playing anything.")


    # --- Auto-Reconnect Check ---
    async def check_voice_state_after_reconnect(self):
        """Checks voice connections after a gateway reconnect."""
        for guild_id, state in list(self.bot.guild_states.items()):
            guild = self.bot.get_guild(guild_id)
            if guild and state.get('should_play') and not state.get('is_resuming'):
                vc = guild.voice_client
                if not vc or not vc.is_connected():
                    logger.warning(f"[{guild_id}] Post-reconnect check: VC missing/disconnected while should_play=True. Triggering reconnect.")
                    state['retries'] = 0
                    asyncio.create_task(self.reconnect_after_delay(guild_id))
                elif not vc.is_playing() and state.get('url'):
                     logger.warning(f"[{guild_id}] Post-reconnect check: Connected but not playing while should_play=True. Attempting to restart play.")
                     state['retries'] = 0
                     asyncio.create_task(self._play_internal(guild_id, vc))


# --- Setup Function ---
async def setup(bot: RadioBot):
    await bot.add_cog(Playback(bot))