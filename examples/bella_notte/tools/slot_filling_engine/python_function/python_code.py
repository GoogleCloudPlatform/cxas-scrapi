"""Slot-filling DAG engine — reusable across projects.

FRAMEWORK CODE — shared across all agents using the slot-filling engine.
Do not add agent-specific logic here; customize behavior
via the per-agent {config_id}_dag tool.

Takes config + state dict, runs one turn of the DAG engine,
returns an action dict. All state flows through the sm dict
passed in and returned. CES-agnostic: no CES types, no
LlmRequest/LlmResponse, no tool visibility mutations.

Called from the before_model_callback via:
  tools.slot_filling_engine({"input_data": {...}}).json()["result"]
"""

import copy
import datetime
import random
from typing import Any, Optional


# ═════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════


def _normalize_sources(source) -> list[str]:
  """Normalize slot source to a list."""
  if isinstance(source, list):
    return source
  return [source] if source else ["user"]


# ── Built-in readback formatters ──────────────────────────────────


def _format_date(v: str) -> str:
  """Format date as 'on Month Nth'."""
  try:
    dt = datetime.datetime.strptime(str(v), "%Y-%m-%d")
    day = dt.day
    suffix = (
        "th"
        if 11 <= day <= 13
        else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )
    return f"on {dt.strftime('%B')} {day}{suffix}"
  except (ValueError, TypeError):
    return f"on {v}"


def _format_time(v: str) -> str:
  """Format time as 'at H:MM AM/PM'."""
  try:
    parts = str(v).split(":")
    h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"at {h12}:{m:02d} {period}"
  except (ValueError, TypeError, IndexError):
    return f"at {v}"


def _format_prefix(v, text: str = "") -> str:
  """Format value with a text prefix."""
  return f"{text} {v}"


def _format_plural(v, one: str = "", other: str = "") -> str:
  """Format value with singular/plural unit."""
  n = int(v)
  return f"{n} {one if n == 1 else other}"


def _format_none_sub(v, default: str = "") -> str:
  """Substitute a default for none-like values."""
  if str(v).lower() in ("none", "no", "nothing"):
    return default
  return str(v)


_BUILTIN_FORMATTERS = {
    "date": _format_date,
    "time": _format_time,
}


def _resolve_exhaust_action(
    exhaust: dict[str, Any],
    filled: dict[str, Any],
) -> Optional[dict[str, Any]]:
  """Resolve on_exhaust 'then' to a function_call dict.

  Supports:
    "then": "escalate"  -> {"name": "escalate", "args": {}}
    "then": {"tool": "transfer", "args": {"name": "{guest_name}"}}
      -> args values with {slot} placeholders are filled from filled.

  Args:
    exhaust: The on_exhaust config dict with optional 'then' key.
    filled: Currently filled slot values for placeholder resolution.

  Returns:
    A function_call dict {"name": ..., "args": {...}} or None.
  """
  then = exhaust.get("then")
  if not then:
    return None
  if isinstance(then, str):
    return {"name": then, "args": {}}
  if isinstance(then, dict):
    tool = then.get("tool", "")
    raw_args = dict(then.get("args", {}))
    for k, v in raw_args.items():
      if isinstance(v, str):
        try:
          raw_args[k] = v.format(**filled)
        except KeyError:
          pass
    return {"name": tool, "args": raw_args}
  return None


def _substitute_response(
    response: list[dict[str, Any]],
    filled: dict[str, Any],
) -> list[dict[str, Any]]:
  """Recursively substitute {slot_name} in all string values.

  Supports ``options_from`` on chip objects: splits a filled slot
  value by ", " to generate individual chip options.  Optional
  ``event_name`` on the same object wraps each option with an event.

  Args:
    response: List of response part dicts to substitute into.
    filled: Mapping of slot names to their filled values.

  Returns:
    A deep copy of response with all substitutions applied.
  """
  def _sub(obj):
    if isinstance(obj, str):
      try:
        return obj.format(**filled)
      except KeyError:
        return obj
    elif isinstance(obj, dict):
      result = {k: _sub(v) for k, v in obj.items()}
      if "options_from" in result:
        source_slot = result.pop("options_from")
        event_name = result.pop("event_name", None)
        source_val = filled.get(source_slot, "")
        if isinstance(source_val, list):
          items = source_val
        else:
          items = str(source_val).split(",")
        options = []
        for v in items:
          v = str(v).strip()
          if not v:
            continue
          opt = {"text": v}
          if event_name:
            opt["event"] = {
                "name": event_name,
                "parameters": {event_name: v},
            }
          options.append(opt)
        result["options"] = options
      return result
    elif isinstance(obj, list):
      return [_sub(v) for v in obj]
    return obj
  return _sub(copy.deepcopy(response))


def _resolve_response(
    definition: dict[str, Any], field: str, filled: dict[str, Any],
    channel: str = "",
) -> Optional[list[dict[str, Any]]]:
  """Get response parts with channel override and variable substitution.

  Args:
    definition: Slot or task definition dict.
    field: Response field name (e.g. 'response', 'then_response').
    filled: Filled slot values for placeholder substitution.
    channel: Optional channel for channel-specific overrides.

  Returns:
    List of response part dicts, or None if no response defined.
  """
  channel_field = f"channel_{field}"
  channel_responses = definition.get(channel_field, {})
  response = (
      channel_responses.get(channel) if channel
      else None
  ) or definition.get(field)
  if response:
    return _substitute_response(response, filled)
  return None


# ── Config compilation ────────────────────────────────────────────

_SAFE_EVAL_GLOBALS = {
    "__builtins__": {
        "int": int, "str": str, "len": len,
        "float": float, "bool": bool,
    },
}

_COMPILED_CONFIGS = {}
_DEBUG = False
_DEBUG_LOG = []


def _compile_formatter(fmt):
  """Resolve a format spec to a callable."""
  if fmt is None:
    return None
  if callable(fmt):
    return fmt
  if isinstance(fmt, str):
    if fmt in _BUILTIN_FORMATTERS:
      return _BUILTIN_FORMATTERS[fmt]
    return eval(fmt, _SAFE_EVAL_GLOBALS)  # pylint: disable=eval-used
  if isinstance(fmt, dict):
    fmt_type = fmt.get("type", "")
    if fmt_type == "prefix":
      text = fmt["text"]
      return lambda v, _t=text: _format_prefix(v, text=_t)
    if fmt_type == "plural":
      one, other = fmt["one"], fmt["other"]
      return lambda v, _o=one, _p=other: _format_plural(v, one=_o, other=_p)
    if fmt_type == "none_sub":
      default = fmt["default"]
      return lambda v, _d=default: _format_none_sub(v, default=_d)
    if fmt_type in _BUILTIN_FORMATTERS:
      return _BUILTIN_FORMATTERS[fmt_type]
    raise ValueError(f"Unknown readback_fmt type: {fmt_type!r}")
  return None


def _compile_config(config: dict[str, Any]) -> dict[str, Any]:
  """Compile string conditions and formatters to callables."""
  compiled_slots = []
  for slot_def in config["slots"]:
    slot = dict(slot_def)
    cond = slot.get("condition")
    if isinstance(cond, str):
      slot["condition"] = eval(  # pylint: disable=eval-used
          cond, _SAFE_EVAL_GLOBALS,
      )
    slot["readback_fmt"] = _compile_formatter(slot.get("readback_fmt"))
    compiled_slots.append(slot)
  compiled_tasks = []
  for task_def in config["tasks"]:
    task = dict(task_def)
    cond = task.get("condition")
    if isinstance(cond, str):
      task["condition"] = eval(  # pylint: disable=eval-used
          cond, _SAFE_EVAL_GLOBALS,
      )
    compiled_tasks.append(task)
  compiled = dict(config)
  compiled["slots"] = compiled_slots
  compiled["tasks"] = compiled_tasks
  return compiled


