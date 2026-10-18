# ./discord-radio-bot-modular/cogs/playback.py

import discord
from discord.ext import commands, tasks
import asyncio
import concurrent.futures
import functools
import logging
import re
import datetime
import time
from typing import Dict, Any, Optional
import aiohttp

# Import configuration and the main bot class type hint
import config
from core.bot import RadioBot # Used for type hinting self.bot

logger = logging.getLogger('discord_bot.cogs.playback')

# --- Local constants (config.py is frozen for this phase; new values defined here) ---
STREAM_PROBE_TIMEOUT = 6        # Seconds for the pre-flight stream health check (I6)
STREAM_PROBE_READ_BYTES = 2048  # Bytes sampled from the stream during the probe (I6)
PLAY_VERIFY_GRACE = 2.75        # Grace after voice_client.play() before verifying is_playing() (I4)
WATCHDOG_INTERVAL = 10          # Seconds between silent-stream checks (I8)
WATCHDOG_SILENT_THRESHOLD = 15  # Seconds with no audio before watchdog recovery (I8)
VOICE_CONNECT_TIMEOUT = 30.0    # Voice channel connect timeout (I10)

class Playback(commands.Cog):
    """Handles voice connection, radio playback, related commands and tasks."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        self.metadata_loop.start()
        self.watchdog_loop.start()
        logger.info("Playback Cog initialized.")

    def cog_unload(self):
        self.metadata_loop.cancel()
        self.watchdog_loop.cancel()
        logger.info("Playback Cog unloaded, metadata and watchdog loops cancelled.")

    # --- Helper Methods ---
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
        requester_mention = f"<@{requester_id}>" if requester_id else "Unknown" # Mention built from the ID, no REST call (I10)
        metadata = state.get('current_metadata')

        embed.add_field(name="Stream", value=f"`{stream_name}`", inline=False)
        if metadata: embed.add_field(name="Current Track", value=f"```{metadata}```", inline=False)
        embed.add_field(name="Requested By", value=requester_mention, inline=False)
        embed.add_field(name="Playback Position", value="🔵 **LIVE**", inline=False)
        try: embed.set_footer(text=f"{self.bot.user.name} Radio", icon_url=self.bot.user.display_avatar.url)
        except Exception: logger.debug("Embed footer icon failed, using text-only footer."); embed.set_footer(text=f"{self.bot.user.name} Radio")

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


    async def _notify_playback_failure(self, guild_id: int, message: str):
        """Posts a short, non-technical failure notice to the guild's text channel (I5)."""
        state = self.bot.guild_states.get(guild_id)
        if not state: return
        channel_id = state.get('text_channel_id')
        guild = self.bot.get_guild(guild_id)
        if not guild or not channel_id: logger.warning(f"[{guild_id}] Failure notice not sent: guild/text channel missing."); return
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel): logger.warning(f"[{guild_id}] Failure notice not sent: channel {channel_id} invalid."); return
        try:
            await channel.send(message)
            logger.info(f"[{guild_id}] Posted playback failure notice.")
        except discord.HTTPException as e:
            logger.warning(f"[{guild_id}] Failed to post playback failure notice: {e}")

    async def _probe_stream(self, guild_id: int, stream_url: str) -> Optional[str]:
        """Pre-flight stream health check before joining voice (I6). Returns None if healthy, else a user-facing error."""
        if not self.bot.http_session or self.bot.http_session.closed:
            logger.warning(f"[{guild_id}] Stream probe skipped: HTTP session unavailable.")
            return None # Cannot probe; let normal playback error handling decide
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Icy-MetaData': '1',
        }
        try:
            async with self.bot.http_session.get(stream_url, headers=headers, timeout=aiohttp.ClientTimeout(total=STREAM_PROBE_TIMEOUT)) as response:
                status = response.status
                content_type = (response.headers.get('Content-Type') or '').lower()
                chunk = await response.content.read(STREAM_PROBE_READ_BYTES)
        except asyncio.TimeoutError:
            logger.info(f"[{guild_id}] Stream probe timed out for {stream_url}")
            return "That station looks offline right now (timed out), try another."
        except aiohttp.ClientError as e:
            logger.info(f"[{guild_id}] Stream probe connection error for {stream_url}: {e}")
            return "That station looks offline right now (connection failed), try another."

        if status in (401, 403):
            return f"That station rejected the connection (HTTP {status}) — it may be region-blocked. Try another."
        if not 200 <= status < 400:
            return f"That station looks offline right now (HTTP {status}), try another."
        content_type_ok = any(part in content_type for part in ('audio/', 'video/', 'application/octet-stream'))
        if content_type_ok or chunk:
            logger.debug(f"[{guild_id}] Stream probe healthy: HTTP {status}, type='{content_type}', bytes={len(chunk)}")
            return None
        logger.info(f"[{guild_id}] Stream probe unhealthy: HTTP {status}, type='{content_type}', no bytes received.")
        return "That station looks offline right now (no audio received), try another."

    def _cancel_reconnect_task(self, guild_id: int):
        """Cancels any live reconnect task for a guild. Safe when called from that task itself (I7)."""
        state = self.bot.guild_states.get(guild_id)
        if not state: return
        task = state.get('reconnect_task')
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            state['reconnect_task'] = None
            logger.info(f"[{guild_id}] Cancelled live reconnect task (single-flight).")

    async def _schedule_reconnect(self, guild_id: int):
        """Single-flight reconnect scheduler: cancels any live task first, then stores a strong reference to the new one (I7)."""
        state = self.bot.guild_states.get(guild_id)
        if not state or not state.get('should_play'):
            logger.info(f"[{guild_id}] Reconnect not scheduled: no state or should_play=False.")
            return
        self._cancel_reconnect_task(guild_id)
        state['reconnect_task'] = asyncio.create_task(self.reconnect_after_delay(guild_id))
        logger.info(f"[{guild_id}] Scheduled reconnect task (attempt {state.get('retries', '?')}).")

    async def _play_internal(self, guild_id: int, voice_client: discord.VoiceClient) -> bool:
        """Internal logic to start FFmpeg playback. Returns True only if playback started."""
        state = self.bot.guild_states.get(guild_id)
        if not state or not state.get('should_play') or not state.get('url'):
            logger.warning(f"[{guild_id}] _play_internal skipped: invalid state or URL.")
            if state: state['should_play'] = False; self.bot.save_state() # Ensure state is saved if stopping
            return False

        stream_url = state['url']
        stream_name = state.get('stream_name', 'Unknown Stream')
        logger.info(f"[{guild_id}] Attempting FFmpeg playback for: {stream_name}")

        try:
            if voice_client.is_playing() or voice_client.is_paused(): voice_client.stop(); await asyncio.sleep(0.5)
            ffmpeg_options = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -analyzeduration 5000000 -probesize 5000000', 'options': '-vn -loglevel warning'}
            audio_source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_options)
            play_epoch = state.get('play_epoch', 0)
            after_callback = functools.partial(self.after_playback_handler, guild_id, play_epoch) # Epoch makes superseded callbacks stale (I1)
            voice_client.play(audio_source, after=after_callback)

            logger.info(f"[{guild_id}] FFmpeg playback started for {stream_name}")
            state['retries'] = 0 # Retries reset ONLY on a successful start (I7)
            state['is_resuming'] = False # Clear resuming flag
            self.bot.save_state() # Save state after successful start

            await self.send_or_edit_now_playing_embed(guild_id, force_new=True)
            return True

        except Exception as e:
            logger.error(f"[{guild_id}] Error starting FFmpeg playback for '{stream_name}': {e}", exc_info=True)
            state['should_play'] = False
            self.bot.save_state()
            await self._notify_playback_failure(guild_id, f"⚠️ Couldn't start `{stream_name}` — the stream may be down. Try another station.") # I5a
            return False

    def after_playback_handler(self, guild_id: int, play_epoch: int, error: Optional[Exception]):
        """Callback after playback ends or errors. Handles state and reconnection. Stale (superseded) epochs never mutate state (I1)."""
        state = self.bot.guild_states.get(guild_id)
        if not state:
            logger.warning(f"[{guild_id}] after_playback_handler called but no state found.")
            return

        if state.get('play_epoch', 0) != play_epoch:
            # A newer play/stop superseded this source; do not touch current state or embed (I1)
            logger.info(f"[{guild_id}] Stale after-callback (epoch {play_epoch} != current {state.get('play_epoch', 0)}). Ignoring.")
            return

        should_play = state.get('should_play', False) # Check intent *before* modifying state
        logger.info(f"[{guild_id}] Playback finished/stopped in 'after' handler. Error: {error}, should_play flag was: {should_play}")

        # Use run_coroutine_threadsafe to schedule async cleanup
        cleanup_future = asyncio.run_coroutine_threadsafe(self.cleanup_now_playing_message(guild_id), self.bot.loop)
        try:
            cleanup_future.result(timeout=5) # Optional: wait briefly for task submission
        except concurrent.futures.TimeoutError:
            logger.warning(f"[{guild_id}] Timeout waiting for cleanup task submission in 'after' handler.")
        except Exception as e:
             logger.error(f"[{guild_id}] Error submitting cleanup task in 'after' handler: {e}")


        if error:
            logger.error(f"[{guild_id}] Playback Error reported in 'after' handler: {error}")
            if should_play:
                state['retries'] = state.get('retries', 0) + 1
                if state['retries'] <= config.MAX_RECONNECT_ATTEMPTS:
                    logger.warning(f"[{guild_id}] Playback error while should_play=True. Attempting reconnect {state['retries']}/{config.MAX_RECONNECT_ATTEMPTS} in {config.RECONNECT_DELAY}s.")
                    # Single-flight reconnect scheduler (I7), submitted from the player thread
                    reconnect_future = asyncio.run_coroutine_threadsafe(self._schedule_reconnect(guild_id), self.bot.loop)
                    try: reconnect_future.result(timeout=5)
                    except concurrent.futures.TimeoutError: logger.warning(f"[{guild_id}] Timeout waiting for reconnect task submission.")
                    except Exception as e: logger.error(f"[{guild_id}] Error submitting reconnect task: {e}")
                else:
                    logger.error(f"[{guild_id}] Max reconnect attempts ({config.MAX_RECONNECT_ATTEMPTS}) reached after error. Stopping playback permanently.")
                    state['should_play'] = False
                    self.bot.save_state() # Save the stopped state
                    notify_future = asyncio.run_coroutine_threadsafe(self._notify_playback_failure(guild_id, f"⚠️ Couldn't reconnect to `{state.get('stream_name', 'the stream')}` after {config.MAX_RECONNECT_ATTEMPTS} tries. Playback stopped — try again later."), self.bot.loop) # I5b
                    try: notify_future.result(timeout=5)
                    except concurrent.futures.TimeoutError: logger.warning(f"[{guild_id}] Timeout waiting for give-up notice submission.")
                    except Exception as e: logger.error(f"[{guild_id}] Error submitting give-up notice: {e}")
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
            state['should_play'] = False; self.bot.save_state()
            await self._notify_playback_failure(guild_id, "⚠️ Couldn't resume playback — saved state was incomplete. Playback stopped.") # I5
            return

        result = await self.ensure_voice_and_play(guild_id, vc_id, txt_id, url, name, req_id, is_manual_play=False)
        if not result.startswith("▶️") and state.get('should_play'):
            # Give-up paths that already posted a notice clear should_play; post only for otherwise-silent failures (I5)
            logger.warning(f"[{guild_id}] Reconnect attempt failed: {result}")
            await self._notify_playback_failure(guild_id, f"⚠️ Couldn't resume `{name}` automatically. Playback stopped — try playing it again.")


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
        self._cancel_reconnect_task(guild_id) # A fresh play supersedes any pending reconnect (I7)
        self.bot.guild_states[guild_id].update({
            'url': stream_url, 'stream_name': stream_name, 'requester_id': requester_id,
            'text_channel_id': text_channel_id, 'voice_channel_id': voice_channel_id,
            'should_play': True, 'retries': self.bot.guild_states[guild_id].get('retries', 0),
            'now_playing_message_id': self.bot.guild_states[guild_id].get('now_playing_message_id'),
            'current_metadata': None, 'is_resuming': not is_manual_play,
            'play_epoch': self.bot.guild_states[guild_id].get('play_epoch', 0) + 1, # Supersedes the live source's callbacks (I1/I3)
            'starting_at': time.monotonic(), # Watchdog grace while connecting/verifying (I8)
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
                voice_client = await voice_channel.connect(timeout=VOICE_CONNECT_TIMEOUT, reconnect=True)

            if not voice_client or not voice_client.is_connected(): raise Exception("VC connection failed.")

            started = await self._play_internal(guild_id, voice_client) # Start playback
            if not started:
                # _play_internal already posted a failure notice (I5a)
                return f"Error: Could not start playing `{stream_name}`. Try another station."

            await asyncio.sleep(PLAY_VERIFY_GRACE) # I4: give the source a moment before trusting it
            if voice_client.is_playing():
                logger.info(f"[{guild_id}] Playback verified playing for '{stream_name}'.")
                return f"▶️ Now playing: `{stream_name}`"

            # Verification failed: return a real error (I4); reconnect paths post the notice (I5)
            logger.warning(f"[{guild_id}] Playback verification failed for '{stream_name}' (not playing after {PLAY_VERIFY_GRACE}s).")
            return f"Error: Stream `{stream_name}` didn't start in time. Try another station."

        except Exception as e:
            logger.error(f"[{guild_id}] Error in ensure_voice_and_play for '{stream_name}': {e}", exc_info=True)
            self.bot.guild_states[guild_id]['should_play'] = False; self.bot.save_state()
            await self._notify_playback_failure(guild_id, f"⚠️ Couldn't connect and play `{stream_name}`. Playback stopped — try again in a moment.") # I5
            return f"An error occurred connecting or playing: {e}"
        finally:
            self.bot.guild_states[guild_id]['starting_at'] = None # Clear the watchdog grace guard (I8)


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
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state changes, especially for the bot itself."""
        if member.id != self.bot.user.id: return # Only care about the bot's state

        guild_id = member.guild.id
        state = self.bot.guild_states.get(guild_id)

        if before.channel and not after.channel: # Bot disconnected
            logger.info(f"[{guild_id}] Bot disconnected from VC '{before.channel.name}'.")
            if state:
                if state.get('should_play'):
                    logger.warning(f"[{guild_id}] Bot disconnected unexpectedly while should_play=True! Attempting reconnect.")
                    await self._schedule_reconnect(guild_id) # Single-flight (I7); retries reset only on successful start
                else:
                    logger.info(f"[{guild_id}] Bot disconnect expected. Resetting state.")
                    self.bot.save_state()
                    await self.cleanup_now_playing_message(guild_id)

        elif not before.channel and after.channel: # Bot connected
            logger.info(f"[{guild_id}] Bot connected to VC '{after.channel.name}'.")

        elif before.channel != after.channel: # Bot moved
             logger.info(f"[{guild_id}] Bot moved from '{before.channel.name}' to '{after.channel.name}'.")

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
                state['play_epoch'] = state.get('play_epoch', 0) + 1 # Stales the source's after-callback (I1/I2)
                self._cancel_reconnect_task(guild_id)
                self.bot.save_state()
                logger.info(f"[{guild_id}] Stopping playback via reaction.")
                if vc.is_playing() or vc.is_paused(): vc.stop() # After-callback is stale; cleanup is explicit below (I2)
                await self.cleanup_now_playing_message(guild_id)
                try: await reaction.remove(user)
                except discord.HTTPException: pass # Ignore permission errors removing reaction
                try: await reaction.message.channel.send(f"⏹️ Playback stopped by {user.mention}.", delete_after=10)
                except discord.HTTPException: pass
            else: # Bot not connected, but reaction exists
                logger.info(f"[{guild_id}] Stop reaction but bot not connected. Cleaning state/message.")
                state['should_play'] = False; state['play_epoch'] = state.get('play_epoch', 0) + 1
                self._cancel_reconnect_task(guild_id)
                self.bot.save_state(); await self.cleanup_now_playing_message(guild_id)

    # --- Tasks ---
    @tasks.loop(seconds=config.METADATA_FETCH_INTERVAL)
    async def metadata_loop(self):
        """Fetches and updates ICY metadata for active streams."""
        if not self.bot.http_session or self.bot.http_session.closed:
             logger.warning("Metadata loop: HTTP session closed/missing."); return

        active_guild_ids = list(self.bot.guild_states.keys())
        for guild_id in active_guild_ids:
            state = self.bot.guild_states.get(guild_id)
            guild = self.bot.get_guild(guild_id)
            vc = guild.voice_client if guild else None # Resolve the live client each iteration (I9)
            if state and state.get('should_play') and state.get('url') and vc and vc.is_playing():
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


    @tasks.loop(seconds=WATCHDOG_INTERVAL)
    async def watchdog_loop(self):
        """Detects silent streams (should_play=True but no audio) and recovers them (I8)."""
        now = time.monotonic()
        for guild_id, state in list(self.bot.guild_states.items()):
            try:
                if not state.get('should_play'): continue
                guild = self.bot.get_guild(guild_id)
                vc = guild.voice_client if guild else None # Resolve the live client each iteration (I9)
                if vc and vc.is_connected() and (vc.is_playing() or vc.is_paused()):
                    state['last_playing_at'] = now # Refreshed whenever audio is observed
                    continue
                if state.get('starting_at') is not None: continue # Mid-connect/verify grace; never fire here (I8)
                last_playing_at = state.get('last_playing_at')
                if last_playing_at is None:
                    state['last_playing_at'] = now # Baseline before judging silence
                    continue
                if now - last_playing_at <= WATCHDOG_SILENT_THRESHOLD: continue

                state['last_playing_at'] = now # Avoid an immediate re-trigger
                state['retries'] = state.get('retries', 0) + 1 # Recovery counts against retries (I8)
                if state['retries'] > config.MAX_RECONNECT_ATTEMPTS:
                    logger.error(f"[{guild_id}] Watchdog: silent for over {WATCHDOG_SILENT_THRESHOLD}s and max reconnect attempts reached. Giving up.")
                    await self._notify_playback_failure(guild_id, f"⚠️ Lost the stream for `{state.get('stream_name', 'the station')}` and couldn't recover. Playback stopped — try `,play` again later.") # I5c
                    if vc and vc.is_connected(): await vc.disconnect(force=True)
                    state['should_play'] = False
                    self.bot.save_state()
                    await self.cleanup_now_playing_message(guild_id)
                    continue
                logger.warning(f"[{guild_id}] Watchdog: stream silent for over {WATCHDOG_SILENT_THRESHOLD}s. Recovery attempt {state['retries']}/{config.MAX_RECONNECT_ATTEMPTS}.")
                station = self.bot.station_manager.get_station(state.get('stream_name')) if state.get('stream_name') else None
                fresh_url = (station or {}).get('url')
                if fresh_url and fresh_url != state.get('url'):
                    logger.info(f"[{guild_id}] Watchdog: station URL changed in the station list; switching to the fresh URL.")
                    state['url'] = fresh_url
                await self._schedule_reconnect(guild_id) # Recovery uses the single-flight helper (I7/I8)
            except Exception as e:
                logger.exception(f"[{guild_id}] Watchdog error for guild: {e}")

    @watchdog_loop.before_loop
    async def before_watchdog_loop(self):
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
        # Use StationManager's fuzzy finder
        matched_name, station_data = self.bot.station_manager.fuzzy_find_station(stream_lookup_key)

        if station_data:
            stream_url = station_data.get("url")
            stream_name = matched_name # Use the canonical name found by fuzzy search
            
            if not stream_url:
                 logger.error(f"[{guild_id}] Stream '{stream_name}' is missing 'url'.")
                 return f"Error: Configuration for stream '{stream_name}' is invalid."
            logger.info(f"[{guild_id}] Matched stream: {stream_name}")
        elif not stream_url.startswith(('http://', 'https://')):
            return f"Input `{stream_input}` is not a valid URL or found in the station list (try using ID). See `{config.COMMAND_PREFIX}list`."
        # --- END MODIFY DICTIONARY LOOKUP ---

        # Pre-flight health probe BEFORE joining voice (I6)
        probe_error = await self._probe_stream(guild_id, stream_url)
        if probe_error:
            logger.info(f"[{guild_id}] Pre-flight probe rejected '{stream_name}': {probe_error}")
            return probe_error

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
        """Shared logic for stopping playback. Stop paths clean up explicitly; stale after-callbacks do nothing (I1/I2)."""
        state = self.bot.guild_states.get(guild_id)
        guild = self.bot.get_guild(guild_id)
        vc = guild.voice_client if guild else None

        if state:
            state['should_play'] = False
            state['play_epoch'] = state.get('play_epoch', 0) + 1 # Stales the live source's after-callback (I1)
            self._cancel_reconnect_task(guild_id)
            logger.info(f"[{guild_id}] Stop command: should_play=False.")
            self.bot.save_state()
            await self.cleanup_now_playing_message(guild_id) # Explicit cleanup, not via the after-callback (I2)

        if vc and vc.is_connected():
            if vc.is_playing() or vc.is_paused():
                vc.stop() # After-callback is stale per the epoch bump; cleanup already done above (I2)
                return "⏹️ Playback stopped."
            else: # Connected but not playing
                return "Nothing was playing, but I am connected."
        else: # Not connected
            return "Not currently connected to voice."


    @commands.hybrid_command(name="leave", aliases=['dc'], description="Disconnects the bot from the voice channel.")
    async def leave(self, ctx: commands.Context):
        """Disconnects the bot from the voice channel."""
        if not ctx.guild: await ctx.send("Not usable outside servers.", ephemeral=True); return
        if ctx.interaction: await ctx.defer(ephemeral=False)

        result = await self._leave_command_logic(ctx.guild.id, ctx.guild.voice_client)
        await ctx.send(result)

    async def _leave_command_logic(self, guild_id, vc):
        """Shared logic for leaving voice. Cleans up explicitly; stale after-callbacks do nothing (I1/I2)."""
        state = self.bot.guild_states.get(guild_id)

        if state:
            state['should_play'] = False
            state['play_epoch'] = state.get('play_epoch', 0) + 1 # Stales the live source's after-callback (I1)
            self._cancel_reconnect_task(guild_id)
            logger.info(f"[{guild_id}] Leave command: should_play=False."); self.bot.save_state()
            await self.cleanup_now_playing_message(guild_id) # Explicit cleanup

        if vc and vc.is_connected():
            channel_name = vc.channel.name
            logger.info(f"[{guild_id}] Disconnecting from '{channel_name}' via command.")
            await vc.disconnect(force=False) # Triggers on_voice_state_update
            return f"Left `{channel_name}`."
        else:
            return "Not connected."


    @commands.hybrid_command(name="now", aliases=['np'], description="Shows the currently playing stream.")
    async def now(self, ctx: commands.Context):
        """Shows the currently playing stream."""
        if not ctx.guild: await ctx.send("Not usable outside servers.", ephemeral=True); return
        
        result = await self._now_command_logic(ctx.guild.id, ctx.guild.voice_client)
        
        if result == "reshown":
             if ctx.interaction:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message("Showing current stream info.", ephemeral=True)
             elif ctx.command:
                 try: await ctx.message.delete()
                 except discord.HTTPException: pass
        else:
             if ctx.interaction:
                 if not ctx.interaction.response.is_done():
                     await ctx.interaction.response.send_message(result, ephemeral=True)
             else:
                 await ctx.send(result)

    async def _now_command_logic(self, guild_id, vc):
        """Shared logic for showing now playing."""
        state = self.bot.guild_states.get(guild_id)
        if state and state.get('should_play') and vc and vc.is_playing():
            logger.info(f"[{guild_id}] Resending Now Playing embed via command.")
            await self.send_or_edit_now_playing_embed(guild_id, force_new=True)
            return "reshown"
        
        # If we are here, nothing is playing or bot not connected properly for playback
        if state and state.get('stream_name'):
             return f"Currently tasked to play `{state['stream_name']}`, but not actively playing audio right now."
        
        return "Not currently playing anything."


    # --- Auto-Reconnect Check ---
    async def check_voice_state_after_reconnect(self):
        """Checks voice connections after a gateway reconnect."""
        for guild_id, state in list(self.bot.guild_states.items()):
            guild = self.bot.get_guild(guild_id)
            if guild and state.get('should_play') and not state.get('is_resuming'):
                vc = guild.voice_client
                if not vc or not vc.is_connected():
                    logger.warning(f"[{guild_id}] Post-reconnect check: VC missing/disconnected while should_play=True. Triggering reconnect.")
                    await self._schedule_reconnect(guild_id) # Single-flight (I7); retries reset only on successful start
                elif not vc.is_playing() and state.get('url'):
                     logger.warning(f"[{guild_id}] Post-reconnect check: Connected but not playing while should_play=True. Attempting to restart play.")
                     await self._schedule_reconnect(guild_id) # Single-flight (I7); retries reset only on successful start


# --- Setup Function ---
async def setup(bot: RadioBot):
    await bot.add_cog(Playback(bot))