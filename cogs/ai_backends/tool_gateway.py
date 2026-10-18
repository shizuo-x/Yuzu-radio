# ./discord-radio-bot-modular/cogs/ai_backends/tool_gateway.py
"""
Hand-rolled validation gateway for AI tool calls (no external deps).

Every backend MUST route model tool calls through validate_tool_call() before
execution. Unknown tools or invalid arguments produce a short, actionable
error STRING that is fed back to the model as the tool result so it can
repair the call on the next round (bounded by the loop's round budget).
"""

import datetime
import logging

logger = logging.getLogger('discord_bot.ai_backends.tool_gateway')

MAX_SEARCH_LIMIT = 10
DEFAULT_SEARCH_LIMIT = 5
MAX_ORDINAL = 10


def _as_text(value):
    """Coerces a value to a stripped non-empty string, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    text = str(value).strip()
    return text or None


def _v_no_args(args):
    return True, {}


def _v_search_radio(args):
    normalized = {}
    for key in ('query', 'country', 'tag'):
        value = _as_text(args.get(key))
        if value is None and args.get(key) is not None:
            return False, f"Invalid {key} argument: expected non-empty text."
        if value is not None:
            normalized[key] = value

    raw_limit = args.get('limit')
    if raw_limit is None:
        normalized['limit'] = DEFAULT_SEARCH_LIMIT
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return False, f"Invalid limit argument: expected an integer 1-{MAX_SEARCH_LIMIT}."
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            return False, f"Invalid limit argument: expected an integer 1-{MAX_SEARCH_LIMIT}."
        normalized['limit'] = limit
    return True, normalized


def _v_play_radio(args):
    station_id = _as_text(args.get('station_id'))
    station_name = _as_text(args.get('station_name'))
    if station_id is None and station_name is None:
        return False, "Invalid play_radio arguments: provide station_id (from search results) or station_name (a name or an ordinal '1'-'10' from the last search)."
    normalized = {}
    if station_id is not None:
        normalized['station_id'] = station_id
    if station_name is not None:
        normalized['station_name'] = station_name
    return True, normalized


def _v_set_reminder(args):
    message = _as_text(args.get('message'))
    if message is None:
        return False, "Invalid message argument: expected non-empty text."
    due_iso = _as_text(args.get('due_at_utc_iso'))
    if due_iso is None:
        return False, "Invalid due_at_utc_iso argument: expected an ISO 8601 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)."
    try:
        datetime.datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
    except ValueError:
        return False, "Invalid due_at_utc_iso argument: expected an ISO 8601 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)."
    return True, {'message': message, 'due_at_utc_iso': due_iso}


def _v_delete_reminder(args):
    raw_id = args.get('reminder_id')
    try:
        reminder_id = int(raw_id)
    except (TypeError, ValueError):
        return False, "Invalid reminder_id argument: expected an integer."
    return True, {'reminder_id': reminder_id}


TOOL_VALIDATORS = {
    'play_radio': _v_play_radio,
    'search_radio': _v_search_radio,
    'stop_radio': _v_no_args,
    'leave_voice': _v_no_args,
    'now_playing': _v_no_args,
    'set_reminder': _v_set_reminder,
    'list_reminders': _v_no_args,
    'delete_reminder': _v_delete_reminder,
}

TOOL_ALLOWLIST = sorted(TOOL_VALIDATORS.keys())


def validate_tool_call(tool_name, raw_args):
    """
    Validates a model-requested tool call.

    Returns (ok, payload):
      ok=True  -> payload is the normalized args dict, safe to execute.
      ok=False -> payload is a short actionable error STRING to feed back
                  to the model as the tool result for repair.
    """
    if tool_name not in TOOL_VALIDATORS:
        return False, f"Invalid tool '{tool_name}': unknown tool. Allowed tools: {', '.join(TOOL_ALLOWLIST)}."
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        return False, f"Invalid arguments for '{tool_name}': expected an object."
    try:
        return TOOL_VALIDATORS[tool_name](raw_args)
    except Exception as e:
        logger.exception(f"Validator for '{tool_name}' crashed: {e}")
        return False, f"Invalid arguments for '{tool_name}': {e}"
