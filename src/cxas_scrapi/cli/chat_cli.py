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

"""Argparse subcommand handlers for `cxas chat`.

Provides an interactive REPL for chatting with a CES agent, with
integrated trace analysis, slash commands, and rich terminal output.
"""

import argparse
import atexit
import json
import logging
import os
import readline
import shutil
import subprocess
import sys
import warnings

from cxas_scrapi.core.chat_session import ChatSession, SessionEndedError
from cxas_scrapi.core.traces import Traces
from cxas_scrapi.utils.chat_renderer import ChatRenderer
from cxas_scrapi.utils.tracing.trace_config import TraceConfig

logger = logging.getLogger(__name__)

SLASH_COMMANDS = {
    "/help": "Show available commands",
    "/trace": "Fetch and display the trace for this session",
    "/state": "Show current variable/slot state",
    "/slots": "Deep slot machine inspection (phase, DAG, all fields)",
    "/slots <category>": "Show one category (e.g., core_data, configuration)",
    "/slots flows": "List all flows with status (active/suspended/idle)",
    "/slots flow:<name>": "Inspect saved slots in a suspended flow",
    "/log": "Show slot machine lifecycle timeline (INFO+)",
    "/log <level>": "Filter timeline (debug, info, warn, error)",
    "/bug <reason>": "Bundle this conversation as a bug report",
    "/save <path>": "Export the conversation to a file",
    "/clear": "Clear the terminal",
    "/quit": "End the session",
}


def _resolve_app_name(args: argparse.Namespace) -> tuple[str, str, str]:
    """Resolve --app-name to a full resource name and display name.

    Resolution order:
    1. If --app-name is a full resource name (contains "projects/"), use it.
    2. If --app-name is a display name, look it up via the Apps API
       (requires --project-id and --location, or defaults from config).
    3. If --app-name is not provided, fall back to defaults.app_name
       from .cxas/trace.yaml.

    Returns:
        Tuple of (resource_name, display_name, config_path).
    """
    explicit_config = getattr(args, "config", None)
    config_path = TraceConfig._pick_path(explicit_config) or ""
    config = TraceConfig.load(explicit_config)
    app_name = getattr(args, "app_name", None)

    if not app_name:
        app_name = config.defaults.app_name
    if not app_name:
        print("Error: --app-name is required (or set defaults.app_name in .cxas/trace.yaml)")
        sys.exit(1)

    if "projects/" in app_name:
        display_name = app_name
        try:
            from cxas_scrapi.core.apps import Apps
            parts = app_name.split("/")
            pid = parts[parts.index("projects") + 1]
            loc = parts[parts.index("locations") + 1]
            app = Apps(project_id=pid, location=loc).get_app(app_name)
            display_name = app.display_name or app_name
        except Exception:
            pass
        return app_name, display_name, config_path

    display_name = app_name
    project_id = getattr(args, "project_id", None) or config.defaults.project_id
    location = getattr(args, "location", None) or config.defaults.location
    if not project_id or not location:
        print(
            f"Error: Display name '{app_name}' requires --project-id and "
            "--location (or set defaults.project_id and defaults.location "
            "in .cxas/trace.yaml)"
        )
        sys.exit(1)

    from cxas_scrapi.core.apps import Apps
    apps_client = Apps(project_id=project_id, location=location)
    app = apps_client.get_app_by_display_name(app_name)
    if not app:
        print(f"Error: App '{app_name}' not found in {project_id}/{location}")
        sys.exit(1)

    logger.info("Resolved '%s' -> %s", app_name, app.name)
    return app.name, display_name, config_path


def chat_interactive(args: argparse.Namespace) -> None:
    """Main REPL handler for `cxas chat`."""
    warnings.filterwarnings("ignore", message=".*end user credentials.*")
    logging.getLogger("cxas_scrapi.core.traces").setLevel(logging.WARNING)
    logging.getLogger("cxas_scrapi.core.conversation_history").setLevel(
        logging.WARNING
    )

    app_name, display_name, config_path = _resolve_app_name(args)

    historical_contexts = None
    if getattr(args, "fork", None):
        traces = Traces(app_name=app_name)
        fork_result = traces.fork(
            args.fork, at_turn=getattr(args, "fork_at_turn", None)
        )
        historical_contexts = fork_result.get("historical_contexts")

    session = ChatSession(
        app_name=app_name,
        channel=getattr(args, "channel", None),
        deployment_id=getattr(args, "deployment_id", None),
        historical_contexts=historical_contexts,
    )

    renderer = ChatRenderer(verbose=getattr(args, "verbose", False))
    renderer.render_session_start(
        session.session_id, app_name, display_name, config_path,
    )
    _setup_readline()

    try:
        while True:
            try:
                user_input = input("You: ")
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input.strip():
                continue

            if user_input.strip().startswith("/"):
                should_continue = _handle_slash_command(
                    user_input.strip(), session, renderer, args
                )
                if not should_continue:
                    break
                continue

            try:
                turn = session.send(user_input)
                renderer.render_turn(turn)
                if session.is_ended:
                    break
            except SessionEndedError:
                renderer.render_error("Session has already ended.")
                break
    except Exception as e:
        renderer.render_error(f"Unexpected error: {e}")

    # Post-session: auto-trace if requested
    if getattr(args, "trace", False):
        try:
            trace_fmt = getattr(args, "trace_format", "text")
            trace_output = session.get_trace(fmt=trace_fmt)
            print(trace_output)
        except Exception as e:
            renderer.render_error(f"Failed to fetch trace: {e}")

    trace_command = (
        f"cxas trace get --app-name {app_name} "
        f"{session.session_id}"
    )
    renderer.render_session_end(
        session.session_id, len(session.turns), trace_command
    )

    # Save session ID for later reference
    _save_last_session(session.session_id)


