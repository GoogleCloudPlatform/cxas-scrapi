"""Slot filling callback — config + framework.

This callback contains two sections:
1. AGENT CONFIG — agent-specific slot/task definitions (replace per project)
2. FRAMEWORK — reusable DAG engine (do not modify)

Everything lives in one file because CES platform limitations prevent
splitting across tools: (1) tool arguments are JSON-serialized, so
callables (formatters, conditions, executors) can't cross tool boundaries,
(2) tools don't have access to the `tools` global (only callbacks do),
and (3) tool `context.state` may not reflect state changes from other
tools within the same turn — `callback_context.state` always has the
latest values.
"""

import datetime  # noqa: I001
import random
from typing import Any

# ── Affirmative detection (used by auto-confirm) ──────────────────────────────

_AFFIRMATIVES = frozenset({
    "yes", "yeah", "yep", "yup", "yah", "ya",
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
  """True if text is a short, pure affirmative with no correction signals."""
  if not text:
    return False
  normalized = text.lower().strip().rstrip(".,!? ")
  if normalized in _AFFIRMATIVES:
    return True
  # Strip punctuation from each word before comparing, so "Yes, that's
  # correct" and "Yes." work the same as "Yes that's correct".
  words = [w.translate(_STRIP_PUNCT) for w in normalized.split()]
  if len(words) <= 5 and words and words[0] in _AFFIRMATIVES:
    return not any(w in _CORRECTION_SIGNALS for w in words[1:])
  return False


# ═════════════════════════════════════════════════════════════════════
# AGENT CONFIG — Replace this section for each new project
# ═════════════════════════════════════════════════════════════════════


def _get_config() -> dict[str, Any]:
  """Return the complete slot filling configuration."""

  # ─── Formatters ─────────────────────────────────────────────────

  def _fmt_date(v: str) -> str:
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

  def _fmt_time(v: str) -> str:
    try:
      parts = str(v).split(":")
      h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
      period = "AM" if h < 12 else "PM"
      h12 = h % 12 or 12
      return f"at {h12}:{m:02d} {period}"
    except (ValueError, TypeError, IndexError):
      return f"at {v}"

  def _fmt_party_size(v) -> str:
    n = int(v)
    return f"{n} guest{'s' if n != 1 else ''}"

  def _fmt_phone(v) -> str:
    return f"contact phone {v}"

  def _fmt_guest_name(v) -> str:
    return f"under the name {v}"

  def _fmt_special_requests(v) -> str:
    if str(v).lower() not in ("none", "no", "nothing"):
      return f"with special requests: {v}"
    return "with no special requests"

  # ─── Conditions ─────────────────────────────────────────────────

  def _cond_large_party(filled: dict[str, Any]) -> bool:
    return int(filled.get("party_size", 0)) >= 5

  # ─── Executors ──────────────────────────────────────────────────

  def _exec_find_times(filled: dict[str, Any]) -> dict[str, Any]:
    schedule = {
        2: ["6:00 PM", "7:30 PM", "9:00 PM"],
        4: ["7:00 PM", "8:30 PM"],
        6: ["6:00 PM"],
    }
    times = schedule.get(
        int(filled["party_size"]),
        ["6:00 PM", "7:30 PM", "9:00 PM"],
    )
    return {"available_times": ", ".join(times), "success": True}

  def _exec_book(filled: dict[str, Any]) -> dict[str, Any]:
    hash_input = (
        filled["preferred_date"]
        + filled["selected_time"]
        + filled["guest_name"]
    )
    conf = f"BN-{abs(hash(hash_input)) % 10000:04d}"
    return {"confirmation_number": conf, "success": True}

  # ─── Config ─────────────────────────────────────────────────────

  return {
      "slots": [
          {
              "name": "party_size",
              "source": "user",
              "setter": "set_party_size",
              "ask": "How many guests will be dining?",
              "readback_fmt": _fmt_party_size,
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "out_of_range": (
                          "I'm sorry, we accept reservations for"
                          " parties of 1 to 8. For larger parties,"
                          " please contact our events team at"
                          " events@bellanotte.com."
                      ),
                      "parse_error": (
                          "I didn't catch the number of guests."
                          " How many will be dining?"
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the party size."
                          " Please call us at 555-0100 and we'll"
                          " help you directly."
                      ),
                      "then": "escalate",
                  },
              },
          },
          {
              "name": "large_party_phone",
              "source": "user",
              "setter": "set_large_party_phone",
              "condition": _cond_large_party,
              "ask": (
                  "For parties of 5 or more, we require a contact"
                  " phone number in case we need to reach you about"
                  " your reservation. What's the best number?"
              ),
              "readback_fmt": _fmt_phone,
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "invalid_phone": (
                          "I didn't catch a valid phone number."
                          " Could you provide a number with at"
                          " least 7 digits?"
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the phone"
                          " number. Please call us at 555-0100"
                          " and we'll help you directly."
                      ),
                      "then": "escalate",
                  },
              },
          },
          {
              "name": "preferred_date",
              "source": "user",
              "setter": "set_preferred_date",
              "ask": "What date would you like to come in?",
              "readback_fmt": _fmt_date,
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "invalid_format": (
                          "Could you provide the date? For"
                          " example, 2026-06-17 for June 17th."
                      ),
                      "past_date": (
                          "That date is in the past. Could you"
                          " provide a future date? For example,"
                          " 2026-06-17 for June 17th."
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the date."
                          " Please call us at 555-0100 and"
                          " we'll help you directly."
                      ),
                      "then": "escalate",
                  },
              },
          },
          {
              "name": "available_times",
              "source": "task:FindAvailableTimes",
          },
          {
              "name": "selected_time",
              "source": "user",
              "setter": "set_selected_time",
              "requires": ["available_times"],
              "ask": (
                  "We have availability at {available_times}."
                  " Which time works best for you?"
              ),
              "readback_fmt": _fmt_time,
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "prereq_not_met": (
                          "I'd love to get you that time! I just"
                          " need to check availability first. How"
                          " many guests will be joining us, and"
                          " what date works best for you?"
                      ),
                      "not_available": (
                          "That time isn't available. We have"
                          " {available_times}. Which of those"
                          " works best for you?"
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the time"
                          " selection. Please call us at 555-0100"
                          " and we'll help you directly."
                      ),
                      "then": "escalate",
                  },
              },
          },
          {
              "name": "guest_name",
              "source": "user",
              "setter": "set_guest_name",
              "ask": (
                  "What name should I put the reservation under?"
              ),
              "readback_fmt": _fmt_guest_name,
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "empty_name": (
                          "I didn't catch the name. What name"
                          " should I put the reservation under?"
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the name."
                          " Please call us at 555-0100 and"
                          " we'll help you directly."
                      ),
                      "then": "escalate",
                  },
              },
          },
          {
              "name": "special_requests",
              "source": "user",
              "setter": "set_special_requests",
              "ask": (
                  "Do you have any special requests or dietary needs?"
              ),
              "readback_fmt": _fmt_special_requests,
              "requires_readback": True,
          },
          {
              "name": "confirmation_number",
              "source": "task:BookReservation",
          },
      ],
      "tasks": [
          {
              "name": "FindAvailableTimes",
              "inputs": ["party_size", "preferred_date"],
              "outputs": {"available_times": "available_times"},
              "success_check": "success",
              "then_say": (
                  "Great choice! We have availability at"
                  " {available_times}. Which time works best"
                  " for you?"
              ),
              "on_failure": {
                  "retry_say": (
                      "I'm sorry, we don't have availability"
                      " for that date and party size. Could you"
                      " try a different date?"
                  ),
                  "max_retries": 1,
                  "clear_slots": ["preferred_date"],
                  "on_exhaust": {
                      "say": (
                          "I'm unable to find availability for"
                          " your request. Please call us at"
                          " 555-0100 to check for openings."
                      ),
                      "then": "escalate",
                  },
              },
          },
          {
              "name": "BookReservation",
              "inputs": [
                  "party_size",
                  "large_party_phone",
                  "preferred_date",
                  "selected_time",
                  "guest_name",
                  "special_requests",
              ],
              "outputs": {
                  "confirmation_number": "confirmation_number",
              },
              "success_check": "success",
              "terminal": True,
              "then_say": (
                  "Your reservation is confirmed."
                  " Your confirmation number is"
                  " {confirmation_number}. We look forward to"
                  " welcoming you to Bella Notte!"
              ),
              "on_failure": {
                  "retry_say": (
                      "I'm having a bit of trouble completing"
                      " your reservation. Let me try once more."
                  ),
                  "max_retries": 2,
                  "on_exhaust": {
                      "say": (
                          "I'm sorry, I wasn't able to complete"
                          " your reservation. Please call us"
                          " directly at 555-0100 and we'll get"
                          " you sorted."
                      ),
                      "then": "escalate",
                  },
              },
          },
      ],
      "executors": {
          "FindAvailableTimes": _exec_find_times,
          "BookReservation": _exec_book,
      },
      "readback_tools": ["confirm_pending", "reject_pending"],
      "confirm_transition_prefix": [
          "Wonderful!",
          "Perfect!",
          "Great!",
          "Excellent!",
          "Lovely!",
      ],
      "readback_retry": {
          "max_retries": 2,
          "on_exhaust": {
              "say": (
                  "I'm having trouble processing your"
                  " reservation details. Please call us at"
                  " 555-0100 and we'll help you directly."
              ),
              "then": "escalate",
          },
      },
      "progress_stall": {
          "max_turns": 4,
          "on_exhaust": {
              "say": (
                  "I'm having trouble completing your"
                  " reservation. Please call us at 555-0100"
                  " and we'll help you directly."
              ),
              "then": "escalate",
          },
      },
  }


