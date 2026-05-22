# pylint: disable=invalid-name,undefined-variable,unused-argument,broad-exception-caught,line-too-long
"""After-tool callback — setter and executor state management.

FRAMEWORK CODE — fully generic across all agents.
Config-driven: reads bootstrap config from SM (stashed by before_model).
"""

import json as json_lib
import logging
from typing import Any, Optional


_SM_KEY = "sm"

_LEVEL_MAP = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
              "WARN": logging.WARNING, "ERROR": logging.ERROR}
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_logger = logging.getLogger("slot_filling.after_tool")


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
  entry = {"src": "after_tool", "tag": tag, "level": level,
           "data": {k: v for k, v in data.items() if v is not None}}
  _logger.log(_LEVEL_MAP.get(level, logging.INFO),
              json_lib.dumps(entry, default=str))
  sm.setdefault("_log", []).append(entry)


def _get_current_agent_name(callback_context) -> str:
  """Derive current agent display name dynamically from active_config_id."""
  config_id = callback_context.state.get("_active_config_id")
  if not config_id:
    return ""
  raw_map = callback_context.state.get("agent_config_map", "{}")
  try:
    config_map = (
        json_lib.loads(raw_map) if isinstance(raw_map, str) else raw_map
    )
  except Exception:
    config_map = {}
  for name, cid in config_map.items():
    if cid == config_id:
      return name
  return ""


