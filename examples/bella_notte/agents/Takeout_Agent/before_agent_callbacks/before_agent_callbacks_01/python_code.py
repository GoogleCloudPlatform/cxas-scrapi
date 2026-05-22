# pylint: disable=invalid-name,undefined-variable,unused-argument,broad-exception-caught,line-too-long
"""Before-agent callback — SM init and config resolution.

FRAMEWORK CODE — fully generic across all agents.
Config-driven: reads config_id from agent_config_map variable.
Prompt assembly has moved to before_model_callback.
"""

import json as json_lib
import logging
from typing import Any, Optional


_SM_KEY = "sm"

_LEVEL_MAP = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
              "WARN": logging.WARNING, "ERROR": logging.ERROR}
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_logger = logging.getLogger("slot_filling.before_agent")


def _log(sm, tag, level="INFO", **data):
  """Emit structured log entry; append to sm["_log"].

  Args:
    sm: Session state machine dict (callback_context.state).
    tag: Short label identifying the log event.
    level: Severity — DEBUG, INFO, WARN, or ERROR.
    **data: Arbitrary key-value payload for the log entry.
  """
  min_level = sm.get("_log_level", "INFO")
  if _LEVEL_ORDER.get(level, 1) < _LEVEL_ORDER.get(min_level, 1):
    return
  entry = {"src": "before_agent", "tag": tag, "level": level,
           "data": {k: v for k, v in data.items() if v is not None}}
  _logger.log(_LEVEL_MAP.get(level, logging.INFO),
              json_lib.dumps(entry, default=str))
  sm.setdefault("_log", []).append(entry)


_SM_DEFAULTS = {
    "filled": {},
    "pending": {},
    "status": "in_progress",
    "task_results": {},
}


def _ensure_sm_initialized(sm: dict[str, Any]) -> None:
  if sm.get("_initialized"):
    return
  for key, value in _SM_DEFAULTS.items():
    sm.setdefault(key, value)
  sm["_initialized"] = True


def _resolve_config_id(callback_context):
  """Derive config_id from the agent_config_map variable + transfer events.

  Args:
    callback_context: CES CallbackContext with state and events.

  Returns:
    Tuple of (config_id, source) where source describes how it was resolved.
  """
  agent_name = None
  for event in reversed(callback_context.events):
    for part in (event.parts() or []):
      fc = getattr(part, "function_call", None)
      if fc and fc.name == "transfer_to_agent":
        agent_name = fc.args.get("agent_name") or fc.args.get("agent")
        break
    if agent_name:
      break

  if agent_name:
    raw_map = callback_context.state.get("agent_config_map", "{}")
    try:
      config_map = (
          json_lib.loads(raw_map) if isinstance(raw_map, str) else raw_map
      )
    except Exception:
      config_map = {}
    config_id = config_map.get(agent_name)
    if config_id:
      callback_context.state["_active_config_id"] = config_id
      return config_id, "transfer_event"

  cached = callback_context.state.get("_active_config_id")
  if cached:
    return cached, "cached"

  raw_map = callback_context.state.get("agent_config_map", "{}")
  try:
    config_map = (
        json_lib.loads(raw_map) if isinstance(raw_map, str) else raw_map
    )
  except Exception:
    config_map = {}
  if len(config_map) == 1:
    config_id = next(iter(config_map.values()))
    callback_context.state["_active_config_id"] = config_id
    return config_id, "single_entry_map"

  return None, None


def before_agent_callback(
    callback_context: CallbackContext,
) -> Optional[Content]:
  """Runs once per user turn before static variable substitution."""
  # Clear any pending transfer flag since we have successfully arrived!
  callback_context.state.pop("_pending_transfer", None)

  sm = callback_context.state.get(_SM_KEY, {})
  _ensure_sm_initialized(sm)

  config_id, config_source = _resolve_config_id(callback_context)

  callback_context.state["_active_sm_key"] = _SM_KEY
  if config_id:
    _log(sm, "config_resolved", config_id=config_id, source=config_source)
    callback_context.state["_active_config_id"] = config_id

  # ── Deferred rejection ───────────────────────────────────────
  if "_rejection_snapshot" in sm:
    snapshot = sm.pop("_rejection_snapshot")
    sm.pop("_rejection_requested", None)
    sm["_progress_turns"] = 0
    sm.pop("_readback_stall", None)
    sm.pop("_active_readback", None)
    pending = sm.get("pending", {})
    for k in snapshot:
      pending.pop(k, None)
    sm["pending"] = pending
    _log(sm, "rejection_applied", slots=list(snapshot.keys()))

  callback_context.state[_SM_KEY] = sm

  return None
