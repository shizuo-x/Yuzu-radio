# ./discord-radio-bot-modular/cogs/ai_backends/deepseek.py

import logging
import config
from cogs.ai_backends.openai_loop import build_tools_schema, run_openai_tool_loop

logger = logging.getLogger('discord_bot.ai_backends.deepseek')

class DeepSeekBackend:
    def __init__(self, bot):
        self.bot = bot
        if config.DEEPSEEK_API_KEY:
            logger.info(f"DeepSeek Backend Initialized (Model: {config.DEEPSEEK_MODEL})")
        else:
            logger.warning("DEEPSEEK_API_KEY not set.")

    def get_tools_schema(self):
        """Defines the OpenAI-compatible tool schema for DeepSeek."""
        return build_tools_schema()

    async def generate_response(self, history_tuples, user_prompt, system_instructions, tool_executor):
        """
        Generates a response using the shared OpenAI-compatible bounded
        multi-round tool loop.
        """
        if not self.bot.http_session: return "Error: HTTP session not available."

        messages = []
        messages.append({'role': 'system', 'content': system_instructions})
        for role, text in history_tuples:
            messages.append({'role': 'assistant' if role == 'ai' else 'user', 'content': text})
        messages.append({'role': 'user', 'content': user_prompt})

        headers = {
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        try:
            return await run_openai_tool_loop(
                http_session=self.bot.http_session,
                api_url=config.DEEPSEEK_API_URL,
                headers=headers,
                model=config.DEEPSEEK_MODEL,
                messages=messages,
                tool_executor=tool_executor,
                provider_label="DeepSeek"
            )
        except Exception as e:
            logger.exception(f"DeepSeek Execution Error: {e}")
            return "I'm having trouble thinking right now."
