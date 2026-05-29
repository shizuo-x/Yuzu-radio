import logging
import json
import config

logger = logging.getLogger('discord_bot.ai_backends.local')

class LocalBackend:
    def __init__(self, bot):
        self.bot = bot
        logger.info(f"Local AI Backend Initialized (URL: {config.LOCAL_AI_BASE_URL}, Model: {config.LOCAL_AI_MODEL})")

    def get_tools_schema(self):
        """Defines the OpenAI-compatible tool schema."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "play_radio",
                    "description": "Plays a specific radio station by name or fuzzy match.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "station_name": {"type": "string", "description": "The name or search term for the station."}
                        },
                        "required": ["station_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_radio",
                    "description": "Searches for radio stations by tag, country, or genre.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The search term."}
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "set_reminder",
                    "description": "Sets a reminder. Calculate absolute UTC ISO timestamp from relative time.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "What to remind about."},
                            "due_at_utc_iso": {"type": "string", "description": "ISO 8601 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)."}
                        },
                        "required": ["message", "due_at_utc_iso"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "stop_radio",
                    "description": "Stops the currently playing radio stream.",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "leave_voice",
                    "description": "Disconnects the bot from the voice channel.",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "now_playing",
                    "description": "Gets information about the currently playing radio stream.",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_reminders",
                    "description": "Lists the user's upcoming reminders.",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_reminder",
                    "description": "Deletes a specific reminder by its ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reminder_id": {"type": "integer", "description": "The ID of the reminder to delete."}
                        },
                        "required": ["reminder_id"]
                    }
                }
            }
        ]

    async def generate_response(self, history_tuples, user_prompt, system_instructions, tool_executor):
        """
        Generates a response using OpenAI-compatible API (Local/LM Studio), handling tool calls.
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

        payload = {
            "model": config.LOCAL_AI_MODEL,
            "messages": messages,
            "tools": self.get_tools_schema(),
            "tool_choice": "auto",
            "stream": False
        }

        try:
            async with self.bot.http_session.post(api_url, headers=headers, json=payload, timeout=60) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Local AI API Error: {response.status} - {error_text}")
                    return f"I encountered an error with my local brain ({response.status})."
                
                data = await response.json()
                choice = data['choices'][0]
                message = choice['message']

                # Check for tool calls
                if message.get('tool_calls'):
                    tool_calls = message['tool_calls']
                    
                    # Add the assistant's request to messages history
                    messages.append(message)

                    for tool_call in tool_calls:
                        function_name = tool_call['function']['name']
                        arguments_str = tool_call['function']['arguments']
                        call_id = tool_call['id']
                        
                        logger.info(f"Local AI requested tool: {function_name}")
                        
                        try:
                            arguments = json.loads(arguments_str)
                            tool_result = await tool_executor(function_name, arguments)
                        except json.JSONDecodeError:
                            tool_result = "Error: Invalid JSON arguments generated."
                        except Exception as e:
                            tool_result = f"Error executing tool: {e}"

                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": function_name,
                            "content": str(tool_result)
                        })

                    # Second request for final response
                    payload['messages'] = messages
                    
                    async with self.bot.http_session.post(api_url, headers=headers, json=payload, timeout=60) as response2:
                        if response2.status != 200:
                            return "Error generating final response after tool execution."
                        data2 = await response2.json()
                        return data2['choices'][0]['message']['content'].strip()

                else:
                    return message['content'].strip()

        except Exception as e:
            logger.exception(f"Local AI Execution Error: {e}")
            return "I'm having trouble thinking locally right now."