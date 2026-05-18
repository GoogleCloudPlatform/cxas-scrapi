"""After-tool callback — setter and executor state management.

FRAMEWORK CODE — fully generic across all agents.
Config-driven: reads bootstrap config from SM
(stashed by before_model).
"""
# pylint: disable=undefined-variable

from typing import Any, Optional


_SM_KEY = "sm"


def after_tool_callback(
    tool: Tool,
    tool_input: dict[str, Any],  # pylint: disable=unused-argument
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
      callback_context.state[_SM_KEY] = sm
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
            sm.setdefault("_slot_errors", []).append(
                {"slot": mapped_slot,
                 "code": validation.get("error_code", "invalid")},
            )
            continue
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
      if response_data.get(success_key):
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
    sm.setdefault("_slot_errors", []).append(
        {"slot": slot_name, "code": error_code}
    )
    return None

  if response_data.get("stored"):
    value = response_data.get("value")

    slot_requires = sm.get("_slot_requires", {})
    for req in slot_requires.get(slot_name, []):
      if req not in filled:
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
        sm.setdefault("_slot_errors", []).append(
            {"slot": slot_name, "code": validation.get("error_code", "invalid")}
        )
        return None

    sm.setdefault("pending", {})[slot_name] = value

    inferred = response_data.get("inferred", {})
    for inf_slot, inf_value in inferred.items():
      if inf_slot not in sm.get("filled", {}):
        sm["pending"][inf_slot] = inf_value

    callback_context.state[_SM_KEY] = sm

  return None