def chat_tui_interactive(args: argparse.Namespace) -> None:
    """Launch the Textual TUI for `cxas chat --tui`."""
    warnings.filterwarnings("ignore", message=".*end user credentials.*")

    app_name, display_name, _ = _resolve_app_name(args)
    session = ChatSession(
        app_name=app_name,
        channel=getattr(args, "channel", None),
        deployment_id=getattr(args, "deployment_id", None),
    )

    from cxas_scrapi.cli.chat_tui import ChatApp
    app = ChatApp(
        session=session,
        display_name=display_name,
        verbose=getattr(args, "verbose", False),
    )
    app.run()


def chat_script(args: argparse.Namespace) -> None:
    """Handler for `cxas chat --script <path>`."""
    from cxas_scrapi.utils.script_runner import ScriptRunner

    app_name, _, _ = _resolve_app_name(args)
    script = ScriptRunner.load_script(args.script)
    runner = ScriptRunner(
        app_name=app_name,
        channel=getattr(args, "channel", None),
    )
    result = runner.run_script(script)
    print(json.dumps(
        {
            "script_name": result.script_name,
            "passed": result.passed,
            "total_turns": result.total_turns,
            "passed_turns": result.passed_turns,
            "failed_turns": result.failed_turns,
        },
        indent=2,
    ))
    if not result.passed:
        sys.exit(1)


