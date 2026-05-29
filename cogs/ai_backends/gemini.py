import logging
import google.generativeai as genai
import config
from google.ai.generativelanguage_v1beta.types import content

logger = logging.getLogger('discord_bot.ai_backends.gemini')

class GeminiBackend:
    def __init__(self):
        if config.GEMINI_API_KEY:
            try:
                genai.configure(api_key=config.GEMINI_API_KEY)
                self.model = genai.GenerativeModel(config.GEMINI_MODEL)
                logger.info(f"Gemini Backend Initialized (Model: {config.GEMINI_MODEL})")
            except Exception as e:
                logger.error(f"Failed to configure Gemini: {e}")
                self.model = None
        else:
            logger.warning("GEMINI_API_KEY not set.")
            self.model = None

    def get_tools_schema(self):
        """Defines the tool schema for Gemini."""
        return [
            {
                "function_declarations": [
                    {
                        "name": "play_radio",
                        "description": "Plays a specific radio station by name or fuzzy match.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "station_name": {"type": "STRING", "description": "The name or search term for the station."}
                            },
                            "required": ["station_name"]
                        }
                    },
                    {
                        "name": "search_radio",
                        "description": "Searches for radio stations by tag, country, or genre.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "query": {"type": "STRING", "description": "The search term."}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "set_reminder",
                        "description": "Sets a reminder. Calculate absolute UTC ISO timestamp from relative time.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "message": {"type": "STRING", "description": "What to remind about."},
                                "due_at_utc_iso": {"type": "STRING", "description": "ISO 8601 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)."}
                            },
                            "required": ["message", "due_at_utc_iso"]
                        }
                    },
                    {
                        "name": "stop_radio",
                        "description": "Stops the currently playing radio stream.",
                        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
                    },
                    {
                        "name": "leave_voice",
                        "description": "Disconnects the bot from the voice channel.",
                        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
                    },
                    {
                        "name": "now_playing",
                        "description": "Gets information about the currently playing radio stream.",
                        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
                    },
                    {
                        "name": "list_reminders",
                        "description": "Lists the user's upcoming reminders.",
                        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
                    },
                    {
                        "name": "delete_reminder",
                        "description": "Deletes a specific reminder by its ID.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "reminder_id": {"type": "INTEGER", "description": "The ID of the reminder to delete."}
                            },
                            "required": ["reminder_id"]
                        }
                    }
                ]
            }
        ]

    async def generate_response(self, history_tuples, user_prompt, system_instructions, tool_executor):
        """
        Generates a response, handling tool calls if necessary.
        tool_executor: async function(tool_name, tool_args) -> result_string
        """
        if not self.model: return "Gemini is not configured."

        gemini_history = [{'role': 'model' if role == 'ai' else 'user', 'parts': [text]} for role, text in history_tuples]
        
        # Configure model with tools for this chat
        tools_config = self.get_tools_schema()
        # Use a fresh model instance to ensure tools are bound correctly for this session
        chat_model = genai.GenerativeModel(config.GEMINI_MODEL, tools=tools_config)
        chat_session = chat_model.start_chat(history=gemini_history)

        full_prompt = f"SYSTEM INSTRUCTIONS:\n{system_instructions}\n\nUSER REQUEST: {user_prompt}"

        try:
            response = await chat_session.send_message_async(full_prompt)
        except Exception as e:
            logger.error(f"Gemini API Error: {e}")
            return "I'm having trouble connecting to my brain right now."

        # Handle Tool Calls
        try:
            part = response.candidates[0].content.parts[0]
        except (IndexError, AttributeError):
             return response.text.strip() if response.text else "..."

        if hasattr(part, 'function_call') and part.function_call:
            fc = part.function_call
            tool_name = fc.name
            tool_args = fc.args
            
            logger.info(f"Gemini requested tool: {tool_name}")
            
            # Execute tool
            tool_result = await tool_executor(tool_name, tool_args)
            
            # Send result back
            function_response_part = content.Part(
                function_response=content.FunctionResponse(
                    name=tool_name,
                    response={"result": tool_result}
                )
            )
            
            tool_response = await chat_session.send_message_async([function_response_part])
            return tool_response.text.strip()
        
        return response.text.strip()