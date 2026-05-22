# pylint: disable=invalid-name
"""Slot filling DAG config validator — reusable across projects.

FRAMEWORK CODE — shared across all agents using the slot-filling engine.
Do not add agent-specific logic here; this validates DAG config structure
and cross-config interactions generically.

Validates structure and references in a DAG configuration dict.
Catches misconfigurations (broken references, loop risks, missing
fields) before the engine encounters them at runtime.

Usable as a CES tool (via validate_dag_config()) or directly from
pytest (via DagConfigValidator / CrossConfigValidator).
"""

import ast
import dataclasses
import string
from typing import Any


@dataclasses.dataclass
class ValidationResult:
  """Structured output from DAG config validation."""

  valid: bool = True
  errors: list[str] = dataclasses.field(default_factory=list)
  warnings: list[str] = dataclasses.field(default_factory=list)


_VALID_SOURCES = frozenset({"user", "announce", "event"})

_VALID_READBACK_FMT_TYPES = frozenset({
    "prefix", "plural", "none_sub", "date", "time",
})

_READBACK_FMT_REQUIRED_FIELDS = {
    "prefix": ["text"],
    "plural": ["one", "other"],
    "none_sub": ["default"],
}

_VALID_RESPONSE_TYPES = frozenset({
    "text", "payload", "end_session", "transfer",
})

_SAFE_EVAL_GLOBALS = {  # pylint: disable=unused-variable
    "__builtins__": {
        "int": int, "str": str, "len": len,
        "float": float, "bool": bool,
    },
}


def _normalize_sources(source) -> list[str]:
  """Normalize slot source to a list."""
  if isinstance(source, list):
    return source
  return [source] if source else ["user"]


def _extract_format_fields(template: str) -> set[str]:
  """Extract {field_name} placeholders from a format string."""
  try:
    return {
        fname for _, fname, _, _
        in string.Formatter().parse(template)
        if fname is not None
    }
  except (ValueError, KeyError):
    return set()


def _extract_dict_keys_from_source(source: str) -> set[str] | None:
  """Extract string keys from dict literals and subscript assignments.

  Parses Python source and collects keys from:
    - Dict literals: {"key": ...}
    - Subscript assignments: result["key"] = ...
    - values["key"] = ... (nested dict builds)

  Args:
    source: Python source code to parse.

  Returns:
    Tuple of (all_keys, nested_keys) or None if parsing fails.
  """
  try:
    tree = ast.parse(source)
  except SyntaxError:
    return None
  keys: set[str] = set()
  nested_keys: dict[str, set[str]] = {}

  for node in ast.walk(tree):
    if isinstance(node, ast.Dict):
      for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
          keys.add(k.value)
    if isinstance(node, ast.Assign):
      for target in node.targets:
        if (isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and isinstance(target.slice.value, str)):
          var_name = ""
          if isinstance(target.value, ast.Name):
            var_name = target.value.id
          keys.add(target.slice.value)
          if var_name:
            nested_keys.setdefault(var_name, set()).add(
                target.slice.value)

  return keys, nested_keys


def _extract_values_dict_keys(source: str) -> set[str] | None:
  """Extract keys written into a 'values' dict in setter source.

  For multi-setters that build: values = {}; values["field"] = x
  or values = {"field": x}.

  Args:
    source: Python source code of the setter function.

  Returns:
    Set of keys from the values dict, or None if undetermined.
  """
  result = _extract_dict_keys_from_source(source)
  if result is None:
    return None
  _, nested = result
  if "values" in nested:
    return nested["values"]
  return None


