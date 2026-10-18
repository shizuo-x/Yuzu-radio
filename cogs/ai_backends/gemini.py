# ./discord-radio-bot-modular/cogs/ai_backends/gemini.py

import logging
import google.generativeai as genai
import config
from google.ai.generativelanguage_v1beta.types import content as glm_content
from cogs.ai_backends.openai_loop import MAX_MODEL_ROUNDS, FINAL_SUMMARY_NUDGE
from cogs.ai_backends.tool_gateway import validate_tool_call

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
        """Defines the tool schema for Gemini (function_declarations)."""
        return [
            {
                "function_declarations": [
                    {
                        "name": "search_radio",
                        "description": (
                            "Searches the radio station database with structured filters. "
                            "Returns a numbered list; every line ends with a station_id "
                            "that play_radio accepts."
                        ),
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "query": {"type": "STRING", "description": "Free-text search term matched against station names, tags and countries."},
                                "country": {"type": "STRING", "description": "Filter by country (e.g. 'Malaysia', 'International')."},
                                "tag": {"type": "STRING", "description": "Filter by tag/genre (e.g. 'Rock', \"90's\", 'Jazz')."},
                                "limit": {"type": "INTEGER", "description": "Max results to return, 1-10. Default 5."}
                            },
                            "required": []
                        }
                    },
                    {
                        "name": "play_radio",
                        "description": (
                            "Plays a radio station. Prefer station_id from a previous "
                            "search_radio result; station_name fuzzy-matches, or may be "
                            "an ordinal '1'-'10' referring to the last search results in "
                            "this channel."
                        ),
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "station_id": {"type": "STRING", "description": "Exact station_id from a search_radio result (preferred)."},
                                "station_name": {"type": "STRING", "description": "Station name, fuzzy search term, or ordinal '1'-'10' from the last search results."}
                            },
                            "required": []
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

    @staticmethod
    def _extract_candidate(response):
        """Returns (text, function_calls) from a Gemini response, tolerating
        blocked/empty candidates. Iterates ALL parts, preserving mixed
        text+function_call orderings."""
        try:
            candidate = (response.candidates or [None])[0]
        except (TypeError, IndexError):
            return "", []
        if candidate is None:
            return "", []
        parts = getattr(getattr(candidate, 'content', None), 'parts', None) or []
        text_chunks = []
        function_calls = []
        for part in parts:
            fc = getattr(part, 'function_call', None)
            if fc:
                function_calls.append(fc)
                continue
            part_text = getattr(part, 'text', None)
            if isinstance(part_text, str):
                text_chunks.append(part_text)
        return "".join(text_chunks).strip(), function_calls

    async def generate_response(self, history_tuples, user_prompt, system_instructions, tool_executor):
        """
        Generates a response with a bounded multi-round tool loop:
        request -> collect ALL function_call parts -> validate via the tool
        gateway -> execute -> send function_response parts back -> request
        again. Stops on plain text, or forces one final tools-free summary
        when the round budget is exhausted (same semantics as the shared
        OpenAI-compatible loop).
        """
        if not self.model: return "Gemini is not configured."

        gemini_history = [{'role': 'model' if role == 'ai' else 'user', 'parts': [text]} for role, text in history_tuples]

        tools_config = self.get_tools_schema()
        # System instructions passed via the proper parameter, NOT concatenated
        # into the user turn.
        chat_model = genai.GenerativeModel(
            config.GEMINI_MODEL,
            tools=tools_config,
            system_instruction=system_instructions
        )
        chat_session = chat_model.start_chat(history=gemini_history)

        last_text = ""
        try:
            response = await chat_session.send_message_async(user_prompt)
        except Exception as e:
            logger.error(f"Gemini API Error: {e}")
            return "I'm having trouble connecting to my brain right now."

        for round_no in range(1, MAX_MODEL_ROUNDS + 1):
            text, function_calls = self._extract_candidate(response)
            if text:
                last_text = text

            if not function_calls:
                # Plain text answer (or an empty/blocked candidate): done.
                return text or last_text or "..."

            # Execute every requested function call through the gateway.
            response_parts = []
            for fc in function_calls:
                tool_name = fc.name
                logger.info(f"Gemini requested tool: {tool_name}")
                try:
                    tool_args = dict(fc.args or {})
                except (TypeError, ValueError):
                    tool_args = None

                ok, payload = validate_tool_call(tool_name, tool_args)
                if not ok:
                    # Error string fed back so the model can repair the call.
                    tool_result = payload
                else:
                    try:
                        tool_result = await tool_executor(tool_name, payload)
                    except Exception as e:
                        logger.exception(f"Gemini tool execution error ({tool_name}): {e}")
                        tool_result = f"Error executing tool '{tool_name}': {e}"

                response_parts.append(glm_content.Part(
                    function_response=glm_content.FunctionResponse(
                        name=tool_name,
                        response={"result": str(tool_result)}
                    )
                ))

            if round_no == MAX_MODEL_ROUNDS:
                # Budget exhausted mid-tool-calls: one final call WITHOUT tools.
                logger.warning(f"Gemini hit the {MAX_MODEL_ROUNDS}-round tool budget; forcing a final summary.")
                try:
                    await chat_session.send_message_async(response_parts)
                    summary_model = genai.GenerativeModel(
                        config.GEMINI_MODEL,
                        system_instruction=system_instructions
                    )
                    summary_session = summary_model.start_chat(history=list(chat_session.history))
                    final = await summary_session.send_message_async(FINAL_SUMMARY_NUDGE)
                    final_text, final_calls = self._extract_candidate(final)
                    if final_calls:
                        final_text = final_text or last_text
                    return final_text or last_text or "..."
                except Exception as e:
                    logger.error(f"Gemini final-summary error: {e}")
                    return last_text or "I did a few things but ran out of rounds to phrase a reply - try again?"

            try:
                response = await chat_session.send_message_async(response_parts)
            except Exception as e:
                logger.error(f"Gemini API Error during tool loop: {e}")
                return last_text or "I'm having trouble connecting to my brain right now."

        return last_text or "..."
