# ./discord-radio-bot-modular/cogs/ai_backends/local.py

import logging
import config
from cogs.ai_backends.openai_loop import build_tools_schema, run_openai_tool_loop

logger = logging.getLogger('discord_bot.ai_backends.local')

class LocalBackend:
    def __init__(self, bot):
        self.bot = bot
        logger.info(f"Local AI Backend Initialized (URL: {config.LOCAL_AI_BASE_URL}, Model: {config.LOCAL_AI_MODEL})")

    def get_tools_schema(self):
        """Defines the OpenAI-compatible tool schema."""
        return build_tools_schema()

    async def generate_response(self, history_tuples, user_prompt, system_instructions, tool_executor):
        """
        Generates a response using the shared OpenAI-compatible bounded
        multi-round tool loop (Local/LM Studio).
        """
        if not self.bot.http_session: return "Error: HTTP session not available."

        messages = []
        messages.append({'role': 'system', 'content': system_instructions})
        for role, text in history_tuples:
            messages.append({'role': 'assistant' if role == 'ai' else 'user', 'content': text})
        messages.append({'role': 'user', 'content': user_prompt})

        # LM Studio usually doesn't require auth, but we can pass a dummy key
        headers = {
            "Content-Type": "application/json"
        }

        # Ensure base URL ends with chat/completions correctly
        api_url = config.LOCAL_AI_BASE_URL.rstrip('/')
        if not api_url.endswith("/chat/completions"):
            api_url += "/chat/completions"

        try:
            return await run_openai_tool_loop(
                http_session=self.bot.http_session,
                api_url=api_url,
                headers=headers,
                model=config.LOCAL_AI_MODEL,
                messages=messages,
                tool_executor=tool_executor,
                provider_label="Local AI"
            )
        except Exception as e:
            logger.exception(f"Local AI Execution Error: {e}")
            return "I'm having trouble thinking locally right now."