class DagConfigValidator:
  """Validates a slot filling DAG configuration.

  Usage:
      result = DagConfigValidator(raw_config).validate()
      if not result.valid:
          print(result.errors)
  """

  def __init__(self, config: dict[str, Any],
               available_tools: list[str] | None = None,
               setter_sources: dict[str, str] | None = None,
               task_tool_sources: dict[str, str] | None = None):
    self._config = config
    self._slots = config.get("slots", [])
    self._tasks = config.get("tasks", [])
    self._slot_map = {s["name"]: s for s in self._slots if "name" in s}
    self._slot_names = [s["name"] for s in self._slots if "name" in s]
    self._slot_set = set(self._slot_names)
    self._task_map = {t["name"]: t for t in self._tasks if "name" in t}
    self._task_names = {t["name"] for t in self._tasks if "name" in t}
    self._available_tools = (
        set(available_tools) if available_tools else None)
    self._setter_sources = setter_sources or {}
    self._task_tool_sources = task_tool_sources or {}
    self._errors: list[str] = []
    self._warnings: list[str] = []

  def validate(self) -> ValidationResult:
    """Run all checks and return results."""
    self._check_top_level_structure()
    self._check_duplicate_slots()
    self._check_slot_names()
    self._check_slot_references()
    self._check_slot_sources()
    self._check_slot_readback_fmt()
    self._check_slot_validation_config()
    self._check_slot_validate_against()
    self._check_slot_conditions()
    self._check_task_names()
    self._check_task_references()
    self._check_task_on_failure()
    self._check_task_on_complete()
    self._check_loop_risks()
    self._check_circular_requires()
    self._check_bootstrap()
    self._check_gate_slot()
    self._check_steer_back()
    self._check_response_parts()
    self._check_format_string_placeholders()
    self._check_orphaned_slots()
    self._check_reachability()
    self._check_tool_availability()
    self._check_setter_output_keys()
    self._check_task_output_keys()
    self._check_duplicate_setter_mappings()
    self._check_clear_slots_subset()
    self._check_announce_dead_config()
    self._check_user_slot_fields()
    self._check_terminal_task_feedback()
    self._check_task_output_source_alignment()
    self._check_exhaust_tool_exists()
    self._check_options_from_ordering()
    self._check_empty_strings()
    self._check_ask_text_response_conflict()
    return ValidationResult(
        valid=len(self._errors) == 0,
        errors=list(self._errors),
        warnings=list(self._warnings),
    )

  # ── Helpers ──────────────────────────────────────────────

  def _error(self, msg: str):
    self._errors.append(msg)

  def _warn(self, msg: str):
    self._warnings.append(msg)

  # ── Top-level structure ────────────────────────────────

  def _check_top_level_structure(self):
    """Check that config has slots and optionally tasks.

    Without slots, the engine has nothing to collect and
    _compile_config raises KeyError on config["slots"]. Tasks are
    optional (warn-only) since a config could be announce-only.
    """
    if not self._slots and not self._tasks:
      self._error("Config has no 'slots' and no 'tasks'")
      return
    if not self._slots:
      self._error("Config has no 'slots'")
    if not self._tasks:
      self._warn("Config has no 'tasks'")

  # ── Slot checks ────────────────────────────────────────

  def _check_duplicate_slots(self):
    """Check for duplicate slot names.

    Two slots with the same name cause the second to shadow the
    first in slot_map. The engine silently uses only the last
    definition, losing the first slot's setter, validation, and
    readback config.
    """
    if len(self._slot_names) != len(self._slot_set):
      dupes = {
          n for n in self._slot_names
          if self._slot_names.count(n) > 1
      }
      self._error(f"Duplicate slot names: {dupes}")

  def _check_slot_names(self):
    """Check that every slot has a 'name' key.

    Slots without 'name' cause KeyError in slot_map construction
    and are invisible to every engine lookup.
    """
    for i, slot in enumerate(self._slots):
      if "name" not in slot:
        self._error(f"Slot at index {i} has no 'name'")

  def _check_slot_references(self):
    """Validate slot wiring for announce, requires, and options_from.

    Catches: announce without 'message' (KeyError at announce time),
    announce with 'setter' (conflicts with auto-fill), requires
    referencing unknown slot (KeyError in _find_next_slot_action),
    and options_from referencing unknown slot (empty chip lists).
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      sources = _normalize_sources(
          slot.get("source", "user"))
      if "announce" in sources:
        if not slot.get("message"):
          self._error(
              f"Announce slot '{name}' requires 'message'")
        if slot.get("setter"):
          self._error(
              f"Announce slot '{name}'"
              " must not have 'setter'")
      for req in slot.get("requires", []):
        if req not in self._slot_set:
          self._error(
              f"Slot '{name}' requires unknown '{req}'")
      options_from = self._find_options_from(slot)
      if options_from and options_from not in self._slot_set:
        self._error(
            f"Slot '{name}' response has options_from"
            f" '{options_from}' not in slots")

  def _find_options_from(self, slot_or_task: dict[str, Any]) -> str | None:
    """Recursively search response parts for options_from."""
    for resp in slot_or_task.get("response", []):
      found = self._search_options_from(resp)
      if found:
        return found
    return None

  def _search_options_from(self, obj) -> str | None:
    """Recursively search a response part for options_from."""
    if isinstance(obj, dict):
      if "options_from" in obj:
        return obj["options_from"]
      for v in obj.values():
        found = self._search_options_from(v)
        if found:
          return found
    elif isinstance(obj, list):
      for item in obj:
        found = self._search_options_from(item)
        if found:
          return found
    return None

  def _check_slot_sources(self):
    """Validate that slot sources are recognized and well-formed.

    Valid sources: 'user', 'announce', 'event', 'task:TaskName'.
    Also checks task:X references existing tasks, event source has
    event_key, and setter_field has a parent setter.
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      sources = _normalize_sources(
          slot.get("source", "user"))
      for source in sources:
        if source.startswith("task:"):
          src_task = source[5:]
          if src_task not in self._task_names:
            self._error(
                f"Slot '{name}' references unknown task"
                f" '{src_task}'")
        elif source not in _VALID_SOURCES:
          self._error(
              f"Slot '{name}' has unknown source"
              f" '{source}'")
      if "event" in sources and not slot.get("event_key"):
        self._warn(
            f"Slot '{name}' has 'event' source but no"
            " 'event_key' — will default to slot name")
      if slot.get("setter_field") and not slot.get("setter"):
        self._error(
            f"Slot '{name}' has 'setter_field' without"
            " 'setter'")

  def _check_slot_readback_fmt(self):
    """Validate readback_fmt type, required fields, and value type.

    readback_fmt can be a string shorthand, a dict with type+params,
    or a callable. Checks for unknown types and missing required
    fields (e.g. plural without "one"/"other").
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      fmt = slot.get("readback_fmt")
      if fmt is None:
        continue
      if isinstance(fmt, str):
        if fmt not in _VALID_READBACK_FMT_TYPES:
          self._error(
              f"Slot '{name}' readback_fmt string"
              f" '{fmt}' not recognized — valid:"
              f" {sorted(_VALID_READBACK_FMT_TYPES)}")
        continue
      if isinstance(fmt, dict):
        fmt_type = fmt.get("type")
        if not fmt_type:
          self._error(
              f"Slot '{name}' readback_fmt dict"
              " missing 'type'")
          continue
        if fmt_type not in _VALID_READBACK_FMT_TYPES:
          self._error(
              f"Slot '{name}' readback_fmt type"
              f" '{fmt_type}' not recognized — valid:"
              f" {sorted(_VALID_READBACK_FMT_TYPES)}")
          continue
        required = _READBACK_FMT_REQUIRED_FIELDS.get(
            fmt_type, [])
        for field_name in required:
          if field_name not in fmt:
            self._error(
                f"Slot '{name}' readback_fmt type"
                f" '{fmt_type}' missing required"
                f" field '{field_name}'")
        continue
      if not callable(fmt):
        self._error(
            f"Slot '{name}' readback_fmt must be"
            " string, dict, or callable")

  def _check_slot_validation_config(self):
    """Validate the validation block on each slot.

    Checks validation.errors is a dict, max_retries is a positive
    int, and on_exhaust structure is valid. Wrong types cause silent
    failures or TypeErrors at runtime.
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      validation = slot.get("validation")
      if not validation:
        continue
      if not isinstance(validation, dict):
        self._error(
            f"Slot '{name}' validation must be a dict")
        continue
      errors = validation.get("errors")
      if errors is not None and not isinstance(errors, dict):
        self._error(
            f"Slot '{name}' validation.errors"
            " must be a dict")
      max_retries = validation.get("max_retries")
      if max_retries is not None:
        if not isinstance(max_retries, int):
          self._error(
              f"Slot '{name}' validation.max_retries"
              " must be an int")
        elif max_retries < 1:
          self._warn(
              f"Slot '{name}' validation.max_retries"
              f" is {max_retries} — effectively no retries")
      on_exhaust = validation.get("on_exhaust")
      if on_exhaust is not None:
        self._check_on_exhaust(
            on_exhaust,
            f"Slot '{name}' validation.on_exhaust")

  def _check_slot_validate_against(self):
    """Validate cross-slot validate_against configuration.

    Checks that response_field, filled_slot, and error_code are all
    present, and that filled_slot references a known slot.
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      va = slot.get("validate_against")
      if not va:
        continue
      if not isinstance(va, dict):
        self._error(
            f"Slot '{name}' validate_against"
            " must be a dict")
        continue
      for required_field in ("response_field", "filled_slot",
                             "error_code"):
        if required_field not in va:
          self._error(
              f"Slot '{name}' validate_against"
              f" missing '{required_field}'")
      filled_slot = va.get("filled_slot")
      if filled_slot and filled_slot not in self._slot_set:
        self._error(
            f"Slot '{name}' validate_against.filled_slot"
            f" '{filled_slot}' not in slots")

  def _check_slot_conditions(self):
    """Validate that slot condition strings compile without errors.

    Condition strings are eval'd at compile time by _compile_config.
    Compiling early surfaces syntax errors with the slot name.
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      cond = slot.get("condition")
      if cond is None or callable(cond):
        continue
      if isinstance(cond, str):
        try:
          compile(cond, f"<slot:{name}:condition>", "eval")
        except SyntaxError as e:
          self._error(
              f"Slot '{name}' condition syntax error: {e}")
      else:
        self._error(
            f"Slot '{name}' condition must be callable"
            f" or string, got {type(cond).__name__}")

  # ── Task checks ────────────────────────────────────────

  def _check_task_names(self):
    """Check that every task has a 'name' key.

    Tasks without 'name' cause KeyError in task_results tracking
    and cannot be referenced by task:X slot sources.
    """
    for i, task in enumerate(self._tasks):
      if "name" not in task:
        self._error(f"Task at index {i} has no 'name'")

  def _check_task_references(self):
    """Validate task tool, inputs, outputs, requires, and conditions.

    Checks that tasks have a 'tool' key, inputs/outputs/requires
    reference known slots, and condition strings compile.
    """
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      if not task.get("tool"):
        self._error(f"Task '{name}' has no 'tool' key")
      for inp in task.get("inputs", []):
        if inp not in self._slot_set:
          self._error(
              f"Task '{name}' input '{inp}' not in slots")
      for sn in task.get("outputs", {}).values():
        if sn not in self._slot_set:
          self._error(
              f"Task '{name}' output '{sn}' not in slots")
      for req in task.get("requires", []):
        if req not in self._slot_set:
          self._error(
              f"Task '{name}' requires '{req}'"
              " not in slots")
      cond = task.get("condition")
      if cond is not None:
        if callable(cond):
          pass
        elif isinstance(cond, str):
          try:
            compile(cond, f"<task:{name}:condition>", "eval")
          except SyntaxError as e:
            self._error(
                f"Task '{name}' condition syntax error: {e}")
        else:
          self._error(
              f"Task '{name}' condition must be callable"
              f" or string, got {type(cond).__name__}")

  def _check_task_on_failure(self):
    """Validate task on_failure clear_slots and on_exhaust.

    clear_slots referencing unknown names silently no-ops, so
    the intended retry flow never triggers. on_exhaust structure
    is validated by _check_on_exhaust.
    """
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      on_failure = task.get("on_failure")
      if not on_failure:
        continue
      if not isinstance(on_failure, dict):
        self._error(
            f"Task '{name}' on_failure must be a dict")
        continue
      for sn in on_failure.get("clear_slots", []):
        if sn not in self._slot_set:
          self._error(
              f"Task '{name}' on_failure.clear_slots"
              f" references unknown slot '{sn}'")
      on_exhaust = on_failure.get("on_exhaust")
      if on_exhaust is not None:
        self._check_on_exhaust(
            on_exhaust, f"Task '{name}' on_failure.on_exhaust")

  def _check_task_on_complete(self):
    """Validate task on_complete clear_slots references.

    on_complete clear_slots remove filled values so the DAG can
    loop. Unknown slot names silently no-op, so the intended
    reset flow doesn't work properly.
    """
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      on_complete = task.get("on_complete")
      if not on_complete:
        continue
      if not isinstance(on_complete, dict):
        self._error(
            f"Task '{name}' on_complete must be a dict")
        continue
      for sn in on_complete.get("clear_slots", []):
        if sn not in self._slot_set:
          self._error(
              f"Task '{name}' on_complete.clear_slots"
              f" references unknown slot '{sn}'")

  # ── Loop risk checks ───────────────────────────────────

  def _check_loop_risks(self):
    """Detect task configurations that cause infinite loops.

    Checks three patterns: task with no inputs and not terminal
    (fires every call), on_failure without max_retries (unbounded
    retries), and terminal on_complete that doesn't clear all
    inputs (immediate re-fire after reset).
    """
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      if not task.get("inputs", []) and not task.get("terminal"):
        self._error(
            f"Task '{name}' has no inputs and is not"
            " terminal — will fire immediately and may loop")
      on_failure = task.get("on_failure", {})
      if on_failure and "max_retries" not in on_failure:
        self._error(
            f"Task '{name}' has on_failure but no"
            " max_retries — retries would be unbounded")
      if task.get("terminal") and task.get("on_complete"):
        cleared = set(
            task["on_complete"].get("clear_slots", []))
        inputs_s = set(task.get("inputs", []))
        uncovered = inputs_s - cleared
        if uncovered:
          self._warn(
              f"Task '{name}' on_complete doesn't clear"
              f" inputs {uncovered} — may re-fire")

  def _check_circular_requires(self):
    """Detect circular requires chains that stall the flow.

    If slot A requires B and B requires A, neither can ever
    become eligible. Uses DFS cycle detection.
    """
    visited, stack = set(), set()

    def has_cycle(name):
      visited.add(name)
      stack.add(name)
      slot_def = self._slot_map.get(name)
      if slot_def:
        for req in slot_def.get("requires", []):
          if req not in visited:
            if has_cycle(req):
              return True
          elif req in stack:
            return True
      stack.discard(name)
      return False

    for name in self._slot_names:
      if name not in visited:
        if has_cycle(name):
          self._error(
              f"Circular requires involving '{name}'")

  # ── Orphan / reachability / tool checks ─────────────────

  def _check_orphaned_slots(self):
    """Detect slots that have no mechanism to be filled."""
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      sources = _normalize_sources(slot.get("source", "user"))
      if "user" in sources and not slot.get("setter"):
        self._error(
            f"Slot '{name}' has source 'user' but no setter"
            " — cannot be filled by user input")
      if "event" in sources and not slot.get("event_key"):
        self._error(
            f"Slot '{name}' has source 'event' but no"
            " event_key")
      for src in sources:
        if src.startswith("task:"):
          task_ref = src[5:]
          if task_ref not in self._task_names:
            self._error(
                f"Slot '{name}' source references unknown"
                f" task '{task_ref}'")

  def _check_reachability(self):
    """Graph walk from fillable roots to detect unreachable slots/tasks.

    Fillable roots: slots with setter, announce source, event+event_key,
    bootstrap.slot, bootstrap.welcome_slot, gate_slot. Fixed-point
    iteration propagates through requires and task inputs/outputs.
    """
    bootstrap = self._config.get("bootstrap", {})
    if not isinstance(bootstrap, dict):
      bootstrap = {}
    gate_slot = self._config.get("gate_slot")

    fillable: set[str] = set()
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      sources = _normalize_sources(slot.get("source", "user"))
      if slot.get("setter"):
        fillable.add(name)
      if "announce" in sources:
        fillable.add(name)
      if "event" in sources and slot.get("event_key"):
        fillable.add(name)
    if bootstrap.get("slot"):
      fillable.add(bootstrap["slot"])
    if bootstrap.get("welcome_slot"):
      fillable.add(bootstrap["welcome_slot"])
    if gate_slot:
      fillable.add(gate_slot)

    changed = True
    reachable_tasks: set[str] = set()
    while changed:
      changed = False
      for slot in self._slots:
        name = slot.get("name", "<unnamed>")
        if name in fillable:
          continue
        reqs = slot.get("requires", [])
        if not reqs:
          continue
        if all(r in fillable for r in reqs):
          sources = _normalize_sources(
              slot.get("source", "user"))
          has_fill_mechanism = (
              slot.get("setter")
              or "announce" in sources
              or ("event" in sources and slot.get("event_key"))
          )
          if has_fill_mechanism:
            fillable.add(name)
            changed = True
      for task in self._tasks:
        tname = task.get("name", "<unnamed>")
        if tname in reachable_tasks:
          continue
        inputs_ok = all(
            s in fillable for s in task.get("inputs", []))
        reqs_ok = all(
            s in fillable for s in task.get("requires", []))
        if inputs_ok and reqs_ok:
          reachable_tasks.add(tname)
          for slot_name in task.get("outputs", {}).values():
            if slot_name not in fillable:
              fillable.add(slot_name)
              changed = True

    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      if name not in fillable:
        missing = [
            r for r in slot.get("requires", [])
            if r not in fillable]
        if missing:
          self._error(
              f"Slot '{name}' is unreachable: requires"
              f" unfillable {missing}")
        else:
          self._error(f"Slot '{name}' is unreachable")

    for task in self._tasks:
      tname = task.get("name", "<unnamed>")
      if tname not in reachable_tasks:
        missing_inputs = [
            s for s in task.get("inputs", [])
            if s not in fillable]
        missing_reqs = [
            s for s in task.get("requires", [])
            if s not in fillable]
        detail = []
        if missing_inputs:
          detail.append(f"unfillable inputs {missing_inputs}")
        if missing_reqs:
          detail.append(
              f"unfillable requires {missing_reqs}")
        suffix = ": " + ", ".join(detail) if detail else ""
        self._error(
            f"Task '{tname}' is unreachable{suffix}")

  def _check_tool_availability(self):
    """Check that setter/task tools exist in the agent's tool list."""
    if self._available_tools is None:
      return
    for slot in self._slots:
      setter = slot.get("setter")
      if setter and setter not in self._available_tools:
        self._error(
            f"Slot '{slot.get('name', '<unnamed>')}' setter"
            f" '{setter}' not in agent tool list")
    for task in self._tasks:
      tool = task.get("tool")
      if tool and tool not in self._available_tools:
        self._error(
            f"Task '{task.get('name', '<unnamed>')}' tool"
            f" '{tool}' not in agent tool list")
    bootstrap = self._config.get("bootstrap", {})
    if isinstance(bootstrap, dict):
      bt = bootstrap.get("tool")
      if bt and bt not in self._available_tools:
        self._error(
            f"Bootstrap tool '{bt}' not in agent tool list")

  def _check_setter_output_keys(self):
    """Check that setter source code returns the keys the config expects.

    For multi-setters (setter_field), verifies the field name appears
    as a key in the values dict. For simple setters, verifies "value"
    appears in return dicts. Skips if setter source is unavailable or
    unparseable.
    """
    if not self._setter_sources:
      return
    setter_fields: dict[str, list[str]] = {}
    simple_setters: set[str] = set()
    for slot in self._slots:
      setter = slot.get("setter")
      if not setter:
        continue
      field = slot.get("setter_field")
      if field:
        setter_fields.setdefault(setter, []).append(field)
      else:
        simple_setters.add(setter)

    for setter_name, fields in setter_fields.items():
      source = self._setter_sources.get(setter_name)
      if not source:
        continue
      values_keys = _extract_values_dict_keys(source)
      if values_keys is None:
        continue
      for field in fields:
        if field not in values_keys:
          self._error(
              f"Setter '{setter_name}' config expects"
              f" setter_field '{field}' but source code"
              f" never writes values[\"{field}\"]")

    for setter_name in simple_setters:
      source = self._setter_sources.get(setter_name)
      if not source:
        continue
      result = _extract_dict_keys_from_source(source)
      if result is None:
        continue
      all_keys, _ = result
      if "value" not in all_keys:
        self._warn(
            f"Setter '{setter_name}' may not return"
            f" a 'value' key")

  def _check_task_output_keys(self):
    """Check that task tools return the keys declared in outputs.

    Config declares outputs: {result_key: slot_name}. The engine
    reads result[result_key] after the task fires. If the tool
    never returns that key, the output slot is silently never filled.
    """
    if not self._task_tool_sources:
      return
    for task in self._tasks:
      tool_name = task.get("tool")
      outputs = task.get("outputs", {})
      if not tool_name or not outputs:
        continue
      source = self._task_tool_sources.get(tool_name)
      if not source:
        continue
      result = _extract_dict_keys_from_source(source)
      if result is None:
        continue
      all_keys, _ = result
      for result_key in outputs:
        if result_key not in all_keys:
          self._error(
              f"Task '{task.get('name', '<unnamed>')}' expects"
              f" output key '{result_key}' but tool"
              f" '{tool_name}' never returns it")

  def _check_duplicate_setter_mappings(self):
    """Detect multiple slots mapped to the same setter without setter_field.

    The after_tool_callback maps one slot per setter name. If two
    slots point to the same setter without setter_field to
    disambiguate, only the last one in _setter_slots wins.
    """
    simple_setter_users: dict[str, list[str]] = {}
    for slot in self._slots:
      setter = slot.get("setter")
      if not setter:
        continue
      if slot.get("setter_field"):
        continue
      name = slot.get("name", "<unnamed>")
      simple_setter_users.setdefault(setter, []).append(name)
    for setter, slot_names in simple_setter_users.items():
      if len(slot_names) > 1:
        self._error(
            f"Slots {slot_names} all map to setter"
            f" '{setter}' without setter_field —"
            f" only the last will receive values")

  def _check_clear_slots_subset(self):
    """Check that on_failure.clear_slots are inputs of the failing task.

    Clearing a slot that isn't an input to the task doesn't help
    retry it — the task still won't re-fire because its inputs
    haven't changed. Likely a copy-paste error.
    """
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      on_failure = task.get("on_failure")
      if not on_failure or not isinstance(on_failure, dict):
        continue
      clear_slots = set(on_failure.get("clear_slots", []))
      if not clear_slots:
        continue
      inputs = set(task.get("inputs", []))
      extra = clear_slots - inputs
      if extra:
        self._warn(
            f"Task '{name}' on_failure.clear_slots"
            f" {sorted(extra)} are not inputs of the task"
            f" — clearing them won't trigger a retry")

  def _check_announce_dead_config(self):
    """Flag fields on announce slots that have no effect.

    Announce slots auto-fill without user interaction, so ask,
    readback_fmt, validation, and scan_keywords are dead config.
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      sources = _normalize_sources(slot.get("source", "user"))
      if "announce" not in sources:
        continue
      if slot.get("ask"):
        self._warn(
            f"Announce slot '{name}' has 'ask' —"
            " announce slots auto-fill, never prompt")
      if slot.get("readback_fmt"):
        self._warn(
            f"Announce slot '{name}' has 'readback_fmt' —"
            " announce slots are not confirmed by user")
      if slot.get("validation"):
        self._warn(
            f"Announce slot '{name}' has 'validation' —"
            " announce slots auto-fill, never validated")
      if slot.get("scan_keywords"):
        self._warn(
            f"Announce slot '{name}' has 'scan_keywords' —"
            " announce slots don't scan user messages")

  def _check_user_slot_fields(self):
    """Check that user-source slots have ask and hint."""
    bootstrap = self._config.get("bootstrap", {})
    if not isinstance(bootstrap, dict):
      bootstrap = {}
    gate_slot = self._config.get("gate_slot")
    external_slots = set()
    if bootstrap.get("slot"):
      external_slots.add(bootstrap["slot"])
    if gate_slot:
      external_slots.add(gate_slot)

    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      if name in external_slots:
        continue
      sources = _normalize_sources(slot.get("source", "user"))
      if "user" not in sources:
        continue
      if not slot.get("setter"):
        continue
      if not slot.get("ask"):
        self._warn(
            f"Slot '{name}' has source 'user' but no 'ask'"
            " — LLM gets no prompt hint for this slot")
      if not slot.get("hint"):
        self._warn(
            f"Slot '{name}' has source 'user' but no 'hint'"
            " — gate mode shows raw slot name in tool list")

  def _check_terminal_task_feedback(self):
    """Check that terminal tasks provide user feedback."""
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      if not task.get("terminal"):
        continue
      has_then_say = bool(task.get("then_say"))
      has_directive = bool(task.get("then_directive"))
      has_response = bool(task.get("then_response"))
      if not has_then_say and not has_directive and not has_response:
        self._warn(
            f"Terminal task '{name}' has no 'then_say',"
            " 'then_directive', or 'then_response' —"
            " flow ends with no user feedback")

  def _check_task_output_source_alignment(self):
    """Check that task outputs and slot sources agree.

    If a task declares outputs: {key: slot_name}, that slot should
    have source including 'task:TaskName'. Conversely, a slot with
    source 'task:X' should appear in task X's outputs.
    """
    task_output_slots: dict[str, set[str]] = {}
    for task in self._tasks:
      tname = task.get("name", "<unnamed>")
      for slot_name in task.get("outputs", {}).values():
        task_output_slots.setdefault(tname, set()).add(slot_name)

    for task in self._tasks:
      tname = task.get("name", "<unnamed>")
      for slot_name in task.get("outputs", {}).values():
        if slot_name not in self._slot_map:
          continue
        slot_def = self._slot_map[slot_name]
        sources = _normalize_sources(
            slot_def.get("source", "user"))
        expected = f"task:{tname}"
        if expected not in sources:
          self._warn(
              f"Task '{tname}' outputs to slot"
              f" '{slot_name}' but slot source"
              f" {sources} doesn't include '{expected}'")

    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      sources = _normalize_sources(slot.get("source", "user"))
      for src in sources:
        if not src.startswith("task:"):
          continue
        task_name = src[5:]
        if task_name not in self._task_names:
          continue
        output_slots = task_output_slots.get(task_name, set())
        if name not in output_slots:
          self._warn(
              f"Slot '{name}' declares source '{src}'"
              f" but task '{task_name}' has no output"
              f" pointing to '{name}'")

  def _check_exhaust_tool_exists(self):
    """Check that on_exhaust.then tool references exist."""
    if self._available_tools is None:
      return
    known = self._available_tools | {
        "end_session", "transfer_to_agent",
    }

    def _check_then(exhaust, context):
      if not isinstance(exhaust, dict):
        return
      then = exhaust.get("then")
      if isinstance(then, dict):
        tool = then.get("tool")
        if tool and tool not in known:
          self._error(
              f"{context}.then tool '{tool}'"
              " not in agent tool list")

    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      validation = slot.get("validation")
      if isinstance(validation, dict):
        on_exhaust = validation.get("on_exhaust")
        if on_exhaust:
          _check_then(
              on_exhaust,
              f"Slot '{name}' validation.on_exhaust")
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      on_failure = task.get("on_failure")
      if isinstance(on_failure, dict):
        on_exhaust = on_failure.get("on_exhaust")
        if on_exhaust:
          _check_then(
              on_exhaust,
              f"Task '{name}' on_failure.on_exhaust")
    sb = self._config.get("steer_back")
    if isinstance(sb, dict):
      on_exhaust = sb.get("on_exhaust")
      if on_exhaust:
        _check_then(on_exhaust, "steer_back.on_exhaust")

  def _check_options_from_ordering(self):
    """Warn if options_from references a slot filled after this one.

    options_from reads a filled slot's value to build chip options.
    If the source slot requires this slot (or is otherwise ordered
    after it), the options will be empty when the prompt fires.
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      options_from = self._find_options_from(slot)
      if not options_from or options_from not in self._slot_map:
        continue
      source_slot = self._slot_map[options_from]
      source_reqs = source_slot.get("requires", [])
      if name in source_reqs:
        self._warn(
            f"Slot '{name}' options_from '{options_from}'"
            f" but '{options_from}' requires '{name}'"
            " — options won't be filled yet")

  def _check_empty_strings(self):
    """Flag empty strings in user-facing fields."""
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      for field in ("ask", "message"):
        val = slot.get(field)
        if val is not None and isinstance(val, str) and not val.strip():
          self._warn(
              f"Slot '{name}' has empty '{field}' string")
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      for field in ("then_say", "then_directive"):
        val = task.get(field)
        if val is not None and isinstance(val, str) and not val.strip():
          self._warn(
              f"Task '{name}' has empty '{field}' string")

  def _check_ask_text_response_conflict(self):
    """Flag slots that have both 'ask' and a text-type response part.

    If a slot has 'ask', the engine uses it as the user prompt. A
    text-type response part would also emit text, creating duplicate
    or conflicting prompts. Use one or the other.
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      if not slot.get("ask"):
        continue
      for resp in slot.get("response", []):
        if not isinstance(resp, dict):
          continue
        if resp.get("type") == "text":
          self._warn(
              f"Slot '{name}' has both 'ask' and a text-type"
              " response — these are redundant; use one or"
              " the other")
          break

  # ── Bootstrap / gate / top-level checks ────────────────

  def _check_bootstrap(self):
    """Validate bootstrap slot, welcome_slot, and tool references.

    bootstrap.slot and gate_slot are often filled externally (by
    the Root Agent) so missing from local slots is a warning.
    welcome_slot should point to an announce slot.
    """
    bootstrap = self._config.get("bootstrap")
    if not bootstrap:
      return
    if not isinstance(bootstrap, dict):
      self._error("'bootstrap' must be a dict")
      return
    slot = bootstrap.get("slot")
    if slot and slot not in self._slot_set:
      self._warn(
          f"bootstrap.slot '{slot}' not in slots"
          " — may be filled externally")
    welcome = bootstrap.get("welcome_slot")
    if welcome and welcome not in self._slot_set:
      self._warn(
          f"bootstrap.welcome_slot '{welcome}'"
          " not in slots")
    if welcome and welcome in self._slot_map:
      ws = self._slot_map[welcome]
      sources = _normalize_sources(
          ws.get("source", "user"))
      if "announce" not in sources:
        self._warn(
            f"bootstrap.welcome_slot '{welcome}'"
            " is not an announce slot")
    if not bootstrap.get("tool"):
      self._warn("bootstrap has no 'tool'")

  def _check_gate_slot(self):
    """Validate that gate_slot references a known slot if present.

    gate_slot is typically filled by the Root Agent's bootstrap
    tool, so it may not exist in this DAG's slots list (warn).
    """
    gate_slot = self._config.get("gate_slot")
    if gate_slot and gate_slot not in self._slot_set:
      self._warn(
          f"gate_slot '{gate_slot}' not in slots"
          " — may be filled externally")

  def _check_steer_back(self):
    """Validate steer_back thresholds and ordering.

    Thresholds must be ordered (soft <= hard <= escalate) and
    must be ints. Non-int values cause TypeError at runtime.
    """
    sb = self._config.get("steer_back")
    if not sb:
      return
    if not isinstance(sb, dict):
      self._error("'steer_back' must be a dict")
      return
    for key in ("soft_after", "hard_after", "escalate_after"):
      val = sb.get(key)
      if val is not None and not isinstance(val, int):
        self._error(
            f"steer_back.{key} must be an int")
    soft = sb.get("soft_after", 2)
    hard = sb.get("hard_after", 4)
    escalate = sb.get("escalate_after", 6)
    if (isinstance(soft, int) and isinstance(hard, int)
        and isinstance(escalate, int)):
      if not (soft <= hard <= escalate):
        self._error(
            "steer_back ordering violated:"
            f" soft_after ({soft}) <= hard_after ({hard})"
            f" <= escalate_after ({escalate})")
    on_exhaust = sb.get("on_exhaust")
    if on_exhaust is not None:
      self._check_on_exhaust(
          on_exhaust, "steer_back.on_exhaust")

  # ── Shared helpers ─────────────────────────────────────

  def _check_on_exhaust(self, exhaust, context):
    """Validate on_exhaust structure and 'then' action.

    Args:
      exhaust: The on_exhaust config dict to validate.
      context: Human-readable label for error messages.

    'then' can be a string action name or a dict with tool+args.
    A dict without 'tool' produces {"name": None} at runtime.
    """
    if not isinstance(exhaust, dict):
      self._error(f"{context} must be a dict")
      return
    then = exhaust.get("then")
    if then is not None:
      if isinstance(then, str):
        pass
      elif isinstance(then, dict):
        if not then.get("tool"):
          self._error(
              f"{context}.then dict missing 'tool'")
      else:
        self._error(
            f"{context}.then must be string or dict")

  def _check_response_parts(self):
    """Validate response part types and required fields.

    Each part must have a 'type' field. Payload parts need 'data'
    to be a dict.
    """
    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      self._validate_response_list(
          slot.get("response"), f"Slot '{name}'")
    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      self._validate_response_list(
          task.get("then_response"), f"Task '{name}'")
      on_failure = task.get("on_failure", {})
      if isinstance(on_failure, dict):
        self._validate_response_list(
            on_failure.get("retry_response"),
            f"Task '{name}' on_failure")

  def _validate_response_list(
      self, response: Any, context: str,
  ):
    """Validate a list of response parts."""
    if response is None:
      return
    if not isinstance(response, list):
      self._error(f"{context} response must be a list")
      return
    for i, part in enumerate(response):
      if not isinstance(part, dict):
        self._error(
            f"{context} response[{i}] must be a dict")
        continue
      rp_type = part.get("type")
      if not rp_type:
        self._error(
            f"{context} response[{i}] missing 'type'")
      elif rp_type not in _VALID_RESPONSE_TYPES:
        self._warn(
            f"{context} response[{i}] type"
            f" '{rp_type}' not standard")
      if rp_type == "payload" and "data" in part:
        if not isinstance(part["data"], dict):
          self._error(
              f"{context} response[{i}] payload"
              " 'data' must be a dict")

  def _check_format_string_placeholders(self):
    """Warn on format string placeholders referencing unknown names.

    Format strings like ask, message, and then_say use {slot_name}
    placeholders. Warn-only since some come from task outputs.
    """
    all_known = self._slot_set | {"success", "error"}
    for task in self._tasks:
      for sn in task.get("outputs", {}).values():
        all_known.add(sn)
      for key in task.get("outputs", {}):
        all_known.add(key)

    for slot in self._slots:
      name = slot.get("name", "<unnamed>")
      for field_name in ("ask", "message"):
        template = slot.get(field_name)
        if not template or not isinstance(template, str):
          continue
        fields = _extract_format_fields(template)
        for f in fields:
          if f not in all_known:
            self._warn(
                f"Slot '{name}' {field_name} references"
                f" unknown placeholder '{{{f}}}'")

    for task in self._tasks:
      name = task.get("name", "<unnamed>")
      for field_name in ("then_say",):
        template = task.get(field_name)
        if not template or not isinstance(template, str):
          continue
        fields = _extract_format_fields(template)
        for f in fields:
          if f not in all_known:
            self._warn(
                f"Task '{name}' {field_name} references"
                f" unknown placeholder '{{{f}}}'")


class CrossConfigValidator:
  """Validates interactions between multiple DAG configs sharing one SM.

  Multiple DAG configs share a single state machine dict (sm) that
  persists across agent transfers. This validator catches cross-config
  failure modes that single-config validation cannot detect.

  Usage:
      configs = {"bella_notte": {...}, "takeout": {...}}
      result = CrossConfigValidator(configs).validate()
  """

  def __init__(self, configs: dict[str, dict[str, Any]]):
    self._configs = configs
    self._errors: list[str] = []
    self._warnings: list[str] = []

  def validate(self) -> ValidationResult:
    """Run all cross-config checks and return results."""
    if len(self._configs) < 2:
      return ValidationResult(valid=True)
    self._check_status_contamination()
    self._check_task_name_collision()
    self._check_welcome_slot_shadow()
    self._check_shared_slot_no_condition()
    self._check_retry_counter_leakage()
    self._check_steer_back_counter_carryover()
    self._check_gate_slot_consistency()
    self._check_bootstrap_tool_consistency()
    return ValidationResult(
        valid=len(self._errors) == 0,
        errors=list(self._errors),
        warnings=list(self._warnings),
    )

  def _error(self, msg: str):
    self._errors.append(msg)

  def _warn(self, msg: str):
    self._warnings.append(msg)

  def _check_status_contamination(self):
    """Ensure configs with terminal tasks have reset_on_complete.

    Terminal tasks set sm.status='complete'. Before_model_callback
    checks status AFTER config_id switch but does NOT reset it,
    so a subsequent config's engine never runs.
    """
    for config_id, config in self._configs.items():
      has_terminal = any(
          t.get("terminal") for t in config.get("tasks", [])
      )
      if not has_terminal:
        continue
      bootstrap = config.get("bootstrap", {})
      if not isinstance(bootstrap, dict):
        bootstrap = {}
      if not bootstrap.get("reset_on_complete"):
        others = [c for c in self._configs if c != config_id]
        self._error(
            f"Config '{config_id}' has terminal tasks but"
            f" bootstrap.reset_on_complete is not True —"
            f" after completion, configs {others} will see"
            " status='complete' and their engine will not run")

  def _check_task_name_collision(self):
    """Detect task names shared across multiple configs.

    task_results is a flat dict keyed by task name. Collisions
    cause one config to see another's result as its own.
    """
    task_owners: dict[str, list[str]] = {}
    for config_id, config in self._configs.items():
      for task in config.get("tasks", []):
        name = task.get("name")
        if name:
          task_owners.setdefault(name, []).append(config_id)
    for task_name, owners in task_owners.items():
      if len(owners) > 1:
        self._error(
            f"Task '{task_name}' defined in configs"
            f" {owners} — task_results will be corrupted")

  def _check_welcome_slot_shadow(self):
    """Warn if announce slots with the same name exist in 2+ configs.

    Announce slots auto-fill filled[name]=True. The second config
    sees it as already filled and skips its announcement.
    reset_on_complete does NOT clear filled.
    """
    slot_announce_owners: dict[str, list[str]] = {}
    for config_id, config in self._configs.items():
      for slot in config.get("slots", []):
        name = slot.get("name")
        sources = _normalize_sources(
            slot.get("source", "user"))
        if name and "announce" in sources:
          slot_announce_owners.setdefault(
              name, []).append(config_id)
    for slot_name, owners in slot_announce_owners.items():
      if len(owners) > 1:
        self._warn(
            f"Announce slot '{slot_name}' in configs {owners}"
            " — second config's announcement will be skipped"
            " because filled['{slot_name}'] persists")

  def _check_shared_slot_no_condition(self):
    """Warn if shared user slots lack scoping conditions.

    User-source slots sharing a name across configs risk unintended
    data reuse. A scoping condition prevents this.
    """
    slot_owners: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for config_id, config in self._configs.items():
      for slot in config.get("slots", []):
        name = slot.get("name")
        sources = _normalize_sources(
            slot.get("source", "user"))
        if name and "user" in sources:
          slot_owners.setdefault(name, []).append(
              (config_id, slot))
    for slot_name, entries in slot_owners.items():
      if len(entries) < 2:
        continue
      unconditioned = [
          cid for cid, s in entries if not s.get("condition")
      ]
      if len(unconditioned) == len(entries):
        configs = [cid for cid, _ in entries]
        self._warn(
            f"Slot '{slot_name}' shared by configs {configs}"
            " without scoping conditions — data filled by"
            " one config will be silently reused by the other")
      elif unconditioned:
        conditioned = [
            cid for cid, s in entries if s.get("condition")
        ]
        self._warn(
            f"Slot '{slot_name}' shared: {unconditioned} have"
            f" no condition, {conditioned} do — asymmetric"
            " scoping may cause unintended reuse")

  def _check_retry_counter_leakage(self):
    """Warn if shared slots both define max_retries.

    _retries is keyed by 'slot:{name}' and persists across config
    switches, so shared slots share retry budgets.
    """
    slot_retry_owners: dict[str, list[str]] = {}
    for config_id, config in self._configs.items():
      for slot in config.get("slots", []):
        name = slot.get("name")
        validation = slot.get("validation")
        if (name and validation
            and isinstance(validation, dict)
            and validation.get("max_retries")):
          slot_retry_owners.setdefault(
              name, []).append(config_id)
    for slot_name, owners in slot_retry_owners.items():
      if len(owners) > 1:
        self._warn(
            f"Slot '{slot_name}' has validation.max_retries"
            f" in configs {owners} — retry counter"
            " 'slot:{slot_name}' carries over between them")

  def _check_steer_back_counter_carryover(self):
    """Warn if steer_back thresholds differ across configs.

    _steer_back_turns persists across config switches, so
    different thresholds can cause premature escalation.
    """
    steer_configs: dict[str, dict[str, Any]] = {}
    for config_id, config in self._configs.items():
      sb = config.get("steer_back")
      if sb and isinstance(sb, dict):
        steer_configs[config_id] = sb
    if len(steer_configs) < 2:
      return
    thresholds = set()
    for sb in steer_configs.values():
      thresholds.add((
          sb.get("soft_after", 2),
          sb.get("hard_after", 4),
          sb.get("escalate_after", 6),
      ))
    if len(thresholds) > 1:
      self._warn(
          "steer_back thresholds differ across configs"
          f" {list(steer_configs.keys())} —"
          " _steer_back_turns carries over and may trigger"
          " premature escalation in the receiving config")

  def _check_gate_slot_consistency(self):
    """Warn if configs use different gate_slot names.

    The Root Agent's bootstrap tool typically fills a single
    gate slot name, so differing names may leave a gate unfilled.
    """
    gate_slots: dict[str, list[str]] = {}
    for config_id, config in self._configs.items():
      gs = config.get("gate_slot")
      if gs:
        gate_slots.setdefault(gs, []).append(config_id)
    if len(gate_slots) > 1:
      self._warn(
          f"Different gate_slot names across configs:"
          f" {dict(gate_slots)} — Root Agent's bootstrap"
          " may only fill one of them")

  def _check_bootstrap_tool_consistency(self):
    """Warn if configs use different bootstrap tools.

    The after_tool callback's tool.name==bootstrap['tool'] check
    won't match for configs with a different bootstrap tool,
    so reset_on_complete can't fire.
    """
    bootstrap_tools: dict[str, list[str]] = {}
    for config_id, config in self._configs.items():
      bootstrap = config.get("bootstrap", {})
      if isinstance(bootstrap, dict):
        tool = bootstrap.get("tool")
        if tool:
          bootstrap_tools.setdefault(
              tool, []).append(config_id)
    if len(bootstrap_tools) > 1:
      self._warn(
          f"Different bootstrap tools across configs:"
          f" {dict(bootstrap_tools)} — reset_on_complete"
          " may not fire for configs with a different"
          " bootstrap tool than the one called by Root Agent")


# ── CES tool entry point ──────────────────────────────────


def validate_dag_config(
    input_data: dict[str, Any],
) -> dict[str, Any]:
  """Validate DAG config(s) for structural and cross-config issues.

  Args:
    input_data: Dict with either 'raw_config' (single config) or
      'all_configs' (dict of config_id to config for cross-config).

  Returns:
    Dict with 'valid', 'errors', 'warnings'. When all_configs is
    provided, also includes 'per_config' and 'cross_config' dicts.
  """
  all_configs = input_data.get("all_configs")
  available_tools = input_data.get("available_tools")
  if all_configs:
    per_config = {}
    combined_errors: list[str] = []
    combined_warnings: list[str] = []
    for config_id, config in all_configs.items():
      r = DagConfigValidator(
          config, available_tools=available_tools,
      ).validate()
      per_config[config_id] = {
          "valid": r.valid,
          "errors": r.errors,
          "warnings": r.warnings,
      }
      combined_errors.extend(
          f"[{config_id}] {e}" for e in r.errors)
      combined_warnings.extend(
          f"[{config_id}] {w}" for w in r.warnings)
    cross = CrossConfigValidator(all_configs).validate()
    combined_errors.extend(cross.errors)
    combined_warnings.extend(cross.warnings)
    return {
        "valid": len(combined_errors) == 0,
        "errors": combined_errors,
        "warnings": combined_warnings,
        "per_config": per_config,
        "cross_config": {
            "valid": cross.valid,
            "errors": cross.errors,
            "warnings": cross.warnings,
        },
    }

  raw_config = input_data.get("raw_config", {})
  available_tools = input_data.get("available_tools")
  result = DagConfigValidator(
      raw_config, available_tools=available_tools,
  ).validate()
  return {
      "valid": result.valid,
      "errors": result.errors,
      "warnings": result.warnings,
  }
