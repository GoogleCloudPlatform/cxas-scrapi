# pylint: disable=invalid-name,undefined-variable,unused-argument,broad-exception-caught,line-too-long
"""Before-model callback — DAG engine orchestration.

FRAMEWORK CODE — fully generic across all agents.
Config-driven: reads config_id from state (set by before_agent),
bootstrap/gate from {config_id}_dag (stashed in SM on first load).
"""

import json as json_lib
import re
from typing import Optional


_SM_KEY = "sm"

_RAW_CONFIGS = {}

_FRAMEWORK_SENTINEL = "<!-- slot-framework -->"

_EVENT_TAG_PATTERN = re.compile(r"<event>(.*?)</event>")

_TRANSFER_MARKERS = ("transfer_to_agent", "<context>", "</context>")


def _is_real_user_text(txt):
  """Filter out CES transfer markers and empty strings."""
  stripped = txt.strip()
  if not stripped:
    return False
  for marker in _TRANSFER_MARKERS:
    if marker in stripped:
      return False
  return True


def _prefill_from_conversation(sm, contents, slots):
  """Scan conversation history and pre-fill slots with scan_keywords."""
  filled = sm.get("filled", {})
  pending = sm.get("pending", {})
  for slot_def in slots:
    scan_kw = slot_def.get("scan_keywords")
    if not scan_kw:
      continue
    slot_name = slot_def["name"]
    if slot_name in filled or slot_name in pending:
      continue
    cond_str = slot_def.get("condition")
    if cond_str:
      try:
        if not eval(cond_str)(filled):  # pylint: disable=eval-used
          continue
      except Exception:  # pylint: disable=broad-except
        continue
    for content in (contents or []):
      if getattr(content, "role", "") != "user":
        continue
      for part in getattr(content, "parts", []):
        txt = getattr(part, "text", "")
        if not _is_real_user_text(txt):
          continue
        lower = txt.lower()
        for keyword, value in scan_kw.items():
          if keyword in lower:
            sm.setdefault("pending", {})[slot_name] = value
            return

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
  prereq = f" {prereq_note}" if prereq_note else ""
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

4. ORDERING: {ordering}. Accept info out of order.{prereq}
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

  agent = callback_context.state.pop("_pending_transfer", "")
  if agent:
    return LlmResponse.from_parts(
        parts=[Part.from_agent_transfer(agent=agent)],
    )

  sm = callback_context.state.get(_SM_KEY, {})

  # ── Resolve config_id from state (set by before_agent) ──────
  config_id = callback_context.state.get("_active_config_id")
  if not config_id:
    return {"decision": "OK"}

  llm_request.config.hide_tool(f"{config_id}_dag")

  # ── Load raw config (cached per config_id) ─────────────────
  if config_id not in _RAW_CONFIGS:
    dag_tool = getattr(tools, f"{config_id}_dag")
    _RAW_CONFIGS[config_id] = dag_tool({}).json()["result"]
  raw_config = _RAW_CONFIGS[config_id]

  if sm.get("_config_id") != config_id:
    sm["_config_id"] = config_id
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
    for sn in consumed:
      transfer_slots.pop(sn, None)
    if not transfer_slots:
      callback_context.state.pop("_transfer_slots", None)
    else:
      callback_context.state["_transfer_slots"] = transfer_slots

  # ── Fallback: pre-fill from conversation history ───────────
  _prefill_from_conversation(
      sm, llm_request.contents, raw_config.get("slots", []),
  )
  callback_context.state[_SM_KEY] = sm

  debug = sm.get("_debug_mode", False)

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
          **({"debug": True} if debug else {}),
      }},
  ).json()["result"]

  result = engine_result["action"]
  sm = engine_result["sm"]

  if debug:
    sm["_debug_log"] = engine_result.get("_debug_log", [])

  callback_context.state[_SM_KEY] = sm

  for tool_name in result.get("hide_tools", []):
    llm_request.config.hide_tool(tool_name)

  # ── Assemble phase-specific SI suffix ───────────────────────
  gate_user_text = callback_context.state.pop("_gate_user_text", "")
  phase_suffix = _build_phase_suffix(sm, result)

  if gate_user_text and phase_suffix:
    phase_suffix += (
        f"\n<user_context>\nThe user's original message: \"{gate_user_text}\"\n"
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
      sm.pop("_pending_payloads", None)
      sm.pop("_pending_question_payloads", None)
      return LlmResponse.from_parts(parts=parts)

  return {"decision": "OK"}
