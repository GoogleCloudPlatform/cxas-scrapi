# pylint: disable=invalid-name,undefined-variable,unused-argument,broad-exception-caught,line-too-long
"""Before-model callback — DAG engine orchestration.

FRAMEWORK CODE — fully generic across all agents.
Config-driven: reads config_id from state (set by before_agent),
bootstrap/gate from {config_id}_dag (stashed in SM on first load).
"""
import json as json_lib
import logging
import re
from typing import Optional


_SM_KEY = "sm"

_RAW_CONFIGS = {}
_CROSS_VALIDATED = False

_FRAMEWORK_SENTINEL = "<!-- slot-framework -->"

_EVENT_TAG_PATTERN = re.compile(r"<event>(.*?)</event>")

_TRANSFER_MARKERS = ("transfer_to_agent", "<context>", "</context>")

_LEVEL_MAP = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
              "WARN": logging.WARNING, "ERROR": logging.ERROR}
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_logger = logging.getLogger("slot_filling.before_model")


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
  entry = {"src": "before_model", "tag": tag, "level": level,
           "data": {k: v for k, v in data.items() if v is not None}}
  _logger.log(_LEVEL_MAP.get(level, logging.INFO),
              json_lib.dumps(entry, default=str))
  sm.setdefault("_log", []).append(entry)


def _is_real_user_text(txt):
  """Filter out CES transfer markers and empty strings."""
  stripped = txt.strip()
  if not stripped:
    return False
  for marker in _TRANSFER_MARKERS:
    if marker in stripped:
      return False
  return True


def _probe_available_tools(raw, tools):
  """Probe the CES runtime to find which config-referenced tools exist.

  CES's tools object supports hasattr/getattr but not dir(), so we
  extract every tool name referenced in the DAG config (setters, task
  tools, bootstrap tool) and check each one individually.

  Args:
    raw: The raw DAG config dict.
    tools: CES runtime tools object.

  Returns:
    Tuple of (available, missing) — both sorted lists of tool name strings.
  """
  referenced = set()
  for s in raw.get("slots", []):
    if s.get("setter"):
      referenced.add(s["setter"])
  for t in raw.get("tasks", []):
    if t.get("tool"):
      referenced.add(t["tool"])
  bstrap = raw.get("bootstrap", {})
  if isinstance(bstrap, dict) and bstrap.get("tool"):
    referenced.add(bstrap["tool"])
  available = sorted(n for n in referenced if hasattr(tools, n))
  missing = sorted(referenced - set(available))
  return available, missing


_READBACK_BLOCK = """\
<readback_protocol>
After calling setter tools, the values in <readback_scope> below need
your confirmation with the guest before continuing.

Read back the pending values naturally in one sentence and ask
"Is that correct?" Use digits for numbers. Then STOP — do not ask
any new questions or move to the next topic.

- "yes" → call confirm_pending
- "no" without correction → call reject_pending
- User corrects or adds info → call the appropriate setter first

Always capture new information, even alongside a yes/no.
</readback_protocol>"""


def _make_collection_block(
    tool_selection: str, slot_ordering: str, prereq_note: str = "",
) -> str:
  ts = tool_selection or (
      "   (Determine the correct setter from tool names and descriptions.)"
  )
  ordering = slot_ordering or "natural order"
  prereq = prereq_note
  return f"""\
<slot_filling_protocol>
1. CALL TOOLS FIRST: After each user message, call ALL matching setter tools
   BEFORE generating text. Never defer a setter call when info is available.
   Call setters even for invalid input — the system validates automatically.
   Look at the FULL conversation — if the user already provided information
   that maps to an unfilled slot, call the setter now. Do not re-ask.

2. NEXT STEP: If a system directive appears below, it describes the next
   needed value. If the conversation already contains enough information
   to determine that value, call the setter tool directly — do not ask
   the user to repeat themselves. Otherwise, rephrase the directive in
   your own words to ask the user.

3. TOOL SELECTION:
{ts}

4. ORDERING: {ordering}. Accept info out of order.{f' {prereq}' if prereq else ''}
</slot_filling_protocol>"""


