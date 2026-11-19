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

"""CLI integration tests for ``cxas push --patch`` and ``cxas watch``."""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from google.cloud.ces_v1beta import types

from cxas_scrapi.cli import main as cli_main

if TYPE_CHECKING:
    from pathlib import Path

APP_NAME = "projects/demo-proj/locations/us-central1/apps/support-app"
BILLING_AGENT_RES = f"{APP_NAME}/agents/billing-001"


class _CliFakeBackend:
    def __init__(self) -> None:
        self._seq = 1
        self.app = types.App(
            name=APP_NAME,
            display_name="Support App",
            global_instruction="Be helpful.",
            etag="etag-app-1",
        )
        self.agent = types.Agent(
            name=BILLING_AGENT_RES,
            display_name="Billing Agent",
            instruction="Original billing instruction.",
            etag="etag-agent-1",
        )

    def get_app(self, app_name: str) -> types.App:
        assert app_name == APP_NAME
        return types.App.deserialize(types.App.serialize(self.app))

    def list_agents(self) -> list[types.Agent]:
        return [types.Agent.deserialize(types.Agent.serialize(self.agent))]

    def get_agent(self, agent_name: str) -> types.Agent:
        assert agent_name == BILLING_AGENT_RES
        return types.Agent.deserialize(types.Agent.serialize(self.agent))

    def list_tools(self) -> list[types.Tool]:
        return []

    def list_guardrails(self) -> list[types.Guardrail]:
        return []

    def update_agent(self, *, request: types.UpdateAgentRequest) -> types.Agent:
        self._seq += 1
        if "instruction" in request.update_mask.paths:
            self.agent.instruction = request.agent.instruction
        self.agent.etag = f"etag-agent-{self._seq}"
        return types.Agent.deserialize(types.Agent.serialize(self.agent))


class _CliClientAdapter:
    def __init__(self, backend: _CliFakeBackend) -> None:
        self.backend = backend
        self.client = backend
        self.creds = object()

    def get_app(self, app_name: str) -> types.App:
        return self.backend.get_app(app_name)

    def list_agents(self) -> list[types.Agent]:
        return self.backend.list_agents()

    def get_agent(self, agent_name: str) -> types.Agent:
        return self.backend.get_agent(agent_name)

    def list_tools(self) -> list[types.Tool]:
        return self.backend.list_tools()

    def list_guardrails(self) -> list[types.Guardrail]:
        return self.backend.list_guardrails()


def _setup_cli_workspace(root: Path) -> Path:
    (root / "app.json").write_text(
        json.dumps({"displayName": "Support App"}), encoding="utf-8"
    )
    (root / "global_instruction.txt").write_text(
        "Be helpful.", encoding="utf-8"
    )
    billing_dir = root / "agents" / "billing_agent"
    billing_dir.mkdir(parents=True)
    (billing_dir / "billing_agent.json").write_text(
        json.dumps({"displayName": "Billing Agent"}), encoding="utf-8"
    )
    (billing_dir / "instruction.txt").write_text(
        "Original billing instruction.", encoding="utf-8"
    )
    return root


def test_main_parser_registers_push_patch_flags_and_watch() -> None:
    parser = cli_main.get_parser()

    push_args = parser.parse_args(
        [
            "push",
            "--patch",
            "--dry-run",
            "--force-etag",
            "--skip-lint",
            "--expected-etag",
            "etag-1",
            "--app-dir",
            "./my_app",
            "--to",
            APP_NAME,
        ]
    )
    assert push_args.patch is True
    assert push_args.dry_run is True
    assert push_args.force_etag is True
    assert push_args.skip_lint is True
    assert push_args.expected_etag == "etag-1"
    assert push_args.to == APP_NAME

    watch_args = parser.parse_args(
        [
            "watch",
            "--app-dir",
            "./my_app",
            "--to",
            APP_NAME,
            "--interval",
            "0.2",
        ]
    )
    assert watch_args.to == APP_NAME
    assert watch_args.interval == pytest.approx(0.2)


def test_cxas_push_patch_executes_surgical_rpc_without_import_app(
    tmp_path: Path,
) -> None:
    workspace = _setup_cli_workspace(tmp_path)
    backend = _CliFakeBackend()
    adapter = _CliClientAdapter(backend)

    (workspace / "agents" / "billing_agent" / "instruction.txt").write_text(
        "Hot-patched billing prompt via cxas CLI!",
        encoding="utf-8",
    )

    parser = cli_main.get_parser()
    args = parser.parse_args(
        [
            "push",
            "--patch",
            "--app-dir",
            str(workspace),
            "--to",
            APP_NAME,
        ]
    )

    with mock.patch(
        "cxas_scrapi.cli.hot_patch_cli.resolve_patch_target",
        return_value=(adapter, APP_NAME),
    ):
        args.func(args)

    assert (
        backend.agent.instruction == "Hot-patched billing prompt via cxas CLI!"
    )


def test_watch_mode_detects_file_save_and_applies_patch(
    tmp_path: Path,
) -> None:
    workspace = _setup_cli_workspace(tmp_path)
    (workspace / "gecx-config.json").write_text(
        json.dumps({"app_name": APP_NAME}),
        encoding="utf-8",
    )
    backend = _CliFakeBackend()
    adapter = _CliClientAdapter(backend)

    def _modify_after_delay() -> None:
        time.sleep(0.2)
        (workspace / "agents" / "billing_agent" / "instruction.txt").write_text(
            "Live hot-reloaded via cxas watch!",
            encoding="utf-8",
        )

    writer = threading.Thread(target=_modify_after_delay, daemon=True)
    parser = cli_main.get_parser()
    args = parser.parse_args(
        [
            "watch",
            "--app-dir",
            str(workspace),
            "--interval",
            "0.1",
            "--max-events",
            "1",
        ]
    )

    with mock.patch(
        "cxas_scrapi.cli.hot_patch_cli.resolve_patch_target",
        return_value=(adapter, APP_NAME),
    ):
        writer.start()
        args.func(args)
        writer.join(timeout=2.0)

    assert backend.agent.instruction == "Live hot-reloaded via cxas watch!"
