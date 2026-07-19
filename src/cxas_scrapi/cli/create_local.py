from __future__ import annotations

from typing import Any

"""CLI subcommands for setting up CXAS components."""

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

import logging
import sys
from dataclasses import dataclass

import click

from cxas_scrapi.cli.utils import LazyCallable, to_dataclass

CreateUtils = LazyCallable(
    "cxas_scrapi.utils.local.create_utils", "CreateUtils"
)

logger = logging.getLogger(__name__)


@dataclass(frozen=False)
class CreateLocalConfig:
    """Configuration for local resource creation.

    Args:
        target_dir: Target output directory.
        app_dir: Target app directory path.
        create_local_command: Subcommand identifier (agent, tool, guardrail).
        name: Resource name.
        tool_type: Optional tool type.
        add_to_agent: Optional target agent to attach tool to.
        guardrail_type: Guardrail policy type.
    """

    target_dir: str = "."
    app_dir: str = "."
    create_local_command: str | None = None
    name: str | None = None
    tool_type: str | None = None
    add_to_agent: str | None = None
    guardrail_type: str = "llm_policy"


def handle_local_create(config: CreateLocalConfig | Any) -> None:
    """Handles the 'local create' command.

    Args:
        config: Local create configuration object or arguments namespace.
    """
    cfg = to_dataclass(CreateLocalConfig, config)
    type_name = cfg.create_local_command
    print(f"Creating local {type_name} template: {cfg.name}")

    create_utils = CreateUtils()
    try:
        tool_type = cfg.tool_type
        add_to_agent = cfg.add_to_agent
        app_dir = cfg.app_dir

        if type_name == "agent":
            path = create_utils.create_agent(
                display_name=cfg.name, app_dir=app_dir
            )
        elif type_name == "tool":
            path = create_utils.create_tool(
                display_name=cfg.name,
                app_dir=app_dir,
                tool_type=tool_type,
                add_to_agent=add_to_agent,
            )
        elif type_name == "guardrail":
            guardrail_type = cfg.guardrail_type
            path = create_utils.create_guardrail(
                display_name=cfg.name,
                app_dir=app_dir,
                guardrail_type=guardrail_type,
            )
        print(f"Successfully created local template at: {path}")
    except Exception as e:
        print(f"Failed to create local template: {e}")
        sys.exit(1)


@click.command(name="create-local")
@click.argument("name", required=True)
@click.option("--app-dir", default=".", help="App directory path.")
@click.pass_context
def create_local_cmd(ctx: click.Context, name: str, **kwargs: Any) -> None:
    """Create local GECX agent workspace."""
    cfg = to_dataclass(CreateLocalConfig, ctx, name=name, **kwargs)
    handle_local_create(cfg)