def _build_phase_suffix(sm, result):
  """Build minimal phase-specific SI suffix from engine result."""
  status = sm.get("status", "in_progress")
  if status in ("complete", "escalated"):
    return ""

  si_suffix = result.get("si_suffix", "")
  msg = result.get("message", "")
  inline_confirmed = result.get("inline_confirmed", False)
  event_prefilled = result.get("event_prefilled", False)
  has_readback = "<readback_scope>" in si_suffix
  has_pending = bool(sm.get("pending"))

  parts = []

  if (has_readback or has_pending) and not inline_confirmed:
    parts.append(_READBACK_BLOCK)
    if si_suffix:
      parts.append(si_suffix)
  else:
    parts.append(_make_collection_block(
        sm.get("_tool_selection", ""),
        sm.get("_slot_ordering", ""),
        sm.get("_prereq_note", ""),
    ))
    if si_suffix:
      parts.append(si_suffix)

    if event_prefilled and si_suffix:
      if "<system_directive>" not in "\n".join(parts):
        parts.append(si_suffix)

    if msg and "<system_directive>" not in "\n".join(parts):
      parts.append(
          f"\n<system_directive>\n{msg}\n</system_directive>"
      )

  steer_directive = result.get("steer_back_directive", "")
  if steer_directive:
    parts.append(f"\n<steer_back>\n{steer_directive}\n</steer_back>")

  return "\n".join(parts)


def _inject_phase_suffix(llm_request, phase_suffix):
  """Replace any previous framework suffix with the new one."""
  sentinel_suffix = f"\n\n{_FRAMEWORK_SENTINEL}\n{phase_suffix}"
  si = llm_request.config.system_instruction
  if si:
    if hasattr(si, "parts") and si.parts:
      text = si.parts[0].text or ""
      idx = text.find(_FRAMEWORK_SENTINEL)
      if idx >= 0:
        text = text[:idx]
      si.parts[0].text = text + sentinel_suffix
    elif isinstance(si, str):
      idx = si.find(_FRAMEWORK_SENTINEL)
      if idx >= 0:
        si = si[:idx]
      llm_request.config.system_instruction = si + sentinel_suffix