# ═════════════════════════════════════════════════════════════════════
# FRAMEWORK — Reusable DAG engine (do not modify per project)
# ═════════════════════════════════════════════════════════════════════


def before_model_callback(callback_context, llm_request):
  """CES before_model_callback entry point — thin adapter.

  CES calls this before EACH model invocation, including after tool
  results within the same turn. That re-invocation is what lets the
  framework see state changes from setter tools and react immediately
  (e.g., fire a DAG task as soon as its inputs are filled).

  Args:
    callback_context: CES callback context with session state.
    llm_request: CES LLM request object for tool visibility control.

  Returns:
    LlmResponse to preempt the LLM, or {"decision": "OK"} to proceed.
  """
  sm = callback_context.state.get("sm", {})

  if sm.get("status") in ("complete", "escalated"):
    return {"decision": "OK"}

  config = _get_config()
  _validate_config(config)

  # Extract last user text only when this is an initial-user-turn call
  # (last content is role="user"). Post-tool callbacks have a function
  # response as the last content — don't extract there, so auto-confirm
  # never fires on a re-invocation after a tool result.
  last_user_text = ""
  if llm_request.contents:
    last_content = llm_request.contents[-1]
    if getattr(last_content, "role", "") == "user":
      for part in getattr(last_content, "parts", []):
        txt = getattr(part, "text", "")
        if txt:
          last_user_text = txt
          break

  result = _run_slot_filling(config, sm, last_user_text=last_user_text)

  # Only update system_message when there's something meaningful to say
  # (collection phase next-question, fresh readback directive, task fire,
  # error, etc.). For the awaiting_confirmation phase (pending exists but
  # not fresh), before_agent_callback already set the correct directive via
  # static substitution — don't overwrite it with a stale readback message.
  msg = result.get("message", "")
  if msg:
    callback_context.state["system_message"] = msg

  for tool_name in result.get("hide_tools", []):
    llm_request.config.hide_tool(tool_name)

  # Preempt only when contents > 1 (i.e., after tool results, not on
  # the initial user message). On the first invocation (just the user
  # message), we let the LLM respond so it can call setter tools.
  if (result.get("preempt")
      and llm_request.contents
      and len(llm_request.contents) > 1):
    return LlmResponse.from_parts(  # noqa: F821  # pylint: disable=undefined-variable
        parts=[Part.from_text(  # noqa: F821  # pylint: disable=undefined-variable
            text=result["message"]
        )]
    )

  return {"decision": "OK"}


