from __future__ import annotations

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

"""CLI command handlers for interactive sessions."""


import sys
from dataclasses import dataclass
from typing import Any

import click

from cxas_scrapi.cli.utils import to_dataclass


@dataclass(frozen=False)
class RunSessionConfig:
    """Configuration for running agent sessions.

    Args:
        app_name: Target app resource name.
        app_dir: App directory path.
        session_id: Optional session identifier.
        modality: Session modality (e.g. text).
        use_tool_fakes: Whether to use mock tool responses.
    """

    app_name: str | None = None
    app_dir: str = "."
    session_id: str | None = None
    modality: str = "text"
    use_tool_fakes: bool = False


def run_session(config: RunSessionConfig | Any) -> None:
    """Handles the 'run-session' command.

    Args:
        config: Run session configuration object or arguments namespace.
    """
    cfg = to_dataclass(RunSessionConfig, config)
    from cxas_scrapi.core.sessions import Sessions

    if not sys.stdin.isatty():
        msg = "ERROR: 'run-session' requires an interactive terminal."
        print(msg, file=sys.stderr)
        sys.exit(1)

    try:
        session_client = Sessions(cfg.app_name)
        session_id = session_client.create_session_id()

        while True:
            try:
                user_input = input()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input.strip():
                continue

            res = session_client.run(
                session_id=session_id,
                text=user_input,
                modality=cfg.modality,
                use_tool_fakes=cfg.use_tool_fakes,
            )
            session_client.parse_result(res)
    except Exception as e:
        print(f"Failed to run session: {e}")
        sys.exit(1)


@click.command(name="run-session")
@click.argument("modality", type=click.Choice(["text"]), required=False)
@click.argument("app_name", required=False)
@click.option("--app-dir", default=".", help="App directory path.")
@click.option("--use-tool-fakes", is_flag=True, help="Use mock tool responses.")
@click.pass_context
def run_session_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Launch interactive terminal session."""
    cfg = to_dataclass(RunSessionConfig, ctx, **kwargs)
    run_session(cfg)
