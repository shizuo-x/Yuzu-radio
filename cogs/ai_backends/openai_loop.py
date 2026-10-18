# ./discord-radio-bot-modular/cogs/ai_backends/openai_loop.py
"""
Shared bounded multi-round tool loop for OpenAI-compatible chat APIs.

Used by BOTH deepseek.py and local.py. Contract:

  request -> collect ALL tool_calls -> validate each call through the
  tool_gateway -> execute via tool_executor -> append tool results to the
  message history -> request again. Stops when the model returns text with
  no tool calls, or when MAX_MODEL_ROUNDS is exhausted (then ONE final call
  WITHOUT tools asks the model to summarize).

None content is always guarded before any .strip()/access: a None content
WITH tool_calls is normal flow; a None content WITHOUT tool calls is
treated as an empty string.
"""

import json
import logging

from cogs.ai_backends.tool_gateway import validate_tool_call

logger = logging.getLogger('discord_bot.ai_backends.openai_loop')

MAX_MODEL_ROUNDS = 6
FINAL_SUMMARY_NUDGE = (
    "Tool round budget reached. Do not call any more tools. Summarize what "
    "happened above for the user now."
)


def build_tools_schema():
    """Defines the shared OpenAI-compatible tool schema (DeepSeek + local)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_radio",
                "description": (
                    "Searches the radio station database with structured filters. "
                    "Returns a numbered list; every line ends with a station_id "
                    "that play_radio accepts."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Free-text search term matched against station names, tags and countries."},
                        "country": {"type": "string", "description": "Filter by country (e.g. 'Malaysia', 'International')."},
                        "tag": {"type": "string", "description": "Filter by tag/genre (e.g. 'Rock', \"90's\", 'Jazz')."},
                        "limit": {"type": "integer", "description": "Max results to return, 1-10. Default 5."}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "play_radio",
                "description": (
                    "Plays a radio station. Prefer station_id from a previous "
                    "search_radio result; station_name fuzzy-matches, or may be "
                    "an ordinal '1'-'10' referring to the last search results in "
                    "this channel."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "station_id": {"type": "string", "description": "Exact station_id from a search_radio result (preferred)."},
                        "station_name": {"type": "string", "description": "Station name, fuzzy search term, or ordinal '1'-'10' from the last search results."}
                    },
                    "required": []
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


async def run_openai_tool_loop(*, http_session, api_url, headers, model, messages,
                               tool_executor, provider_label="OpenAI-compatible", timeout=60):
    """
    Runs the bounded multi-round tool loop against an OpenAI-compatible
    chat/completions endpoint. Returns the final text response (never raises).
    """
    tools_schema = build_tools_schema()
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools_schema,
        "tool_choice": "auto",
        "stream": False
    }
    last_text = ""

    for round_no in range(1, MAX_MODEL_ROUNDS + 1):
        async with http_session.post(api_url, headers=headers, json=payload, timeout=timeout) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"{provider_label} API Error (round {round_no}): {response.status} - {error_text}")
                return last_text or f"I encountered an error with my brain ({response.status})."
            data = await response.json()

        try:
            message = data['choices'][0]['message']
        except (KeyError, IndexError, TypeError):
            logger.error(f"{provider_label} returned a malformed response: {data}")
            return last_text or "I'm having trouble thinking right now."

        raw_content = message.get('content')
        # None-content guard: with tool_calls it's normal flow; without, it's ''.
        content = raw_content if isinstance(raw_content, str) else ""
        text = content.strip()
        if text:
            last_text = text

        tool_calls = message.get('tool_calls') or []
        if not tool_calls:
            # Plain text answer: done.
            return text

        # Append a sanitized assistant message (only known-safe keys).
        assistant_msg: dict = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        # Execute ALL requested tool calls through the validation gateway.
        for tool_call in tool_calls:
            try:
                function_name = tool_call['function']['name']
                arguments_str = tool_call['function'].get('arguments') or '{}'
                call_id = tool_call.get('id')
            except (KeyError, TypeError):
                messages.append({
                    "role": "tool", "tool_call_id": tool_call.get('id', ''),
                    "name": "?", "content": "Error: malformed tool call."
                })
                continue

            logger.info(f"{provider_label} requested tool: {function_name}")

            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                tool_result = "Error: invalid JSON in tool arguments. Re-issue the call with a valid JSON object."
            else:
                ok, payload_args = validate_tool_call(function_name, arguments)
                if not ok:
                    # Feed the error string back so the model can repair.
                    tool_result = payload_args
                else:
                    try:
                        tool_result = await tool_executor(function_name, payload_args)
                    except Exception as e:
                        logger.exception(f"{provider_label} tool execution error ({function_name}): {e}")
                        tool_result = f"Error executing tool '{function_name}': {e}"

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": function_name,
                "content": str(tool_result)
            })

        if round_no == MAX_MODEL_ROUNDS:
            # Budget exhausted mid-tool-calls: one final call WITHOUT tools.
            logger.warning(f"{provider_label} hit the {MAX_MODEL_ROUNDS}-round tool budget; forcing a final summary.")
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
            messages.append({"role": "user", "content": FINAL_SUMMARY_NUDGE})
            try:
                async with http_session.post(api_url, headers=headers, json=payload, timeout=timeout) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"{provider_label} final-summary error: {response.status} - {error_text}")
                        return last_text or "I did a few things but ran out of rounds to phrase a reply — try again?"
                    data = await response.json()
                final_content = data['choices'][0]['message'].get('content')
                final_text = final_content.strip() if isinstance(final_content, str) else ""
                return final_text or last_text or ""
            except Exception as e:
                logger.exception(f"{provider_label} final-summary exception: {e}")
                return last_text or "I did a few things but ran out of rounds to phrase a reply — try again?"

        # Loop again with the updated message history (tools still enabled).

    return last_text or ""