def _validate_config(config):
  """Validate config structure and references."""
  slots = config["slots"]
  tasks = config["tasks"]
  slot_map = {s["name"]: s for s in slots}

  slot_names = [s["name"] for s in slots]
  slot_set = set(slot_names)
  task_names = {t["name"] for t in tasks}

  if len(slot_names) != len(slot_set):
    dupes = [n for n in slot_names if slot_names.count(n) > 1]
    raise ValueError(f"Duplicate slot names: {set(dupes)}")

  for task in tasks:
    for inp in task["inputs"]:
      if inp not in slot_set:
        raise ValueError(
            f"Task '{task['name']}' input '{inp}' not in slots"
        )
    for slot_name in task.get("outputs", {}).values():
      if slot_name not in slot_set:
        raise ValueError(
            f"Task '{task['name']}' output '{slot_name}'"
            " not in slots"
        )
    if not task.get("tool"):
      raise ValueError(
          f"Task '{task['name']}' has no 'tool' key"
      )
    for req in task.get("requires", []):
      if req not in slot_set:
        raise ValueError(
            f"Task '{task['name']}' requires '{req}'"
            " not in slots"
        )
    condition = task.get("condition")
    if (condition is not None
        and not callable(condition)
        and not isinstance(condition, str)):
      raise ValueError(
          f"Task '{task['name']}' condition must be callable or"
          f" string, got {type(condition)}"
      )

  for slot in slots:
    sources = _normalize_sources(slot.get("source", "user"))
    for source in sources:
      if source.startswith("task:"):
        src_task = source[5:]
        if src_task not in task_names:
          raise ValueError(
              f"Slot '{slot['name']}' references unknown task"
              f" '{src_task}'"
          )
    condition = slot.get("condition")
    if (condition is not None
        and not callable(condition)
        and not isinstance(condition, str)):
      raise ValueError(
          f"Slot '{slot['name']}' condition must be callable or"
          f" string, got {type(condition)}"
      )
    if "announce" in sources:
      if not slot.get("message"):
        raise ValueError(
            f"Announce slot '{slot['name']}'"
            " requires 'message'"
        )
      if slot.get("setter"):
        raise ValueError(
            f"Announce slot '{slot['name']}'"
            " must not have 'setter'"
        )
    for req in slot.get("requires", []):
      if req not in slot_set:
        raise ValueError(
            f"Slot '{slot['name']}' requires unknown"
            f" '{req}'"
        )

  def _has_cycle(name, visited, stack):
    visited.add(name)
    stack.add(name)
    slot_def = slot_map.get(name)
    if slot_def:
      for req in slot_def.get("requires", []):
        if req not in visited:
          if _has_cycle(req, visited, stack):
            return True
        elif req in stack:
          return True
    stack.discard(name)
    return False

  visited, stack = set(), set()
  for name in slot_names:
    if name not in visited:
      if _has_cycle(name, visited, stack):
        raise ValueError(
            f"Circular requires involving '{name}'"
        )


# ═════════════════════════════════════════════════════════════════════
# SLOT STATE HELPERS
# ═════════════════════════════════════════════════════════════════════


def _is_slot_active(slot_def, filled):
  """Check if a conditional slot is active."""
  condition = slot_def.get("condition")
  if condition is None:
    return True
  try:
    return bool(condition(filled))
  except Exception:  # pylint: disable=broad-except
    return True


def _is_task_active(task_def, filled):
  """Check if a conditional task is active."""
  condition = task_def.get("condition")
  if condition is None:
    return True
  try:
    return bool(condition(filled))
  except Exception:  # pylint: disable=broad-except
    return True


def _resolve_formatter(fmt):
  """Resolve a compiled formatter to a callable."""
  if fmt is None:
    return None
  if callable(fmt):
    return fmt
  if isinstance(fmt, str):
    return _BUILTIN_FORMATTERS.get(fmt)
  return None


# ═════════════════════════════════════════════════════════════════════
# AFFIRMATIVE DETECTION & CONFIRMATION
# ═════════════════════════════════════════════════════════════════════
#
# During readback, the engine asks the user to confirm pending slots.
# Two confirmation paths handle user replies:
#
# AUTO-CONFIRM (_is_affirmative → _try_auto_confirm):
#   The entire message is a pure affirmative ("yes", "correct").
#   The engine preempts with a confirm_pending tool call — the LLM
#   never runs. Deterministic, fast, no risk of the LLM ignoring
#   the confirmation.
#
# INLINE-CONFIRM (_starts_affirmative → _apply_inline_confirm):
#   The message starts with an affirmative but has additional
#   content ("Yea, also my wife has a shellfish allergy"). The
#   engine silently confirms pending slots (moving them to filled),
#   defers the task fire, and lets the LLM run with collection
#   instructions so it can call setters for the new content. The
#   before_model_callback swaps readback instructions for collection
#   instructions in the system instruction when inline_confirmed is
#   set, since before_agent_callback already baked in readback-only
#   instructions earlier in the turn.
# ═════════════════════════════════════════════════════════════════════


_AFFIRMATIVES = frozenset({
    "yes", "yeah", "yea", "yep", "yup", "yah", "ya",
    "correct", "right",
    "sure", "sounds good", "looks good",
    "ok", "okay", "perfect", "great", "exactly",
    "confirmed", "confirm",
    "absolutely", "definitely", "certainly",
    "that's right", "that is right",
    "that's correct", "that is correct",
    "looks right", "that looks right", "that sounds right",
})

_CORRECTION_SIGNALS = frozenset({
    "but", "actually", "wait", "change", "different",
    "instead", "not", "no", "wrong", "except", "however",
    "although", "though",
})

_STRIP_PUNCT = str.maketrans("", "", ".,;:!?\"'")


def _is_affirmative(text: str) -> bool:
  """True when the ENTIRE message is a pure affirmative.

  Used for auto-confirm: the user said only "yes" / "correct" /
  "sure, sounds good" with no additional content. The engine can
  preempt with a confirm_pending tool call without running the LLM.

  Allows up to 5 words so "yes that looks right" matches, but
  rejects anything containing a correction signal ("but", "wait",
  "actually", etc.).

  Args:
    text: The user's message text.

  Returns:
    True if the message is a pure affirmative.
  """
  if not text:
    return False
  normalized = text.lower().strip().rstrip(".,!? ")
  if normalized in _AFFIRMATIVES:
    return True
  words = [w.translate(_STRIP_PUNCT) for w in normalized.split()]
  if len(words) <= 5 and words and words[0] in _AFFIRMATIVES:
    return not any(w in _CORRECTION_SIGNALS for w in words[1:])
  return False


def _starts_affirmative(text: str) -> bool:
  """True when the message STARTS with an affirmative but has more content.

  Used for inline-confirm: the user confirmed AND added new info in
  the same message ("Yea, also my wife has a shellfish allergy").
  The engine silently confirms pending slots and lets the LLM run
  to process the additional content (e.g. calling a setter).

  Only checks the first 4 words for correction signals, since the
  rest of the message is new content, not a retraction.

  Args:
    text: The user's message text.

  Returns:
    True if the message starts with an affirmative and has more content.
  """
  if not text:
    return False
  normalized = text.lower().strip().rstrip(".,!? ")
  if normalized in _AFFIRMATIVES:
    return True
  words = [w.translate(_STRIP_PUNCT) for w in normalized.split()]
  if not words or words[0] not in _AFFIRMATIVES:
    return False
  return not any(w in _CORRECTION_SIGNALS for w in words[1:4])