def _run_slot_filling(
    config: dict[str, Any],
    sm: dict[str, Any],
    last_user_text: str = "",
) -> dict[str, Any]:
  """Orchestrate one turn of slot filling. Mutates sm in place.

  Args:
    config: Slot filling configuration from _get_config().
    sm: Session state dict (callback_context.state["sm"]). Mutated in place.
    last_user_text: The user's message text from the current turn (empty
      when called after a tool result). Used for auto-confirm detection.

  Returns:
    Dict with keys: "hide_tools", "preempt", "message" (raw text for
    preemption and system_message state update).
  """
  slots = config["slots"]
  tasks = config["tasks"]
  executors = config["executors"]
  readback_tools = config["readback_tools"]
  readback_retry = config["readback_retry"]
  progress_stall = config["progress_stall"]
  _prefix_cfg = config.get("confirm_transition_prefix", "")
  if isinstance(_prefix_cfg, list):
    confirm_transition_prefix = (
        random.choice(_prefix_cfg) if _prefix_cfg else ""
    )
  else:
    confirm_transition_prefix = _prefix_cfg

  slot_map = {s["name"]: s for s in slots}

  sm["_invoke_n"] = sm.get("_invoke_n", 0) + 1
  inv_n = sm["_invoke_n"]

  filled = sm.get("filled", {})
  pending = sm.get("pending", {})
  task_results = sm.get("task_results", {})

  # ── State change detection ────────────────────────────────────
  # Compare current filled/pending to what we saw last invocation.
  # Any change means a tool ran and made progress, so reset stall
  # counters. Also clear retry counters for newly-filled/pending
  # slots — a successful fill means earlier errors are resolved.
  current_state = {"filled": filled, "pending": pending}
  last_state = sm.get("_last_state", {})

  if current_state != last_state:
    sm["_progress_turns"] = 0
    sm.pop("_readback_stall", None)

    retries = sm.get("_retries", {})
    last_filled = last_state.get("filled", {})
    last_pending = last_state.get("pending", {})
    for name in set(filled) - set(last_filled):
      retries.pop(f"slot:{name}", None)
    for name in set(pending) - set(last_pending):
      retries.pop(f"slot:{name}", None)
    # If pending was cleared (user confirmed), reset readback retries
    if last_pending and not pending:
      retries.pop("readback", None)

    _log_progress(filled, pending, last_state)

  # ── Auto-promote: slots without readback go straight to filled ─
  # Setter tools always write to pending first. Slots that don't
  # need user confirmation (requires_readback=False) skip the
  # readback cycle and promote directly to filled.
  readback_set = {
      s["name"] for s in slots if s.get("requires_readback")
  }
  auto_promoted = [k for k in pending if k not in readback_set]
  for name in auto_promoted:
    filled[name] = pending.pop(name)

  # ── Auto-deactivation: clear slots whose condition is False ────
  # Conditional slots may become inactive if the user corrects a
  # value that the condition depends on. Remove them from
  # filled/pending so the DAG doesn't wait for them.
  retries = sm.setdefault("_retries", {})
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

  # Snapshot state AFTER auto-promote and deactivation, so next
  # invocation compares against the post-processed state.
  sm["_last_state"] = {
      "filled": dict(filled),
      "pending": dict(pending),
  }

  # ── Detect fresh setter turn ──────────────────────────────────
  # True when any new key appeared in pending this invocation. Hides
  # confirm_pending/reject_pending so the LLM must read back the
  # values and wait for explicit user confirmation before proceeding.
  # Without this, the LLM sometimes calls reject_pending → setter →
  # confirm_pending all in one turn, skipping the readback step.
  #
  # Uses set-difference (not empty→non-empty) so it also fires when
  # a new slot is added mid-readback (e.g. user adds a special
  # request while confirming name/date/party). In that case pending
  # was already non-empty, so the old bool(pending) and not
  # bool(last_pending) check would have been False, causing the
  # callback to suppress the readback message and the LLM to just
  # acknowledge the new info instead of issuing a fresh readback.
  last_pending = last_state.get("pending", {})
  fresh_pending = bool(set(pending) - set(last_pending))

  # ── Phase snapshot ────────────────────────────────────────────
  if pending:
    phase = "fresh_readback" if fresh_pending else "awaiting_confirmation"
  else:
    phase = "collection"

  # ── Auto-confirm ──────────────────────────────────────────────
  # When the user sends a pure affirmative ("yes", "correct", etc.)
  # during awaiting_confirmation, merge pending → filled directly
  # instead of relying on the LLM to call confirm_pending. This makes
  # the confirmation transition 100% deterministic. Only fires on the
  # initial user-turn callback (last_user_text is empty after tool
  # results, so this never double-fires within the same turn).
  if (phase == "awaiting_confirmation"
      and last_user_text
      and _is_affirmative(last_user_text)):
    filled.update(pending)
    sm["filled"] = filled
    sm["pending"] = {}
    pending = {}
    sm["_readback_transition"] = True
    sm["_progress_turns"] = 0   # confirmation = progress; reset stall
    sm.pop("_readback_stall", None)
    phase = "collection"
    fresh_pending = False
    _log("auto_confirm", user_msg=last_user_text)

  # ── Tool visibility ───────────────────────────────────────────
  hide_tools = _compute_hidden_tools(
      slots, filled, pending, readback_tools, slot_map,
      fresh_pending=fresh_pending,
  )

  # ── Slot error handling ───────────────────────────────────────
  # Setter tools write errors to sm["_slot_errors"] (e.g.,
  # out_of_range, parse_error). We consume them here and preempt
  # with the configured error message so the LLM doesn't invent
  # its own error text.
  error_msg, _ = _handle_slot_errors(sm, slots)
  if error_msg:
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                preempted=error_msg)
    return {
        "hide_tools": hide_tools,
        "preempt": True,
        "message": error_msg,
    }

  # ── Readback transition flag ──────────────────────────────────
  # Set by confirm_pending tool when the user confirms a readback.
  # Consumed here (popped) so it's one-shot. Used below to: (1) skip
  # stall counting on the confirmation turn, and (2) prepend
  # "Wonderful!" to the next DAG message for a natural transition.
  readback_transition = sm.pop("_readback_transition", False)

  # ── Readback stall detection ──────────────────────────────────
  # If pending values exist but the LLM isn't getting the user to
  # confirm (no state change for 3 consecutive callback invocations),
  # clear pending and retry the readback. After max_retries failed
  # readback cycles, escalate.
  #
  # Skip when reject_pending was called this turn (_rejection_snapshot
  # present). reject_pending is deferred — it doesn't clear pending
  # immediately — so without this guard the stall counter keeps running
  # even though the user actively said "no". The snapshot signals that
  # the rejection will be resolved at the start of the next turn.
  if pending and not readback_transition and "_rejection_snapshot" not in sm:
    stall = sm.get("_readback_stall", 0) + 1
    sm["_readback_stall"] = stall
    if stall >= 3:
      sm["pending"] = {}
      pending = {}
      sm.pop("_readback_stall", None)
      retries = sm.setdefault("_retries", {})
      retries["readback"] = retries.get("readback", 0) + 1
      _log("readback_stall", retries=retries["readback"])
      max_rb = readback_retry.get("max_retries", 2)
      if retries["readback"] >= max_rb:
        exhaust = readback_retry.get("on_exhaust", {})
        if exhaust.get("then") == "escalate":
          sm["status"] = "escalated"
        msg = exhaust.get("say", "Please call us for help.")
        _log_invoke(inv_n, "readback_stall", filled, {}, False, hide_tools,
                    preempted=msg)
        return {
            "hide_tools": hide_tools,
            "preempt": True,
            "message": msg,
        }

  # ── Progress stall detection ──────────────────────────────────
  # Counts user turns without any actionable progress. Gate on
  # last_user_text so post-tool and post-preemption callbacks
  # (where last_user_text is empty) don't inflate the count.
  # Resets to 0 whenever state changes (above) or auto-confirm fires.
  if last_user_text:
    progress = sm.get("_progress_turns", 0) + 1
    sm["_progress_turns"] = progress
  else:
    progress = sm.get("_progress_turns", 0)
  max_turns = progress_stall.get("max_turns", 8)
  if progress >= max_turns:
    _log("progress_stall", turns=progress)
    exhaust = progress_stall.get("on_exhaust", {})
    if exhaust.get("then") == "escalate":
      sm["status"] = "escalated"
    msg = exhaust.get("say", "Please call us for help.")
    _log_invoke(inv_n, "progress_stall", filled, pending, fresh_pending,
                hide_tools, preempted=msg)
    return {
        "hide_tools": hide_tools,
        "preempt": True,
        "message": msg,
    }

  # ── DAG evaluation ────────────────────────────────────────────
  dag_result = _compute_dag_state(
      tasks, slots, filled, pending, task_results, slot_map,
  )

  # ── Execute the action ────────────────────────────────────────
  msg = _execute_dag_step(
      sm, dag_result, executors, tasks, slots, slot_map,
  )

  # Re-save _last_state to include any task outputs. Without this, task-filled
  # slots appear as stale "progress task+" events on the next invocation even
  # though they were already captured in the fired entry above.
  sm["_last_state"] = {"filled": dict(filled), "pending": dict(pending)}

  # For awaiting_confirmation (pending exists, not fresh), the system_message
  # was already set by before_agent_callback via static substitution. Return
  # empty so we don't overwrite it with the stale readback directive.
  if dag_result["action"] == "awaiting_readback" and not fresh_pending:
    msg = ""

  # Preempt when a task fires — the framework controls the exact
  # message (with values from the executor result) so the LLM
  # doesn't hallucinate different values.
  preempt = False
  if dag_result["action"] == "fire" and msg:
    preempt = True

  # After user confirms a readback, bridge to the next question
  # with "Wonderful!" for a natural transition. Apply even when a
  # task fires on the same turn (preempt already True) — without
  # this, confirm → task-fire skips the warm opener entirely.
  if readback_transition and msg and confirm_transition_prefix:
    if not msg.lower().startswith(confirm_transition_prefix.lower()):
      msg = f"{confirm_transition_prefix} {msg}"
    if not preempt:
      preempt = True

  # ── Single compact log entry per invocation ───────────────────
  action = dag_result["action"]
  if preempt:
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                preempted=msg, fired=dag_result.get("task_name"))
  elif action == "next_question":
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                asking=msg or dag_result.get("system_message", ""))
  elif action == "awaiting_readback" and fresh_pending:
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                reading_back=dag_result.get("system_message", ""))
  else:
    _log_invoke(inv_n, phase, filled, pending, fresh_pending, hide_tools,
                done=(action == "all_done"))

  return {
      "hide_tools": hide_tools,
      "preempt": preempt,
      "message": msg,
  }


