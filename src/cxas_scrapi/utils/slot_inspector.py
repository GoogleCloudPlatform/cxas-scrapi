"""Deep inspection and categorization of CES slot machine state."""

from __future__ import annotations

from typing import Any


SLOT_CATEGORIES = {
    "core_data": {
        "label": "Core Data",
        "fields": ["filled", "pending", "deferred", "status", "task_results"],
    },
    "engine_control": {
        "label": "Engine Control",
        "fields": ["_invoke_n", "_last_state", "_retries", "_log", "_log_level"],
    },
    "configuration": {
        "label": "Configuration",
        "fields": [
            "_config_id", "_bootstrap", "_cancel_tool", "_gate_slot",
            "_setter_slots", "_multi_setter_slots", "_slot_requires",
            "_slot_validates", "_executor_tasks", "_correction_tool",
        ],
    },
    "phase_state": {
        "label": "Phase State",
        "fields": [
            "_first_engine_run", "_initialized", "_auto_confirm_pending",
            "_inline_confirm", "_readback_transition", "_deferred_transition",
        ],
    },
    "confirmation": {
        "label": "Confirmation",
        "fields": [
            "_rejection_snapshot", "_rejection_requested", "_correction_pending",
            "_post_correction_readback", "_correction_applied",
        ],
    },
    "steer_back": {
        "label": "Steer-back Control",
        "fields": [
            "_steer_back_turns", "_steer_last_text",
            "_correction_grace_used", "_progress_turns",
        ],
    },
    "system_instruction": {
        "label": "System Instruction Fragments",
        "fields": [
            "_tool_selection", "_slot_ordering", "_prereq_note", "_correction_hint",
        ],
    },
    "payloads": {
        "label": "Payloads",
        "fields": [
            "_pending_payloads", "_pending_question_payloads",
            "_event_prefilled_this_turn",
        ],
    },
    "task_state": {
        "label": "Task State",
        "fields": [
            "_task_just_completed", "_just_completed_task", "_zombie",
        ],
    },
    "transfer": {
        "label": "Transfer",
        "fields": [
            "_cancel_requested", "_pending_transfer", "_transfer_slots",
            "_active_sm_key",
        ],
    },
    "flow_context": {
        "label": "Flow Context",
        "fields": [
            "_shared_slots", "_flow_state", "_flow_instance_seq",
            "_restored_flow", "_auto_resume_deferred",
        ],
    },
    "channel": {
        "label": "Channel",
        "fields": ["channel"],
    },
}


