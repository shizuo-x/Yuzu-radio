# ./discord-radio-bot-modular/cogs/ai_assistant.py

import discord
from discord.ext import commands
import logging
import google.generativeai as genai
from collections import deque
from typing import Dict, Deque, Tuple, Optional
import asyncio

import config
from core.bot import RadioBot

logger = logging.getLogger('discord_bot.cogs.ai_assistant')

# --- PERSONALITY & CONFIGURATION ---
BOT_NAME = "Ishee"
SYSTEM_PROMPT_TEMPLATE = f"You are a friendly, kind, and cheerful girl named {BOT_NAME}, a Discord bot. Your purpose is to be helpful and engaging. Keep your responses concise and positive."
MAX_HISTORY_LENGTH = 10
COOLDOWN_SECONDS = 5.0

class AIAssistant(commands.Cog):
    """Cog for a conversational AI assistant with swappable backends (Gemini/DeepSeek)."""

    def __init__(self, bot: RadioBot):
        self.bot = bot
        self.conversation_history: Dict[int, Deque[Tuple[str, str]]] = {}
        self.model = None
        self.is_enabled = False
        
        self.provider = config.AI_PROVIDER
        logger.info(f"AI Assistant attempting to initialize with provider: '{self.provider}'")
        
        if self.provider == 'gemini':
            if config.GEMINI_API_KEY:
                try:
                    genai.configure(api_key=config.GEMINI_API_KEY)
                    self.model = genai.GenerativeModel(config.GEMINI_MODEL)
                    self.is_enabled = True
                    logger.info(f"AI Assistant ENABLED with Google Gemini (Model: {config.GEMINI_MODEL}).")
                except Exception as e: logger.error(f"Failed to configure Gemini: {e}")
            else: logger.warning("AI provider is 'gemini' but GEMINI_API_KEY is not set. AI Assistant is DISABLED.")
        elif self.provider == 'deepseek':
            if config.DEEPSEEK_API_KEY:
                self.is_enabled = True
                logger.info(f"AI Assistant ENABLED with DeepSeek (Model: {config.DEEPSEEK_MODEL}).")
            else: logger.warning("AI provider is 'deepseek' but DEEPSEEK_API_KEY is not set. AI Assistant is DISABLED.")
        else:
            logger.warning(f"Invalid AI_PROVIDER '{self.provider}'. Must be 'gemini' or 'deepseek'. AI Assistant is DISABLED.")
            
        self.cooldown = commands.CooldownMapping.from_cooldown(1, COOLDOWN_SECONDS, commands.BucketType.user)

    async def get_ai_response(self, channel_id: int, user_prompt: str) -> Optional[str]:
        history = self.conversation_history.get(channel_id, deque(maxlen=MAX_HISTORY_LENGTH * 2))
        if self.provider == 'gemini': return await self._get_gemini_response(list(history), user_prompt)
        elif self.provider == 'deepseek': return await self._get_deepseek_response(list(history), user_prompt)
        return None

    async def _get_gemini_response(self, history_tuples, user_prompt):
        gemini_history = [{'role': 'model' if role == 'ai' else 'user', 'parts': [text]} for role, text in history_tuples]
        chat_session = self.model.start_chat(history=gemini_history)
        bot_name = self.bot.user.name if self.bot.user else "Yuzu"
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(bot_name=bot_name)
        prompt = user_prompt if gemini_history else f"{system_prompt}\n\nUSER: {user_prompt}\nASSISTANT:"
        response = await chat_session.send_message_async(prompt)
        return response.text.strip()

    async def _get_deepseek_response(self, history_tuples, user_prompt):
        if not self.bot.http_session: return None
        messages = []
        bot_name = self.bot.user.name if self.bot.user else "Ishee"
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(bot_name=bot_name)
        messages.append({'role': 'system', 'content': system_prompt})
        for role, text in history_tuples: messages.append({'role': 'assistant' if role == 'ai' else 'user', 'content': text})
        messages.append({'role': 'user', 'content': user_prompt})
        headers = {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}
        payload = {"model": config.DEEPSEEK_MODEL, "messages": messages, "stream": False}
        async with self.bot.http_session.post(config.DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30) as response:
            if response.status == 200: data = await response.json(); return data['choices'][0]['message']['content'].strip()
            else: raise Exception(f"DeepSeek API Error: {response.status} - {await response.text()}")

    # --- UPDATED Event Listener with Debug Logging ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # The earliest check to see if the event is firing at all
        logger.debug(f"on_message fired for author: {message.author.id}, content: '{message.content[:50]}...'")

        if not self.is_enabled:
            # This would only happen if the log showed a warning on startup
            logger.debug("AI is disabled, ignoring message.")
            return

        if message.author.bot:
            logger.debug(f"Ignoring message because author is a bot.")
            return

        if not message.guild:
            logger.debug(f"Ignoring message because it is in a DM.")
            return
        
        # Make sure bot user object is available before checking mention
        if not self.bot.user:
            logger.debug("Ignoring message because bot user object is not yet available.")
            return

        if not message.content.startswith(self.bot.user.mention):
            logger.debug(f"Ignoring message because it does not start with a mention.")
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
                    await message.reply(f"Hello there! I'm {BOT_NAME}, how can I help? 😊"); return

                channel_id = message.channel.id
                ai_response_text = await self.get_ai_response(channel_id, user_input)
                
                if not ai_response_text: raise Exception("Received an empty response from the AI.")

                if channel_id not in self.conversation_history: self.conversation_history[channel_id] = deque(maxlen=MAX_HISTORY_LENGTH * 2)
                self.conversation_history[channel_id].append(('user', user_input))
                self.conversation_history[channel_id].append(('ai', ai_response_text))
                
                if len(ai_response_text) <= 2000: await message.reply(ai_response_text)
                else:
                    for chunk in [ai_response_text[i:i+2000] for i in range(0, len(ai_response_text), 2000)]: await message.channel.send(chunk)
                await asyncio.sleep(1)
        except Exception as e:
            logger.exception("An error occurred in the AI Assistant on_message handler.")
            if "safety" in str(e).lower() or "blocked" in str(e).lower():
                await message.reply("I can't respond to that. Let's talk about something else!", delete_after=10)
            else:
                await message.reply("I'm having trouble thinking right now. Please try again later.", delete_after=10)


async def setup(bot: RadioBot):
    await bot.add_cog(AIAssistant(bot))