# ═════════════════════════════════════════════════════════════════════
# FRAMEWORK INTERNALS
# ═════════════════════════════════════════════════════════════════════


def _validate_config(config):
  """Validate config at call time. Raises ValueError on errors."""
  slots = config["slots"]
  tasks = config["tasks"]
  executors = config["executors"]
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
    if task["name"] not in executors:
      raise ValueError(
          f"Task '{task['name']}' has no executor"
      )
    condition = task.get("condition")
    if condition is not None and not callable(condition):
      raise ValueError(
          f"Task '{task['name']}' has non-callable condition"
      )

  for slot in slots:
    source = slot.get("source", "user")
    if source.startswith("task:"):
      src_task = source[5:]
      if src_task not in task_names:
        raise ValueError(
            f"Slot '{slot['name']}' references unknown task"
            f" '{src_task}'"
        )
    condition = slot.get("condition")
    if condition is not None and not callable(condition):
      raise ValueError(
          f"Slot '{slot['name']}' has non-callable condition"
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


def _log(tag, **data):
  """Print a structured debug entry to stdout."""
  parts = " ".join(f"{k}={v!r}" for k, v in data.items())
  print(f"[slot-filling:{tag}]" + (f" {parts}" if parts else ""))


def _log_progress(filled, pending, last_state):
  """Print slot state changes caused by tool calls."""
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
                preempted=None):
  """Print one compact entry per callback invocation."""
  _log("invoke",
       n=n, phase=phase,
       **({} if not filled else {"filled": sorted(filled)}),
       **({} if not pending else {"pending": sorted(pending)}),
       **({} if not fresh_pending else {"fresh": True}),
       **({} if not hidden else {"hidden": sorted(hidden)}),
       **({} if asking is None else {"asking": asking[:80]}),
       **({} if reading_back is None else {"rb": reading_back[:80]}),
       **({} if fired is None else {"fired": fired}),
       **({} if not done else {"done": True}),
       **({} if preempted is None else {"preempted": preempted[:80]}))