class SlotInspector:
    """Categorizes and inspects raw slot machine state dicts."""

    @staticmethod
    def inspect(slot_machine: dict[str, Any]) -> dict[str, Any]:
        """Categorize all fields, derive phase, build slot DAG.

        Args:
            slot_machine: Raw slot machine dict from CES state.

        Returns:
            JSON-serializable dict with summary, categories, and slot_dag.
        """
        categorized: dict[str, Any] = {}
        all_known_fields: set[str] = set()

        for cat_key, cat_def in SLOT_CATEGORIES.items():
            all_known_fields.update(cat_def["fields"])
            group = {}
            for field_name in cat_def["fields"]:
                if field_name in slot_machine:
                    group[field_name] = slot_machine[field_name]
            if group:
                categorized[cat_key] = {
                    "label": cat_def["label"],
                    "fields": group,
                }

        uncategorized = {
            k: v for k, v in slot_machine.items() if k not in all_known_fields
        }
        if uncategorized:
            categorized["uncategorized"] = {
                "label": "Uncategorized",
                "fields": uncategorized,
            }

        filled = slot_machine.get("filled", {})
        pending = slot_machine.get("pending", {})
        deferred = slot_machine.get("deferred", {})

        flow_state = slot_machine.get("_flow_state", [])
        suspended = []
        for entry in flow_state if isinstance(flow_state, list) else []:
            suspended.append({
                "flow": entry.get("flow", "?"),
                "slots": sorted(entry.get("slots", {}).keys()),
                "pending": sorted(entry.get("pending", {}).keys()),
                "deferred": sorted(entry.get("deferred", {}).keys()),
                "slot_values": dict(entry.get("slots", {})),
                "pending_values": dict(entry.get("pending", {})),
                "deferred_values": dict(entry.get("deferred", {})),
            })

        return {
            "summary": {
                "phase": SlotInspector._derive_phase(slot_machine),
                "status": slot_machine.get("status"),
                "filled_count": len(filled) if isinstance(filled, dict) else 0,
                "pending_count": len(pending) if isinstance(pending, dict) else 0,
                "deferred_count": len(deferred) if isinstance(deferred, dict) else 0,
                "retries": slot_machine.get("_retries", {}),
                "steer_back_turns": slot_machine.get("_steer_back_turns", 0),
                "config_id": slot_machine.get("_config_id"),
                "suspended_flows": suspended,
                "restored_flow": bool(slot_machine.get("_restored_flow")),
                "auto_resume_deferred": bool(
                    slot_machine.get("_auto_resume_deferred")
                ),
            },
            "categories": categorized,
            "slot_dag": SlotInspector._build_slot_dag(slot_machine),
        }

    @staticmethod
    def inspect_from_trace(
        normalized_trace: dict[str, Any],
        turn: int | None = None,
    ) -> dict[str, Any]:
        """Extract slot_machine from normalized trace entries and inspect.

        Walks variable_update and variable_default entries to find the latest
        slot_machine state, optionally up to a specific turn number.

        Args:
            normalized_trace: Output of Traces.get_normalized().
            turn: If set, only consider entries up to this turn (inclusive).

        Returns:
            Result of inspect() on the extracted slot_machine, or inspect({})
            if no slot_machine found.
        """
        entries = normalized_trace.get("entries", [])
        last_sm: dict[str, Any] = {}

        for entry in entries:
            if turn is not None and entry.get("turn", 0) > turn:
                break
            kind = entry.get("kind")
            if kind in ("variable_update", "variable_default"):
                variables = entry.get("variables", {})
                if "slot_machine" in variables:
                    last_sm = variables["slot_machine"]

        return SlotInspector.inspect(last_sm)

    @staticmethod
    def _derive_phase(sm: dict[str, Any]) -> str:
        """Derive the current slot machine phase from state flags."""
        if sm.get("status") in ("complete", "escalated", "zombie"):
            return sm["status"]
        if sm.get("_correction_pending"):
            return "correction"
        if sm.get("_post_correction_readback"):
            return "post_correction_readback"
        pending = sm.get("pending", {})
        if sm.get("_auto_confirm_pending") and pending:
            return "awaiting_confirmation"
        if sm.get("_readback_transition"):
            return "readback_transition"
        if sm.get("_deferred_transition"):
            return "deferred_transition"
        if pending:
            return "fresh_readback"
        if not sm.get("_initialized", True):
            return "uninitialized"
        return "collection"

    @staticmethod
    def _meta_slots(sm: dict[str, Any]) -> set[str]:
        """Identify bootstrap/routing slots that aren't flow data."""
        meta: set[str] = set()
        bootstrap = sm.get("_bootstrap", {})
        if isinstance(bootstrap, dict):
            for key in ("slot", "welcome_slot"):
                val = bootstrap.get(key)
                if val:
                    meta.add(val)
        gate = sm.get("_gate_slot")
        if gate:
            meta.add(gate)
        return meta

    @staticmethod
    def _flow_slots(sm: dict[str, Any]) -> set[str]:
        """Identify slots owned by the current flow's tools."""
        flow: set[str] = set()
        setter = sm.get("_setter_slots", {})
        if isinstance(setter, dict):
            for slots in setter.values():
                if isinstance(slots, str):
                    flow.add(slots)
                elif isinstance(slots, list):
                    flow.update(slots)
        multi = sm.get("_multi_setter_slots", {})
        if isinstance(multi, dict):
            for mapping in multi.values():
                if isinstance(mapping, dict):
                    flow.update(mapping.values())
        ordering = sm.get("_slot_ordering", "")
        if isinstance(ordering, str) and ordering:
            for slot in ordering.replace("→", ",").split(","):
                s = slot.strip()
                if s:
                    flow.add(s)
        return flow

    @staticmethod
    def _build_slot_dag(sm: dict[str, Any]) -> dict[str, Any]:
        """Build a DAG of slot states, dependencies, and flow ownership."""
        filled = sm.get("filled", {})
        pending = sm.get("pending", {})
        deferred = sm.get("deferred", {})
        requires = sm.get("_slot_requires", {})

        meta = SlotInspector._meta_slots(sm)
        flow = SlotInspector._flow_slots(sm)
        shared_list = sm.get("_shared_slots", [])
        shared = set(shared_list) if isinstance(shared_list, list) else set()

        blocked: list[dict[str, Any]] = []
        if isinstance(requires, dict) and isinstance(filled, dict):
            for slot, prereqs in requires.items():
                if isinstance(prereqs, list):
                    unmet = [p for p in prereqs if p not in filled]
                    if unmet and slot not in filled:
                        blocked.append({"slot": slot, "blocked_by": unmet})

        return {
            "filled": sorted(filled.keys()) if isinstance(filled, dict) else [],
            "pending": sorted(pending.keys()) if isinstance(pending, dict) else [],
            "deferred": sorted(deferred.keys()) if isinstance(deferred, dict) else [],
            "blocked": blocked,
            "meta_slots": sorted(meta),
            "flow_slots": sorted(flow),
            "shared_slots": sorted(shared),
        }