# ═════════════════════════════════════════════════════════════════════
# LOGGING
# ═════════════════════════════════════════════════════════════════════


def _log(tag, **data):
  """Emit a structured log line."""
  parts = " ".join(f"{k}={v!r}" for k, v in data.items())
  line = f"[slot-filling:{tag}]" + (f" {parts}" if parts else "")
  print(line)
  if _DEBUG:
    _DEBUG_LOG.append(line)


def _log_progress(filled, pending, last_state):
  """Log slot state changes since last turn."""
  last_filled = last_state.get("filled", {})
  last_pending = last_state.get("pending", {})
  confirmed = sorted(
      k for k in set(last_pending) if k in filled and k not in last_filled
  )
  task_out = {
      k: filled[k]
      for k in set(filled) - set(last_filled) - set(confirmed)
  }
  new_pending = {k: pending[k] for k in set(pending) - set(last_pending)}
  rejected = sorted(
      k for k in set(last_pending) if k not in pending and k not in filled
  )
  if not (confirmed or task_out or new_pending or rejected):
    return
  _log("progress",
       **({} if not new_pending else {"pending+": new_pending}),
       **({} if not confirmed else {"confirmed": confirmed}),
       **({} if not task_out else {"task+": task_out}),
       **({} if not rejected else {"rejected": rejected}))


def _log_invoke(n, phase, filled, pending, fresh_pending, hidden, *,
                asking=None, reading_back=None, fired=None, done=False,
                preempted=None, deferred=None):
  """Log a DAG invocation with current state."""
  _log("invoke",
       n=n, phase=phase,
       **({} if not filled else {"filled": sorted(filled)}),
       **({} if not pending else {"pending": sorted(pending)}),
       **({} if not deferred else {"deferred": sorted(deferred)}),
       **({} if not fresh_pending else {"fresh": True}),
       **({} if not hidden else {"hidden": sorted(hidden)}),
       **({} if asking is None else {"asking": asking[:80]}),
       **({} if reading_back is None else {"rb": reading_back[:80]}),
       **({} if fired is None else {"fired": fired}),
       **({} if not done else {"done": True}),
       **({} if preempted is None else {"preempted": preempted[:80]}))


# ═════════════════════════════════════════════════════════════════════
# DAG ENGINE COMPONENTS
# ═════════════════════════════════════════════════════════════════════


def _handle_state_change(
    sm: dict[str, Any],
    filled: dict[str, Any], pending: dict[str, Any],
    last_state: dict[str, Any],
) -> None:
  """Reset stall counters on state changes."""
  current_state = {
      "filled": filled, "pending": pending,
      "deferred": sm.get("deferred", {}),
  }
  if current_state == last_state:
    return

  if not sm.pop("_auto_confirm_pending", False):
    sm["_steer_back_turns"] = 0

  retries = sm.get("_retries", {})
  last_filled = last_state.get("filled", {})
  last_pending = last_state.get("pending", {})
  for name in set(filled) - set(last_filled):
    retries.pop(f"slot:{name}", None)
  for name in set(pending) - set(last_pending):
    retries.pop(f"slot:{name}", None)
  if last_pending and not pending:
    retries.pop("readback", None)

  _log_progress(filled, pending, last_state)


def _auto_promote_and_route(
    slots, tasks, task_results, slot_map,
    filled: dict[str, Any], pending: dict[str, Any],
    deferred: dict[str, Any],
) -> list[str]:
  """Promote non-readback pending slots and route deferred.

  Args:
    slots: List of slot definition dicts.
    tasks: List of task definition dicts.
    task_results: Dict of task name to result.
    slot_map: Dict mapping slot name to slot definition.
    filled: Currently filled slot values (mutated in place).
    pending: Currently pending slot values (mutated in place).
    deferred: Currently deferred slot values (mutated in place).

  Returns:
    The names of slots promoted from deferred to pending.
  """
  readback_set = {
      s["name"] for s in slots if s.get("requires_readback")
  }
  for name in [k for k in pending if k not in readback_set]:
    filled[name] = pending.pop(name)

  deferred_eligible = _compute_deferred_eligible(
      slots, tasks, task_results, slot_map,
  )
  for name in [k for k in pending if k in deferred_eligible]:
    deferred[name] = pending.pop(name)
  return _check_deferred_groups(
      tasks, filled, pending, deferred, task_results, slot_map,
  )


def _deactivate_conditional_slots(
    slots, filled: dict[str, Any], pending: dict[str, Any],
    deferred: dict[str, Any], retries: dict[str, Any],
) -> None:
  """Remove slots whose conditions are no longer met."""
  for slot_def in slots:
    if "condition" not in slot_def:
      continue
    name = slot_def["name"]
    if not _is_slot_active(slot_def, filled):
      if name in filled:
        filled.pop(name)
        retries.pop(f"slot:{name}", None)
        _log("slot_deactivated", slot=name, source="filled")
      if name in pending:
        pending.pop(name)
        _log("slot_deactivated", slot=name, source="pending")
      if name in deferred:
        deferred.pop(name)
        _log("slot_deactivated", slot=name, source="deferred")


def fill_slots(
    sm: dict[str, Any],
    config: dict[str, Any],
    values: dict[str, Any],
    skip_readback: bool = True,
) -> dict[str, list[str]]:
  """Fill slots programmatically.

  Args:
    sm: The state machine dict.
    config: The compiled DAG config from _compile_config().
    values: Dict of {slot_name: value} to fill.
    skip_readback: If True (default), write directly to
      filled (no user confirmation). If False, write to
      pending (triggers readback/confirm flow).

  Returns:
    {"filled": [names written], "skipped": [names skipped]}.
  """
  slot_map = {s["name"]: s for s in config["slots"]}
  filled = sm.setdefault("filled", {})
  result = {"filled": [], "skipped": []}
  for name, value in values.items():
    slot_def = slot_map.get(name)
    if not slot_def:
      result["skipped"].append(name)
      continue
    if name in filled:
      result["skipped"].append(name)
      continue
    if not _is_slot_active(slot_def, filled):
      result["skipped"].append(name)
      continue
    if skip_readback:
      filled[name] = value
    else:
      sm.setdefault("pending", {})[name] = value
    _log("fill_slots", slot=name, value=value)
    result["filled"].append(name)
  return result


def _try_auto_confirm(
    phase: str, last_user_text: str, sm: dict[str, Any],
) -> Optional[dict[str, Any]]:
  """Handle affirmative replies during readback confirmation.

  Two paths depending on message length:

  AUTO-CONFIRM -- pure affirmative ("yes", "correct"):
    Returns a preemptive confirm_pending tool call. The LLM
    never runs; pending slots move to filled deterministically.

  INLINE-CONFIRM -- affirmative + new content ("Yea, also my
    wife has a shellfish allergy"):
    Sets _inline_confirm flag on sm and returns None. The
    engine processes this flag later in _apply_inline_confirm:
    pending slots are silently confirmed, the task fire is
    deferred, and the LLM runs with collection instructions
    so it can call setters for the new content.

  Args:
    phase: Current engine phase.
    last_user_text: The user's last message text.
    sm: State machine dict (mutated in place).

  Returns:
    A preemptive action dict for auto-confirm, or None.
  """
  if phase != "awaiting_confirmation" or not last_user_text:
    return None
  if _is_affirmative(last_user_text):
    _log("auto_confirm", user_msg=last_user_text)
    sm["_auto_confirm_pending"] = True
    return {
        "hide_tools": [],
        "preempt": True,
        "force_preempt": True,
        "function_call": {"name": "confirm_pending", "args": {}},
        "message": "",
    }
  if _starts_affirmative(last_user_text):
    sm["_inline_confirm"] = True
    return None
  return None