def _is_slot_active(slot_def, filled):
  """Check whether a conditional slot is active given current filled values.

  Defaults to active on exception — safer to ask for an unnecessary
  slot than to silently skip a required one.

  Args:
    slot_def: Slot definition dict from config.
    filled: Dict of currently filled slot values.

  Returns:
    True if the slot is active (or has no condition), False otherwise.
  """
  condition = slot_def.get("condition")
  if condition is None:
    return True
  try:
    return bool(condition(filled))
  except Exception:  # pylint: disable=broad-except
    return True


def _is_task_active(task_def, filled):
  """Check whether a conditional task is active given current filled values."""
  condition = task_def.get("condition")
  if condition is None:
    return True
  try:
    return bool(condition(filled))
  except Exception:  # pylint: disable=broad-except
    return True


def _resolve_formatter(fmt):
  """Resolve a readback_fmt value to a callable or None."""
  if fmt is None:
    return None
  if callable(fmt):
    return fmt
  return None


def _build_readback(slots, pending, filled):
  """Build the readback action dict for pending slot values.

  Args:
    slots: list of slot definition dicts.
    pending: current pending slots dict.
    filled: current filled slots dict.

  Returns:
    Dict with "action" and "system_message" keys, or None if no active
    pending slots exist.
  """
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
  return {
      "action": "awaiting_readback",
      "system_message": f"Just to confirm — {summary}. Is that correct?",
  }