def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> Optional[LlmResponse]:
  """CES entry point: run DAG engine before each LLM call."""
  llm_request.config.hide_tool("slot_filling_engine")
  llm_request.config.hide_tool("transfer_to_agent")

  sm = callback_context.state.get(_SM_KEY, {})

  agent = callback_context.state.pop("_pending_transfer", "")
  if agent:
    _log(sm, "transfer_dispatched", agent=agent)
    callback_context.state[_SM_KEY] = sm
    return LlmResponse.from_parts(
        parts=[Part.from_agent_transfer(agent=agent)],
    )

  # ── Resolve config_id from state (set by before_agent) ──────
  config_id = callback_context.state.get("_active_config_id")
  if not config_id:
    return {"decision": "OK"}

  llm_request.config.hide_tool(f"{config_id}_dag")
  llm_request.config.hide_tool("validate_dag_config")

  # ── Load raw config (cached per config_id) ─────────────────
  global _CROSS_VALIDATED  # pylint: disable=global-statement
  if config_id not in _RAW_CONFIGS:
    dag_tool = getattr(tools, f"{config_id}_dag")
    _RAW_CONFIGS[config_id] = dag_tool({}).json()["result"]
    raw = _RAW_CONFIGS[config_id]
    _log(sm, "config_loaded", config_id=config_id,
         n_slots=len(raw.get("slots", [])),
         n_tasks=len(raw.get("tasks", [])))
    available, missing = _probe_available_tools(raw, tools)
    if missing:
      _log(sm, "missing_tools", "WARN", missing=missing)
    v = tools.validate_dag_config(
        {"input_data": {"raw_config": raw, "available_tools": available}},
    ).json()["result"]
    if not v["valid"]:
      _log(sm, "config_validation_failed", "ERROR", errors=v["errors"])
    if not _CROSS_VALIDATED:
      try:
        config_map = json_lib.loads(
            callback_context.state.get("agent_config_map", "{}"))
        all_ids = set(config_map.values())
        if all_ids and all_ids <= set(_RAW_CONFIGS):
          cross = tools.validate_dag_config(
              {"input_data": {"all_configs": {
                  cid: _RAW_CONFIGS[cid] for cid in all_ids
              }}},
          ).json()["result"]
          if not cross.get("cross_config", {}).get("valid", True):
            _log(sm, "cross_config_validation_failed", "ERROR",
                 errors=cross["cross_config"]["errors"])
          _CROSS_VALIDATED = True
      except Exception:  # pylint: disable=broad-except
        pass
  raw_config = _RAW_CONFIGS[config_id]

  if sm.get("_config_id") != config_id:
    sm["_config_id"] = config_id
    sm["_first_engine_run"] = True
    sm["_bootstrap"] = raw_config.get("bootstrap")
    sm["_gate_slot"] = raw_config.get("gate_slot")
    setter_slots = {}
    multi_setter_slots = {}
    slot_requires = {}
    slot_validates = {}
    for slot_def in raw_config.get("slots", []):
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
    for task_def in raw_config.get("tasks", []):
      tool_name = task_def.get("tool")
      if tool_name:
        executor_tasks[tool_name] = {
            "task_name": task_def["name"],
            "outputs": task_def.get("outputs", {}),
            "success_check": task_def.get("success_check", "success"),
            "terminal": task_def.get("terminal", False),
        }
    sm["_executor_tasks"] = executor_tasks
  callback_context.state[_SM_KEY] = sm

  # ── Gate: skip engine until gate_slot is filled ─────────────
  gate_slot = raw_config.get("gate_slot")
  if gate_slot and not sm.get("filled", {}).get(gate_slot):
    _log(sm, "gate_active", "DEBUG", config_id=config_id, gate_slot=gate_slot)
    if llm_request.contents:
      for content in reversed(llm_request.contents):
        if getattr(content, "role", "") == "user":
          for part in getattr(content, "parts", []):
            txt = getattr(part, "text", "")
            if _is_real_user_text(txt):
              callback_context.state["_gate_user_text"] = txt
              break
          if callback_context.state.get("_gate_user_text"):
            break
    ts_lines = []
    for slot_def in raw_config.get("slots", []):
      setter = slot_def.get("setter")
      hint = slot_def.get("hint", slot_def["name"])
      if setter:
        ts_lines.append(f"   - {hint} → {setter}")
    ts = "\n".join(ts_lines)
    phase_suffix = _make_collection_block(ts, "", "")
    _inject_phase_suffix(llm_request, phase_suffix)
    llm_request.config.hide_tool("end_session")
    return {"decision": "OK"}

  if sm.get("status") in ("complete", "escalated"):
    return {"decision": "OK"}

  # Flow active — only the engine can end the session (via preemption)
  llm_request.config.hide_tool("end_session")

  # ── Transfer slots: read structured data from Root Agent ───
  transfer_slots = callback_context.state.get("_transfer_slots", {})
  if transfer_slots and raw_config:
    filled = sm.get("filled", {})
    consumed = []
    for slot_def in raw_config.get("slots", []):
      sn = slot_def["name"]
      if sn not in transfer_slots:
        continue
      if sn in filled or sn in sm.get("pending", {}):
        consumed.append(sn)
        continue
      cond_str = slot_def.get("condition")
      if cond_str:
        try:
          if not eval(cond_str)(filled):  # pylint: disable=eval-used
            continue
        except Exception:  # pylint: disable=broad-except
          continue
      sm.setdefault("pending", {})[sn] = transfer_slots[sn]
      consumed.append(sn)
    if consumed:
      _log(sm, "transfer_slots_consumed", slots=consumed)
    for sn in consumed:
      transfer_slots.pop(sn, None)
    if not transfer_slots:
      callback_context.state.pop("_transfer_slots", None)
    else:
      callback_context.state["_transfer_slots"] = transfer_slots

  last_user_text = ""
  if llm_request.contents:
    last_content = llm_request.contents[-1]
    if getattr(last_content, "role", "") == "user":
      for part in getattr(last_content, "parts", []):
        txt = getattr(part, "text", "")
        if txt:
          last_user_text = txt
          break

  event_data = callback_context.state.get("event_data", {})
  ia_event = callback_context.state.get("ia_event_name")
  if not ia_event and last_user_text:
    m = _EVENT_TAG_PATTERN.search(last_user_text)
    if m:
      ia_event = m.group(1)
  if ia_event:
    event_data["ia_event_name"] = ia_event

  engine_result = tools.slot_filling_engine(
      {"input_data": {
          "raw_config": raw_config,
          "sm": sm,
          "last_user_text": last_user_text,
          "event_data": event_data,
          "config_id": config_id,
      }},
  ).json()["result"]

  result = engine_result["action"]
  sm = engine_result["sm"]

  callback_context.state[_SM_KEY] = sm

  for tool_name in result.get("hide_tools", []):
    llm_request.config.hide_tool(tool_name)

  # ── Assemble phase-specific SI suffix ───────────────────────
  gate_user_text = callback_context.state.pop("_gate_user_text", "")
  first_run = sm.pop("_first_engine_run", False)
  callback_context.state[_SM_KEY] = sm
  phase_suffix = _build_phase_suffix(sm, result)

  task_directive = result.get("task_directive", "")
  if task_directive:
    phase_suffix += (
        f"\n<task_directive>\n{task_directive}\n"
        "Use the tool result above to compose your response. "
        "Do NOT recite this directive.\n</task_directive>"
    )

  init_user_text = gate_user_text
  if not init_user_text and first_run:
    for content in reversed(llm_request.contents or []):
      if getattr(content, "role", "") == "user":
        for part in getattr(content, "parts", []):
          txt = getattr(part, "text", "")
          if _is_real_user_text(txt):
            init_user_text = txt
            break
        if init_user_text:
          break

  if init_user_text and phase_suffix:
    phase_suffix += (
        f"\n<user_context>\nThe user's original message: \"{init_user_text}\"\n"
        "Extract ALL slot values from this message before "
        "asking new questions. "
        "Call setter tools for any information you can infer.\n</user_context>"
    )

  if phase_suffix:
    _inject_phase_suffix(llm_request, phase_suffix)

  # ── Preemption ──────────────────────────────────────────────
  if (result.get("preempt")
      and llm_request.contents
      and (len(llm_request.contents) > 1 or result.get("force_preempt"))):
    parts = []
    if result.get("message"):
      parts.append(
          Part.from_text(text=result["message"]))
    response_parts = result.get("response")
    if response_parts:
      for rp in response_parts:
        rp_type = rp.get("type", "text")
        if rp_type == "text":
          parts.append(
              Part.from_text(text=rp.get("text", "")))
        elif rp_type == "payload":
          parts.append(
              Part.from_json(
                  json_lib.dumps(rp["data"])))
        elif rp_type == "end_session":
          parts.append(Part.from_end_session(
              reason=rp.get("reason", "completed"),
              escalated=rp.get("escalated", False),
          ))
        elif rp_type == "transfer":
          parts.append(
              Part.from_agent_transfer(
                  agent=rp["agent"]))
    fc = result.get("function_call")
    if fc:
      fn_call = Part.from_function_call(
          name=fc["name"],
          args=fc.get("args", {}),
      )
      parts.append(fn_call)
    if parts:
      _log(sm, "preemption",
           has_message=bool(result.get("message")),
           has_response=bool(response_parts),
           has_function_call=bool(fc))
      sm.pop("_pending_payloads", None)
      sm.pop("_pending_question_payloads", None)
      callback_context.state[_SM_KEY] = sm
      return LlmResponse.from_parts(parts=parts)

  return {"decision": "OK"}