def _apply_inline_confirm(
    sm: dict[str, Any],
    filled: dict[str, Any],
    pending: dict[str, Any],
    phase: str,
    fresh_pending: bool,
) -> tuple[bool, str, bool]:
  """Apply the inline-confirm flag set by _try_auto_confirm.

  When the user's message started with an affirmative but
  contained additional content (e.g. "Yea, also note the
  shellfish allergy"), _try_auto_confirm set sm["_inline_confirm"]
  = True and returned None so the engine continues normally.

  This function consumes that flag and:
  1. Moves all pending slots to filled (the confirmation).
  2. Resets phase to "collection" so the LLM gets collection
     instructions (not readback) and can call setters for the
     new content in the user's message.
  3. Returns inline_confirmed=True, which causes the task
     fire to be deferred and the before_model_callback to swap
     readback instructions for collection instructions.

  Args:
    sm: State machine dict (mutated in place).
    filled: Currently filled slot values (mutated in place).
    pending: Currently pending slot values (mutated in place).
    phase: Current engine phase.
    fresh_pending: Whether pending was just populated this turn.

  Returns:
    Tuple of (inline_confirmed, phase, fresh_pending).
  """
  if not sm.pop("_inline_confirm", False) or not pending:
    return False, phase, fresh_pending
  committed = list(pending.keys())
  filled.update(pending)
  pending.clear()
  sm["_readback_transition"] = True
  _log("auto_confirm_inline", committed=committed)
  return True, "collection", False


def _handle_steer_back(
    sm: dict[str, Any], last_user_text: str,
    steer_back_cfg: dict[str, Any],
    slots, filled: dict[str, Any], pending: dict[str, Any],
    deferred: dict[str, Any], slot_map: dict[str, Any],
    fresh_pending: bool, hide_tools: list[str],
    inv_n: int,
    channel: str = "",
) -> Optional[dict[str, Any]]:
  """3-tier steer-back: soft SI → hard preempt → escalate."""
  if not last_user_text:
    return None

  # After a hard steer preemption, yield one turn to the LLM
  # so it can process the user's response to the forced question.
  if sm.pop("_hard_steer_yielded", False):
    _log("steer_back_yield", turns=sm.get("_steer_back_turns", 0))
    return None

  turns = sm.get("_steer_back_turns", 0) + 1
  sm["_steer_back_turns"] = turns

  soft_after = steer_back_cfg.get("soft_after", 2)
  hard_after = steer_back_cfg.get("hard_after", 4)
  escalate_after = steer_back_cfg.get("escalate_after", 6)

  if turns < soft_after:
    return None

  if pending:
    target = (
        "read back the pending values and ask"
        " the guest to confirm"
    )
  else:
    next_q = _find_next_question(
        slots, filled, pending, slot_map,
        deferred=deferred, channel=channel,
    )
    target = next_q.get(
        "system_message",
        "ask for the next piece of information",
    )

  if turns >= escalate_after:
    _log("steer_back_escalate", turns=turns)
    exhaust = steer_back_cfg.get("on_exhaust", {})
    fc = _resolve_exhaust_action(exhaust, filled)
    if fc:
      sm["status"] = "escalated"
    msg = exhaust.get("say", "Please call us for help.")
    _log_invoke(inv_n, "steer_back_escalate", filled, pending,
                fresh_pending, hide_tools, preempted=msg,
                deferred=deferred)
    result = {
        "hide_tools": hide_tools, "preempt": True,
        "force_preempt": True, "message": msg,
    }
    if fc:
      result["function_call"] = fc
    resp = _resolve_response(exhaust, "response", filled, channel)
    if resp:
      result["response"] = resp
    return result

  if turns >= hard_after:
    if pending:
      hint = _build_readback_hint(
          slots, pending, filled, True,
      )
      msg = f"Just to confirm — {hint}. Is that correct?"
    else:
      next_q = _find_next_question(
          slots, filled, {}, slot_map,
          deferred=deferred, channel=channel,
      )
      msg = next_q.get("system_message", "")
    sm["_hard_steer_yielded"] = True
    _log("steer_back_hard", turns=turns, msg=msg)
    _log_invoke(inv_n, "steer_back_hard", filled, pending,
                fresh_pending, hide_tools, preempted=msg,
                deferred=deferred)
    return {
        "hide_tools": hide_tools, "preempt": True,
        "force_preempt": True, "message": msg,
    }

  directive = f"The conversation has drifted — steer back. {target}"
  _log("steer_back_soft", turns=turns, directive=directive)
  return {"steer_back_directive": directive}


def _handle_post_executor(
    sm: dict[str, Any], tasks: list[dict[str, Any]],
    task_results: dict[str, Any],
    filled: dict[str, Any], pending: dict[str, Any],
    deferred: dict[str, Any],
    retries: dict[str, Any], confirm_transition_prefix: str,
    inv_n: int, phase: str, fresh_pending: bool,
    hide_tools: list[str],
    channel: str = "",
) -> tuple[Optional[dict[str, Any]], str, Optional[list[dict[str, Any]]]]:
  """Handle task executor results and retries."""
  task_just = sm.pop("_task_just_completed", None)
  if not task_just:
    return None, "", None

  task_def = next(t for t in tasks if t["name"] == task_just)
  success_key = task_def.get("success_check", "success")
  result = task_results.get(task_just, {})

  if result.get(success_key):
    _log("task", name=task_just, ok=True)
    retries.pop(task_just, None)
    sub_context = {**filled, **result}
    task_msg = ""
    msg_template = task_def.get("then_say", "")
    if msg_template:
      task_msg = msg_template.format(**sub_context)
    deferred_transition = sm.pop("_deferred_transition", False)
    if deferred_transition and task_msg and confirm_transition_prefix:
      task_msg = f"{confirm_transition_prefix} {task_msg}"
    if task_def.get("terminal"):
      on_complete = task_def.get("on_complete")
      if on_complete:
        for sn in on_complete.get("clear_slots", []):
          filled.pop(sn, None)
        task_results.clear()
        sm["status"] = "in_progress"
        _log("on_complete", task=task_just,
             cleared=on_complete.get("clear_slots", []))
      else:
        sm["status"] = "complete"
      _log_invoke(inv_n, phase, filled, pending, fresh_pending,
                  hide_tools, fired=task_just, preempted=task_msg,
                  deferred=deferred)
      preempt_result = {
          "hide_tools": [], "preempt": True, "message": task_msg,
      }
      resp = _resolve_response(
          task_def, "then_response", sub_context, channel,
      )
      if resp:
        preempt_result["response"] = resp
      return preempt_result, task_msg, None
    task_resp = _resolve_response(
        task_def, "then_response", sub_context, channel,
    )
    return None, task_msg, task_resp

  _log("task", name=task_just, ok=False)
  on_failure = task_def.get("on_failure", {})
  max_retries = on_failure.get("max_retries", 0)
  retries[task_just] = retries.get(task_just, 0) + 1
  for sn in on_failure.get("clear_slots", []):
    filled.pop(sn, None)
  if retries[task_just] >= max_retries:
    exhaust = on_failure.get("on_exhaust", {})
    fc = _resolve_exhaust_action(exhaust, filled)
    if fc:
      sm["status"] = "escalated"
    _log("task_exhaust", name=task_just)
    exhaust_msg = exhaust.get("say", "An error occurred.")
    _log_invoke(inv_n, phase, filled, pending, fresh_pending,
                hide_tools, preempted=exhaust_msg, deferred=deferred)
    result = {
        "hide_tools": hide_tools, "preempt": True, "message": exhaust_msg,
    }
    if fc:
      result["function_call"] = fc
    resp = _resolve_response(exhaust, "response", filled, channel)
    if resp:
      result["response"] = resp
    return result, "", None
  retry_msg = on_failure.get("retry_say", "Let me try again.")
  _log_invoke(inv_n, phase, filled, pending, fresh_pending,
              hide_tools, preempted=retry_msg, deferred=deferred)
  retry_result = {
      "hide_tools": hide_tools, "preempt": True, "message": retry_msg,
  }
  resp = _resolve_response(on_failure, "retry_response", filled, channel)
  if resp:
    retry_result["response"] = resp
  return retry_result, "", None