def _find_next_question(slots, filled, pending, slot_map):
  """Find the first unfilled user slot whose deps are met.

  Slot order in config defines the natural conversation flow. A slot
  is skipped if: inactive (condition=False), already filled/pending,
  not user-sourced (task-sourced slots are filled by executors), or
  its requires deps aren't met. A dep counts as met if it's filled
  OR if the dep slot itself is inactive (conditional slot whose
  condition is False), so inactive deps never block progress.

  Args:
    slots: List of slot definition dicts from config.
    filled: Dict of currently filled slot values.
    pending: Dict of values awaiting user confirmation.
    slot_map: Dict mapping slot names to their definitions.

  Returns:
    Action dict with "action" and "message" keys.
  """
  for slot_def in slots:
    name = slot_def["name"]
    if not _is_slot_active(slot_def, filled):
      continue
    if name in filled or name in pending:
      continue
    if slot_def.get("source", "user") != "user":
      continue
    requires = slot_def.get("requires", [])
    # A required slot counts as satisfied if filled, or if it's
    # inactive (conditional slot whose condition is False).
    if not all(
        req in filled
        or not _is_slot_active(slot_map[req], filled)
        for req in requires
    ):
      continue
    ask_template = slot_def.get("ask", f"Please provide {name}.")
    ask = ask_template.format(**filled)
    return {"action": "next_question", "system_message": ask}
  return {
      "action": "all_done",
      "system_message": "All information collected!",
  }


