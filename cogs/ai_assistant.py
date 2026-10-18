# ./discord-radio-bot-modular/cogs/ai_assistant.py

import discord
from discord.ext import commands
import logging
import re
from collections import OrderedDict, deque
from typing import Dict, Deque, Tuple, Optional, List
import asyncio
import datetime

import config
from core.bot import RadioBot
from cogs.ai_backends.deepseek import DeepSeekBackend
from cogs.ai_backends.local import LocalBackend

logger = logging.getLogger('discord_bot.cogs.ai_assistant')

# --- PERSONALITY & CONFIGURATION ---
BOT_NAME = "Ishee"
MAX_HISTORY_LENGTH = 10
COOLDOWN_SECONDS = 5.0
# LRU cap: only the N most recently active channels keep conversation state.
MAX_ACTIVE_CHANNELS = 50

TRANSIENT_ERROR_PATTERNS = [
    "join a voice channel",
    "not in a voice channel",
    "not in a voice",
    "connect to a voice",
    "nothing is currently playing",
    "no audio is being played",
    "not connected to a voice",
    "i'm not in a voice",
    "no stations found"
]

class AIAssistant(commands.Cog):
    """Cog for a conversational AI assistant with swappable backends."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        # OrderedDict doubles as an LRU: most-recently-active channel last.
        self.conversation_history: OrderedDict[int, Deque[Tuple[str, str]]] = OrderedDict()
        # Per-channel disambiguation store: last search result as an ordered
        # list of (station_id, station_name).
        self.last_search_results: Dict[int, List[Tuple[str, str]]] = {}
        self.is_enabled = False
        self.backend = None
        
        self.provider = config.AI_PROVIDER
        logger.info(f"AI Assistant attempting to initialize with provider: '{self.provider}'")
        
        if self.provider == 'gemini':
            # Lazy import: only pull in google.generativeai when actually needed.
            from cogs.ai_backends.gemini import GeminiBackend
            self.backend = GeminiBackend()
            if self.backend.model:
                self.is_enabled = True
        elif self.provider == 'deepseek':
            self.backend = DeepSeekBackend(bot)
            if config.DEEPSEEK_API_KEY:
                self.is_enabled = True
        elif self.provider == 'local':
            self.backend = LocalBackend(bot)
            if config.LOCAL_AI_BASE_URL:
                self.is_enabled = True
        else:
            logger.warning(f"Invalid AI_PROVIDER '{self.provider}'. Must be 'gemini', 'deepseek', or 'local'. AI Assistant is DISABLED.")
            
        self.cooldown = commands.CooldownMapping.from_cooldown(1, COOLDOWN_SECONDS, commands.BucketType.user)

    def _touch_channel(self, channel_id: int) -> Deque[Tuple[str, str]]:
        """Returns the channel's history deque, refreshing its LRU position.
        Creates it if needed and evicts the least-recently-active channel
        (plus its search-candidate store) beyond MAX_ACTIVE_CHANNELS."""
        history = self.conversation_history.get(channel_id)
        if history is None:
            history = deque(maxlen=MAX_HISTORY_LENGTH * 2)
            self.conversation_history[channel_id] = history
            while len(self.conversation_history) > MAX_ACTIVE_CHANNELS:
                evicted_id, _ = self.conversation_history.popitem(last=False)
                self.last_search_results.pop(evicted_id, None)
                logger.debug(f"Evicted AI history for inactive channel {evicted_id} (LRU cap {MAX_ACTIVE_CHANNELS}).")
        else:
            self.conversation_history.move_to_end(channel_id)
        return history

    def _last_search_block(self, channel_id: int) -> str:
        """Renders the per-channel candidate store for the system prompt."""
        candidates = self.last_search_results.get(channel_id) or []
        if not candidates:
            return "None yet in this channel."
        lines = [f"{i}. {name} (station_id: {sid or 'n/a'})"
                 for i, (sid, name) in enumerate(candidates[:10], 1)]
        return "\n".join(lines)

    async def get_ai_response(self, channel_id: int, user_prompt: str, user_id: int, guild_id: int,
                              message_obj: discord.Message, pending_context: Optional[List[str]] = None) -> Optional[str]:
        if not self.backend: return None
        
        history = self._touch_channel(channel_id)
        
        # Real-time state checks
        bot_name = self.bot.user.name if self.bot.user else BOT_NAME
        current_time_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        
        user_voice = message_obj.author.voice if isinstance(message_obj.author, discord.Member) else None
        user_vc_status = f"IN voice channel '{user_voice.channel.name}'" if user_voice else "NOT in a voice channel"
        
        bot_voice = message_obj.guild.voice_client if message_obj.guild else None
        bot_vc_status = f"IN voice channel '{bot_voice.channel.name}'" if bot_voice else "NOT in a voice channel"

        system_instructions = f"""You are a friendly, kind, and cheerful girl named {bot_name}, a Discord bot. Keep your responses concise and positive.