def _build_readback_hint(
    slots, pending: dict[str, Any], filled: dict[str, Any],
    fresh_pending: bool, promoted_from_deferred: bool = False,
) -> str:
  """Build readback hint for system instruction."""
  if not fresh_pending or not pending:
    return ""
  hint_parts = []
  for slot_def in slots:
    name = slot_def["name"]
    if name not in pending:
      continue
    if not _is_slot_active(slot_def, filled):
      continue
    formatter = _resolve_formatter(slot_def.get("readback_fmt"))
    val = formatter(pending[name]) if formatter else str(pending[name])
    if promoted_from_deferred:
      hint_parts.append(f"{name}: {val}")
    else:
      hint_parts.append(val)
  if not hint_parts:
    return ""
  if promoted_from_deferred:
    return "\n".join(f"  - {p}" for p in hint_parts)
  return ", ".join(hint_parts)


# ═════════════════════════════════════════════════════════════════════
# DAG EVALUATION
# ═════════════════════════════════════════════════════════════════════


def _build_readback(slots, pending, filled, config=None, channel=""):
  """Build readback confirmation prompt."""
  fragments = []
  for slot_def in slots:
    name = slot_def["name"]
    if name not in pending:
      continue
    if not _is_slot_active(slot_def, filled):
      continue
    formatter = _resolve_formatter(slot_def.get("readback_fmt"))
    if formatter:
      fragments.append(formatter(pending[name]))
    else:
      fragments.append(f"{name}: {pending[name]}")
  if not fragments:
    return None
  summary = ", ".join(fragments)
  result = {
      "action": "awaiting_readback",
      "system_message": f"Just to confirm — {summary}. Is that correct?",
  }
  if config:
    resp = _resolve_response(
        config, "readback_response", {**filled, **pending}, channel,
    )
    if resp:
      result["response"] = resp
  return result


def _find_next_question(
    slots, filled, pending, slot_map, deferred=None,
    channel="",
):
  """Find the next unfilled user slot to ask about."""
  deferred = deferred or {}
  for slot_def in slots:
    name = slot_def["name"]
    if not _is_slot_active(slot_def, filled):
      continue
    if name in filled or name in pending or name in deferred:
      continue
    if "user" not in _normalize_sources(slot_def.get("source", "user")):
      continue
    requires = slot_def.get("requires", [])
    if not all(
        req in filled
        or not _is_slot_active(slot_map[req], filled)
        for req in requires
    ):
      continue
    ask_template = slot_def.get("ask", f"Please provide {name}.")
    ask = ask_template.format(**filled)
    result = {
        "action": "next_question",
        "system_message": ask,
        "slot_name": name,
    }
    response = _resolve_response(slot_def, "response", filled, channel)
    if response:
      result["response"] = response
    return result
  return {
      "action": "all_done",
      "system_message": "All information collected!",
  }


def _find_next_slot_action(
    slots, filled, pending, slot_map, deferred=None,
    channel="",
):
  """Find the next slot action (announce or user question).

  Walks slots in declaration order. Returns the first
  eligible announce or user slot. Announce slots are filled
  by the framework; user slots produce a question prompt.

  Args:
    slots: Ordered list of slot definitions.
    filled: Dict of filled slot values.
    pending: Dict of pending slot values.
    slot_map: Dict mapping slot name to slot definition.
    deferred: Optional dict of deferred slot values.
    channel: Optional channel for channel-specific responses.

  Returns:
    Action dict with 'action' key ('announce',
    'next_question', or 'all_done').
  """
  deferred = deferred or {}
  for slot_def in slots:
    name = slot_def["name"]
    if not _is_slot_active(slot_def, filled):
      continue
    if name in filled or name in pending or name in deferred:
      continue
    sources = _normalize_sources(
        slot_def.get("source", "user"),
    )
    requires = slot_def.get("requires", [])
    if not all(
        req in filled
        or not _is_slot_active(slot_map[req], filled)
        for req in requires
    ):
      continue
    if "announce" in sources:
      return {
          "action": "announce",
          "slot_def": slot_def,
      }
    if "user" in sources:
      ask = slot_def.get(
          "ask", f"Please provide {name}.",
      )
      try:
        ask = ask.format(**filled)
      except KeyError:
        pass
      result = {
          "action": "next_question",
          "system_message": ask,
          "slot_name": name,
      }
      response = _resolve_response(slot_def, "response", filled, channel)
      if response:
        result["response"] = response
      return result
  return {
      "action": "all_done",
      "system_message": "All information collected!",
  }


def _compute_dag_state(
    tasks, slots, filled, pending, task_results, slot_map,
    deferred=None, channel="", config=None,
):
  """Evaluate the DAG to determine the next action.

  Task firing is checked before readback so that unrelated
  pending items (e.g. promoted from a deferred group) don't
  block a task whose inputs are all in filled.

  Args:
    tasks: List of task definition dicts.
    slots: List of slot definition dicts.
    filled: Currently filled slot values.
    pending: Currently pending slot values.
    task_results: Dict of task name to result.
    slot_map: Dict mapping slot name to slot definition.
    deferred: Currently deferred slot values.
    channel: Channel identifier for channel-aware responses.
    config: Full compiled config (for readback_response).

  Returns:
    Action dict describing the next step (fire, next_question, etc.).
  """
  for task in tasks:
    task_name = task["name"]
    success_key = task.get("success_check", "success")

    if (task_name in task_results
        and task_results[task_name].get(success_key)):
      continue

    if not _is_task_active(task, filled):
      continue

    active_inputs = [
        s for s in task["inputs"]
        if _is_slot_active(slot_map[s], filled)
    ]
    if not all(s in filled for s in active_inputs):
      continue
    task_reqs = [
        r for r in task.get("requires", [])
        if _is_slot_active(slot_map[r], filled)
    ]
    if not all(r in filled for r in task_reqs):
      continue

    return {
        "action": "fire",
        "task_name": task_name,
        "task_def": task,
    }

  if pending:
    rb = _build_readback(slots, pending, filled, config=config, channel=channel)
    if rb is not None:
      return rb

  return _find_next_slot_action(
      slots, filled, pending, slot_map, deferred=deferred,
      channel=channel,
  )


