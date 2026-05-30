"""Textual TUI for `cxas chat --tui`.

Interactive chat with clickable chip buttons, scrollable history,
and rich payload rendering. Uses VerticalScroll with Static widgets
for Rich renderables and Textual Button widgets for interactive elements.
"""

import json
import webbrowser

from rich.panel import Panel
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Static

from cxas_scrapi.core.chat_session import ChatSession, SessionEndedError

_BUTTON_ICONS = {
    "doc": "\U0001f4c4",
    "hyperLink": "\U0001f517",
    "deepLink": "\U0001f4f1",
    "event": "▶",
    "cms": "\U0001f4c4",
}


class ChatApp(App):
    """Interactive chat TUI with clickable payload widgets."""

    TITLE = "cxas chat"
    CSS = """
    #chat-log {
        height: 1fr;
        scrollbar-gutter: stable;
        padding: 0 1;
    }
    #user-input {
        dock: bottom;
        margin: 0 1;
    }
    .agent-panel { margin: 0 0 1 0; }
    .user-msg { margin: 0 0 1 0; }
    .chip-row {
        height: auto;
        layout: horizontal;
        margin: 0 0 1 0;
    }
    .chip-btn {
        min-width: 0;
        width: auto;
        margin: 0 1 0 0;
    }
    .link-btn {
        min-width: 0;
        width: auto;
        margin: 0 1 0 0;
    }
    .info-panel { margin: 0 0 1 0; }
    .scenario-panel { margin: 0 0 1 0; }
    .slash-output { margin: 0 0 1 0; }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
    ]

    def __init__(
        self,
        session: ChatSession,
        display_name: str = "",
        verbose: bool = False,
    ):
        super().__init__()
        self._session = session
        self._display_name = display_name
        self._verbose = verbose

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat-log")
        yield Input(placeholder="Type a message or /help ...", id="user-input")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self._display_name or "cxas chat"
        self.sub_title = f"Session: {self._session.session_id}"
        self.query_one("#user-input", Input).focus()

    # ── Input handling ──────────────────────────────────

    @on(Input.Submitted, "#user-input")
    def on_user_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()

        if text.startswith("/"):
            self._handle_slash(text)
            return

        self._append_user(text)
        self._send_message(text)

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        ces_event = getattr(event.button, "_ces_event", None)
        if ces_event:
            display = ces_event.get("display", ces_event.get("name", ""))
            self._append_user(f"[{display}]")
            self._fire_event(ces_event["name"])
            return
        chip_text = getattr(event.button, "_chip_text", None)
        if chip_text:
            self._append_user(chip_text)
            self._send_message(chip_text)
            return
        link = getattr(event.button, "_link", None)
        if link:
            webbrowser.open(link)

    # ── Message sending (background worker) ─────────────

    @work(thread=True)
    def _send_message(self, text: str) -> None:
        try:
            turn = self._session.send(text)
            self.call_from_thread(self._render_turn, turn)
            if self._session.is_ended:
                self.call_from_thread(self._show_ended)
        except SessionEndedError:
            self.call_from_thread(self._show_ended)
        except Exception as exc:
            self.call_from_thread(
                self._append_static,
                Panel(
                    Text(str(exc), style="bold white"),
                    title="Error",
                    border_style="red",
                ),
            )

    @work(thread=True)
    def _fire_event(self, event_name: str) -> None:
        try:
            turn = self._session.send_event(event_name)
            self.call_from_thread(self._render_turn, turn)
            if self._session.is_ended:
                self.call_from_thread(self._show_ended)
        except SessionEndedError:
            self.call_from_thread(self._show_ended)
        except Exception as exc:
            self.call_from_thread(
                self._append_static,
                Panel(
                    Text(str(exc), style="bold white"),
                    title="Error",
                    border_style="red",
                ),
            )

    # ── Rendering ───────────────────────────────────────

    def _append_user(self, text: str) -> None:
        label = Text()
        label.append("You: ", style="bold blue")
        label.append(text)
        log = self.query_one("#chat-log")
        log.mount(Static(label, classes="user-msg"))
        log.scroll_end(animate=False)

    def _append_static(self, renderable, classes: str = "") -> None:
        log = self.query_one("#chat-log")
        log.mount(Static(renderable, classes=classes))
        log.scroll_end(animate=False)

    def _render_turn(self, turn) -> None:
        log = self.query_one("#chat-log")

        payloads = getattr(turn, "payloads", [])
        show_text = turn.agent_text and not self._text_in_payloads(
            turn.agent_text, payloads,
        )
        if show_text:
            panel = Panel(
                Text(turn.agent_text),
                title=f"Agent [Turn {turn.turn_index}]",
                border_style="green",
            )
            log.mount(Static(panel, classes="agent-panel"))

        if self._verbose:
            for tc in turn.tool_calls:
                tool = tc.get("action", tc.get("name", "?"))
                agent = tc.get("agent", "")
                args = tc.get("args", {})
                title = f"{agent}: {tool}" if agent else tool
                log.mount(Static(
                    Panel(
                        Text(json.dumps(args, indent=2, default=str)),
                        title=title,
                        border_style="red",
                    ),
                    classes="agent-panel",
                ))

        for payload in getattr(turn, "payloads", []):
            self._mount_payload(log, payload)

        if turn.agent_transfer:
            target = turn.agent_transfer
            if isinstance(target, dict):
                name = target.get("display_name", target.get("target_agent", str(target)))
            elif hasattr(target, "display_name"):
                name = target.display_name
            else:
                name = str(target)
            t = Text()
            t.append("Transferred to: ", style="bold cyan")
            t.append(name, style="cyan")
            log.mount(Static(t, classes="agent-panel"))

        log.scroll_end(animate=False)

    @staticmethod
    def _text_in_payloads(text: str, payloads: list[dict]) -> bool:
        """True if agent_text is duplicated inside a payload."""
        if not text or not payloads:
            return False
        stripped = text.strip()
        for payload in payloads:
            for scenario in payload.get("scenarios", []):
                if not isinstance(scenario, dict):
                    continue
                for resp in scenario.get("responses", []):
                    if (
                        isinstance(resp, dict)
                        and resp.get("type") == "text"
                        and resp.get("text", "").strip() == stripped
                    ):
                        return True
            for group in payload.get("richContent", []):
                if not isinstance(group, list):
                    continue
                for item in group:
                    if not isinstance(item, dict):
                        continue
                    for field in ("title", "subtitle", "text"):
                        if item.get(field, "").strip() == stripped:
                            return True
        return False

    def _show_ended(self) -> None:
        panel = Panel(
            Text(f"Session ended after {len(self._session.turns)} turns."),
            title="Session Ended",
            border_style="red",
        )
        self._append_static(panel)
        self.query_one("#user-input", Input).disabled = True

    # ── Payload mounting ────────────────────────────────

    def _mount_payload(self, container, payload: dict) -> None:
        if "richContent" in payload:
            self._mount_rich_content(container, payload["richContent"])
        elif "scenarios" in payload:
            self._mount_scenarios(container, payload["scenarios"])
        else:
            container.mount(Static(
                Panel(
                    Text(json.dumps(payload, indent=2, default=str), style="dim"),
                    title="Custom Payload",
                    border_style="dim",
                ),
                classes="scenario-panel",
            ))

    def _mount_rich_content(self, container, content: list) -> None:
        for group in content:
            if not isinstance(group, list):
                continue
            for item in group:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")
                if item_type == "chips":
                    self._mount_chips(container, item.get("options", []))
                elif item_type == "info":
                    self._mount_info_card(container, item)

    def _mount_chips(self, container, options: list) -> None:
        if not options:
            return
        row = Horizontal(classes="chip-row")
        container.mount(row)
        for opt in options:
            label = opt.get("text", "")
            btn = Button(label, classes="chip-btn", variant="primary")
            btn._chip_text = label
            row.mount(btn)

    def _mount_info_card(self, container, item: dict) -> None:
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
        container.mount(Static(
            Panel(
                content,
                title=title or None,
                title_align="left",
                border_style="blue",
                padding=(0, 1),
            ),
            classes="info-panel",
        ))

    def _mount_scenarios(self, container, scenarios: list) -> None:
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                continue
            name = scenario.get("name", "")
            responses = scenario.get("responses", [])
            if not responses:
                continue

            text_content = Text()
            buttons = []
            for resp in responses:
                if not isinstance(resp, dict):
                    continue
                if resp.get("type") == "text":
                    t = resp.get("text", "")
                    if t:
                        if text_content.plain:
                            text_content.append("\n")
                        text_content.append(t)
                elif resp.get("type") == "button":
                    buttons.append(resp)

            if text_content.plain:
                container.mount(Static(
                    Panel(
                        text_content,
                        title=name or None,
                        title_align="left",
                        border_style="dim cyan",
                        padding=(0, 1),
                    ),
                    classes="scenario-panel",
                ))

            if buttons:
                row = Horizontal(classes="chip-row")
                container.mount(row)
                for btn_data in buttons:
                    btn_type = btn_data.get("buttonType", "")
                    icon = _BUTTON_ICONS.get(btn_type, "▶")
                    label = f"{icon} {btn_data.get('text', '')}"
                    link = btn_data.get("link", "")
                    event_data = btn_data.get("event", {})
                    btn = Button(label, classes="link-btn")
                    btn._chip_text = None
                    btn._link = None
                    btn._ces_event = None
                    if event_data and event_data.get("name"):
                        btn._ces_event = event_data
                    elif link:
                        btn._link = link
                    else:
                        btn._chip_text = btn_data.get("text", "")
                    row.mount(btn)

    # ── Slash commands ──────────────────────────────────

    def _handle_slash(self, cmd: str) -> None:
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command == "/quit":
            self.exit()
            return

        if command == "/clear":
            self.action_clear()
            return

        if command == "/help":
            from cxas_scrapi.utils.chat_renderer import SLASH_COMMANDS
            from rich.table import Table
            table = Table(title="Commands", show_header=True)
            table.add_column("Command", style="bold cyan")
            table.add_column("Description")
            for c, desc in SLASH_COMMANDS.items():
                table.add_row(c, desc)
            self._append_static(table, classes="slash-output")
            return

        if command == "/state":
            state = self._session.get_state()
            from rich.table import Table
            table = Table(title="Session State", show_header=True)
            table.add_column("Key", style="bold")
            table.add_column("Value")
            for key, value in state.items():
                if isinstance(value, dict):
                    display = json.dumps(value, indent=2, default=str)
                else:
                    display = str(value)
                table.add_row(key, display)
            self._append_static(table, classes="slash-output")
            return

        if command == "/slots":
            if not self._session.turns:
                self._append_static(
                    Panel("Send a message first.", border_style="red"),
                )
                return
            try:
                from cxas_scrapi.utils.slot_inspector import SlotInspector
                from cxas_scrapi.utils.chat_renderer import ChatRenderer
                from io import StringIO
                from rich.console import Console

                sm = self._session.get_slot_machine()
                if not sm:
                    self._append_static(
                        Panel("No slot machine state.", border_style="red"),
                    )
                    return
                inspection = SlotInspector.inspect(sm)
                flow_context = self._session.get_flow_context()
                buf = StringIO()
                c = Console(file=buf, force_terminal=True, width=100)
                r = ChatRenderer(console=c)
                cat = arg.strip() if arg.strip() else None
                r.render_slots(inspection, category=cat, flow_context=flow_context)
                self._append_static(
                    Text.from_ansi(buf.getvalue()), classes="slash-output",
                )
            except Exception as e:
                self._append_static(
                    Panel(Text(str(e), style="bold white"), border_style="red"),
                )
            return

        if command == "/log":
            if not self._session.turns:
                self._append_static(
                    Panel("Send a message first.", border_style="red"),
                )
                return
            try:
                from cxas_scrapi.utils.chat_renderer import ChatRenderer
                from io import StringIO
                from rich.console import Console

                sm = self._session.get_slot_machine()
                if not sm:
                    self._append_static(
                        Panel("No slot machine state.", border_style="red"),
                    )
                    return
                log_entries = sm.get("_log", [])
                if not log_entries:
                    self._append_static(
                        Panel("No log entries.", border_style="dim"),
                    )
                    return
                level = arg.strip().upper() if arg.strip() else "INFO"
                buf = StringIO()
                c = Console(file=buf, force_terminal=True, width=100)
                r = ChatRenderer(console=c)
                r.render_log(log_entries, min_level=level)
                self._append_static(
                    Text.from_ansi(buf.getvalue()), classes="slash-output",
                )
            except Exception as e:
                self._append_static(
                    Panel(Text(str(e), style="bold white"), border_style="red"),
                )
            return

        if command == "/trace":
            try:
                fmt = arg.strip() if arg.strip() else "text"
                trace_output = self._session.get_trace(fmt=fmt)
                self._append_static(
                    Text(trace_output), classes="slash-output",
                )
            except Exception as e:
                self._append_static(
                    Panel(Text(str(e), style="bold white"), border_style="red"),
                )
            return

        self._append_static(
            Panel(
                Text(f"Unknown command: {command}. Type /help", style="bold white"),
                border_style="red",
            ),
        )

    # ── Actions ─────────────────────────────────────────

    def action_clear(self) -> None:
        self.query_one("#chat-log").remove_children()

    def action_quit(self) -> None:
        self.exit()