=== CURRENT REAL-TIME STATE ===
Time: {current_time_str}
User Voice: {user_vc_status}
Bot Voice: {bot_vc_status}
CRITICAL: Always trust this state over past conversation history.

=== TOOL USE PROTOCOL (READ CAREFULLY) ===
You control a real Discord bot through tools. Typing an action WITHOUT calling a tool does NOTHING.
Tools may CHAIN within one reply: call a tool, read its result, then call another tool or answer the user — all before replying. Example: "play some jazz" -> call search_radio(tag="jazz"), then call play_radio with the top hit's station_id, then confirm to the user.

1. search_radio(query?, country?, tag?, limit?): structured search over the station database.
   - query: free text (name/tag/country match); country: e.g. "Malaysia"; tag: e.g. "Rock", "90's"; limit: 1-10 (default 5).
   - NEVER invent station names. Always search first when unsure.
2. play_radio(station_id or station_name): plays a station.
   - Prefer station_id taken from a search_radio result (exact, never fails on typos).
   - station_name is fuzzy-matched, or may be an ordinal "1".."10" from the last search results in this channel.
3. Other tools: now_playing, stop_radio, leave_voice, set_reminder, list_reminders, delete_reminder.
4. If a tool returns an error, fix your arguments and try again, or tell the user the exact error. Never pretend an action succeeded.
5. When a search returns multiple matches and the user's request was ambiguous, either play the single obvious top hit, or present up to 8 numbered options and STOP (wait for the user to pick).