def _handle_slot_errors(sm, slots, channel=""):
  """Process validation errors and manage retries.

  Args:
    sm: State machine dict (mutated in place).
    slots: List of slot definition dicts.
    channel: Optional channel for channel-specific responses.

  Returns:
    Tuple of (message, exhausted, function_call, response).
  """
  errors = sm.pop("_slot_errors", [])
  if not errors:
    return None, False, None, None

  retries = sm.setdefault("_retries", {})
  filled = sm.get("filled", {})
  messages = []
  error_response = None

  for err in errors:
    slot_name = err["slot"]
    error_code = err["code"]
    retry_key = f"slot:{slot_name}"

    slot_def = next(
        (s for s in slots if s["name"] == slot_name), None,
    )
    if not slot_def:
      continue

    validation = slot_def.get("validation", {})
    max_retries = validation.get("max_retries", 3)

    retries[retry_key] = retries.get(retry_key, 0) + 1
    _log("slot_error", slot=slot_name,
         code=error_code, retries=retries[retry_key])

    if retries[retry_key] >= max_retries:
      exhaust = validation.get("on_exhaust", {})
      fc = _resolve_exhaust_action(exhaust, filled)
      if fc:
        sm["status"] = "escalated"
      msg = exhaust.get(
          "say", "An error occurred. Please call us for help.",
      )
      try:
        msg = msg.format(**filled)
      except KeyError:
        pass
      resp = _resolve_response(exhaust, "response", filled, channel)
      _log("slot_error_exhaust", slot=slot_name)
      return msg, True, fc, resp

    error_messages = validation.get("errors", {})
    msg = error_messages.get(
        error_code, "Could you try that again?",
    )
    try:
      msg = msg.format(**filled)
    except KeyError:
      pass
    messages.append(msg)

    if not error_response:
      error_responses = validation.get("error_responses", {})
      channel_error_responses = validation.get(
          "channel_error_responses", {},
      )
      resp = (
          channel_error_responses.get(channel, {}).get(error_code)
          if channel else None
      ) or error_responses.get(error_code)
      if resp:
        error_response = _substitute_response(resp, filled)

  if not messages:
    return None, False, None, None

  combined = " ".join(messages)
  return combined, False, None, error_response


def _compute_deferred_eligible(slots, tasks, task_results, slot_map):
  """Identify slots eligible for deferred confirmation."""
  eligible = set()
  for slot_def in slots:
    name = slot_def["name"]
    if not slot_def.get("requires_readback"):
      continue
    has_task_requires = False
    for req in slot_def.get("requires", []):
      req_def = slot_map.get(req)
      if req_def and any(
          s.startswith("task:")
          for s in _normalize_sources(req_def.get("source", "user"))
      ):
        has_task_requires = True
        break
    if has_task_requires:
      continue
    is_deferred_input = False
    blocked = False
    for task in tasks:
      if name not in task["inputs"]:
        continue
      if task.get("readback_inputs"):
        is_deferred_input = True
      else:
        sk = task.get("success_check", "success")
        if not (task["name"] in task_results
                and task_results[task["name"]].get(sk)):
          blocked = True
          break
    if is_deferred_input and not blocked:
      eligible.add(name)
  return eligible


def _check_deferred_groups(
    tasks, filled, pending, deferred, task_results, slot_map,
) -> list[str]:
  """Promote deferred slots when all group inputs are ready.

  Args:
    tasks: List of task definition dicts.
    filled: Currently filled slot values.
    pending: Currently pending slot values.
    deferred: Currently deferred slot values (mutated in place).
    task_results: Dict of task name to result.
    slot_map: Dict mapping slot name to slot definition.

  Returns:
    The names of slots promoted from deferred to pending.
  """
  promoted = []
  for task_def in tasks:
    if not task_def.get("readback_inputs"):
      continue
    sk = task_def.get("success_check", "success")
    if (task_def["name"] in task_results
        and task_results[task_def["name"]].get(sk)):
      continue
    deferred_inputs = []
    all_ready = True
    for inp in task_def["inputs"]:
      sd = slot_map.get(inp)
      if not sd:
        continue
      if "user" not in _normalize_sources(sd.get("source", "user")):
        continue
      if not _is_slot_active(sd, filled):
        continue
      if inp in filled or inp in pending:
        continue
      if inp in deferred:
        deferred_inputs.append(inp)
      else:
        all_ready = False
        break
    if all_ready and deferred_inputs:
      for inp in deferred_inputs:
        pending[inp] = deferred.pop(inp)
        promoted.append(inp)
  return promoted


def _compute_hidden_tools(
    slots, filled, pending, readback_tools, slot_map,
    *, fresh_pending=False, executor_tools=None,
):
  """Determine which tools to hide from the LLM."""
  hidden = []
  if pending:
    if fresh_pending:
      hidden.extend(readback_tools)
    else:
      pass
  else:
    hidden.extend(readback_tools)
  for slot_def in slots:
    setter = slot_def.get("setter")
    if not setter:
      continue
    name = slot_def["name"]
    if not _is_slot_active(slot_def, filled):
      hidden.append(setter)
    elif name in filled:
      hidden.append(setter)
    elif name in pending and fresh_pending:
      hidden.append(setter)
    elif not all(
        r in filled
        or not _is_slot_active(slot_map[r], filled)
        for r in slot_def.get("requires", [])
    ):
      hidden.append(setter)
  # Un-hide multi-setter tools that still have unfilled active slots
  multi_setters = {}
  for slot_def in slots:
    setter = slot_def.get("setter")
    field = slot_def.get("setter_field")
    if setter and field:
      multi_setters.setdefault(setter, []).append(slot_def)
  for tool_name, slot_defs in multi_setters.items():
    for sd in slot_defs:
      name = sd["name"]
      if (name not in filled
          and _is_slot_active(sd, filled)
          and not (name in pending and fresh_pending)
          and all(
              r in filled
              or not _is_slot_active(slot_map[r], filled)
              for r in sd.get("requires", [])
          )):
        while tool_name in hidden:
          hidden.remove(tool_name)
        break

  if executor_tools:
    hidden.extend(executor_tools)
  return hidden


# ═════════════════════════════════════════════════════════════════════
# SYSTEM INSTRUCTION SUFFIX
# ═════════════════════════════════════════════════════════════════════


def _build_si_suffix(
    config, slots, pending, filled, fresh_pending,
    promoted_from_deferred, deferred_hint,
):
  """Build the system instruction suffix from engine state."""
  si_suffix = ""

  # Structured ground-truth filled state injection
  confirmed_parts = []
  gate_slot = config.get("gate_slot")

  for slot_def in slots:
    name = slot_def["name"]
    if name not in filled:
      continue
    if not _is_slot_active(slot_def, filled):
      continue
    # Skip active gate slot
    if name == gate_slot:
      continue
    # Only include user or event sourced slots (skip announce and task-sourced
    # slots)
    sources = _normalize_sources(slot_def.get("source", "user"))
    if "announce" in sources or any(s.startswith("task:") for s in sources):
      continue

    formatter = _resolve_formatter(slot_def.get("readback_fmt"))
    val = formatter(filled[name]) if formatter else str(filled[name])
    confirmed_parts.append(f"- {slot_def.get('hint', name)}: {val}")

  if confirmed_parts:
    confirmed_summary = "\n".join(confirmed_parts)
    si_suffix += (
        f"\n\n<confirmed_details>"
        f"\nThe user has already provided and confirmed the following details:"
        f"\n{confirmed_summary}"
        f"\nDo NOT re-ask the user for these details, and do NOT call setter tools for them."
        f"\n</confirmed_details>"
    )

  readback_hint = _build_readback_hint(
      slots, pending, filled, fresh_pending,
      promoted_from_deferred=promoted_from_deferred,
  )
  if readback_hint:
    if promoted_from_deferred:
      si_suffix += (
          f"\n\n<readback_scope>"
          f"\nYou MUST confirm ALL of the following values together"
          f" in a single readback — do NOT omit any:\n{readback_hint}"
          f"\n</readback_scope>"
      )
    else:
      si_suffix += (
          f"\n\n<readback_scope>"
          f"\nPending confirmation: {readback_hint}."
          f"\n</readback_scope>"
      )
  if deferred_hint:
    si_suffix += (
        f"\n\n<deferred_collection>"
        f"\n{deferred_hint}"
        f"\n</deferred_collection>"
    )
  return si_suffix



# ═════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════


