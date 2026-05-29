# ./discord-radio-bot-modular/cogs/ai_assistant.py

import discord
from discord.ext import commands
import logging
from collections import deque
from typing import Dict, Deque, Tuple, Optional
import asyncio
import datetime

import config
from core.bot import RadioBot
from cogs.ai_backends.gemini import GeminiBackend
from cogs.ai_backends.deepseek import DeepSeekBackend
from cogs.ai_backends.local import LocalBackend

logger = logging.getLogger('discord_bot.cogs.ai_assistant')

# --- PERSONALITY & CONFIGURATION ---
BOT_NAME = "Ishee"
MAX_HISTORY_LENGTH = 10
COOLDOWN_SECONDS = 5.0

TRANSIENT_ERROR_PATTERNS = [
    "join a voice channel",
    "not in a voice channel",
    "not in a voice",
    "connect to a voice",
    "nothing is currently playing",
    "no audio is being played",
    "not connected to a voice",
    "i'm not in a voice",
    "no stations found",
    "error executing"
]

class AIAssistant(commands.Cog):
    """Cog for a conversational AI assistant with swappable backends."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        self.conversation_history: Dict[int, Deque[Tuple[str, str]]] = {}
        self.is_enabled = False
        self.backend = None
        
        self.provider = config.AI_PROVIDER
        logger.info(f"AI Assistant attempting to initialize with provider: '{self.provider}'")
        
        if self.provider == 'gemini':
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

    async def get_ai_response(self, channel_id: int, user_prompt: str, user_id: int, guild_id: int, message_obj: discord.Message) -> Optional[str]:
        if not self.backend: return None
        
        history = self.conversation_history.get(channel_id, deque(maxlen=MAX_HISTORY_LENGTH * 2))
        
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

=== CRITICAL TOOL EXECUTION MANDATES (READ CAREFULLY) ===
You are an AI controlling a real Discord bot. YOU CANNOT PERFORM ACTIONS JUST BY TYPING TEXT. 
If you roleplay an action (e.g. saying "Playing now!" without using the tool), the bot will do NOTHING.

1. TO PLAY MUSIC: You MUST physically call the `play_radio` tool. 
   -> If the user tells you to play a specific station, CALL THE TOOL.
   -> If you ask the user "Did you mean [Station]?" and they reply "yes", YOU MUST CALL THE `play_radio` TOOL IMMEDIATELY for that station.
2. TO SEARCH: You MUST call the `search_radio` tool. NEVER guess, hallucinate, or invent radio station names.
   -> The database has tags (e.g., "90's", "Rock", "English") and countries (e.g., "Malaysia", "International"). You can search using these terms!
3. TO CHECK NOW PLAYING: You MUST call the `now_playing` tool.
4. TO STOP: You MUST call the `stop_radio` tool.

=== WORKFLOW EXAMPLES ===
- User: "Play rock" -> You: call `search_radio` with query "rock". Show results to user.
- User: "Play a Malaysian station" -> You: call `search_radio` with query "Malaysia".
- User: "yes" (confirming a station you just suggested) -> You: call `play_radio` with the exact station name immediately. Do not just say "Playing it!".
- If any tool returns an error, tell the user the exact error. Do not pretend the action succeeded.
"""

        # Define the tool executor callback
        async def tool_executor_callback(tool_name, tool_args):
            return await self._execute_tool(tool_name, tool_args, user_id, guild_id, message_obj)

        return await self.backend.generate_response(list(history), user_prompt, system_instructions, tool_executor_callback)

    async def _execute_tool(self, name, args, user_id, guild_id, message_obj):
        """Executes the tool requested by the AI."""
        try:
            if name == "play_radio":
                playback_cog = self.bot.get_cog("Playback")
                if not playback_cog: return "Error: Playback module is not loaded."
                
                member = message_obj.guild.get_member(user_id)
                if not member or not member.voice: return "Error: User must be in a voice channel to play radio."
                
                result = await playback_cog._play_command_logic(
                    guild_id=guild_id,
                    user=member,
                    text_channel_id=message_obj.channel.id,
                    voice_channel=member.voice.channel,
                    stream_input=args["station_name"]
                )
                return f"Tool Execution Result: {result}"

            elif name == "search_radio":
                matches = self.bot.station_manager.search_stations(args["query"], limit=5)
                if not matches: return "Error: No stations found matching that query."
                
                result_text = "Found these stations:\n"
                for name_str, data in matches:
                    # Dynamically extract country and tags to feed to the AI
                    country = data.get('country', '')
                    tags = data.get('tags', [])
                    
                    # Formatting tags from array into a comma-separated string if needed
                    if isinstance(tags, list):
                        tags_str = ", ".join(tags)
                    else:
                        tags_str = str(tags)
                        
                    # Build a smart description based on available Pocketbase metadata
                    details = []
                    if country: details.append(f"Country: {country}")
                    if tags_str: details.append(f"Tags: {tags_str}")
                    
                    # Fallback to general desc if neither tags nor country exist
                    if not details and 'desc' in data:
                        details.append(f"Desc: {data['desc']}")
                        
                    details_str = " | ".join(details)
                    result_text += f"- {name_str} ({details_str})\n"
                    
                return result_text

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

        if not message.content.startswith(self.bot.user.mention):
            return

        logger.info(f"Bot was mentioned by {message.author}. Proceeding with AI response logic.")
        
        bucket = self.cooldown.get_bucket(message)
        if bucket and bucket.update_rate_limit():
            logger.warning(f"User {message.author} is on AI cooldown. Ignoring message.")
            return

        try:
            async with message.channel.typing():
                user_input = message.content[len(self.bot.user.mention):].strip()
                if not user_input:
                    await message.reply(f"Hello there! I'm {BOT_NAME}, how can I help? 😊")
                    return

                channel_id = message.channel.id
                ai_response_text = await self.get_ai_response(
                    channel_id=channel_id,
                    user_prompt=user_input,
                    user_id=message.author.id,
                    guild_id=message.guild.id,
                    message_obj=message
                )
                
                if not ai_response_text: raise Exception("Received an empty response from the AI.")

                if channel_id not in self.conversation_history: 
                    self.conversation_history[channel_id] = deque(maxlen=MAX_HISTORY_LENGTH * 2)
                
                if not self._is_transient_error(ai_response_text):
                    self.conversation_history[channel_id].append(('user', user_input))
                    self.conversation_history[channel_id].append(('ai', ai_response_text))
                
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