=== LAST SEARCH RESULTS (reply with ordinal or station_id to play) ===
{self._last_search_block(channel_id)}
"""

        # Define the tool executor callback
        async def tool_executor_callback(tool_name, tool_args):
            return await self._execute_tool(tool_name, tool_args, user_id, guild_id, message_obj, pending_context)

        return await self.backend.generate_response(list(history), user_prompt, system_instructions, tool_executor_callback)

    async def _execute_tool(self, name, args, user_id, guild_id, message_obj, pending_context=None):
        """Executes the tool requested by the AI. Args arrive pre-validated
        and normalized by the backend's validation gateway."""
        try:
            if name == "play_radio":
                playback_cog = self.bot.get_cog("Playback")
                if not playback_cog: return "Error: Playback module is not loaded."
                
                member = message_obj.guild.get_member(user_id)
                if not member or not member.voice: return "Error: User must be in a voice channel to play radio."

                channel_id = message_obj.channel.id
                station_id = args.get('station_id')
                station_name = (args.get('station_name') or '').strip()
                target = None

                if station_id:
                    # ID-reference protocol: resolve exact id first.
                    await self.bot.station_manager.ensure_fresh()
                    hit = self.bot.station_manager.get_by_id(station_id)
                    if not hit:
                        return f"Error: station_id '{station_id}' was not found. Ask the user to pick a station from the latest search results."
                    target = hit[0]
                elif station_name:
                    if station_name.isdigit() and 1 <= int(station_name) <= 10:
                        # Ordinal referencing the last search results.
                        ordinal = int(station_name)
                        candidates = self.last_search_results.get(channel_id) or []
                        if ordinal > len(candidates):
                            return (f"Error: '{ordinal}' is outside the last search results "
                                    f"({len(candidates)} shown). Ask the user to choose again.")
                        target = candidates[ordinal - 1][1]
                    else:
                        # Frozen fuzzy-resolving play pipeline handles the rest.
                        target = station_name
                else:
                    # Bare confirmation ("yes") with exactly one stored candidate.
                    candidates = self.last_search_results.get(channel_id) or []
                    if len(candidates) == 1:
                        target = candidates[0][1]
                    else:
                        return ("Error: no station specified. Ask the user which station to play "
                                "(an ordinal 1-10 from the last search, a station_id, or a name).")

                result = await playback_cog._play_command_logic(
                    guild_id=guild_id,
                    user=member,
                    text_channel_id=message_obj.channel.id,
                    voice_channel=member.voice.channel,
                    stream_input=target
                )
                return f"Tool Execution Result: {result}"

            elif name == "search_radio":
                station_manager = self.bot.station_manager
                await station_manager.ensure_fresh()
                matches = station_manager.search_stations(
                    query=args.get('query') or None,
                    limit=args.get('limit', 5),
                    country=args.get('country'),
                    tag=args.get('tag')
                )
                if not matches: return "Error: No stations found matching that query."

                channel_id = message_obj.channel.id
                self.last_search_results[channel_id] = [
                    ((data.get('station_id') or ''), sname) for sname, data in matches
                ]

                lines = []
                compact = []
                for i, (sname, data) in enumerate(matches, 1):
                    country = data.get('country') or 'Unknown'
                    tags = data.get('tags') or []
                    tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
                    sid = data.get('station_id') or ''
                    lines.append(f"{i}. {sname} | Country: {country} | Tags: {tags_str or 'none'} | station_id: {sid}")
                    compact.append(f"{i}. {sname} (station_id: {sid})")

                # Ground follow-up turns on a compact summary of these results.
                if pending_context is not None:
                    pending_context.append("Last search results: " + "; ".join(compact))

                return ("Found these stations (play one with play_radio using its station_id "
                        "or the ordinal):\n" + "\n".join(lines))

            elif name == "stop_radio":
                playback_cog = self.bot.get_cog("Playback")
                if not playback_cog: return "Error: Playback module is not loaded."
                return await playback_cog._stop_command_logic(guild_id)

            elif name == "leave_voice":
                playback_cog = self.bot.get_cog("Playback")
                if not playback_cog: return "Error: Playback module is not loaded."
                return await playback_cog._leave_command_logic(guild_id, message_obj.guild.voice_client)

            elif name == "now_playing":
                playback_cog = self.bot.get_cog("Playback")
                if not playback_cog: return "Error: Playback module is not loaded."
                result = await playback_cog._now_command_logic(guild_id, message_obj.guild.voice_client)
                return "Embed shown" if result == "reshown" else result

            elif name == "set_reminder":
                reminders_cog = self.bot.get_cog("Reminders")
                if not reminders_cog: return "Error: Reminders module is not loaded."
                
                try:
                    due_at = datetime.datetime.fromisoformat(args["due_at_utc_iso"].replace("Z", "+00:00"))
                    await reminders_cog.create_reminder(
                        user_id=user_id,
                        channel_id=message_obj.channel.id,
                        guild_id=guild_id,
                        message=args["message"],
                        due_at_utc=due_at
                    )
                    return f"Reminder set for {args['due_at_utc_iso']}."
                except ValueError:
                    return "Error: Invalid ISO timestamp format provided by AI."

            elif name == "list_reminders":
                reminders_cog = self.bot.get_cog("Reminders")
                if not reminders_cog: return "Error: Reminders module is not loaded."
                
                reminders = await reminders_cog.get_user_reminders(user_id)
                if not reminders: return "You have no upcoming reminders."
                
                result_text = "Your Reminders:\n"
                for rem in reminders:
                    due_ts = int(datetime.datetime.fromisoformat(rem['due_at']).timestamp())
                    result_text += f"ID: {rem['id']} | Time: <t:{due_ts}:f> | Message: {rem['reminder_content']}\n"
                return result_text

            elif name == "delete_reminder":
                reminders_cog = self.bot.get_cog("Reminders")
                if not reminders_cog: return "Error: Reminders module is not loaded."
                
                success = await reminders_cog.delete_user_reminder(user_id, int(args["reminder_id"]))
                if success: return f"Reminder {args['reminder_id']} deleted."
                else: return "Reminder not found or you don't own it."

        except Exception as e:
            logger.exception(f"Tool execution error ({name}): {e}")
            return f"Error executing tool '{name}': {e}"

        return f"Error: Unknown tool '{name}'."

    def _is_transient_error(self, text: str) -> bool:
        """Checks if an AI response is a transient error that shouldn't be cached in history."""
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in TRANSIENT_ERROR_PATTERNS)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.is_enabled or message.author.bot or not message.guild or not self.bot.user:
            return

        # Mention gate: matches both <@id> and the nickname form <@!id>.
        mention_match = re.match(rf'^<@!?{self.bot.user.id}>\s*', message.content)
        if not mention_match:
            return

        logger.info(f"Bot was mentioned by {message.author}. Proceeding with AI response logic.")
        
        bucket = self.cooldown.get_bucket(message)
        if bucket and bucket.update_rate_limit():
            logger.warning(f"User {message.author} is on AI cooldown. Ignoring message.")
            return

        try:
            async with message.channel.typing():
                user_input = message.content[mention_match.end():].strip()
                if not user_input:
                    await message.reply(f"Hello there! I'm {BOT_NAME}, how can I help? 😊")
                    return

                channel_id = message.channel.id
                pending_context: List[str] = []
                ai_response_text = await self.get_ai_response(
                    channel_id=channel_id,
                    user_prompt=user_input,
                    user_id=message.author.id,
                    guild_id=message.guild.id,
                    message_obj=message,
                    pending_context=pending_context
                )
                
                if not ai_response_text: raise Exception("Received an empty response from the AI.")

                if not self._is_transient_error(ai_response_text):
                    history = self._touch_channel(channel_id)
                    history.append(('user', user_input))
                    # Compact tool-result summaries ground follow-up turns.
                    for context_line in pending_context:
                        history.append(('ai', context_line))
                    history.append(('ai', ai_response_text))
                
                if len(ai_response_text) <= 2000: 
                    await message.reply(ai_response_text)
                else:
                    for chunk in [ai_response_text[i:i+2000] for i in range(0, len(ai_response_text), 2000)]: 
                        await message.channel.send(chunk)
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.exception("An error occurred in the AI Assistant on_message handler.")
            if "safety" in str(e).lower() or "blocked" in str(e).lower():
                await message.reply("I can't respond to that. Let's talk about something else!", delete_after=10)
            else:
                await message.reply("I'm having trouble thinking right now. Please try again later.", delete_after=10)

async def setup(bot: RadioBot):
    await bot.add_cog(AIAssistant(bot))