def _run_slot_filling(
    config: dict[str, Any],
    sm: dict[str, Any],
    last_user_text: str = "",
) -> dict[str, Any]:
  """Run one turn of the slot-filling DAG engine."""
  slots = config["slots"]
  tasks = config["tasks"]
  executors = {t["name"]: t["tool"] for t in tasks}
  readback_tools = ["confirm_pending", "reject_pending"]
  steer_back_cfg = config.get("steer_back", {})
  _prefix_cfg = config.get("confirm_transition_prefix", "")
  if isinstance(_prefix_cfg, list):
    confirm_transition_prefix = (
        random.choice(_prefix_cfg) if _prefix_cfg else ""
    )
  else:
    confirm_transition_prefix = _prefix_cfg

  slot_map = {s["name"]: s for s in slots}
  executor_tool_names = list(executors.values())
  channel = sm.get("channel", "")

  sm["_invoke_n"] = sm.get("_invoke_n", 0) + 1
  inv_n = sm["_invoke_n"]

  filled = sm.get("filled", {})
  pending = sm.get("pending", {})
  deferred = sm.setdefault("deferred", {})
  task_results = sm.get("task_results", {})
  last_state = sm.get("_last_state", {})

  _handle_state_change(sm, filled, pending, last_state)

  deferred_promoted = _auto_promote_and_route(
      slots, tasks, task_results, slot_map,
      filled, pending, deferred,
  )

  retries = sm.setdefault("_retries", {})
  _deactivate_conditional_slots(
      slots, filled, pending, deferred, retries,
  )

  sm["_last_state"] = {
      "filled": dict(filled),
      "pending": dict(pending),
      "deferred": dict(deferred),
  }
  last_pending = last_state.get("pending", {})
  fresh_pending = pending != last_pending
  last_deferred = last_state.get("deferred", {})
  fresh_deferred = bool(set(deferred) - set(last_deferred))
  promoted_from_deferred = bool(
      set(pending) & (set(last_deferred) - set(last_pending))
  )
  if pending:
    phase = "fresh_readback" if fresh_pending else "awaiting_confirmation"
  else:
    phase = "collection"

  result = _try_auto_confirm(phase, last_user_text, sm)
  if result:
    return result

  inline_confirmed, phase, fresh_pending = _apply_inline_confirm(
      sm, filled, pending, phase, fresh_pending,
  )

  hide_tools = _compute_hidden_tools(
      slots, filled, pending, readback_tools, slot_map,
      fresh_pending=fresh_pending, executor_tools=executor_tool_names,
  )
  bootstrap_tool = config.get("bootstrap", {}).get("tool")
  if bootstrap_tool and bootstrap_tool in hide_tools:
    while bootstrap_tool in hide_tools:
      hide_tools.remove(bootstrap_tool)

  error_msg, _, error_fc, error_resp = _handle_slot_errors(
      sm, slots, channel=channel,
  )
  if error_msg:
    sm["_steer_back_turns"] = 0
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                preempted=error_msg, deferred=deferred)
    result = {"hide_tools": hide_tools, "preempt": True, "message": error_msg}
    if error_fc:
      result["function_call"] = error_fc
    if error_resp:
      result["response"] = error_resp
    return result

  readback_transition = sm.pop("_readback_transition", False)

  steer_result = _handle_steer_back(
      sm, last_user_text, steer_back_cfg,
      slots, filled, pending, deferred, slot_map,
      fresh_pending, hide_tools, inv_n,
      channel=channel,
  )
  if steer_result and steer_result.get("preempt"):
    return steer_result

  result, task_msg, task_resp = _handle_post_executor(
      sm, tasks, task_results, filled, pending, deferred,
      retries, confirm_transition_prefix,
      inv_n, phase, fresh_pending, hide_tools,
      channel=channel,
  )
  if result:
    return result

  # ── Announce slots (cascade through consecutive) ────────
  announce_msgs = []
  announce_responses = []
  any_announce_preempt = False
  dag_result = _compute_dag_state(
      tasks, slots, filled, pending, task_results, slot_map,
      deferred=deferred, channel=channel, config=config,
  )
  while dag_result["action"] == "announce":
    slot_def_a = dag_result["slot_def"]
    name_a = slot_def_a["name"]
    msg_a = slot_def_a["message"]
    try:
      msg_a = msg_a.format(**filled)
    except KeyError:
      pass
    filled[name_a] = True
    announce_msgs.append(msg_a)
    resp_a = _resolve_response(slot_def_a, "response", filled, channel)
    if resp_a:
      announce_responses.extend(resp_a)
    if slot_def_a.get("preempt", True):
      any_announce_preempt = True
    _log("announce", slot=name_a)
    dag_result = _compute_dag_state(
        tasks, slots, filled, pending, task_results,
        slot_map, deferred=deferred, channel=channel, config=config,
    )

  # Skip task fire on inline confirm — the LLM needs to run first
  # to process the additional content in the user's message. The
  # task will fire on the next before_model_callback invocation
  # after the LLM calls setters for the new content.
  if dag_result["action"] == "fire" and not inline_confirmed:
    task_def_f = dag_result["task_def"]
    task_name_f = task_def_f["name"]
    tool_name = task_def_f["tool"]
    active_inputs = [
        s for s in task_def_f["inputs"]
        if _is_slot_active(slot_map[s], filled)
    ]
    args = {k: filled[k] for k in active_inputs if k in filled}
    for name in deferred_promoted:
      if name in pending and name not in active_inputs:
        deferred[name] = pending.pop(name)
        _log("re_deferred", slot=name, task=task_name_f)
    if readback_transition:
      sm["_deferred_transition"] = True
    sm["_last_state"] = {
        "filled": dict(filled),
        "pending": dict(pending),
        "deferred": dict(deferred),
    }
    combined_msg = task_msg
    if announce_msgs:
      announce_text = " ".join(announce_msgs)
      combined_msg = (
          f"{announce_text} {task_msg}"
          if task_msg else announce_text
      )
    _log_invoke(inv_n, phase, filled, pending, fresh_pending,
                hide_tools, fired=task_name_f, deferred=deferred)
    fire_hide = [t for t in hide_tools if t != tool_name]
    fire_result = {
        "hide_tools": fire_hide,
        "preempt": True,
        "force_preempt": any_announce_preempt,
        "function_call": {"name": tool_name, "args": args},
        "message": combined_msg,
    }
    if announce_responses:
      fire_result["response"] = announce_responses
    return fire_result

  dag_msg = dag_result.get("system_message", "")
  if task_msg and dag_msg:
    msg = f"{task_msg} {dag_msg}"
  else:
    msg = task_msg or dag_msg
  if announce_msgs:
    announce_text = " ".join(announce_msgs)
    msg = (
        f"{announce_text} {msg}" if msg
        else announce_text
    )
  sm["_last_state"] = {
      "filled": dict(filled), "pending": dict(pending),
      "deferred": dict(deferred),
  }
  if dag_result["action"] == "awaiting_readback" and not fresh_pending:
    msg = ""

  event_prefilled = sm.get("_event_prefilled_this_turn", False)
  preempt = bool(task_msg) or any_announce_preempt or event_prefilled
  if readback_transition and msg and confirm_transition_prefix:
    if not msg.lower().startswith(confirm_transition_prefix.lower()):
      msg = f"{confirm_transition_prefix} {msg}"
    preempt = True

  action = dag_result["action"]
  if preempt:
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                preempted=msg, deferred=deferred)
  elif action == "next_question":
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                asking=msg or dag_result.get("system_message", ""),
                deferred=deferred)
  elif action == "awaiting_readback" and fresh_pending:
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                reading_back=dag_result.get("system_message", ""),
                deferred=deferred)
  else:
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                done=(action == "all_done"), deferred=deferred)

  deferred_hint = ""
  if fresh_deferred and not pending:
    next_q = _find_next_question(
        slots, filled, pending, slot_map, deferred=deferred,
        channel=channel,
    )
    next_msg = next_q.get("system_message", "")
    if next_msg:
      deferred_names = sorted(set(deferred) - set(last_deferred))
      deferred_hint = (
          f"The value(s) just collected ({', '.join(deferred_names)})"
          f" are noted and will be confirmed later together with"
          f" related information. Do NOT read them back or ask for"
          f" confirmation now. Instead, proceed to ask: {next_msg}"
      )

  si_suffix = _build_si_suffix(
      config, slots, pending, filled, fresh_pending,
      promoted_from_deferred, deferred_hint,
  )

  combined_response = announce_responses or []
  if task_resp:
    combined_response = combined_response + task_resp
  dag_response = dag_result.get("response")
  if dag_response:
    combined_response = combined_response + dag_response

  final = {
      "hide_tools": hide_tools,
      "preempt": preempt,
      "force_preempt": any_announce_preempt or inline_confirmed,
      "message": msg,
      "si_suffix": si_suffix,
      "inline_confirmed": inline_confirmed,
  }
  if steer_result and steer_result.get("steer_back_directive"):
    final["steer_back_directive"] = steer_result["steer_back_directive"]

  sm.pop("_pending_payloads", None)
  sm.pop("_pending_question_payloads", None)

  unconditional = announce_responses or []
  if task_resp:
    unconditional = unconditional + task_resp
  awaiting_rb = dag_result.get("action") == "awaiting_readback"
  if dag_response and awaiting_rb and fresh_pending:
    unconditional = unconditional + dag_response
  if unconditional:
    sm["_pending_payloads"] = unconditional
    _log("payload_route", path="stash_unconditional",
         n_parts=len(unconditional))
  if dag_response and not awaiting_rb:
    sm["_pending_question_payloads"] = {
        "slot": dag_result.get("slot_name"),
        "parts": dag_response,
    }
    _log("payload_route", path="stash_question",
         slot=dag_result.get("slot_name"),
         n_parts=len(dag_response))
  if not unconditional and not dag_response:
    _log("payload_route", path="none")

  if preempt and combined_response:
    final["response"] = combined_response
    _log("payload_route", path="preempt_dispatch",
         n_parts=len(combined_response))
  return final