def _handle_slash_command(
    cmd: str,
    session: ChatSession,
    renderer: ChatRenderer,
    args: argparse.Namespace,
) -> bool:
    """Dispatch slash commands. Returns True if session should continue."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    command_arg = parts[1] if len(parts) > 1 else ""

    if command == "/help":
        renderer.render_slash_help()
        return True

    if command == "/state":
        state = session.get_state()
        _paged(renderer, lambda: renderer.render_state(state))
        return True

    if command == "/trace":
        try:
            trace_fmt = getattr(args, "trace_format", "text")
            trace_output = session.get_trace(fmt=trace_fmt)
            print(trace_output)
        except Exception as e:
            renderer.render_error(f"Failed to fetch trace: {e}")
        return True

    if command == "/slots":
        if not session.turns:
            renderer.render_error("Send at least one message before using /slots.")
            return True
        try:
            from cxas_scrapi.utils.slot_inspector import SlotInspector
            sm = session.get_slot_machine()
            if not sm:
                renderer.render_error("No slot machine state found.")
                return True
            inspection = SlotInspector.inspect(sm)
            flow_context = session.get_flow_context()
            category_filter = command_arg.strip() if command_arg.strip() else None
            _paged(renderer, lambda: renderer.render_slots(
                inspection,
                category=category_filter,
                flow_context=flow_context,
            ))
        except Exception as e:
            renderer.render_error(f"Slot inspection failed: {e}")
        return True

    if command == "/log":
        if not session.turns:
            renderer.render_error("Send at least one message before using /log.")
            return True
        try:
            sm = session.get_slot_machine()
            if not sm:
                renderer.render_error("No slot machine state found.")
                return True
            log_entries = sm.get("_log", [])
            if not log_entries:
                renderer.render_error("No log entries in slot machine.")
                return True
            level = command_arg.strip().upper() if command_arg.strip() else "INFO"
            valid_levels = {"DEBUG", "INFO", "WARN", "ERROR"}
            if level not in valid_levels:
                renderer.render_error(
                    f"Unknown level '{level}'. Use: debug, info, warn, error"
                )
                return True
            _paged(renderer, lambda: renderer.render_log(log_entries, min_level=level))
        except Exception as e:
            renderer.render_error(f"Log display failed: {e}")
        return True

    if command == "/bug":
        if not command_arg:
            renderer.render_error("Usage: /bug <reason>")
            return True
        try:
            traces = Traces(app_name=session._app_name)
            result = traces.report_bug(
                conversation_id=session.session_id,
                reason=command_arg,
            )
            print(json.dumps(result, indent=2))
        except Exception as e:
            renderer.render_error(f"Failed to report bug: {e}")
        return True

    if command == "/save":
        if not command_arg:
            renderer.render_error("Usage: /save <path>")
            return True
        try:
            summary = session.export_turns_summary()
            with open(command_arg, "w") as f:
                json.dump(summary, f, indent=2)
            print(f"Saved to {command_arg}")
        except Exception as e:
            renderer.render_error(f"Failed to save: {e}")
        return True

    if command == "/clear":
        renderer.console.clear()
        return True

    if command == "/quit":
        return False

    renderer.render_error(f"Unknown command: {command}. Type /help for help.")
    return True


def _save_last_session(session_id: str) -> None:
    """Save the session ID to .cxas/last_session.txt."""
    try:
        os.makedirs(".cxas", exist_ok=True)
        with open(".cxas/last_session.txt", "w") as f:
            f.write(session_id)
    except OSError:
        pass


_HISTORY_FILE = os.path.join(".cxas", "chat_history")
_COMPLETABLE_COMMANDS = sorted({
    k.split()[0] for k in SLASH_COMMANDS
})


def _slash_completer(text: str, state: int) -> str | None:
    if text.startswith("/"):
        matches = [c for c in _COMPLETABLE_COMMANDS if c.startswith(text)]
        return matches[state] if state < len(matches) else None
    return None


def _paged(renderer: ChatRenderer, fn: callable) -> None:
    """Run fn() and display output in less -R (alternate screen, ANSI colors).

    Bypasses pydoc.pager() which misdetects TTY and garbles Rich output.
    Falls back to inline rendering if less is not available.
    """
    if not shutil.which("less"):
        fn()
        return
    from io import StringIO
    from rich.console import Console
    buf = StringIO()
    pager_console = Console(file=buf, force_terminal=True, width=renderer.console.width)
    original = renderer.console
    renderer.console = pager_console
    try:
        fn()
    finally:
        renderer.console = original
    content = buf.getvalue()
    if not content.strip():
        return
    try:
        proc = subprocess.Popen(
            ["less", "-R"],
            stdin=subprocess.PIPE,
            text=True,
        )
        proc.communicate(input=content)
    except OSError:
        original.print(content, highlight=False)


def _setup_readline() -> None:
    """Enable command history, tab completion, and Ctrl+R support."""
    readline.set_history_length(1000)
    try:
        readline.read_history_file(_HISTORY_FILE)
    except FileNotFoundError:
        pass

    readline.set_completer(_slash_completer)
    readline.set_completer_delims(" \t\n")
    if "libedit" in (getattr(readline, "__doc__", "") or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    readline.parse_and_bind("tab: complete")

    def _save() -> None:
        try:
            os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
            readline.write_history_file(_HISTORY_FILE)
        except OSError:
            pass
    atexit.register(_save)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Adds the `chat` subcommand to the CLI."""
    parser_chat = subparsers.add_parser(
        "chat",
        help="Interactive chat with a CES agent, with trace integration.",
    )
    parser_chat.add_argument(
        "--app-name",
        default=None,
        help=(
            "App resource name or display name. "
            "Falls back to defaults.app_name in .cxas/trace.yaml."
        ),
    )
    parser_chat.add_argument(
        "--project-id",
        default=None,
        help="GCP Project ID (required when using a display name).",
    )
    parser_chat.add_argument(
        "--location",
        default=None,
        help="GCP Location (required when using a display name).",
    )
    parser_chat.add_argument(
        "--channel",
        default=None,
        help="Channel to inject as event_data.channel variable.",
    )
    parser_chat.add_argument(
        "--deployment-id",
        default=None,
        help="Deployment ID to target.",
    )
    parser_chat.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show tool calls, responses, and variable updates.",
    )
    parser_chat.add_argument(
        "--trace",
        action="store_true",
        help="Auto-fetch and display trace on session end.",
    )
    parser_chat.add_argument(
        "--trace-format",
        choices=["json", "md", "text", "html"],
        default="text",
        help="Format for auto-trace output (default: text).",
    )
    parser_chat.add_argument(
        "--metrics",
        action="store_true",
        help="Show per-turn latency and token metrics.",
    )
    parser_chat.add_argument(
        "--script",
        default=None,
        help="Path to a YAML conversation script (non-interactive mode).",
    )
    parser_chat.add_argument(
        "--fork",
        default=None,
        metavar="CONV_ID",
        help=(
            "Fork from an existing conversation "
            "(load as historical context)."
        ),
    )
    parser_chat.add_argument(
        "--fork-at-turn",
        type=int,
        default=None,
        help="When forking, load only the first N turns.",
    )
    parser_chat.add_argument(
        "--config",
        default=None,
        help="Path to trace.yaml config file.",
    )
    parser_chat.add_argument(
        "--tui",
        action="store_true",
        help="Launch interactive TUI with clickable buttons (requires textual).",
    )

    def _chat_dispatch(args: argparse.Namespace) -> None:
        if args.script:
            chat_script(args)
        elif args.tui:
            chat_tui_interactive(args)
        else:
            chat_interactive(args)

    parser_chat.set_defaults(func=_chat_dispatch)