def _compute_dag_state(
    tasks, slots, filled, pending, task_results, slot_map,
):
  """Evaluate the DAG. Pure function — no mutations.

  Priority order: (1) readback pending values, (2) fire ready tasks,
  (3) ask next question. This ensures readback always happens before
  tasks fire, and tasks fire as soon as their inputs are satisfied
  (even if later slots are still unfilled).

  Args:
    tasks: List of task definition dicts from config.
    slots: List of slot definition dicts from config.
    filled: Dict of currently filled slot values.
    pending: Dict of values awaiting user confirmation.
    task_results: Dict of completed task results keyed by task name.
    slot_map: Dict mapping slot names to their definitions.

  Returns:
    Action dict describing the next step (readback, fire, question, or done).
  """
  if pending:
    rb = _build_readback(slots, pending, filled)
    if rb is not None:
      return rb

  for task in tasks:
    task_name = task["name"]
    success_key = task.get("success_check", "success")

    if (task_name in task_results
        and task_results[task_name].get(success_key)):
      continue

    if not _is_task_active(task, filled):
      continue

    # Only check active inputs — inactive conditional slots don't
    # block the task.
    active_inputs = [
        s for s in task["inputs"]
        if _is_slot_active(slot_map[s], filled)
    ]
    if not all(s in filled for s in active_inputs):
      continue

    return {
        "action": "fire",
        "task_name": task_name,
        "task_def": task,
    }

  return _find_next_question(slots, filled, pending, slot_map)


def _handle_slot_errors(sm, slots):
  """Process slot validation errors.

  Setter tools don't return error messages directly — they append
  error codes to sm["_slot_errors"] and return {error: True}. This
  function maps those codes to human-readable messages from the
  config, tracks retry counts, and escalates when retries exhaust.

  Args:
    sm: Session state dict. Mutated to pop errors and update retries.
    slots: List of slot definition dicts from config.

  Returns:
    Tuple of (error_message, exhausted). error_message is None if no errors.
  """
  errors = sm.pop("_slot_errors", [])
  if not errors:
    return None, False

  retries = sm.setdefault("_retries", {})
  filled = sm.get("filled", {})
  messages = []

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
      if exhaust.get("then") == "escalate":
        sm["status"] = "escalated"
      msg = exhaust.get(
          "say", "An error occurred. Please call us for help.",
      )
      try:
        msg = msg.format(**filled)
      except KeyError:
        pass
      _log("slot_error_exhaust", slot=slot_name)
      return msg, True

    error_messages = validation.get("errors", {})
    msg = error_messages.get(
        error_code, "Could you try that again?",
    )
    try:
      msg = msg.format(**filled)
    except KeyError:
      pass
    messages.append(msg)

  if not messages:
    return None, False

  combined = " ".join(messages)
  return combined, False