# ═════════════════════════════════════════════════════════════════════
# TOOL ENTRY POINT
# ═════════════════════════════════════════════════════════════════════


def slot_filling_engine(input_data: dict[str, Any]) -> dict[str, Any]:
  """Run one turn of the slot-filling DAG engine.

  Called from before_model_callback. All state flows through
  the sm dict: passed in via input_data, modified in place,
  and returned in the result.

  Args:
    input_data: Dict with keys 'raw_config' (DAG config),
      'sm' (state machine dict), 'last_user_text' (user message),
      and 'event_data' (event data for pre-fill).

  Returns:
    Dict with 'action' (the engine result) and 'sm'
    (the updated state machine).
  """
  # pylint: disable=global-variable-not-assigned
  global _COMPILED_CONFIGS, _DEBUG, _DEBUG_LOG

  _DEBUG = bool(input_data.get("debug"))
  _DEBUG_LOG = []

  raw_config = input_data.get("raw_config", {})
  sm = input_data.get("sm", {})
  last_user_text = input_data.get("last_user_text", "")
  event_data = input_data.get("event_data") or {}
  config_id = input_data.get("config_id", "default")

  # ── Compile config (cached per config_id) ──────────────────
  if config_id not in _COMPILED_CONFIGS:
    _validate_config(raw_config)
    _COMPILED_CONFIGS[config_id] = _compile_config(raw_config)
  config = _COMPILED_CONFIGS[config_id]

  # ── Event mappings (CES event name → slot values) ───────────
  event_mappings = config.get("event_mappings", {})
  if event_mappings and event_data:
    ia_event = event_data.get("ia_event_name", "")
    if ia_event and ia_event in event_mappings:
      for slot_name, value in event_mappings[ia_event].items():
        event_data[slot_name] = value

  # ── Event pre-fill ──────────────────────────────────────────
  # Process events on every engine call. fill_slots is idempotent
  # for already-filled slots, so re-processing the same event is
  # safe. No persistent guard needed.
  if event_data:
    event_values = {}
    for slot_def in config["slots"]:
      if "event" not in _normalize_sources(
          slot_def.get("source", "user")
      ):
        continue
      key = slot_def.get("event_key", slot_def["name"])
      value = event_data.get(key)
      if value is not None:
        event_values[slot_def["name"]] = value
    if event_values:
      result = fill_slots(sm, config, event_values)
      if result["filled"]:
        sm["_event_prefilled_this_turn"] = True

  # ── Derive mappings for after_tool_callback ─────────────────
  if "_setter_slots" not in sm:
    setter_slots = {}
    multi_setter_slots = {}
    slot_requires = {}
    slot_validates = {}
    for slot_def in config["slots"]:
      setter = slot_def.get("setter")
      setter_field = slot_def.get("setter_field")
      if setter:
        if setter_field:
          multi_setter_slots.setdefault(
              setter, {},
          )[setter_field] = slot_def["name"]
        else:
          setter_slots[setter] = slot_def["name"]
      if slot_def.get("requires"):
        slot_requires[slot_def["name"]] = slot_def["requires"]
      if slot_def.get("validate_against"):
        slot_validates[slot_def["name"]] = slot_def["validate_against"]
    sm["_setter_slots"] = setter_slots
    sm["_multi_setter_slots"] = multi_setter_slots
    sm["_slot_requires"] = slot_requires
    sm["_slot_validates"] = slot_validates
    executor_tasks = {}
    for task_def in config["tasks"]:
      tool_name = task_def.get("tool")
      if tool_name:
        executor_tasks[tool_name] = {
            "task_name": task_def["name"],
            "outputs": task_def.get("outputs", {}),
            "success_check": task_def.get("success_check", "success"),
            "terminal": task_def.get("terminal", False),
        }
    sm["_executor_tasks"] = executor_tasks

  # ── Generate tool selection for before_agent_callback ───────
  filled_for_ts = sm.get("filled", {})
  ts_lines = []
  ordering_parts = []
  prereq_parts = []
  for slot_def in config["slots"]:
    sources = _normalize_sources(slot_def.get("source", "user"))
    if "user" not in sources:
      continue
    name = slot_def["name"]
    hint = slot_def.get("hint", "")
    setter = slot_def.get("setter", "")
    if name in filled_for_ts:
      continue
    if hint and setter and _is_slot_active(slot_def, filled_for_ts):
      ts_lines.append(f"   - {hint} → {setter}")
    if not slot_def.get("condition") and hint and setter:
      ordering_parts.append(name)
    if slot_def.get("requires") and setter:
      prereq_parts.append(
          f"Never call {setter} before"
          f" {', '.join(slot_def['requires'])} are presented."
      )
  sm["_tool_selection"] = "\n".join(ts_lines)
  sm["_slot_ordering"] = " → ".join(ordering_parts)
  sm["_prereq_note"] = " ".join(prereq_parts)

  # ── Run the DAG engine ─────────────────────────────────────
  action = _run_slot_filling(config, sm, last_user_text=last_user_text)

  # ── Post-engine: directive and event-prefill SI handling ──
  msg = action.get("message", "")
  if msg:
    sm["_next_directive"] = msg

  event_prefilled = sm.pop("_event_prefilled_this_turn", False)
  if event_prefilled and msg:
    si_suffix = action.get("si_suffix", "")
    si_suffix += (
        f"\n\n<system_directive>\n{msg}\n</system_directive>"
    )
    action["si_suffix"] = si_suffix
    action["event_prefilled"] = True
  else:
    action["event_prefilled"] = False

  result = {"action": action, "sm": sm}
  if _DEBUG:
    result["_debug_log"] = _DEBUG_LOG
  return result
