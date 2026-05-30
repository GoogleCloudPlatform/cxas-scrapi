# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Rich-based terminal renderer for interactive chat sessions.

Uses the same visual language as cxas trace HTML/text output:
- User messages: blue background
- Agent messages: green background
- Tool calls: red/dark with collapsible args
- Transfers: cyan
- Variables: gold/yellow
"""

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from cxas_scrapi.core.chat_session import TurnRecord

# Slash commands available during interactive chat sessions.
SLASH_COMMANDS = {
    "/help": "Show available commands",
    "/trace": "Fetch and display the trace for this session",
    "/state": "Show current variable/slot state",
    "/slots": "Deep slot machine inspection (phase, DAG, all fields)",
    "/slots <category>": "Show one category (e.g., core_data, configuration)",
    "/slots flows": "List all flows with status (active/suspended/idle)",
    "/slots flow:<name>": "Inspect saved slots in a suspended flow",
    "/spans": "Show execution spans for the last turn",
    "/bug <reason>": "Bundle this conversation as a bug report",
    "/save <path>": "Export the conversation to a file",
    "/clear": "Clear the terminal",
    "/quit": "End the session",
}


# ---------------------------------------------------------------------------
# /log timeline constants
# ---------------------------------------------------------------------------

TAG_PHASE: dict[str, str] = {
    # init
    "config_loaded": "init",
    "config_registered": "init",
    "dag_initialized": "init",
    # flow
    "bootstrap_stored": "flow",
    "bootstrap_transfer": "flow",
    "transfer_dispatched": "flow",
    "flow_state_saved": "flow",
    "flow_state_restored": "flow",
    "flow_deferred": "flow",
    "flow_resumed": "flow",
    "cancel_flow": "flow",
    "cancel_requested": "flow",
    # collection
    "invoke": "collection",
    "progress": "collection",
    "auto_confirm": "collection",
    "auto_confirm_inline": "collection",
    "readback_transition": "collection",
    "deferred_transition": "collection",
    "skip_deferred": "collection",
    # setter
    "setter_stored": "setter",
    "multi_setter_stored": "setter",
    "event_prefill": "setter",
    "announce_stored": "setter",
    # validation
    "slot_error": "validation",
    "slot_error_retry": "validation",
    "slot_error_exhaust": "validation",
    "slot_validated": "validation",
    "validate_against_fail": "validation",
    "validate_against_pass": "validation",
    # task
    "task": "task",
    "task_dispatched": "task",
    "task_completed": "task",
    "task_failed": "task",
    "task_retry": "task",
    "task_exhaust": "task",
    # steer
    "steer_back_soft": "steer",
    "steer_back_hard": "steer",
    "steer_back_escalate": "steer",
    "steer_back_reset": "steer",
    "progress_turns": "steer",
    # correction
    "correction_pending": "correction",
    "correction_applied": "correction",
    "correction_cleared": "correction",
    "post_correction_readback": "correction",
    "rejection_snapshot": "correction",
    # deferred
    "deferred_slot": "deferred",
    "deferred_restored": "deferred",
    "auto_resume_deferred": "deferred",
    # preempt
    "preempt": "preempt",
    "preempt_payload": "preempt",
    "preempt_response": "preempt",
    # zombie
    "zombie_created": "zombie",
    "zombie_reaped_on_reentry": "zombie",
}

PHASE_STYLE: dict[str, str] = {
    "init": "blue",
    "flow": "magenta",
    "collection": "green",
    "setter": "green",
    "validation": "red",
    "task": "cyan",
    "steer": "yellow",
    "correction": "magenta",
    "deferred": "blue",
    "preempt": "dim",
    "zombie": "red",
}

LEVEL_ORDER: dict[str, int] = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}

LEVEL_MARKER: dict[str, tuple[str, str]] = {
    "DEBUG": (" ", "dim"),
    "INFO": ("·", ""),
    "WARN": ("⚠", "yellow"),
    "ERROR": ("✗", "bold red"),
}

_TIMELINE_EVENTS: dict[str, tuple[str, str, str]] = {
    # Flow lifecycle
    "config_loaded": ("FLOW", "▶", "magenta"),
    "bootstrap_stored": ("FLOW", "▶", "magenta"),
    "bootstrap_transfer": ("FLOW", "▶", "magenta"),
    "flow_state_saved": ("FLOW", "▶", "magenta"),
    "flow_state_restored": ("FLOW", "▶", "magenta"),
    "cancel_flow_called": ("FLOW", "▶", "magenta"),
    # Slot collection
    "setter_stored": ("SET", "→", "green"),
    "multi_setter_stored": ("SET", "→", "green"),
    "event_prefill": ("SET", "→", "green"),
    # Confirmation
    "auto_confirm": ("CONFIRM", "✓", "cyan"),
    "auto_confirm_inline": ("CONFIRM", "✓", "cyan"),
    "progress": ("PROGRESS", "→", "green"),
    # Tasks
    "task_completed": ("TASK", "⚙", "cyan"),
    "task_retry": ("TASK", "⚙", "yellow"),
    "task_exhaust": ("TASK", "⚙", "red"),
    # Corrections
    "correction_applied": ("CORRECT", "↻", "yellow"),
    "slot_correction_pending": ("CORRECT", "↻", "yellow"),
    # Errors
    "slot_error": ("ERROR", "✗", "red"),
    "slot_error_exhaust": ("ERROR", "✗", "bold red"),
    "validation_failed": ("ERROR", "✗", "red"),
    # Steer-back
    "steer_back_soft": ("STEER", "⚠", "yellow"),
    "steer_back_hard": ("STEER", "⚠", "yellow"),
    "steer_back_escalate": ("STEER", "⚠", "bold red"),
    # Zombie lifecycle
    "zombie_created": ("ZOMBIE", "💀", "red"),
    "zombie_reaped_on_reentry": ("ZOMBIE", "♻", "green"),
}

_DEDUP_TAGS = frozenset({
    "config_loaded", "bootstrap_transfer",
})


def _trunc(s: str, n: int) -> str:
    """Return *s* truncated to *n* chars with an ellipsis if needed."""
    return s[:n] + "…" if len(s) > n else s


def _format_log_detail(tag: str, data: dict) -> str:
    """Return a compact detail string for a log entry based on its tag."""
    if not data:
        return ""

    if tag == "invoke":
        return (
            f"#{data.get('n', '')} {data.get('phase', '')}  "
            f"filled={data.get('filled', 0)} asking={data.get('asking', '')}"
        )

    if tag in ("setter_stored", "announce_stored", "event_prefill",
               "bootstrap_stored"):
        return (
            f"{data.get('slot', '')} = "
            f"'{_trunc(str(data.get('value', '')), 50)}'"
        )

    if tag == "multi_setter_stored":
        stored = data.get("stored", {})
        return ", ".join(f"{k}={v}" for k, v in stored.items())

    if tag in ("task", "task_dispatched"):
        return f"{data.get('name', '')}"

    if tag == "task_completed":
        return f"{data.get('name', '')} ✓"

    if tag in ("task_failed", "task_exhaust"):
        return f"{data.get('name', '')} ✗"

    if tag == "task_retry":
        return f"{data.get('name', '')} retry #{data.get('attempt', '')}"

    if tag in ("bootstrap_transfer", "transfer_dispatched"):
        return f"-> {data.get('agent', '')}"

    if tag in ("flow_state_saved", "flow_state_restored"):
        slots = data.get("slots", [])
        if isinstance(slots, dict):
            keys = sorted(slots.keys())[:5]
        else:
            keys = list(slots)[:5]
        return f"flow={data.get('flow', '')} [{', '.join(keys)}]"

    if tag in ("steer_back_soft", "steer_back_hard", "steer_back_escalate"):
        return f"turns={data.get('turns', '')}"

    if tag in ("slot_error", "slot_error_retry"):
        return (
            f"{data.get('slot', '')} code={data.get('code', '')} "
            f"retries={data.get('retries', '')}"
        )

    if tag == "slot_error_exhaust":
        return f"{data.get('slot', '')}"

    if tag == "progress":
        parts = []
        confirmed = data.get("confirmed", [])
        if confirmed:
            parts.append(f"confirmed: {', '.join(confirmed)}")
        pending = data.get("pending+", {})
        if pending:
            names = ", ".join(sorted(pending.keys()) if isinstance(pending, dict) else pending)
            parts.append(f"pending: {names}")
        task_out = data.get("task+", {})
        if task_out:
            names = ", ".join(sorted(task_out.keys()) if isinstance(task_out, dict) else task_out)
            parts.append(f"task-filled: {names}")
        rejected = data.get("rejected", [])
        if rejected:
            parts.append(f"rejected: {', '.join(rejected)}")
        return " | ".join(parts) if parts else "no change"

    if tag == "auto_confirm":
        return f"\"{_trunc(str(data.get('text', '')), 50)}\""

    if tag == "auto_confirm_inline":
        return f"{', '.join(data.get('slots', []))}"

    if tag == "correction_applied":
        return (
            f"{data.get('slot', '')} "
            f"'{_trunc(str(data.get('old', '')), 20)}' -> "
            f"'{_trunc(str(data.get('new', '')), 20)}'"
        )

    if tag == "config_loaded":
        return (
            f"{data.get('config_id', '')}  "
            f"slots={data.get('slot_count', '')} "
            f"tasks={data.get('task_count', '')}"
        )

    if tag == "zombie_created":
        zombie = data.get("zombie", {})
        parts = [f"task={data.get('task', '')}"]
        if zombie.get("flow"):
            parts.append(f"flow={zombie['flow']}")
        exit_keys = list(zombie.get("exit_status", {}).keys())
        if exit_keys:
            parts.append(f"exit={','.join(exit_keys)}")
        if zombie.get("transfer_to"):
            parts.append(f"transfer→{zombie['transfer_to']}")
        return " ".join(parts)

    if tag == "zombie_reaped_on_reentry":
        return (
            f"gate={data.get('gate_slot', '')} "
            f"val={_trunc(str(data.get('gate_val', '')), 30)}"
        )

    # Fallback: compact key=val for first 4 items
    items = list(data.items())[:4]
    return " ".join(f"{k}={v}" for k, v in items)


def _timeline_detail(tag: str, data: dict) -> str:
    """Return a human-readable detail string for a timeline event."""
    if not data:
        return ""

    if tag == "config_loaded":
        return f"Loaded {data.get('config_id', '')} ({data.get('n_slots', '')} slots, {data.get('n_tasks', '')} tasks)"
    if tag == "bootstrap_stored":
        return f"Set {data.get('slot', '')} = \"{_trunc(str(data.get('value', '')), 40)}\""
    if tag == "bootstrap_transfer":
        return f"Transfer → {data.get('target', data.get('agent', ''))}"
    if tag == "flow_state_saved":
        slots = data.get('slots', [])
        names = sorted(slots.keys()) if isinstance(slots, dict) else list(slots)
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f" (+{len(names) - 5})"
        return f"Paused {data.get('flow', '')} — saved: {label}" if names else f"Paused {data.get('flow', '')}"
    if tag == "flow_state_restored":
        slots = data.get('slots', [])
        names = sorted(slots.keys()) if isinstance(slots, dict) else list(slots)
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f" (+{len(names) - 5})"
        return f"Resumed {data.get('flow', '')} — restored: {label}" if names else f"Resumed {data.get('flow', '')}"
    if tag == "cancel_flow_called":
        return f"Cancelled: {data.get('reason', '')}"

    if tag in ("setter_stored", "multi_setter_stored"):
        return f"{data.get('slot', '')} = \"{_trunc(str(data.get('value', '')), 40)}\""
    if tag == "event_prefill":
        return f"{data.get('slot', '')} = \"{_trunc(str(data.get('value', '')), 40)}\" (pre-filled)"

    if tag == "auto_confirm":
        return f"User: \"{_trunc(str(data.get('user_msg', '')), 50)}\""
    if tag == "auto_confirm_inline":
        return ", ".join(data.get("committed", []))

    if tag == "progress":
        parts = []
        confirmed = data.get("confirmed", [])
        if confirmed:
            parts.append(f"Confirmed: {', '.join(confirmed)}")
        pending = data.get("pending+", {})
        if pending:
            names = sorted(pending.keys()) if isinstance(pending, dict) else list(pending)
            parts.append(f"Pending: {', '.join(names)}")
        task_out = data.get("task+", {})
        if task_out:
            names = sorted(task_out.keys()) if isinstance(task_out, dict) else list(task_out)
            parts.append(f"Task-filled: {', '.join(names)}")
        rejected = data.get("rejected", [])
        if rejected:
            parts.append(f"Rejected: {', '.join(rejected)}")
        return " | ".join(parts) if parts else "State changed"

    if tag == "task_completed":
        mark = "✓" if data.get("success") else "✗"
        return f"{data.get('task', '')} {mark}"
    if tag == "task_retry":
        return f"{data.get('name', '')} ↻ retry #{data.get('attempt', '')}"
    if tag == "task_exhaust":
        return f"{data.get('name', '')} ✗ exhausted"

    if tag == "correction_applied":
        return f"{data.get('slot', '')}: \"{_trunc(str(data.get('old', '')), 20)}\" → \"{_trunc(str(data.get('new', '')), 20)}\""
    if tag == "slot_correction_pending":
        return f"Pending: {data.get('slot', '')} = \"{_trunc(str(data.get('value', '')), 30)}\""

    if tag == "slot_error":
        return f"{data.get('slot', '')}: {data.get('code', '')} (retry {data.get('retries', '')})"
    if tag == "slot_error_exhaust":
        return f"{data.get('slot', '')} — retries exhausted"
    if tag == "validation_failed":
        return f"{data.get('slot', '')}: {data.get('code', '')}"

    if tag == "steer_back_soft":
        return f"Soft redirect (turn {data.get('turns', '')})"
    if tag == "steer_back_hard":
        return f"Hard redirect (turn {data.get('turns', '')})"
    if tag == "steer_back_escalate":
        return f"Escalated (turn {data.get('turns', '')})"

    if tag == "zombie_created":
        zombie = data.get("zombie", {})
        flow = zombie.get("flow", "")
        transfer = zombie.get("transfer_to", "")
        parts = [f"Task '{data.get('task', '')}' → zombie"]
        if flow:
            parts[0] += f" (flow={flow})"
        if transfer:
            parts.append(f"transfer → {transfer}")
        return ", ".join(parts)

    if tag == "zombie_reaped_on_reentry":
        gate = data.get("gate_slot", "")
        val = _trunc(str(data.get("gate_val", "")), 30)
        return f"Reaped → new flow via {gate}={val}"

    # Fallback
    items = list(data.items())[:4]
    return " ".join(f"{k}={v}" for k, v in items)


class ChatRenderer:
    """Renders chat turns and state to the terminal using Rich."""

    def __init__(
        self,
        console: Console | None = None,
        verbose: bool = False,
    ):
        self.console = console or Console()
        self.verbose = verbose

    def render_session_start(
        self, session_id: str, app_name: str,
        display_name: str = "", config_path: str = "",
    ) -> None:
        """Print session header with ID and app name."""
        content = Text()
        content.append("Session: ", style="bold")
        content.append(session_id, style="cyan")
        content.append("\n")
        content.append("App: ", style="bold")
        if display_name and display_name != app_name:
            content.append(display_name, style="cyan")
            content.append(f"  ({app_name})", style="dim")
        else:
            content.append(app_name, style="dim")
        if config_path:
            content.append("\n")
            content.append("Config: ", style="bold")
            content.append(config_path, style="dim")
        panel = Panel(
            content,
            title="Chat Session Started",
            border_style="green",
        )
        self.console.print(panel)

    def render_user_message(self, turn_index: int, text: str) -> None:
        """Render a user message."""
        label = Text(f"[Turn {turn_index}] You: ", style="bold blue")
        label.append(text)
        self.console.print(label)

    def render_turn(self, turn: TurnRecord) -> None:
        """Render a complete agent turn: text, tool calls, transfers.

        In non-verbose mode: only agent text + transfers.
        In verbose mode: tool calls with args, tool responses,
        variable updates.
        """
        if turn.agent_text:
            self.render_agent_text(turn.turn_index, turn.agent_text)

        if turn.payloads:
            self.render_payloads(turn.payloads)

        if self.verbose:
            for tc in turn.tool_calls:
                tool_name = tc.get("action", tc.get("name", "unknown"))
                args = tc.get("args", {})
                self.render_tool_call(tool_name, args)

            for tr in turn.tool_responses:
                tool_name = tr.get("action", tr.get("name", "unknown"))
                response = tr.get("response", {})
                self.render_tool_response(tool_name, response)

        if turn.agent_transfer:
            target = turn.agent_transfer
            if hasattr(target, "display_name"):
                target_name = target.display_name
            elif isinstance(target, dict):
                target_name = target.get(
                    "display_name", target.get("target_agent", str(target))
                )
            else:
                target_name = str(target)
            self.render_transfer(target_name)

    def render_agent_text(
        self, turn_index: int, text: str, role: str = ""
    ) -> None:
        """Render agent response text."""
        title = f"Agent [Turn {turn_index}]"
        if role:
            title = f"{role} [Turn {turn_index}]"
        panel = Panel(
            Text(text),
            title=title,
            border_style="green",
        )
        self.console.print(panel)

    def render_tool_call(self, tool_name: str, args: dict) -> None:
        """Render a tool call (verbose mode only)."""
        args_text = json.dumps(args, indent=2, default=str)
        panel = Panel(
            Text(args_text),
            title=f"Tool Call: {tool_name}",
            border_style="red",
        )
        self.console.print(panel)

    def render_tool_response(
        self, tool_name: str, response: dict
    ) -> None:
        """Render a tool response (verbose mode only)."""
        resp_text = json.dumps(response, indent=2, default=str)
        panel = Panel(
            Text(resp_text),
            title=f"Tool Response: {tool_name}",
            border_style="yellow",
        )
        self.console.print(panel)

    def render_transfer(self, target: str) -> None:
        """Render an agent transfer."""
        text = Text()
        text.append("Transferred to: ", style="bold cyan")
        text.append(target, style="cyan")
        self.console.print(text)

    def render_state(self, state: dict) -> None:
        """Render the current session state as a Rich table/panel.

        Used by /state slash command.
        """
        table = Table(title="Session State", show_header=True)
        table.add_column("Key", style="bold")
        table.add_column("Value")

        for key, value in state.items():
            if isinstance(value, dict):
                display = json.dumps(value, indent=2, default=str)
            else:
                display = str(value)
            table.add_row(key, display)

        self.console.print(table)

    def render_session_end(
        self,
        session_id: str,
        turn_count: int,
        trace_command: str,
    ) -> None:
        """Print session footer with summary and trace command hint."""
        content = Text()
        content.append("Session: ", style="bold")
        content.append(session_id, style="cyan")
        content.append("\n")
        content.append("Turns: ", style="bold")
        content.append(str(turn_count))
        content.append("\n")
        content.append("View trace: ", style="bold")
        content.append(trace_command, style="dim")
        panel = Panel(
            content,
            title="Session Ended",
            border_style="red",
        )
        self.console.print(panel)

    def render_metrics(
        self,
        duration_ms: float | None,
        tokens: dict[str, Any] | None,
        tool_count: int,
    ) -> None:
        """Render per-turn metrics inline (when --metrics flag is set)."""
        parts = []
        if duration_ms is not None:
            parts.append(f"{duration_ms:.0f}ms")
        if tokens:
            parts.append(f"tokens={tokens}")
        parts.append(f"tools={tool_count}")
        metrics_text = Text(" | ".join(parts), style="dim")
        self.console.print(metrics_text)

    # Categories collapsed by default (shown only via /slots <category>).
    # Key state from these is already surfaced in the Flags line.
    _COLLAPSED_CATEGORIES = frozenset({
        "configuration", "engine_control", "system_instruction",
        "channel", "phase_state", "transfer", "steer_back",
        "flow_context",
    })

    def render_slots(
        self,
        inspection: dict[str, Any],
        category: str | None = None,
        flow_context: dict[str, Any] | None = None,
    ) -> None:
        """Render deep slot inspection with focus on meaningful state."""
        summary = inspection.get("summary", {})
        dag = inspection.get("slot_dag", {})
        categories = inspection.get("categories", {})

        if category:
            if category == "flows":
                self._render_flow_list(summary, flow_context)
                return
            if category.startswith("flow:"):
                flow_name = category[5:]
                self._render_flow_detail(flow_name, summary, dag)
                return
            if category not in categories:
                available = sorted(categories.keys())
                self.render_error(
                    f"Unknown category '{category}'. "
                    f"Available: {', '.join(available)}, flows, flow:<name>"
                )
                return
            self._render_category_detail(category, categories[category])
            return

        phase = summary.get("phase", "unknown")
        phase_style = {
            "collection": "bold green",
            "fresh_readback": "bold yellow",
            "awaiting_confirmation": "bold yellow",
            "readback_transition": "bold yellow",
            "deferred_transition": "bold blue",
            "correction": "bold magenta",
            "post_correction_readback": "bold magenta",
            "complete": "bold cyan",
            "escalated": "bold red",
            "uninitialized": "dim",
        }.get(phase, "bold")

        # --- Phase header with flow context ---
        header = Text()
        header.append("Phase: ", style="bold")
        header.append(phase, style=phase_style)

        core = categories.get("core_data", {}).get("fields", {})
        filled = core.get("filled", {})
        if isinstance(filled, dict):
            flow_val = filled.get("active_flow")
            if flow_val:
                header.append(f"  flow={flow_val}", style="bold cyan")

        suspended = summary.get("suspended_flows", [])
        if suspended:
            names = [s["flow"] for s in suspended]
            header.append(
                f"  suspended: {', '.join(names)}",
                style="bold yellow",
            )

        if summary.get("restored_flow"):
            header.append("  [restored]", style="bold magenta")

        fc = flow_context or {}
        agent_map = fc.get("agent_config_map", {})
        if agent_map and len(agent_map) > 1:
            active_cfg = fc.get("active_config_id") or summary.get("config_id")
            other_flows = [
                agent for agent, cfg in agent_map.items()
                if cfg != active_cfg
            ]
            if other_flows:
                header.append(
                    f"  (other: {', '.join(other_flows)})",
                    style="dim",
                )
        self.console.print(header)

        # --- Slot values by state ---
        pending = core.get("pending", {})
        deferred = core.get("deferred", {})

        if filled or pending or deferred or dag.get("blocked"):
            self.console.print()
            self._render_slot_values(filled, pending, deferred, dag)

        # --- Suspended flows ---
        suspended = summary.get("suspended_flows", [])
        if suspended:
            self.console.print()
            self._render_suspended_flows(suspended)

        # --- Active flags (only non-default/interesting state) ---
        flags = self._collect_active_flags(summary, categories)
        if flags:
            self.console.print()
            flags_text = Text()
            flags_text.append("Flags: ", style="bold")
            flags_text.append("  ".join(flags), style="dim yellow")
            self.console.print(flags_text)

        # --- Expanded categories (non-collapsed, non-core) ---
        shown_cats = []
        for cat_key, cat_data in categories.items():
            if cat_key == "core_data":
                continue
            if cat_key in self._COLLAPSED_CATEGORIES:
                continue
            fields = cat_data.get("fields", {})
            interesting = self._filter_interesting_fields(cat_key, fields)
            if interesting:
                shown_cats.append((cat_data["label"], interesting))

        for label, fields in shown_cats:
            self.console.print()
            self._render_state_fields(label, fields)

        # --- Collapsed hint ---
        collapsed = [
            k for k in self._COLLAPSED_CATEGORIES if k in categories
        ]
        if collapsed:
            self.console.print()
            hint = Text("Collapsed: ", style="dim")
            hint.append(
                ", ".join(f"/slots {c}" for c in sorted(collapsed)),
                style="dim cyan",
            )
            self.console.print(hint)

    def _render_slot_values(
        self,
        filled: Any,
        pending: Any,
        deferred: Any,
        dag: dict[str, Any],
    ) -> None:
        """Render slot name=value pairs grouped by state with color.

        Meta/bootstrap slots (active_flow, welcome) are separated from
        flow data slots. Shared slots are annotated with a marker.
        Slots not owned by the current flow's tools are dimmed.
        """
        meta_slots = set(dag.get("meta_slots", []))
        flow_slots = set(dag.get("flow_slots", []))
        shared_slots = set(dag.get("shared_slots", []))

        table = Table(
            title="Slots",
            show_header=True,
            title_style="bold",
            border_style="dim",
            pad_edge=False,
        )
        table.add_column("Slot", style="bold")
        table.add_column("Value")
        table.add_column("State", justify="right")

        blocked_map: dict[str, list[str]] = {}
        for b in dag.get("blocked", []):
            blocked_map[b["slot"]] = b["blocked_by"]

        def _slot_name(slot: str) -> Text:
            name = Text()
            is_owned = not flow_slots or slot in flow_slots
            name.append(slot, style="bold" if is_owned else "dim")
            if slot in shared_slots:
                name.append(" *", style="cyan")
            return name

        def _add_slots(
            slots: dict, state_label: str, state_style: str
        ) -> None:
            if not isinstance(slots, dict):
                return
            for slot in sorted(slots):
                if slot in meta_slots:
                    continue
                val = self._format_slot_value(slots[slot])
                table.add_row(
                    _slot_name(slot),
                    val,
                    Text(state_label, style=state_style),
                )

        _add_slots(filled, "filled", "green")
        _add_slots(pending, "pending", "yellow")
        _add_slots(deferred, "deferred", "blue")

        for slot, blocked_by in sorted(blocked_map.items()):
            if (
                slot not in (filled or {})
                and slot not in (pending or {})
                and slot not in (deferred or {})
                and slot not in meta_slots
            ):
                needs = ", ".join(blocked_by)
                table.add_row(
                    _slot_name(slot),
                    Text(f"needs {needs}", style="dim"),
                    Text("blocked", style="red"),
                )

        if table.row_count > 0:
            self.console.print(table)
            if shared_slots:
                self.console.print(
                    Text("  * shared across flows", style="dim cyan")
                )

    def _render_suspended_flows(
        self, suspended: list[dict[str, Any]]
    ) -> None:
        """Render suspended flow states showing saved private slots."""
        table = Table(
            title="Suspended Flows",
            show_header=True,
            title_style="bold yellow",
            border_style="dim yellow",
            pad_edge=False,
        )
        table.add_column("Flow", style="bold")
        table.add_column("Saved Slots")
        table.add_column("Pending")
        table.add_column("Deferred")

        for entry in suspended:
            slots = entry.get("slots", [])
            pending = entry.get("pending", [])
            deferred = entry.get("deferred", [])
            table.add_row(
                entry.get("flow", "?"),
                ", ".join(slots) if slots else "-",
                ", ".join(pending) if pending else "-",
                ", ".join(deferred) if deferred else "-",
            )
        self.console.print(table)

    def _render_flow_list(
        self,
        summary: dict[str, Any],
        flow_context: dict[str, Any] | None,
    ) -> None:
        """Render a table of all flows with their status and slot counts."""
        fc = flow_context or {}
        agent_map = fc.get("agent_config_map", {})
        active_cfg = fc.get("active_config_id") or summary.get("config_id")
        suspended = summary.get("suspended_flows", [])
        suspended_map = {s["flow"]: s for s in suspended}

        table = Table(
            title="Flows",
            show_header=True,
            title_style="bold",
            border_style="dim",
        )
        table.add_column("Flow", style="bold")
        table.add_column("Agent")
        table.add_column("Status")
        table.add_column("Slots")

        for agent_name, config_id in sorted(
            agent_map.items(), key=lambda x: x[1]
        ):
            is_active = config_id == active_cfg
            flow_name = config_id

            if is_active:
                status = Text("active", style="bold green")
                filled = summary.get("filled_count", 0)
                pending = summary.get("pending_count", 0)
                counts = f"{filled} filled, {pending} pending"
            elif flow_name in suspended_map:
                status = Text("suspended", style="bold yellow")
                s = suspended_map[flow_name]
                n_slots = len(s.get("slots", []))
                n_pending = len(s.get("pending", []))
                counts = f"{n_slots} saved, {n_pending} pending"
            else:
                status = Text("idle", style="dim")
                counts = "-"

            table.add_row(agent_name, flow_name, status, counts)

        if not agent_map:
            active = summary.get("config_id")
            if active:
                table.add_row(
                    "-", active,
                    Text("active", style="bold green"),
                    f"{summary.get('filled_count', 0)} filled",
                )

        self.console.print(table)
        self.console.print(
            Text(
                "  Use /slots flow:<name> to inspect a suspended flow",
                style="dim",
            )
        )

    def _render_flow_detail(
        self,
        flow_name: str,
        summary: dict[str, Any],
        dag: dict[str, Any],
    ) -> None:
        """Render full slot detail for a suspended flow."""
        suspended = summary.get("suspended_flows", [])
        match = None
        for s in suspended:
            if s.get("flow") == flow_name:
                match = s
                break
        if not match:
            available = [s["flow"] for s in suspended]
            if available:
                self.render_error(
                    f"No suspended flow '{flow_name}'. "
                    f"Suspended: {', '.join(available)}"
                )
            else:
                self.render_error(
                    f"No suspended flow '{flow_name}'. "
                    "No flows are currently suspended."
                )
            return

        shared_slots = set(dag.get("shared_slots", []))

        table = Table(
            title=f"Suspended: {flow_name}",
            show_header=True,
            title_style="bold yellow",
            border_style="dim yellow",
            pad_edge=False,
        )
        table.add_column("Slot", style="bold")
        table.add_column("Value")
        table.add_column("State", justify="right")

        for slot, val in sorted(match.get("slot_values", {}).items()):
            name = Text(slot, style="bold")
            if slot in shared_slots:
                name.append(" *", style="cyan")
            table.add_row(
                name,
                self._format_slot_value(val),
                Text("saved", style="green"),
            )
        for slot, val in sorted(match.get("pending_values", {}).items()):
            name = Text(slot, style="bold")
            if slot in shared_slots:
                name.append(" *", style="cyan")
            table.add_row(
                name,
                self._format_slot_value(val),
                Text("pending", style="yellow"),
            )
        for slot, val in sorted(match.get("deferred_values", {}).items()):
            name = Text(slot, style="bold")
            if slot in shared_slots:
                name.append(" *", style="cyan")
            table.add_row(
                name,
                self._format_slot_value(val),
                Text("deferred", style="blue"),
            )

        if table.row_count > 0:
            self.console.print(table)
            if shared_slots:
                self.console.print(
                    Text("  * shared across flows", style="dim cyan")
                )
        else:
            self.console.print(
                Text(f"  Flow '{flow_name}' has no saved slots.", style="dim")
            )

    @staticmethod
    def _format_slot_value(value: Any) -> Text:
        """Format a single slot value for display."""
        if value is None or value == "":
            return Text("-", style="dim")
        if isinstance(value, dict):
            if not value:
                return Text("{}", style="dim")
            parts = ", ".join(
                f"{k}={v}" for k, v in list(value.items())[:5]
            )
            if len(value) > 5:
                parts += f" (+{len(value) - 5} more)"
            return Text(parts)
        if isinstance(value, list):
            if not value:
                return Text("[]", style="dim")
            return Text(", ".join(str(v) for v in value[:5]))
        return Text(str(value))

    def _collect_active_flags(
        self,
        summary: dict[str, Any],
        categories: dict[str, Any],
    ) -> list[str]:
        """Collect non-default state flags worth surfacing."""
        flags: list[str] = []

        steer = summary.get("steer_back_turns", 0)
        if steer:
            flags.append(f"steer_back={steer}")

        retries = summary.get("retries", {})
        if isinstance(retries, dict) and retries:
            for tool, count in retries.items():
                flags.append(f"retry({tool})={count}")

        flag_fields = {
            "confirmation": [
                ("_correction_pending", "correction_pending"),
                ("_correction_applied", "correction_applied"),
                ("_rejection_requested", "rejection_requested"),
                ("_post_correction_readback", "post_correction_readback"),
            ],
            "phase_state": [
                ("_auto_confirm_pending", "auto_confirm"),
                ("_readback_transition", "readback_transition"),
                ("_deferred_transition", "deferred_transition"),
                ("_inline_confirm", "inline_confirm"),
            ],
            "transfer": [
                ("_cancel_requested", "cancel_requested"),
                ("_pending_transfer", "pending_transfer"),
            ],
            "task_state": [
                ("_task_just_completed", "task_completed"),
                ("_zombie", "zombie"),
            ],
            "flow_context": [
                ("_restored_flow", "restored_flow"),
                ("_auto_resume_deferred", "auto_resume"),
            ],
        }

        for cat_key, field_defs in flag_fields.items():
            cat_fields = (
                categories.get(cat_key, {}).get("fields", {})
            )
            for field_name, display_name in field_defs:
                val = cat_fields.get(field_name)
                if val and val is not None:
                    if isinstance(val, bool):
                        flags.append(display_name)
                    elif isinstance(val, dict):
                        detail = val.get(
                            "flow", val.get("name", "active")
                        )
                        flags.append(f"{display_name}({detail})")
                    elif isinstance(val, str) and val not in ("", "false"):
                        flags.append(f"{display_name}={val}")
                    elif isinstance(val, (int, float)) and val:
                        flags.append(f"{display_name}={val}")

        return flags

    def _filter_interesting_fields(
        self, cat_key: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Filter out empty/default values from a category's fields.

        For categories like confirmation, steer_back, etc — only show
        fields that have non-default values.
        """
        result: dict[str, Any] = {}
        for k, v in fields.items():
            if v is None or v == "" or v == {} or v == [] or v == 0:
                continue
            if isinstance(v, bool) and not v:
                continue
            if isinstance(v, str) and v in ("false", "False"):
                continue
            result[k] = v
        return result

    def _render_state_fields(
        self, label: str, fields: dict[str, Any]
    ) -> None:
        """Render a category's non-empty fields as a compact table."""
        table = Table(
            title=label,
            show_header=False,
            title_style="bold",
            border_style="dim",
            pad_edge=False,
        )
        table.add_column("Field", style="bold dim")
        table.add_column("Value", overflow="fold")
        for k, v in fields.items():
            table.add_row(
                k.lstrip("_"),
                self._format_field_value(v),
            )
        self.console.print(table)

    @staticmethod
    def _format_field_value(value: Any) -> str:
        """Format a field value — flatten dicts/lists for readability."""
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, dict):
            if not value:
                return "-"
            if len(value) <= 4:
                return ", ".join(f"{k}={v}" for k, v in value.items())
            display = json.dumps(value, default=str)
            if len(display) > 120:
                return display[:120] + "..."
            return display
        if isinstance(value, list):
            if not value:
                return "-"
            return ", ".join(str(v) for v in value)
        return str(value)

    def _render_category_detail(
        self, cat_key: str, cat_data: dict[str, Any]
    ) -> None:
        """Render a single category in full detail (for /slots <category>)."""
        table = Table(
            title=cat_data["label"],
            show_header=True,
            title_style="bold",
            border_style="dim",
        )
        table.add_column("Field", style="bold")
        table.add_column("Value", overflow="fold")
        for field_name, value in cat_data.get("fields", {}).items():
            if isinstance(value, (dict, list)):
                display = json.dumps(value, indent=2, default=str)
            else:
                display = str(value)
            if len(display) > 300:
                display = display[:300] + "..."
            table.add_row(field_name.lstrip("_"), display)
        self.console.print(table)

    def _render_log_debug(
        self,
        log_entries: list[dict],
        min_level: str = "INFO",
    ) -> None:
        """Render SM lifecycle log as a colour-coded timeline.

        Args:
            log_entries: List of ``{src, tag, level, data}`` dicts.
            min_level: Minimum severity to display (DEBUG/INFO/WARN/ERROR).
        """
        min_level = min_level.upper()
        if min_level not in LEVEL_ORDER:
            min_level = "INFO"
        threshold = LEVEL_ORDER[min_level]

        visible = [
            e for e in log_entries
            if LEVEL_ORDER.get(e.get("level", "INFO"), 1) >= threshold
        ]

        if not visible:
            self.console.print(
                Panel("No log entries at this level.", border_style="dim")
            )
            return

        self.console.print(Rule("Slot Filling Timeline", style="dim"))

        current_phase: str | None = None
        for entry in visible:
            tag = entry.get("tag", "")
            phase = TAG_PHASE.get(tag, "collection")

            if phase != current_phase:
                self.console.print(
                    Rule(phase, style=PHASE_STYLE.get(phase, "dim"))
                )
                current_phase = phase

            level = entry.get("level", "INFO")
            marker, marker_style = LEVEL_MARKER.get(level, ("·", ""))
            detail = _format_log_detail(tag, entry.get("data", {}))
            tag_style = PHASE_STYLE.get(phase, "")

            line = Text()
            line.append(marker, style=marker_style)
            line.append(" ")
            line.append(tag.ljust(22), style=tag_style)
            line.append("  ")
            line.append(detail)
            self.console.print(line)

    def render_log(
        self,
        log_entries: list[dict],
        min_level: str = "INFO",
    ) -> None:
        """Render SM lifecycle as a high-level conversation timeline.

        Default view shows only user-facing events in a table.
        Use min_level="DEBUG" for the detailed tag-by-tag view.
        """
        min_level = min_level.upper()
        if min_level not in LEVEL_ORDER:
            min_level = "INFO"

        if min_level == "DEBUG":
            self._render_log_debug(log_entries, "DEBUG")
            return

        threshold = LEVEL_ORDER[min_level]
        total = len(log_entries)

        table = Table(
            title="Conversation Timeline",
            show_header=True,
            title_style="bold",
            border_style="dim",
            pad_edge=False,
        )
        table.add_column("#", style="dim", justify="right", width=4)
        table.add_column("Event", width=10)
        table.add_column("Details")

        seq = 0
        seen: dict[str, str] = {}
        for entry in log_entries:
            tag = entry.get("tag", "")
            event_def = _TIMELINE_EVENTS.get(tag)
            if not event_def:
                continue

            level = entry.get("level", "INFO")
            if LEVEL_ORDER.get(level, 1) < threshold:
                continue

            detail = _timeline_detail(tag, entry.get("data", {}))

            dedup_key = f"{tag}:{detail}"
            if tag in _DEDUP_TAGS and seen.get(tag) == dedup_key:
                continue
            seen[tag] = dedup_key

            seq += 1
            label, marker, style = event_def
            event_text = Text(f"{marker} {label}", style=style)

            detail_style = ""
            if label == "ERROR":
                detail_style = "red"
            elif label == "STEER":
                detail_style = "yellow"

            table.add_row(str(seq), event_text, Text(detail, style=detail_style))

        if seq == 0:
            self.console.print(
                Panel("No events at this level.", border_style="dim")
            )
            return

        self.console.print(table)

        hidden = total - seq
        if hidden > 0:
            self.console.print(
                Text(
                    f" {seq} events shown ({hidden} internal events hidden"
                    " — use /log debug)",
                    style="dim",
                )
            )

    def render_error(self, message: str) -> None:
        """Render an error message."""
        panel = Panel(
            Text(message, style="bold white"),
            title="Error",
            border_style="red",
        )
        self.console.print(panel)

    def render_slash_help(self) -> None:
        """Print available slash commands."""
        table = Table(
            title="Available Commands", show_header=True
        )
        table.add_column("Command", style="bold cyan")
        table.add_column("Description")

        for cmd, desc in SLASH_COMMANDS.items():
            table.add_row(cmd, desc)

        self.console.print(table)

    # ── Payload rendering ───────────────────────────────────

    _BUTTON_ICONS: dict[str, str] = {
        "doc": "\U0001f4c4",
        "hyperLink": "\U0001f517",
        "deepLink": "\U0001f4f1",
        "event": "▶",
        "cms": "\U0001f4c4",
    }

    def render_payloads(self, payloads: list[dict]) -> None:
        """Render custom payloads as terminal UI elements."""
        for payload in payloads:
            if "richContent" in payload:
                self._render_rich_content(payload["richContent"])
            elif "scenarios" in payload:
                self._render_scenarios(payload["scenarios"])
            else:
                self._render_unknown_payload(payload)

    def _render_rich_content(
        self, content: list[list[dict]]
    ) -> None:
        """Render Dialogflow richContent items."""
        for group in content:
            if not isinstance(group, list):
                continue
            for item in group:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")
                if item_type == "chips":
                    options = item.get("options", [])
                    if options:
                        self._render_chips(options)
                    elif item.get("options_from"):
                        self._render_chips(
                            [{"text": f"(from {item['options_from']})"}]
                        )
                elif item_type == "info":
                    self._render_info_card(item)
                else:
                    self._render_unknown_payload(item)

    def _render_chips(self, options: list[dict]) -> None:
        """Render chip options as a horizontal button bar."""
        if not options:
            return
        labels = [opt.get("text", "") for opt in options]
        width = self.console.width

        rows: list[list[str]] = []
        current_row: list[str] = []
        current_width = 2
        for label in labels:
            chip_width = len(label) + 5
            if current_row and current_width + chip_width > width:
                rows.append(current_row)
                current_row = []
                current_width = 2
            current_row.append(label)
            current_width += chip_width
        if current_row:
            rows.append(current_row)

        for row in rows:
            top = Text("  ")
            mid = Text("  ")
            bot = Text("  ")
            for i, label in enumerate(row):
                w = len(label) + 2
                if i > 0:
                    top.append(" ")
                    mid.append(" ")
                    bot.append(" ")
                top.append(
                    f"╭{'─' * w}╮", style="cyan"
                )
                mid.append(
                    f"│ {label} │", style="bold cyan"
                )
                bot.append(
                    f"╰{'─' * w}╯", style="cyan"
                )
            self.console.print(top)
            self.console.print(mid)
            self.console.print(bot)

    def _render_info_card(self, item: dict) -> None:
        """Render an info card with title, subtitle, and body text."""
        content = Text()
        subtitle = item.get("subtitle", "")
        if subtitle:
            content.append(subtitle, style="bold")
        body = item.get("text", "")
        if body:
            if subtitle:
                content.append("\n\n")
            content.append(body)

        title = item.get("title", "")
        self.console.print(Panel(
            content,
            title=title if title else None,
            title_align="left",
            border_style="blue",
            padding=(0, 1),
        ))

    def _render_scenarios(
        self, scenarios: list[dict]
    ) -> None:
        """Render scenario payloads with text and buttons."""
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                continue
            name = scenario.get("name", "")
            responses = scenario.get("responses", [])
            if not responses:
                continue

            content = Text()
            for resp in responses:
                if not isinstance(resp, dict):
                    continue
                resp_type = resp.get("type", "")
                if resp_type == "text":
                    text = resp.get("text", "")
                    if text:
                        if content.plain:
                            content.append("\n")
                        content.append(text)
                elif resp_type == "button":
                    self._render_scenario_button(resp, content)

            if content.plain:
                self.console.print(Panel(
                    content,
                    title=name if name else None,
                    title_align="left",
                    border_style="dim cyan",
                    padding=(0, 1),
                ))

    def _render_scenario_button(
        self, btn: dict, content: Text
    ) -> None:
        """Append a button to scenario content."""
        btn_type = btn.get("buttonType", "")
        icon = self._BUTTON_ICONS.get(btn_type, "▶")
        label = btn.get("text", "")
        link = btn.get("link", "")

        if content.plain:
            content.append("\n")
        content.append(f"  {icon} ", style="blue")
        content.append(label, style="bold blue")
        if link:
            content.append(f"\n     {link}", style="dim")

    def _render_unknown_payload(
        self, payload: dict
    ) -> None:
        """Fallback: render unknown payload as dimmed JSON."""
        self.console.print(Panel(
            Text(
                json.dumps(payload, indent=2, default=str),
                style="dim",
            ),
            title="Custom Payload",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        ))