def _execute_dag_step(
    sm, dag_result, executors, tasks, slots, slot_map,
):
  """Execute the DAG action and update session state."""
  action = dag_result["action"]
  filled = sm.get("filled", {})
  retries = sm.setdefault("_retries", {})
  task_results = sm.get("task_results", {})

  if action in ("awaiting_readback", "next_question", "all_done"):
    return dag_result.get("system_message", "")

  # Cascading loop: after a task fires successfully, re-evaluate the
  # DAG — its outputs may satisfy another task's inputs. This avoids
  # waiting for an extra LLM round-trip between chained tasks.
  while action == "fire":
    task_def = dag_result["task_def"]
    task_name = task_def["name"]
    executor = executors[task_name]
    success_key = task_def.get("success_check", "success")
    try:
      result = executor(filled)
    except Exception:  # pylint: disable=broad-except
      _log("task", name=task_name, ok=False, error="exception")
      result = {success_key: False}

    is_success = result.get(success_key)
    if is_success:
      # Verify all declared outputs are present in the result
      outputs = task_def.get("outputs", {})
      if any(k not in result for k in outputs):
        is_success = False

    _log("task", name=task_name, ok=bool(is_success))

    if is_success:
      task_results[task_name] = result
      for result_key, slot_name in (
          task_def.get("outputs", {}).items()
      ):
        filled[slot_name] = result[result_key]
      if task_def.get("terminal"):
        sm["status"] = "complete"
      retries.pop(task_name, None)
      msg = task_def.get("then_say", "")
      if msg:
        msg = msg.format(**filled)

      if task_def.get("terminal"):
        return msg

      # Re-evaluate DAG to cascade into the next ready task
      dag_result = _compute_dag_state(
          tasks, slots, filled, sm.get("pending", {}),
          task_results, slot_map,
      )
      action = dag_result["action"]
      if action == "fire":
        continue
      return dag_result.get("system_message", msg)

    retries[task_name] = retries.get(task_name, 0) + 1
    on_failure = task_def.get("on_failure", {})
    max_retries = on_failure.get("max_retries", 0)

    for slot_name in on_failure.get("clear_slots", []):
      filled.pop(slot_name, None)

    if retries[task_name] >= max_retries:
      exhaust = on_failure.get("on_exhaust", {})
      if exhaust.get("then") == "escalate":
        sm["status"] = "escalated"
      _log("task_exhaust", name=task_name)
      msg = exhaust.get("say", "An error occurred.")
      return msg

    msg = on_failure.get("retry_say", "Let me try again.")
    return msg

  return ""


def _compute_hidden_tools(
    slots, filled, pending, readback_tools, slot_map,
    *, fresh_pending=False,
):
  """Compute which tools to hide this turn.

  Tool visibility is the primary mechanism for guiding the LLM's
  behavior. By hiding tools, we prevent the LLM from calling them
  at the wrong time — more reliable than instruction-only guardrails.

  Two modes:
  - pending exists: readback phase — show confirm/reject and all
    setters for unfilled active slots whose deps are met (same rules
    as collection). This allows corrections (calling a setter to
    overwrite a pending value) and additions (setting a new slot the
    user volunteers). If pending is fresh (setter just ran this turn),
    hide confirm/reject to force the LLM to read back first.
  - no pending: collection phase — hide confirm/reject, show only
    setters for unfilled active slots whose deps are met.

  Args:
    slots: List of slot definition dicts from config.
    filled: Dict of currently filled slot values.
    pending: Dict of values awaiting user confirmation.
    readback_tools: List of readback tool names (confirm/reject).
    slot_map: Dict mapping slot names to their definitions.
    fresh_pending: True if pending values were just set this turn.

  Returns:
    List of tool names to hide.
  """
  hidden = []
  if pending:
    if fresh_pending:
      hidden.extend(readback_tools)
  else:
    hidden.extend(readback_tools)
  # Same setter visibility rules for both readback and collection:
  # hide inactive, filled, or deps-not-met. During fresh_pending (setter
  # just ran), also hide the setter for any slot already in pending —
  # preventing the LLM from re-calling the same setter and accidentally
  # skipping the readback step.
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
  return hidden