def after_tool_callback(
    tool: Tool,
    tool_input: dict[str, Any],
    callback_context: CallbackContext,
    tool_response: dict[str, Any],
) -> Optional[dict[str, Any]]:
  """Route setter tool results to sm state."""
  sm = callback_context.state.get(_SM_KEY, {})

  # Bootstrap: config-driven pre-engine tool handling.
  # _bootstrap is stashed in SM by before_model_callback on first run.
  bootstrap = sm.get("_bootstrap")
  if bootstrap and tool.name == bootstrap["tool"]:
    response_data = tool_response.get("result", tool_response)
    if response_data.get("stored"):
      slot_name = bootstrap["slot"]
      if (bootstrap.get("reset_on_complete")
          and sm.get("status") in ("complete", "escalated")):
        sm["status"] = "in_progress"
        sm["task_results"] = {}
        sm["pending"] = {}
      sm.setdefault("filled", {})[slot_name] = response_data["value"]
      _log(sm, "bootstrap_stored",
           tool=tool.name, slot=slot_name, value=response_data["value"])
      callback_context.state[_SM_KEY] = sm

      target = response_data.get("target_agent")
      if target:
        current_agent = _get_current_agent_name(callback_context)
        if target != current_agent:
          _log(sm, "bootstrap_transfer", tool=tool.name, target=target)
          callback_context.state["_pending_transfer"] = target
    return None

  setter_slots = sm.get("_setter_slots", {})

  slot_name = setter_slots.get(tool.name)
  if slot_name is None:
    multi_setter_slots = sm.get("_multi_setter_slots", {})
    field_map = multi_setter_slots.get(tool.name)
    if field_map:
      response_data = tool_response.get("result", tool_response)
      filled = sm.get("filled", {})
      field_errors = response_data.get("field_errors", {})
      for field_name, error_code in field_errors.items():
        mapped_slot = field_map.get(field_name)
        if mapped_slot:
          _log(sm, "multi_setter_error", "WARN",
               tool=tool.name, field=field_name,
               slot=mapped_slot, code=error_code)
          sm.setdefault("_slot_errors", []).append(
              {"slot": mapped_slot, "code": error_code},
          )
      for field_name, value in response_data.get("values", {}).items():
        mapped_slot = field_map.get(field_name)
        if not mapped_slot:
          continue
        slot_requires = sm.get("_slot_requires", {})
        prereq_fail = False
        for req in slot_requires.get(mapped_slot, []):
          if req not in filled:
            _log(sm, "prereq_not_met", "WARN", slot=mapped_slot, missing=req)
            sm.setdefault("_slot_errors", []).append(
                {"slot": mapped_slot, "code": "prereq_not_met"},
            )
            prereq_fail = True
            break
        if prereq_fail:
          continue
        slot_validates = sm.get("_slot_validates", {})
        validation = slot_validates.get(mapped_slot)
        if validation:
          check_value = response_data.get(
              validation["response_field"], "",
          )
          against_raw = filled.get(validation["filled_slot"], "")
          valid_options = [t.strip() for t in against_raw.split(",")]
          if check_value not in valid_options:
            _log(sm, "validation_failed", "WARN", slot=mapped_slot,
                 code=validation.get("error_code", "invalid"))
            sm.setdefault("_slot_errors", []).append(
                {"slot": mapped_slot,
                 "code": validation.get("error_code", "invalid")},
            )
            continue
        _log(sm, "multi_setter_stored",
             tool=tool.name, field=field_name,
             slot=mapped_slot, value=value)
        sm.setdefault("pending", {})[mapped_slot] = value
      callback_context.state[_SM_KEY] = sm
      return None

    executor_tasks = sm.get("_executor_tasks", {})
    task_info = executor_tasks.get(tool.name)
    if task_info:
      response_data = tool_response.get("result", tool_response)
      task_results_map = sm.setdefault("task_results", {})
      task_results_map[task_info["task_name"]] = response_data

      success_key = task_info.get("success_check", "success")
      success = bool(response_data.get(success_key))
      _log(sm, "task_completed", "INFO" if success else "WARN",
           tool=tool.name, task=task_info["task_name"], success=success)
      if success:
        outputs = task_info.get("outputs", {})
        if all(k in response_data for k in outputs):
          filled_map = sm.setdefault("filled", {})
          for result_key, slot_nm in outputs.items():
            filled_map[slot_nm] = response_data[result_key]

      sm["_task_just_completed"] = task_info["task_name"]
    return None

  response_data = tool_response.get("result", tool_response)
  filled = sm.get("filled", {})

  if response_data.get("error"):
    error_code = response_data.get("error_code", "unknown")
    _log(sm, "setter_error", "WARN", tool=tool.name, slot=slot_name, code=error_code)
    sm.setdefault("_slot_errors", []).append(
        {"slot": slot_name, "code": error_code}
    )
    return None

  if response_data.get("stored"):
    value = response_data.get("value")

    slot_requires = sm.get("_slot_requires", {})
    for req in slot_requires.get(slot_name, []):
      if req not in filled:
        _log(sm, "prereq_not_met", slot=slot_name, missing=req)
        sm.setdefault("_slot_errors", []).append(
            {"slot": slot_name, "code": "prereq_not_met"}
        )
        return None

    slot_validates = sm.get("_slot_validates", {})
    validation = slot_validates.get(slot_name)
    if validation:
      check_value = response_data.get(validation["response_field"], "")
      against_raw = filled.get(validation["filled_slot"], "")
      valid_options = [t.strip() for t in against_raw.split(",")]
      if check_value not in valid_options:
        _log(sm, "validation_failed", "WARN", slot=slot_name,
             code=validation.get("error_code", "invalid"))
        sm.setdefault("_slot_errors", []).append(
            {"slot": slot_name, "code": validation.get("error_code", "invalid")}
        )
        return None

    _log(sm, "setter_stored", tool=tool.name, slot=slot_name, value=value)
    sm.setdefault("pending", {})[slot_name] = value

    inferred = response_data.get("inferred", {})
    if inferred:
      _log(sm, "inferred_slots", source_slot=slot_name, inferred=inferred)
    for inf_slot, inf_value in inferred.items():
      if inf_slot not in sm.get("filled", {}):
        sm["pending"][inf_slot] = inf_value

    callback_context.state[_SM_KEY] = sm

  return None
