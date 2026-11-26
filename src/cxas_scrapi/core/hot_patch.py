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

"""In-Flight Prompt Hot-Patching Engine for CXAS / GECX.

Compiles workspace file deltas against ``.cxas/state_manifest.json`` (or a live
remote baseline on first run), executes scoped <50ms pre-flight validation on
modified files only, enforces optimistic concurrency via resource ``etag``s
(verified atomically by CES on commit with zero extra pre-read RPCs when
warm), applies surgical ``google.protobuf.FieldMask`` RPC updates, and performs
instant in-memory atomic rollback if any multi-resource patch fails.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import datetime
import enum
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import google.auth
import jinja2
import yaml
from google.api_core import exceptions as google_exceptions
from google.api_core.client_options import ClientOptions
from google.auth.transport.requests import Request
from google.cloud.ces_v1beta import AgentServiceClient, types
from google.oauth2.credentials import Credentials
from google.protobuf import field_mask_pb2, json_format

if TYPE_CHECKING:
    from collections.abc import Callable

MANIFEST_REL_PATH = os.path.join(".cxas", "state_manifest.json")
MANIFEST_SCHEMA_VERSION = "2.0"

GLOBAL_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language.retriever",
]

CALLBACK_FIELD_SPECS: dict[str, tuple[str, int, str]] = {
    "before_agent_callbacks": (
        "before_agent_callback",
        1,
        "(callback_context: CallbackContext)",
    ),
    "after_agent_callbacks": (
        "after_agent_callback",
        1,
        "(callback_context: CallbackContext)",
    ),
    "before_model_callbacks": (
        "before_model_callback",
        2,
        "(callback_context: CallbackContext, llm_request: LlmRequest)",
    ),
    "after_model_callbacks": (
        "after_model_callback",
        2,
        "(callback_context: CallbackContext, llm_response: LlmResponse)",
    ),
    "before_tool_callbacks": (
        "before_tool_callback",
        3,
        (
            "(tool: Tool, tool_input: dict[str, Any], "
            "callback_context: CallbackContext)"
        ),
    ),
    "after_tool_callbacks": (
        "after_tool_callback",
        4,
        (
            "(tool: Tool, tool_input: dict[str, Any], "
            "callback_context: CallbackContext, "
            "tool_response: dict[str, Any])"
        ),
    ),
}

CAMEL_TO_SNAKE_CALLBACKS: dict[str, str] = {
    "beforeAgentCallbacks": "before_agent_callbacks",
    "afterAgentCallbacks": "after_agent_callbacks",
    "beforeModelCallbacks": "before_model_callbacks",
    "afterModelCallbacks": "after_model_callbacks",
    "beforeToolCallbacks": "before_tool_callbacks",
    "afterToolCallbacks": "after_tool_callbacks",
}

SNAKE_TO_CAMEL_CALLBACKS: dict[str, str] = {
    v: k for k, v in CAMEL_TO_SNAKE_CALLBACKS.items()
}

SANDBOX_GLOBALS: frozenset[str] = frozenset(
    {
        "CallbackContext",
        "LlmRequest",
        "LlmResponse",
        "Content",
        "Part",
    }
)

PLATFORM_SYSTEM_TOOLS: frozenset[str] = frozenset(
    {"end_session", "customize_response"}
)

_MACRO_OPEN_RE = re.compile(r"\{@(TOOL|AGENT):")
_TOOL_MACRO_RE = re.compile(r"\{@TOOL:\s*([^}]+)\}")
_AGENT_MACRO_RE = re.compile(r"\{@AGENT:\s*([^}]+)\}")
_ANY_MACRO_RE = re.compile(r"\{@(?:TOOL|AGENT):[^}]*\}")

_IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".cxas",
        ".git",
        ".hg",
        ".jj",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
    }
)


def extract_project_id(resource_name: str) -> str | None:
    parts = resource_name.split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        return parts[1]
    return None


def extract_location(resource_name: str) -> str | None:
    parts = resource_name.split("/")
    if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "locations":
        return parts[3]
    return None


class DirectCesControlPlane:
    """Fast single-channel CES gRPC client avoiding heavy analytics imports."""

    def __init__(
        self,
        project_id: str,
        location: str,
        creds: Any = None,
    ):
        self.project_id = project_id
        self.location = location
        self.parent = f"projects/{project_id}/locations/{location}"

        if creds is not None:
            self.creds = creds
        elif os.environ.get("CXAS_OAUTH_TOKEN"):
            self.creds = Credentials(token=os.environ["CXAS_OAUTH_TOKEN"])
        else:
            self.creds, _ = google.auth.default(scopes=GLOBAL_SCOPES)
            if not getattr(self.creds, "valid", False):
                self.creds.refresh(Request())

        endpoint = os.environ.get("CES_API_ENDPOINT", "ces.googleapis.com")
        transport = os.environ.get("CES_TRANSPORT", "grpc").lower()
        self.client = AgentServiceClient(
            credentials=self.creds,
            transport="rest" if transport == "rest" else None,
            client_options=ClientOptions(api_endpoint=endpoint),
        )

    def get_app(self, app_name: str) -> types.App:
        return self.client.get_app(request=types.GetAppRequest(name=app_name))

    def get_app_by_display_name(self, display_name: str) -> types.App | None:
        req = types.ListAppsRequest(
            parent=self.parent,
            filter=f'display_name="{display_name}"',
        )
        for app in self.client.list_apps(request=req):
            if app.display_name == display_name:
                return app
        for app in self.client.list_apps(
            request=types.ListAppsRequest(parent=self.parent)
        ):
            if app.display_name == display_name:
                return app
        return None

    def list_agents(self, app_name: str) -> list[types.Agent]:
        return list(
            self.client.list_agents(
                request=types.ListAgentsRequest(parent=app_name)
            )
        )

    def get_agent(self, agent_name: str) -> types.Agent:
        return self.client.get_agent(
            request=types.GetAgentRequest(name=agent_name)
        )

    def list_tools(self, app_name: str) -> list[types.Tool]:
        return list(
            self.client.list_tools(
                request=types.ListToolsRequest(parent=app_name)
            )
        )

    def get_tool(self, tool_name: str) -> types.Tool:
        return self.client.get_tool(
            request=types.GetToolRequest(name=tool_name)
        )

    def list_guardrails(self, app_name: str) -> list[types.Guardrail]:
        return list(
            self.client.list_guardrails(
                request=types.ListGuardrailsRequest(parent=app_name)
            )
        )

    def get_guardrail(self, guardrail_name: str) -> types.Guardrail:
        return self.client.get_guardrail(
            request=types.GetGuardrailRequest(name=guardrail_name)
        )


class _AppScopedAdapter:
    """Adapts ``DirectCesControlPlane`` to per-app Agent/Tool/Guardrail RPCs."""

    def __init__(self, app_name: str, cp: DirectCesControlPlane):
        self.app_name = app_name
        self._cp = cp
        self.client = cp.client
        self.creds = cp.creds

    def get_app(self, app_name: str) -> types.App:
        return self._cp.get_app(app_name)

    def list_agents(self) -> list[types.Agent]:
        return self._cp.list_agents(self.app_name)

    def get_agent(self, agent_name: str) -> types.Agent:
        return self._cp.get_agent(agent_name)

    def list_tools(self) -> list[types.Tool]:
        return self._cp.list_tools(self.app_name)

    def get_tool(self, tool_name: str) -> types.Tool:
        return self._cp.get_tool(tool_name)

    def list_guardrails(self) -> list[types.Guardrail]:
        return self._cp.list_guardrails(self.app_name)

    def get_guardrail(self, guardrail_name: str) -> types.Guardrail:
        return self._cp.get_guardrail(guardrail_name)


class PatchTargetKind(str, enum.Enum):
    """Hot-patchable, DAG-creatable, and structural file categories."""

    GLOBAL_INSTRUCTION = "global_instruction"
    APP_CONFIG = "app_config"
    AGENT_INSTRUCTION = "agent_instruction"
    AGENT_CALLBACK = "agent_callback"
    AGENT_CONFIG = "agent_config"
    PYTHON_TOOL = "python_tool"
    TOOL_CONFIG = "tool_config"
    GUARDRAIL = "guardrail"
    STRUCTURAL_CONFIG = "structural_config"


@dataclasses.dataclass(frozen=True)
class FileDelta:
    """Represents a single modified workspace file detected by SHA-256 diff."""

    rel_path: str
    abs_path: Path
    kind: PatchTargetKind
    resource_key: str
    field_mask_path: str
    old_sha256: str | None
    new_sha256: str
    size_bytes: int
    change_type: str  # "modified" | "added"


@dataclasses.dataclass(frozen=True)
class LintFinding:
    """Single validation issue produced by ScopedPreflightLinter."""

    file: str
    rule_id: str
    severity: str  # "error" | "warning"
    message: str
    line: int | None = None

    def format_short(self) -> str:
        loc = f"{self.file}:{self.line}" if self.line else self.file
        prefix = "E" if self.severity == "error" else "W"
        return f"[{prefix}] {loc} [{self.rule_id}] {self.message}"


@dataclasses.dataclass
class AgentStateSnapshot:
    """Immutable pre-patch state of a single CES Agent for atomic rollback."""

    resource_name: str
    display_name: str
    etag: str
    instruction: str
    callbacks: dict[str, list[types.Callback]] = dataclasses.field(
        default_factory=dict
    )
    description: str = ""
    tools: list[str] = dataclasses.field(default_factory=list)
    child_agents: list[str] = dataclasses.field(default_factory=list)
    guardrails: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "display_name": self.display_name,
            "etag": self.etag,
            "instruction": self.instruction,
            "description": self.description,
            "tools": list(self.tools),
            "child_agents": list(self.child_agents),
            "guardrails": list(self.guardrails),
            "callbacks": {
                cb_field: [
                    {
                        "python_code": cb.python_code,
                        "description": cb.description,
                        "disabled": bool(cb.disabled),
                    }
                    for cb in cb_list
                ]
                for cb_field, cb_list in self.callbacks.items()
                if cb_list
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentStateSnapshot:
        raw_cbs = data.get("callbacks") or {}
        parsed_cbs: dict[str, list[types.Callback]] = {
            cb_field: [] for cb_field in CALLBACK_FIELD_SPECS
        }
        for cb_field, items in raw_cbs.items():
            if cb_field in parsed_cbs and isinstance(items, list):
                parsed_cbs[cb_field] = [
                    types.Callback(
                        python_code=item.get("python_code", ""),
                        description=item.get("description", ""),
                        disabled=bool(item.get("disabled", False)),
                    )
                    for item in items
                    if isinstance(item, dict)
                ]
        return cls(
            resource_name=data["resource_name"],
            display_name=data.get("display_name", ""),
            etag=data.get("etag", ""),
            instruction=data.get("instruction", ""),
            callbacks=parsed_cbs,
            description=data.get("description", ""),
            tools=list(data.get("tools") or []),
            child_agents=list(data.get("child_agents") or []),
            guardrails=list(data.get("guardrails") or []),
        )


@dataclasses.dataclass
class ToolStateSnapshot:
    """Immutable pre-patch state of a single CES Python Function Tool."""

    resource_name: str
    display_name: str
    etag: str
    python_code: str
    function_name: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "display_name": self.display_name,
            "etag": self.etag,
            "python_code": self.python_code,
            "function_name": self.function_name,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolStateSnapshot:
        return cls(
            resource_name=data["resource_name"],
            display_name=data.get("display_name", ""),
            etag=data.get("etag", ""),
            python_code=data.get("python_code", ""),
            function_name=data.get("function_name", ""),
            description=data.get("description", ""),
        )


@dataclasses.dataclass
class GuardrailStateSnapshot:
    """Immutable pre-patch state of a single CES Guardrail."""

    resource_name: str
    display_name: str
    etag: str
    guardrail_dict: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "display_name": self.display_name,
            "etag": self.etag,
            "guardrail_dict": dict(self.guardrail_dict),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuardrailStateSnapshot:
        return cls(
            resource_name=data["resource_name"],
            display_name=data.get("display_name", ""),
            etag=data.get("etag", ""),
            guardrail_dict=dict(data.get("guardrail_dict") or {}),
        )


@dataclasses.dataclass
class PrePatchSnapshot:
    """In-memory snapshot of all target resources prior to applying patches."""

    app_name: str
    app_display_name: str = "App"
    app_etag: str = ""
    global_instruction: str | None = None
    app_guardrails: list[str] | None = None
    app_root_agent: str | None = None
    app_description: str | None = None
    agents: dict[str, AgentStateSnapshot] = dataclasses.field(
        default_factory=dict
    )
    tools: dict[str, ToolStateSnapshot] = dataclasses.field(
        default_factory=dict
    )
    guardrails: dict[str, GuardrailStateSnapshot] = dataclasses.field(
        default_factory=dict
    )


@dataclasses.dataclass
class ResourcePatchResult:
    """Result of a surgical Create/Update App/Agent/Tool/Guardrail RPC."""

    resource_type: str  # "app" | "agent" | "tool" | "guardrail"
    resource_key: str
    resource_name: str
    display_name: str
    field_mask_paths: list[str]
    old_etag: str
    new_etag: str
    latency_ms: float
    payload_bytes: int
    operation: str = "UPDATE"  # "UPDATE" | "CREATE"
    dag_level: int = 1


@dataclasses.dataclass
class RollbackAction:
    """Outcome of reverting or deleting a mutated resource during rollback."""

    resource_type: str
    resource_name: str
    display_name: str
    field_mask_paths: list[str]
    restored: bool
    latency_ms: float
    error: str | None = None
    operation: str = "REVERT"  # "REVERT" | "DELETE"


@dataclasses.dataclass
class HotPatchReport:
    """End-to-end execution report for a HotPatchEngine run."""

    app_name: str
    app_root: Path
    status: str  # "noop" | "dry_run" | "patched" | "rolled_back" | "aborted"
    deltas: list[FileDelta] = dataclasses.field(default_factory=list)
    structural_warnings: list[str] = dataclasses.field(default_factory=list)
    lint_findings: list[LintFinding] = dataclasses.field(default_factory=list)
    applied_patches: list[ResourcePatchResult] = dataclasses.field(
        default_factory=list
    )
    rollback_actions: list[RollbackAction] = dataclasses.field(
        default_factory=list
    )
    bootstrapped_from_remote: bool = False
    diff_ms: float = 0.0
    lint_ms: float = 0.0
    snapshot_ms: float = 0.0
    rpc_ms: float = 0.0
    rollback_ms: float = 0.0
    total_ms: float = 0.0
    error_message: str | None = None

    @property
    def total_payload_bytes(self) -> int:
        return sum(p.payload_bytes for p in self.applied_patches)


class HotPatchError(Exception):
    """Base exception for hot-patching failures."""


class ScopedLintError(HotPatchError):
    """Raised when scoped pre-flight linting detects blocking errors."""

    def __init__(self, findings: list[LintFinding], report: HotPatchReport):
        self.findings = findings
        self.report = report
        joined = "; ".join(f.format_short() for f in findings)
        super().__init__(f"Pre-flight lint failed: {joined}")


class EtagConflictError(HotPatchError):
    """Raised when a remote resource etag differs from the expected baseline."""

    def __init__(
        self,
        resource_name: str,
        display_name: str,
        expected_etag: str,
        remote_etag: str,
        report: HotPatchReport,
    ):
        self.resource_name = resource_name
        self.display_name = display_name
        self.expected_etag = expected_etag
        self.remote_etag = remote_etag
        self.report = report
        super().__init__(
            f"Optimistic concurrency conflict on '{display_name}' "
            f"({resource_name}): expected etag '{expected_etag}', "
            f"but live server has '{remote_etag}'. "
            "Run 'cxas pull' to merge remote edits or pass --force-etag."
        )


class HotPatchApplyError(HotPatchError):
    """Raised when a patch RPC or post-patch check fails."""

    def __init__(self, message: str, report: HotPatchReport):
        self.report = report
        super().__init__(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    return sha256_bytes(raw), len(raw)


def resolve_app_root(app_dir: str | Path) -> Path:
    """Resolves the root directory of a local CXAS app workspace."""
    base = Path(app_dir).resolve()
    if not base.exists():
        raise FileNotFoundError(f"App directory does not exist: {base}")

    gecx_cfg = base / "gecx-config.json"
    if gecx_cfg.is_file():
        try:
            cfg = json.loads(gecx_cfg.read_text(encoding="utf-8"))
            configured = cfg.get("app_dir")
            if configured:
                candidate = (base / configured).resolve()
                if candidate.is_dir():
                    base = candidate
        except (OSError, json.JSONDecodeError):
            pass

    markers = ("app.json", "app.yaml", "global_instruction.txt", "agents")
    if any((base / m).exists() for m in markers):
        return base

    subdirs = [
        d
        for d in sorted(base.iterdir())
        if d.is_dir() and not d.name.startswith(".")
    ]
    for sub in subdirs:
        if any((sub / m).exists() for m in markers):
            return sub

    return base


def classify_workspace_file(
    rel_posix: str,
) -> tuple[PatchTargetKind, str, str] | None:
    """Classifies a relative POSIX path into (kind, resource_key, mask_path)."""
    parts = rel_posix.split("/")

    if rel_posix == "global_instruction.txt":
        return (
            PatchTargetKind.GLOBAL_INSTRUCTION,
            "app",
            "global_instruction",
        )

    if rel_posix in ("app.json", "app.yaml", "app.yml"):
        return (
            PatchTargetKind.APP_CONFIG,
            "app",
            "app_config",
        )

    if (
        len(parts) == 3
        and parts[0] == "agents"
        and parts[2] in ("instruction.txt", "instructions.txt")
    ):
        return (
            PatchTargetKind.AGENT_INSTRUCTION,
            parts[1],
            "instruction",
        )

    if (
        len(parts) == 5
        and parts[0] == "agents"
        and parts[2] in CALLBACK_FIELD_SPECS
        and parts[4] == "python_code.py"
    ):
        return (
            PatchTargetKind.AGENT_CALLBACK,
            parts[1],
            parts[2],
        )

    if (
        len(parts) == 3
        and parts[0] == "agents"
        and parts[2].endswith((".json", ".yaml", ".yml"))
    ):
        return (
            PatchTargetKind.AGENT_CONFIG,
            parts[1],
            "agent_config",
        )

    if (
        len(parts) == 4
        and parts[0] == "tools"
        and parts[2] == "python_function"
        and parts[3] == "python_code.py"
    ):
        return (
            PatchTargetKind.PYTHON_TOOL,
            parts[1],
            "python_function.python_code",
        )

    if (
        len(parts) == 3
        and parts[0] == "tools"
        and parts[2].endswith((".json", ".yaml", ".yml"))
    ):
        return (
            PatchTargetKind.TOOL_CONFIG,
            parts[1],
            "tool_config",
        )

    if (
        len(parts) == 3
        and parts[0] == "guardrails"
        and parts[2].endswith((".json", ".yaml", ".yml"))
    ):
        return (
            PatchTargetKind.GUARDRAIL,
            parts[1],
            "guardrail",
        )

    if (
        len(parts) >= 3
        and parts[0] == "guardrails"
        and parts[-1] == "python_code.py"
    ):
        return (
            PatchTargetKind.GUARDRAIL,
            parts[1],
            "code_callback",
        )

    if rel_posix == "environment.json" or (
        parts[0]
        in (
            "agents",
            "tools",
            "guardrails",
            "toolsets",
            "evaluations",
            "evaluationExpectations",
        )
        and rel_posix.endswith((".json", ".yaml", ".yml"))
    ):
        return (PatchTargetKind.STRUCTURAL_CONFIG, parts[0], "")

    return None


def scan_workspace_hashes(app_root: Path) -> dict[str, dict[str, Any]]:
    """Scans all relevant files under ``app_root`` and computes SHA-256s."""
    entries: dict[str, dict[str, Any]] = {}
    for root, dirs, files in os.walk(app_root):
        dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIR_NAMES)
        for fname in sorted(files):
            if fname.startswith(".") or fname.endswith(".pyc"):
                continue
            fpath = Path(root) / fname
            if not fpath.is_file():
                continue
            rel_posix = fpath.relative_to(app_root).as_posix()
            classified = classify_workspace_file(rel_posix)
            if classified is None:
                continue
            digest, size_bytes = sha256_file(fpath)
            entries[rel_posix] = {
                "sha256": digest,
                "size": size_bytes,
                "kind": classified[0].value,
            }
    return entries


class StateManifest:
    """Manages ``.cxas/state_manifest.json`` for diffing, etags & snapshots."""

    def __init__(
        self,
        app_name: str,
        app_display_name: str = "App",
        app_etag: str = "",
        global_instruction: str | None = None,
        app_guardrails: list[str] | None = None,
        app_root_agent: str | None = None,
        app_description: str | None = None,
        agent_etags: dict[str, dict[str, str]] | None = None,
        tool_etags: dict[str, dict[str, str]] | None = None,
        guardrail_etags: dict[str, dict[str, str]] | None = None,
        agent_snapshots: dict[str, AgentStateSnapshot] | None = None,
        tool_snapshots: dict[str, ToolStateSnapshot] | None = None,
        guardrail_snapshots: dict[str, GuardrailStateSnapshot] | None = None,
        files: dict[str, dict[str, Any]] | None = None,
        synced_at: str | None = None,
    ):
        self.app_name = app_name
        self.app_display_name = app_display_name
        self.app_etag = app_etag
        self.global_instruction = global_instruction
        self.app_guardrails = app_guardrails
        self.app_root_agent = app_root_agent
        self.app_description = app_description
        self.agent_etags = agent_etags or {}
        self.tool_etags = tool_etags or {}
        self.guardrail_etags = guardrail_etags or {}
        self.agent_snapshots = agent_snapshots or {}
        self.tool_snapshots = tool_snapshots or {}
        self.guardrail_snapshots = guardrail_snapshots or {}
        self.files = files or {}
        self.synced_at = (
            synced_at
            or datetime.datetime.now(datetime.timezone.utc).isoformat()
        )

    @classmethod
    def manifest_path(cls, app_root: Path) -> Path:
        return app_root / MANIFEST_REL_PATH

    @classmethod
    def load(
        cls, app_root: Path, expected_app_name: str
    ) -> StateManifest | None:
        """Loads manifest if present and matching ``expected_app_name``."""
        path = cls.manifest_path(app_root)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("app_name") != expected_app_name:
            return None

        raw_snaps = data.get("snapshots") or {}
        agent_snaps = {
            k: AgentStateSnapshot.from_dict(v)
            for k, v in (raw_snaps.get("agents") or {}).items()
            if isinstance(v, dict) and "resource_name" in v
        }
        tool_snaps = {
            k: ToolStateSnapshot.from_dict(v)
            for k, v in (raw_snaps.get("tools") or {}).items()
            if isinstance(v, dict) and "resource_name" in v
        }
        guardrail_snaps = {
            k: GuardrailStateSnapshot.from_dict(v)
            for k, v in (raw_snaps.get("guardrails") or {}).items()
            if isinstance(v, dict) and "resource_name" in v
        }
        return cls(
            app_name=data["app_name"],
            app_display_name=data.get("app_display_name", "App"),
            app_etag=data.get("app_etag", ""),
            global_instruction=raw_snaps.get("global_instruction"),
            app_guardrails=raw_snaps.get("app_guardrails"),
            app_root_agent=raw_snaps.get("app_root_agent"),
            app_description=raw_snaps.get("app_description"),
            agent_etags=data.get("agent_etags", {}),
            tool_etags=data.get("tool_etags", {}),
            guardrail_etags=data.get("guardrail_etags", {}),
            agent_snapshots=agent_snaps,
            tool_snapshots=tool_snaps,
            guardrail_snapshots=guardrail_snaps,
            files=data.get("files", {}),
            synced_at=data.get("synced_at"),
        )

    def save(self, app_root: Path) -> Path:
        """Atomically writes ``.cxas/state_manifest.json``."""
        path = self.manifest_path(app_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.synced_at = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "app_name": self.app_name,
            "app_display_name": self.app_display_name,
            "synced_at": self.synced_at,
            "app_etag": self.app_etag,
            "agent_etags": self.agent_etags,
            "tool_etags": self.tool_etags,
            "guardrail_etags": self.guardrail_etags,
            "snapshots": {
                "global_instruction": self.global_instruction,
                "app_guardrails": self.app_guardrails,
                "app_root_agent": self.app_root_agent,
                "app_description": self.app_description,
                "agents": {
                    k: v.to_dict() for k, v in self.agent_snapshots.items()
                },
                "tools": {
                    k: v.to_dict() for k, v in self.tool_snapshots.items()
                },
                "guardrails": {
                    k: v.to_dict() for k, v in self.guardrail_snapshots.items()
                },
            },
            "files": self.files,
        }
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
        return path


def _agent_proto_to_snapshot(
    agent: types.Agent, fallback_name: str
) -> AgentStateSnapshot:
    return AgentStateSnapshot(
        resource_name=agent.name,
        display_name=agent.display_name or fallback_name,
        etag=getattr(agent, "etag", "") or "",
        instruction=getattr(agent, "instruction", "") or "",
        callbacks={
            cb_field: _clone_callbacks(getattr(agent, cb_field, []))
            for cb_field in CALLBACK_FIELD_SPECS
        },
        description=getattr(agent, "description", "") or "",
        tools=list(getattr(agent, "tools", []) or []),
        child_agents=list(getattr(agent, "child_agents", []) or []),
        guardrails=list(getattr(agent, "guardrails", []) or []),
    )


def _tool_proto_to_snapshot(tool: Any, fallback_name: str) -> ToolStateSnapshot:
    py_fn = getattr(tool, "python_function", None)
    return ToolStateSnapshot(
        resource_name=tool.name,
        display_name=getattr(tool, "display_name", fallback_name)
        or fallback_name,
        etag=getattr(tool, "etag", "") or "",
        python_code=getattr(py_fn, "python_code", "") if py_fn else "",
        function_name=getattr(py_fn, "name", "") if py_fn else "",
        description=getattr(py_fn, "description", "") if py_fn else "",
    )


def _guardrail_proto_to_snapshot(
    guardrail: Any, fallback_name: str
) -> GuardrailStateSnapshot:
    g_dict: dict[str, Any] = {}
    try:
        pb = getattr(guardrail, "_pb", guardrail)
        g_dict = json_format.MessageToDict(pb)
        for strip_key in ("name", "etag", "createTime", "updateTime"):
            g_dict.pop(strip_key, None)
    except Exception:
        pass
    return GuardrailStateSnapshot(
        resource_name=getattr(guardrail, "name", ""),
        display_name=getattr(guardrail, "display_name", fallback_name)
        or fallback_name,
        etag=getattr(guardrail, "etag", "") or "",
        guardrail_dict=g_dict,
    )


def sync_manifest_from_workspace(
    app_dir: str | Path,
    app_name: str,
    apps_client: Any = None,
    agents_client: Any = None,
    tools_client: Any = None,
    guardrails_client: Any = None,
) -> Path | None:
    """Seeds local manifest (hashes + etags + rollback snapshots)."""
    try:
        app_root = resolve_app_root(app_dir)
        files = scan_workspace_hashes(app_root)
        app_etag = ""
        app_display = "App"
        global_inst: str | None = None
        app_guardrails: list[str] | None = None
        app_root_agent: str | None = None
        app_description: str | None = None
        agent_etags: dict[str, dict[str, str]] = {}
        tool_etags: dict[str, dict[str, str]] = {}
        guardrail_etags: dict[str, dict[str, str]] = {}
        agent_snaps: dict[str, AgentStateSnapshot] = {}
        tool_snaps: dict[str, ToolStateSnapshot] = {}
        guardrail_snaps: dict[str, GuardrailStateSnapshot] = {}

        if apps_client is not None:
            try:
                live_app = apps_client.get_app(app_name)
                app_etag = getattr(live_app, "etag", "") or ""
                app_display = getattr(live_app, "display_name", "App") or "App"
                global_inst = getattr(live_app, "global_instruction", "") or ""
                app_guardrails = list(getattr(live_app, "guardrails", []) or [])
                app_root_agent = getattr(live_app, "root_agent", "") or ""
                app_description = getattr(live_app, "description", "") or ""

                ag_client = agents_client
                if ag_client is None and hasattr(apps_client, "client"):
                    ag_client = _SharedClientWrapper(app_name, apps_client)
                if ag_client is not None:
                    remote_agents = ag_client.list_agents()
                    mapping = match_local_agents_to_remote(
                        app_root, remote_agents
                    )
                    for dir_name, remote_agent in mapping.items():
                        agent_etags[dir_name] = {
                            "resource_name": remote_agent.name,
                            "display_name": remote_agent.display_name,
                            "etag": getattr(remote_agent, "etag", "") or "",
                        }
                        agent_snaps[dir_name] = _agent_proto_to_snapshot(
                            remote_agent, dir_name
                        )

                tl_client = tools_client or ag_client
                if tl_client is not None and hasattr(tl_client, "list_tools"):
                    remote_tools = tl_client.list_tools()
                    t_map = _match_local_tools_to_remote(app_root, remote_tools)
                    for dir_name, remote_tool in t_map.items():
                        tool_etags[dir_name] = {
                            "resource_name": remote_tool.name,
                            "display_name": getattr(
                                remote_tool, "display_name", dir_name
                            ),
                            "etag": getattr(remote_tool, "etag", "") or "",
                        }
                        tool_snaps[dir_name] = _tool_proto_to_snapshot(
                            remote_tool, dir_name
                        )

                gr_client = guardrails_client or ag_client
                if gr_client is not None and hasattr(
                    gr_client, "list_guardrails"
                ):
                    remote_grs = gr_client.list_guardrails()
                    g_map = _match_local_guardrails_to_remote(
                        app_root, remote_grs
                    )
                    for dir_name, remote_gr in g_map.items():
                        guardrail_etags[dir_name] = {
                            "resource_name": remote_gr.name,
                            "display_name": getattr(
                                remote_gr, "display_name", dir_name
                            ),
                            "etag": getattr(remote_gr, "etag", "") or "",
                        }
                        guardrail_snaps[dir_name] = (
                            _guardrail_proto_to_snapshot(remote_gr, dir_name)
                        )
            except Exception:
                pass

        manifest = StateManifest(
            app_name=app_name,
            app_display_name=app_display,
            app_etag=app_etag,
            global_instruction=global_inst,
            app_guardrails=app_guardrails,
            app_root_agent=app_root_agent,
            app_description=app_description,
            agent_etags=agent_etags,
            tool_etags=tool_etags,
            guardrail_etags=guardrail_etags,
            agent_snapshots=agent_snaps,
            tool_snapshots=tool_snaps,
            guardrail_snapshots=guardrail_snaps,
            files=files,
        )
        return manifest.save(app_root)
    except Exception:
        return None


class _SharedClientWrapper:
    """Wraps an object exposing ``.client`` for CES resource RPCs."""

    def __init__(self, app_name: str, holder: Any):
        self.app_name = app_name
        self.client = holder.client

    def list_agents(self) -> list[types.Agent]:
        return list(
            self.client.list_agents(
                request=types.ListAgentsRequest(parent=self.app_name)
            )
        )

    def get_agent(self, agent_name: str) -> types.Agent:
        return self.client.get_agent(
            request=types.GetAgentRequest(name=agent_name)
        )

    def list_tools(self) -> list[Any]:
        return list(
            self.client.list_tools(
                request=types.ListToolsRequest(parent=self.app_name)
            )
        )

    def get_tool(self, tool_name: str) -> Any:
        return self.client.get_tool(
            request=types.GetToolRequest(name=tool_name)
        )

    def list_guardrails(self) -> list[Any]:
        if hasattr(self.client, "list_guardrails"):
            return list(
                self.client.list_guardrails(
                    request=types.ListGuardrailsRequest(parent=self.app_name)
                )
            )
        return []

    def get_guardrail(self, guardrail_name: str) -> Any:
        return self.client.get_guardrail(
            request=types.GetGuardrailRequest(name=guardrail_name)
        )


class ScopedPreflightLinter:
    """Ultra-fast (<50ms) static validator scoped strictly to modified files."""

    def __init__(self, app_root: Path):
        self.app_root = app_root
        self._known_tools = self._discover_local_tool_names()
        self._known_agents = self._discover_local_agent_names()
        self._known_guardrails = self._discover_local_guardrail_names()
        self._has_local_tools_dir = (self.app_root / "tools").is_dir()
        self._has_local_agents_dir = (self.app_root / "agents").is_dir()
        self._has_local_guardrails_dir = (self.app_root / "guardrails").is_dir()
        self._jinja_env = jinja2.Environment()

    def _discover_local_tool_names(self) -> set[str]:
        names = set(PLATFORM_SYSTEM_TOOLS)
        tools_dir = self.app_root / "tools"
        if tools_dir.is_dir():
            for d in tools_dir.iterdir():
                if d.is_dir():
                    names.add(d.name)
                    cfg = self._read_json_or_yaml(d)
                    if cfg:
                        for key in ("displayName", "display_name", "name"):
                            if cfg.get(key):
                                names.add(str(cfg[key]))
        toolsets_dir = self.app_root / "toolsets"
        if toolsets_dir.is_dir():
            for d in toolsets_dir.iterdir():
                if d.is_dir():
                    names.add(f"{d.name}_*")
        return names

    def _discover_local_agent_names(self) -> set[str]:
        names: set[str] = set()
        agents_dir = self.app_root / "agents"
        if agents_dir.is_dir():
            for d in agents_dir.iterdir():
                if d.is_dir():
                    names.add(d.name)
                    names.add(d.name.replace("_", " "))
                    cfg = self._read_json_or_yaml(d)
                    if cfg:
                        for key in ("displayName", "display_name", "name"):
                            if cfg.get(key):
                                names.add(str(cfg[key]))
        return names

    def _discover_local_guardrail_names(self) -> set[str]:
        names: set[str] = set()
        guardrails_dir = self.app_root / "guardrails"
        if guardrails_dir.is_dir():
            for d in guardrails_dir.iterdir():
                if d.is_dir():
                    names.add(d.name)
                    names.add(d.name.replace("_", " "))
                    cfg = self._read_json_or_yaml(d)
                    if cfg:
                        for key in ("displayName", "display_name", "name"):
                            if cfg.get(key):
                                names.add(str(cfg[key]))
        return names

    @staticmethod
    def _read_json_or_yaml(directory: Path) -> dict[str, Any] | None:
        candidates = [
            directory / f"{directory.name}.json",
            directory / "agent.json",
            directory / "tool.json",
            directory / "guardrail.json",
            directory / f"{directory.name}.yaml",
            directory / "agent.yaml",
            directory / "tool.yaml",
            directory / "guardrail.yaml",
        ]
        for path in candidates:
            if path.is_file():
                try:
                    if path.suffix == ".json":
                        return json.loads(path.read_text(encoding="utf-8"))
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
                    return data if isinstance(data, dict) else None
                except Exception:
                    return None
        return None

    def lint_deltas(self, deltas: list[FileDelta]) -> list[LintFinding]:
        findings: list[LintFinding] = []
        for delta in deltas:
            if delta.kind in (
                PatchTargetKind.GLOBAL_INSTRUCTION,
                PatchTargetKind.AGENT_INSTRUCTION,
            ):
                findings.extend(self._lint_instruction(delta))
            elif delta.kind == PatchTargetKind.AGENT_CALLBACK:
                findings.extend(self._lint_callback(delta))
            elif delta.kind == PatchTargetKind.PYTHON_TOOL:
                findings.extend(self._lint_python_tool(delta))
            elif delta.kind == PatchTargetKind.GUARDRAIL:
                findings.extend(self._lint_guardrail(delta))
            elif delta.kind == PatchTargetKind.AGENT_CONFIG:
                findings.extend(self._lint_agent_config(delta))
            elif delta.kind == PatchTargetKind.TOOL_CONFIG:
                findings.extend(self._lint_tool_config(delta))
            elif delta.kind == PatchTargetKind.APP_CONFIG:
                findings.extend(self._lint_app_config(delta))
        return findings

    def _parse_config_delta(
        self, delta: FileDelta, err_rule_id: str
    ) -> tuple[dict[str, Any] | None, list[LintFinding]]:
        try:
            raw = delta.abs_path.read_text(encoding="utf-8")
            if delta.abs_path.suffix == ".json":
                data = json.loads(raw)
            else:
                data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                return None, [
                    LintFinding(
                        file=delta.rel_path,
                        rule_id=err_rule_id,
                        severity="error",
                        message="Config file root must be a JSON/YAML object.",
                        line=1,
                    )
                ]
            return data, []
        except Exception as exc:
            return None, [
                LintFinding(
                    file=delta.rel_path,
                    rule_id=err_rule_id,
                    severity="error",
                    message=f"Invalid config syntax: {exc}",
                    line=1,
                )
            ]

    def _lint_guardrail(self, delta: FileDelta) -> list[LintFinding]:
        if delta.abs_path.suffix == ".py":
            return self._lint_python_tool(delta)
        data, errs = self._parse_config_delta(delta, "HP-G001")
        if errs or data is None:
            return errs
        findings: list[LintFinding] = []
        rule_keys = (
            "contentFilter",
            "content_filter",
            "llmPromptSecurity",
            "llm_prompt_security",
            "llmPolicy",
            "llm_policy",
            "modelSafety",
            "model_safety",
            "codeCallback",
            "code_callback",
            "supervisor",
        )
        if not any(k in data for k in rule_keys):
            findings.append(
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-G002",
                    severity="error",
                    message=(
                        "Guardrail must specify a policy type (contentFilter, "
                        "llmPromptSecurity, llmPolicy, modelSafety, "
                        "codeCallback, or supervisor)."
                    ),
                    line=1,
                )
            )
        return findings

    def _lint_agent_config(self, delta: FileDelta) -> list[LintFinding]:
        data, errs = self._parse_config_delta(delta, "HP-A001")
        if errs or data is None:
            return errs
        findings: list[LintFinding] = []
        wildcard_prefixes = [
            t[:-1] for t in self._known_tools if t.endswith("*")
        ]
        if self._has_local_tools_dir and isinstance(data.get("tools"), list):
            for t_ref in data["tools"]:
                t_str = str(t_ref).strip()
                if (
                    t_str.startswith("projects/")
                    or t_str in self._known_tools
                    or any(t_str.startswith(p) for p in wildcard_prefixes)
                ):
                    continue
                findings.append(
                    LintFinding(
                        file=delta.rel_path,
                        rule_id="HP-A002",
                        severity="error",
                        message=(
                            f"Agent config references unknown tool '{t_str}'."
                        ),
                        line=1,
                    )
                )
        child_list = data.get("childAgents") or data.get("child_agents")
        if self._has_local_agents_dir and isinstance(child_list, list):
            for c_ref in child_list:
                c_str = str(c_ref).strip()
                if c_str.startswith("projects/") or c_str in self._known_agents:
                    continue
                findings.append(
                    LintFinding(
                        file=delta.rel_path,
                        rule_id="HP-A003",
                        severity="error",
                        message=(
                            "Agent config references unknown "
                            f"childAgent '{c_str}'."
                        ),
                        line=1,
                    )
                )
        if self._has_local_guardrails_dir and isinstance(
            data.get("guardrails"), list
        ):
            for g_ref in data["guardrails"]:
                g_str = str(g_ref).strip()
                if (
                    g_str.startswith("projects/")
                    or g_str in self._known_guardrails
                ):
                    continue
                findings.append(
                    LintFinding(
                        file=delta.rel_path,
                        rule_id="HP-A004",
                        severity="error",
                        message=(
                            "Agent config references unknown "
                            f"guardrail '{g_str}'."
                        ),
                        line=1,
                    )
                )
        return findings

    def _lint_tool_config(self, delta: FileDelta) -> list[LintFinding]:
        data, errs = self._parse_config_delta(delta, "HP-T003")
        if errs or data is None:
            return errs
        return []

    def _lint_app_config(self, delta: FileDelta) -> list[LintFinding]:
        data, errs = self._parse_config_delta(delta, "HP-P001")
        if errs or data is None:
            return errs
        findings: list[LintFinding] = []
        root_ag = data.get("rootAgent") or data.get("root_agent")
        if (
            self._has_local_agents_dir
            and isinstance(root_ag, str)
            and root_ag.strip()
            and not root_ag.startswith("projects/")
            and root_ag.strip() not in self._known_agents
        ):
            findings.append(
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-P002",
                    severity="error",
                    message=(
                        f"app.json references unknown rootAgent '{root_ag}'."
                    ),
                    line=1,
                )
            )
        if self._has_local_guardrails_dir and isinstance(
            data.get("guardrails"), list
        ):
            for g_ref in data["guardrails"]:
                g_str = str(g_ref).strip()
                if (
                    g_str.startswith("projects/")
                    or g_str in self._known_guardrails
                ):
                    continue
                findings.append(
                    LintFinding(
                        file=delta.rel_path,
                        rule_id="HP-P003",
                        severity="error",
                        message=(
                            f"app.json references unknown guardrail '{g_str}'."
                        ),
                        line=1,
                    )
                )
        return findings

    def _lint_instruction(self, delta: FileDelta) -> list[LintFinding]:
        findings: list[LintFinding] = []
        try:
            content = delta.abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return [
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-I000",
                    severity="error",
                    message=f"Instruction file is not valid UTF-8: {exc}",
                    line=1,
                )
            ]

        if not content.strip():
            findings.append(
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-I001",
                    severity="warning",
                    message="Instruction file is empty.",
                    line=1,
                )
            )

        for idx, line in enumerate(content.splitlines(), start=1):
            for match in _MACRO_OPEN_RE.finditer(line):
                tail = line[match.start() :]
                if "}" not in tail:
                    findings.append(
                        LintFinding(
                            file=delta.rel_path,
                            rule_id="HP-I002",
                            severity="error",
                            message=(
                                f"Unclosed {{@{match.group(1)}: ...}} "
                                "reference."
                            ),
                            line=idx,
                        )
                    )

        sanitized = _ANY_MACRO_RE.sub("MACRO_REF", content)
        try:
            self._jinja_env.parse(sanitized)
        except jinja2.TemplateSyntaxError as exc:
            findings.append(
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-I003",
                    severity="error",
                    message=f"Invalid Jinja2 template syntax: {exc.message}",
                    line=exc.lineno,
                )
            )

        wildcard_prefixes = [
            t[:-1] for t in self._known_tools if t.endswith("*")
        ]
        if self._has_local_tools_dir:
            for match in _TOOL_MACRO_RE.finditer(content):
                tool_ref = match.group(1).strip()
                if tool_ref in self._known_tools:
                    continue
                if any(tool_ref.startswith(pfx) for pfx in wildcard_prefixes):
                    continue
                lineno = content[: match.start()].count("\n") + 1
                findings.append(
                    LintFinding(
                        file=delta.rel_path,
                        rule_id="HP-I004",
                        severity="error",
                        message=(
                            f"Unknown tool reference '{{@TOOL: {tool_ref}}}' "
                            "not found in local tools/ directory."
                        ),
                        line=lineno,
                    )
                )

        if self._has_local_agents_dir:
            for match in _AGENT_MACRO_RE.finditer(content):
                agent_ref = match.group(1).strip()
                if agent_ref in self._known_agents:
                    continue
                lineno = content[: match.start()].count("\n") + 1
                findings.append(
                    LintFinding(
                        file=delta.rel_path,
                        rule_id="HP-I005",
                        severity="error",
                        message=(
                            "Unknown agent reference "
                            f"'{{@AGENT: {agent_ref}}}' "
                            "not found in local agents/ directory."
                        ),
                        line=lineno,
                    )
                )

        return findings

    def _lint_callback(self, delta: FileDelta) -> list[LintFinding]:
        findings: list[LintFinding] = []
        try:
            code = delta.abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return [
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-C000",
                    severity="error",
                    message=f"Callback file is not valid UTF-8: {exc}",
                    line=1,
                )
            ]

        try:
            tree = ast.parse(code, filename=delta.rel_path)
        except SyntaxError as exc:
            return [
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-C001",
                    severity="error",
                    message=f"Python syntax error: {exc.msg}",
                    line=exc.lineno or 1,
                )
            ]

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in SANDBOX_GLOBALS:
                        findings.append(
                            LintFinding(
                                file=delta.rel_path,
                                rule_id="HP-C002",
                                severity="error",
                                message=(
                                    f"Do not import '{alias.name}'; CES "
                                    "injects sandbox globals automatically."
                                ),
                                line=node.lineno,
                            )
                        )

        spec = CALLBACK_FIELD_SPECS.get(delta.field_mask_path)
        if spec is None:
            return findings

        expected_fn, expected_argc, sig_desc = spec
        fn_defs = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        if expected_fn not in fn_defs:
            defined = ", ".join(sorted(fn_defs)) or "none"
            findings.append(
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-C003",
                    severity="error",
                    message=(
                        f"Callback function must be named '{expected_fn}' "
                        f"(found: {defined})."
                    ),
                    line=1,
                )
            )
            return findings

        target_fn = fn_defs[expected_fn]
        actual_argc = len(target_fn.args.args)
        if actual_argc != expected_argc:
            findings.append(
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-C004",
                    severity="error",
                    message=(
                        f"'{expected_fn}' expects {expected_argc} positional "
                        f"argument(s) {sig_desc}, but defines {actual_argc}."
                    ),
                    line=target_fn.lineno,
                )
            )

        return findings

    def _lint_python_tool(self, delta: FileDelta) -> list[LintFinding]:
        try:
            code = delta.abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return [
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-T000",
                    severity="error",
                    message=f"Tool file is not valid UTF-8: {exc}",
                    line=1,
                )
            ]

        try:
            tree = ast.parse(code, filename=delta.rel_path)
        except SyntaxError as exc:
            return [
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-T001",
                    severity="error",
                    message=f"Python syntax error in tool: {exc.msg}",
                    line=exc.lineno or 1,
                )
            ]

        top_fns = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not top_fns:
            return [
                LintFinding(
                    file=delta.rel_path,
                    rule_id="HP-T002",
                    severity="error",
                    message=(
                        "Python function tool defines no top-level function."
                    ),
                    line=1,
                )
            ]
        return []


def _load_local_resource_metadata(resource_dir: Path) -> dict[str, Any]:
    candidates = [
        resource_dir / f"{resource_dir.name}.json",
        resource_dir / "agent.json",
        resource_dir / "tool.json",
        resource_dir / "guardrail.json",
        resource_dir / f"{resource_dir.name}.yaml",
        resource_dir / "agent.yaml",
        resource_dir / "tool.yaml",
        resource_dir / "guardrail.yaml",
    ]
    for cfg_path in candidates:
        if cfg_path.is_file():
            try:
                text = cfg_path.read_text(encoding="utf-8")
                if cfg_path.suffix == ".json":
                    parsed = json.loads(text)
                else:
                    parsed = yaml.safe_load(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return {}


def _load_app_metadata(app_root: Path) -> dict[str, Any]:
    for fname in ("app.json", "app.yaml", "app.yml"):
        fpath = app_root / fname
        if fpath.is_file():
            try:
                text = fpath.read_text(encoding="utf-8")
                parsed = (
                    json.loads(text)
                    if fpath.suffix == ".json"
                    else yaml.safe_load(text)
                )
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return {}


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[\s_-]+", "_", value.strip().lower())


def match_local_agents_to_remote(
    app_root: Path, remote_agents: list[types.Agent]
) -> dict[str, types.Agent]:
    """Maps local ``agents/<dir_name>`` dirs to ``types.Agent`` protos."""
    agents_dir = app_root / "agents"
    if not agents_dir.is_dir():
        return {}

    by_id = {
        a.name.rsplit("/", maxsplit=1)[-1]: a for a in remote_agents if a.name
    }
    by_display = {a.display_name: a for a in remote_agents if a.display_name}
    by_norm: dict[str, types.Agent] = {}
    for agent in remote_agents:
        if agent.display_name:
            by_norm[_normalize_identifier(agent.display_name)] = agent
        if agent.name:
            short_id = agent.name.rsplit("/", maxsplit=1)[-1]
            by_norm[_normalize_identifier(short_id)] = agent

    resolved: dict[str, types.Agent] = {}
    for sub in sorted(agents_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        meta = _load_local_resource_metadata(sub)
        candidates = [
            meta.get("displayName"),
            meta.get("display_name"),
            meta.get("name"),
            sub.name,
            sub.name.replace("_", " "),
        ]
        matched: types.Agent | None = None
        for cand in candidates:
            if not cand:
                continue
            cand_str = str(cand)
            if cand_str in by_display:
                matched = by_display[cand_str]
                break
            if cand_str in by_id:
                matched = by_id[cand_str]
                break
            norm = _normalize_identifier(cand_str)
            if norm in by_norm:
                matched = by_norm[norm]
                break
        if matched is not None:
            resolved[sub.name] = matched
    return resolved


_match_local_agents_to_remote = match_local_agents_to_remote


def _match_local_tools_to_remote(
    app_root: Path, remote_tools: list[Any]
) -> dict[str, Any]:
    tools_dir = app_root / "tools"
    if not tools_dir.is_dir():
        return {}

    only_tools = [
        t for t in remote_tools if "/tools/" in getattr(t, "name", "")
    ]
    by_id = {t.name.rsplit("/", maxsplit=1)[-1]: t for t in only_tools}
    by_display = {
        t.display_name: t for t in only_tools if getattr(t, "display_name", "")
    }
    by_norm = {
        _normalize_identifier(t.display_name): t
        for t in only_tools
        if getattr(t, "display_name", "")
    }
    for t in only_tools:
        short_id = t.name.rsplit("/", maxsplit=1)[-1]
        by_norm[_normalize_identifier(short_id)] = t

    resolved: dict[str, Any] = {}
    for sub in sorted(tools_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        meta = _load_local_resource_metadata(sub)
        for cand in (
            meta.get("displayName"),
            meta.get("display_name"),
            meta.get("name"),
            sub.name,
        ):
            if not cand:
                continue
            cand_str = str(cand)
            if cand_str in by_display:
                resolved[sub.name] = by_display[cand_str]
                break
            if cand_str in by_id:
                resolved[sub.name] = by_id[cand_str]
                break
            norm = _normalize_identifier(cand_str)
            if norm in by_norm:
                resolved[sub.name] = by_norm[norm]
                break
    return resolved


def _match_local_guardrails_to_remote(
    app_root: Path, remote_guardrails: list[Any]
) -> dict[str, Any]:
    guardrails_dir = app_root / "guardrails"
    if not guardrails_dir.is_dir():
        return {}

    only_grs = [
        g for g in remote_guardrails if "/guardrails/" in getattr(g, "name", "")
    ]
    by_id = {g.name.rsplit("/", maxsplit=1)[-1]: g for g in only_grs}
    by_display = {
        g.display_name: g for g in only_grs if getattr(g, "display_name", "")
    }
    by_norm: dict[str, Any] = {}
    for g in only_grs:
        if getattr(g, "display_name", ""):
            by_norm[_normalize_identifier(g.display_name)] = g
        short_id = g.name.rsplit("/", maxsplit=1)[-1]
        by_norm[_normalize_identifier(short_id)] = g

    resolved: dict[str, Any] = {}
    for sub in sorted(guardrails_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        meta = _load_local_resource_metadata(sub)
        for cand in (
            meta.get("displayName"),
            meta.get("display_name"),
            meta.get("name"),
            sub.name,
            sub.name.replace("_", " "),
        ):
            if not cand:
                continue
            cand_str = str(cand)
            if cand_str in by_display:
                resolved[sub.name] = by_display[cand_str]
                break
            if cand_str in by_id:
                resolved[sub.name] = by_id[cand_str]
                break
            norm = _normalize_identifier(cand_str)
            if norm in by_norm:
                resolved[sub.name] = by_norm[norm]
                break
    return resolved


def _clone_callbacks(callbacks: Any) -> list[types.Callback]:
    return [
        types.Callback(
            python_code=getattr(cb, "python_code", ""),
            description=getattr(cb, "description", ""),
            disabled=bool(getattr(cb, "disabled", False)),
        )
        for cb in callbacks
    ]


def _build_updated_callbacks_for_field(
    app_root: Path,
    agent_dir_name: str,
    cb_field: str,
    existing_callbacks: list[types.Callback],
) -> list[types.Callback]:
    """Builds the ordered ``types.Callback`` list for ``cb_field`` from disk."""
    agent_dir = app_root / "agents" / agent_dir_name
    meta = _load_local_resource_metadata(agent_dir)
    camel_key = SNAKE_TO_CAMEL_CALLBACKS.get(cb_field, "")
    configured_list = meta.get(camel_key) or meta.get(cb_field)
    existing = list(existing_callbacks)

    if isinstance(configured_list, list) and configured_list:
        built: list[types.Callback] = []
        for idx, item in enumerate(configured_list):
            if not isinstance(item, dict):
                continue
            code_ref = item.get("pythonCode") or item.get("python_code") or ""
            code_text = code_ref
            if code_ref:
                ref_path = app_root / code_ref
                if ref_path.is_file():
                    code_text = ref_path.read_text(encoding="utf-8")
            fallback_desc = (
                getattr(existing[idx], "description", "")
                if idx < len(existing)
                else ""
            )
            fallback_disabled = (
                bool(getattr(existing[idx], "disabled", False))
                if idx < len(existing)
                else False
            )
            built.append(
                types.Callback(
                    python_code=code_text,
                    description=item.get("description", fallback_desc),
                    disabled=bool(item.get("disabled", fallback_disabled)),
                )
            )
        if built:
            return built

    cb_root = agent_dir / cb_field
    built_from_dirs: list[types.Callback] = []
    if cb_root.is_dir():
        subdirs = [
            d
            for d in sorted(cb_root.iterdir())
            if d.is_dir() and (d / "python_code.py").is_file()
        ]
        for idx, sub in enumerate(subdirs):
            code_text = (sub / "python_code.py").read_text(encoding="utf-8")
            desc = (
                getattr(existing[idx], "description", "")
                if idx < len(existing)
                else ""
            )
            disabled = (
                bool(getattr(existing[idx], "disabled", False))
                if idx < len(existing)
                else False
            )
            built_from_dirs.append(
                types.Callback(
                    python_code=code_text,
                    description=desc,
                    disabled=disabled,
                )
            )
    return built_from_dirs


def _is_etag_conflict_exception(exc: Exception) -> bool:
    if isinstance(exc, google_exceptions.Aborted):
        return True
    msg = str(exc).lower()
    return (
        "409" in msg
        or "aborted" in msg
        or "etag" in msg
        or "modified by a more recent request" in msg
    )


def _sanitize_resource_id(raw_key: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_key.strip()).strip("-_")
    return cleaned or "resource"


def _register_resource_lookup(
    lookup: dict[str, str],
    local_key: str,
    display_name: str,
    resource_name: str,
) -> None:
    if not resource_name:
        return
    short_id = resource_name.rsplit("/", maxsplit=1)[-1]
    for alias in (local_key, display_name, short_id, resource_name):
        if alias:
            lookup[alias] = resource_name
            lookup[_normalize_identifier(alias)] = resource_name


def _resolve_ref_list(
    refs: list[Any],
    lookup: dict[str, str],
    app_name: str,
    collection: str,
) -> list[str]:
    resolved: list[str] = []
    for item in refs:
        if not item:
            continue
        ref_str = str(item).strip()
        if not ref_str:
            continue
        if ref_str.startswith("projects/"):
            target = ref_str
        elif ref_str in lookup:
            target = lookup[ref_str]
        elif _normalize_identifier(ref_str) in lookup:
            target = lookup[_normalize_identifier(ref_str)]
        else:
            target = f"{app_name}/{collection}/{_sanitize_resource_id(ref_str)}"
        if target not in resolved:
            resolved.append(target)
    return resolved


def _build_guardrail_proto_from_dict(
    data: dict[str, Any],
    app_root: Path,
    fallback_name: str,
    resource_name: str = "",
) -> tuple[types.Guardrail, list[str], int]:
    cleaned = {
        k: v
        for k, v in data.items()
        if k not in ("name", "etag", "createTime", "updateTime")
    }
    pb = types.Guardrail()._pb
    json_format.ParseDict(cleaned, pb, ignore_unknown_fields=True)
    proto = types.Guardrail.wrap(pb)

    display_name = (
        proto.display_name
        or str(data.get("displayName") or data.get("display_name") or "")
        or fallback_name
    )
    proto.display_name = display_name
    if "enabled" not in data:
        proto.enabled = True
    if resource_name:
        proto.name = resource_name

    # Resolve any relative file paths in code_callback
    if "codeCallback" in data or "code_callback" in data:
        for cb_attr in (
            "before_agent_callback",
            "after_agent_callback",
            "before_model_callback",
            "after_model_callback",
        ):
            cb_obj = getattr(proto.code_callback, cb_attr, None)
            code_val = getattr(cb_obj, "python_code", "") if cb_obj else ""
            if code_val and code_val.endswith(".py"):
                cand_path = app_root / code_val
                if cand_path.is_file():
                    cb_obj.python_code = cand_path.read_text(encoding="utf-8")

    mask_paths: list[str] = ["display_name", "enabled"]
    if proto.description or "description" in data:
        mask_paths.append("description")
    if "action" in data:
        mask_paths.append("action")
    rule_map = {
        "contentFilter": "content_filter",
        "content_filter": "content_filter",
        "llmPromptSecurity": "llm_prompt_security",
        "llm_prompt_security": "llm_prompt_security",
        "llmPolicy": "llm_policy",
        "llm_policy": "llm_policy",
        "modelSafety": "model_safety",
        "model_safety": "model_safety",
        "codeCallback": "code_callback",
        "code_callback": "code_callback",
        "supervisor": "supervisor",
    }
    for key, snake_field in rule_map.items():
        if key in data and snake_field not in mask_paths:
            mask_paths.append(snake_field)

    payload_bytes = len(json.dumps(cleaned).encode("utf-8"))
    return proto, mask_paths, payload_bytes


def _build_tool_proto_from_local(
    app_root: Path,
    tool_key: str,
    resource_name: str = "",
) -> tuple[types.Tool, str, str, str, str, int]:
    tool_dir = app_root / "tools" / tool_key
    meta = _load_local_resource_metadata(tool_dir)
    display_name = str(
        meta.get("displayName")
        or meta.get("display_name")
        or meta.get("name")
        or tool_key
    )
    py_fn_meta = meta.get("pythonFunction") or meta.get("python_function") or {}
    if not isinstance(py_fn_meta, dict):
        py_fn_meta = {}
    fn_name = str(py_fn_meta.get("name") or display_name or tool_key)
    fn_desc = str(
        py_fn_meta.get("description") or meta.get("description") or ""
    )

    code_path = tool_dir / "python_function" / "python_code.py"
    python_code = ""
    if code_path.is_file():
        python_code = code_path.read_text(encoding="utf-8")
    else:
        code_ref = str(
            py_fn_meta.get("pythonCode") or py_fn_meta.get("python_code") or ""
        )
        if code_ref:
            ref_file = app_root / code_ref
            if ref_file.is_file():
                python_code = ref_file.read_text(encoding="utf-8")
            else:
                python_code = code_ref

    py_fn_proto = types.PythonFunction(
        name=fn_name,
        python_code=python_code,
    )
    if fn_desc:
        py_fn_proto.description = fn_desc

    tool_proto = types.Tool(
        display_name=display_name,
        python_function=py_fn_proto,
    )
    if resource_name:
        tool_proto.name = resource_name

    payload_bytes = len(python_code.encode("utf-8")) + len(
        display_name.encode("utf-8")
    )
    return (
        tool_proto,
        display_name,
        fn_name,
        python_code,
        fn_desc,
        payload_bytes,
    )


def _discover_local_agent_alias_map(app_root: Path) -> dict[str, str]:
    """Maps local agent aliases (dir, displayName, norm) -> dir_name."""
    alias_to_dir: dict[str, str] = {}
    agents_dir = app_root / "agents"
    if not agents_dir.is_dir():
        return alias_to_dir
    for sub in sorted(agents_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        alias_to_dir[sub.name] = sub.name
        alias_to_dir[_normalize_identifier(sub.name)] = sub.name
        meta = _load_local_resource_metadata(sub)
        for k in ("displayName", "display_name", "name"):
            val = meta.get(k)
            if val:
                alias_to_dir[str(val)] = sub.name
                alias_to_dir[_normalize_identifier(str(val))] = sub.name
    return alias_to_dir


def _discover_local_tool_alias_map(app_root: Path) -> dict[str, str]:
    """Maps local tool aliases (dir, displayName, norm) -> dir_name."""
    alias_to_dir: dict[str, str] = {}
    tools_dir = app_root / "tools"
    if not tools_dir.is_dir():
        return alias_to_dir
    for sub in sorted(tools_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        alias_to_dir[sub.name] = sub.name
        alias_to_dir[_normalize_identifier(sub.name)] = sub.name
        meta = _load_local_resource_metadata(sub)
        for k in ("displayName", "display_name", "name"):
            val = meta.get(k)
            if val:
                alias_to_dir[str(val)] = sub.name
                alias_to_dir[_normalize_identifier(str(val))] = sub.name
    return alias_to_dir


def _discover_local_guardrail_alias_map(app_root: Path) -> dict[str, str]:
    """Maps local guardrail aliases (dir, displayName, norm) -> dir_name."""
    alias_to_dir: dict[str, str] = {}
    guardrails_dir = app_root / "guardrails"
    if not guardrails_dir.is_dir():
        return alias_to_dir
    for sub in sorted(guardrails_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        alias_to_dir[sub.name] = sub.name
        alias_to_dir[_normalize_identifier(sub.name)] = sub.name
        meta = _load_local_resource_metadata(sub)
        for k in ("displayName", "display_name", "name"):
            val = meta.get(k)
            if val:
                alias_to_dir[str(val)] = sub.name
                alias_to_dir[_normalize_identifier(str(val))] = sub.name
    return alias_to_dir


def _topological_sort_agents(
    app_root: Path, agent_keys: list[str]
) -> list[tuple[str, int]]:
    """Topologically sorts agents bottom-up (child sub-agents before parents).

    Returns a list of ``(agent_key, dag_level)`` where leaf agents start at
    ``dag_level = 2`` and parent agents that reference them in ``childAgents``
    or ``{@AGENT: ...}`` receive ``dag_level = max(child_level) + 1``.
    """
    if not agent_keys:
        return []

    alias_to_dir = _discover_local_agent_alias_map(app_root)
    key_set = set(agent_keys)
    deps_of: dict[str, set[str]] = {k: set() for k in agent_keys}
    dependents_of: dict[str, set[str]] = {k: set() for k in agent_keys}

    for ak in agent_keys:
        agent_dir = app_root / "agents" / ak
        meta = _load_local_resource_metadata(agent_dir)
        raw_children = list(
            meta.get("childAgents") or meta.get("child_agents") or []
        )
        for inst_name in ("instruction.txt", "instructions.txt"):
            inst_path = agent_dir / inst_name
            if inst_path.is_file():
                try:
                    text = inst_path.read_text(encoding="utf-8")
                    for m in _AGENT_MACRO_RE.finditer(text):
                        raw_children.append(m.group(1).strip())
                except Exception:
                    pass

        for c_ref in raw_children:
            c_str = str(c_ref).strip()
            if c_str.startswith("projects/"):
                c_str = c_str.rsplit("/", maxsplit=1)[-1]
            target_dir = alias_to_dir.get(c_str) or alias_to_dir.get(
                _normalize_identifier(c_str)
            )
            if target_dir and target_dir in key_set and target_dir != ak:
                deps_of[ak].add(target_dir)
                dependents_of[target_dir].add(ak)

    in_degree = {k: len(deps_of[k]) for k in agent_keys}
    dag_level: dict[str, int] = {}
    ready = sorted(k for k in agent_keys if in_degree[k] == 0)
    for k in ready:
        dag_level[k] = 2

    ordered: list[tuple[str, int]] = []
    while ready:
        curr = ready.pop(0)
        curr_lvl = dag_level[curr]
        ordered.append((curr, curr_lvl))
        for parent in sorted(dependents_of[curr]):
            dag_level[parent] = max(dag_level.get(parent, 2), curr_lvl + 1)
            in_degree[parent] -= 1
            if in_degree[parent] == 0:
                ready.append(parent)
                ready.sort()

    if len(ordered) < len(agent_keys):
        visited = {k for k, _ in ordered}
        max_lvl = max((lvl for _, lvl in ordered), default=2)
        for leftover in sorted(agent_keys):
            if leftover not in visited:
                ordered.append((leftover, max_lvl + 1))

    return ordered


class HotPatchEngine:
    """Differential & Topological DAG engine for ``cxas push --patch``."""

    def __init__(
        self,
        app_name: str,
        app_dir: str | Path,
        apps_client: Any = None,
        agents_client: Any = None,
        tools_client: Any = None,
        guardrails_client: Any = None,
    ):
        self.app_name = app_name
        self.app_root = resolve_app_root(app_dir)

        project_id = extract_project_id(app_name)
        location = extract_location(app_name)
        if apps_client is not None:
            self.apps_client = apps_client
        else:
            if not project_id or not location:
                raise ValueError(
                    f"Invalid CES app resource name '{app_name}'. Expected "
                    "projects/<project>/locations/<location>/apps/<app>."
                )
            cp = DirectCesControlPlane(project_id=project_id, location=location)
            scoped = _AppScopedAdapter(app_name, cp)
            self.apps_client = scoped
            agents_client = agents_client or scoped
            tools_client = tools_client or scoped
            guardrails_client = guardrails_client or scoped

        shared = _SharedClientWrapper(app_name, self.apps_client)
        self.agents_client = agents_client or shared
        self.tools_client = tools_client or shared
        self.guardrails_client = guardrails_client or shared

    def _bootstrap_remote_hashes(
        self,
    ) -> tuple[
        dict[str, dict[str, Any]],
        types.App,
        dict[str, types.Agent],
        dict[str, Any],
        dict[str, Any],
    ]:
        """Builds a virtual file-hash map from live CES state."""
        live_app = self.apps_client.get_app(self.app_name)
        remote_agents = self.agents_client.list_agents()
        try:
            remote_tools = self.tools_client.list_tools()
        except Exception:
            remote_tools = []
        try:
            remote_guardrails = self.guardrails_client.list_guardrails()
        except Exception:
            remote_guardrails = []

        agent_map = match_local_agents_to_remote(self.app_root, remote_agents)
        tool_map = _match_local_tools_to_remote(self.app_root, remote_tools)
        guardrail_map = _match_local_guardrails_to_remote(
            self.app_root, remote_guardrails
        )

        # Build lookup tables of existing remote resources for config parity
        known_agents: dict[str, str] = {}
        for ag in remote_agents:
            _register_resource_lookup(
                known_agents, "", ag.display_name, ag.name
            )
        for k, ag in agent_map.items():
            _register_resource_lookup(known_agents, k, ag.display_name, ag.name)

        known_tools: dict[str, str] = {}
        for tl in remote_tools:
            if "/tools/" in getattr(tl, "name", ""):
                _register_resource_lookup(
                    known_tools, "", getattr(tl, "display_name", ""), tl.name
                )
        for k, tl in tool_map.items():
            _register_resource_lookup(
                known_tools, k, getattr(tl, "display_name", ""), tl.name
            )

        known_grs: dict[str, str] = {}
        for gr in remote_guardrails:
            if "/guardrails/" in getattr(gr, "name", ""):
                _register_resource_lookup(
                    known_grs, "", getattr(gr, "display_name", ""), gr.name
                )
        for k, gr in guardrail_map.items():
            _register_resource_lookup(
                known_grs, k, getattr(gr, "display_name", ""), gr.name
            )

        remote_hashes: dict[str, dict[str, Any]] = {}

        global_inst_path = self.app_root / "global_instruction.txt"
        if global_inst_path.is_file():
            live_global = getattr(live_app, "global_instruction", "") or ""
            remote_hashes["global_instruction.txt"] = {
                "sha256": sha256_text(live_global),
                "size": len(live_global.encode("utf-8")),
                "kind": PatchTargetKind.GLOBAL_INSTRUCTION.value,
            }

        # Check if app.json matches remote live_app
        for app_cfg_name in ("app.json", "app.yaml", "app.yml"):
            app_cfg_path = self.app_root / app_cfg_name
            if app_cfg_path.is_file():
                app_meta = _load_app_metadata(self.app_root)
                matches_remote = True
                if "guardrails" in app_meta:
                    local_gr_refs = app_meta.get("guardrails") or []
                    if any(
                        not str(r).startswith("projects/")
                        and str(r) not in known_grs
                        and _normalize_identifier(str(r)) not in known_grs
                        for r in local_gr_refs
                    ):
                        matches_remote = False
                    else:
                        resolved_grs = _resolve_ref_list(
                            local_gr_refs,
                            known_grs,
                            self.app_name,
                            "guardrails",
                        )
                        if resolved_grs != list(
                            getattr(live_app, "guardrails", []) or []
                        ):
                            matches_remote = False
                if matches_remote:
                    digest, sz = sha256_file(app_cfg_path)
                    remote_hashes[app_cfg_name] = {
                        "sha256": digest,
                        "size": sz,
                        "kind": PatchTargetKind.APP_CONFIG.value,
                    }

        for dir_name, remote_agent in agent_map.items():
            agent_dir = self.app_root / "agents" / dir_name
            for inst_name in ("instruction.txt", "instructions.txt"):
                inst_path = agent_dir / inst_name
                if inst_path.is_file():
                    live_inst = getattr(remote_agent, "instruction", "") or ""
                    rel = inst_path.relative_to(self.app_root).as_posix()
                    remote_hashes[rel] = {
                        "sha256": sha256_text(live_inst),
                        "size": len(live_inst.encode("utf-8")),
                        "kind": PatchTargetKind.AGENT_INSTRUCTION.value,
                    }

            for cb_field in CALLBACK_FIELD_SPECS:
                cb_root = agent_dir / cb_field
                if not cb_root.is_dir():
                    continue
                live_cbs = list(getattr(remote_agent, cb_field, []))
                subdirs = [
                    d
                    for d in sorted(cb_root.iterdir())
                    if d.is_dir() and (d / "python_code.py").is_file()
                ]
                for idx, sub in enumerate(subdirs):
                    rel = (
                        (sub / "python_code.py")
                        .relative_to(self.app_root)
                        .as_posix()
                    )
                    if idx < len(live_cbs):
                        live_code = getattr(live_cbs[idx], "python_code", "")
                        remote_hashes[rel] = {
                            "sha256": sha256_text(live_code),
                            "size": len(live_code.encode("utf-8")),
                            "kind": PatchTargetKind.AGENT_CALLBACK.value,
                        }

            # Seed existing agent config JSON if its bindings match remote_agent
            meta = _load_local_resource_metadata(agent_dir)
            cfg_matches = True
            if "tools" in meta:
                t_refs = meta.get("tools") or []
                if any(
                    not str(r).startswith("projects/")
                    and str(r) not in known_tools
                    and _normalize_identifier(str(r)) not in known_tools
                    for r in t_refs
                ):
                    cfg_matches = False
                else:
                    resolved_t = _resolve_ref_list(
                        t_refs, known_tools, self.app_name, "tools"
                    )
                    if resolved_t != list(
                        getattr(remote_agent, "tools", []) or []
                    ):
                        cfg_matches = False
            if "guardrails" in meta:
                g_refs = meta.get("guardrails") or []
                if any(
                    not str(r).startswith("projects/")
                    and str(r) not in known_grs
                    and _normalize_identifier(str(r)) not in known_grs
                    for r in g_refs
                ):
                    cfg_matches = False
                else:
                    resolved_g = _resolve_ref_list(
                        g_refs, known_grs, self.app_name, "guardrails"
                    )
                    if resolved_g != list(
                        getattr(remote_agent, "guardrails", []) or []
                    ):
                        cfg_matches = False
            if "childAgents" in meta or "child_agents" in meta:
                c_refs = (
                    meta.get("childAgents") or meta.get("child_agents") or []
                )
                if any(
                    not str(r).startswith("projects/")
                    and str(r) not in known_agents
                    and _normalize_identifier(str(r)) not in known_agents
                    for r in c_refs
                ):
                    cfg_matches = False
                else:
                    resolved_c = _resolve_ref_list(
                        c_refs, known_agents, self.app_name, "agents"
                    )
                    if resolved_c != list(
                        getattr(remote_agent, "child_agents", []) or []
                    ):
                        cfg_matches = False

            if cfg_matches:
                for cfg_cand in (
                    agent_dir / f"{dir_name}.json",
                    agent_dir / "agent.json",
                    agent_dir / f"{dir_name}.yaml",
                    agent_dir / "agent.yaml",
                ):
                    if cfg_cand.is_file():
                        rel = cfg_cand.relative_to(self.app_root).as_posix()
                        digest, sz = sha256_file(cfg_cand)
                        remote_hashes[rel] = {
                            "sha256": digest,
                            "size": sz,
                            "kind": PatchTargetKind.AGENT_CONFIG.value,
                        }

        for tool_dir_name, remote_tool in tool_map.items():
            tool_dir = self.app_root / "tools" / tool_dir_name
            code_path = tool_dir / "python_function" / "python_code.py"
            if code_path.is_file():
                py_fn = getattr(remote_tool, "python_function", None)
                live_code = getattr(py_fn, "python_code", "") if py_fn else ""
                rel = code_path.relative_to(self.app_root).as_posix()
                remote_hashes[rel] = {
                    "sha256": sha256_text(live_code),
                    "size": len(live_code.encode("utf-8")),
                    "kind": PatchTargetKind.PYTHON_TOOL.value,
                }
            for cfg_cand in (
                tool_dir / f"{tool_dir_name}.json",
                tool_dir / "tool.json",
                tool_dir / f"{tool_dir_name}.yaml",
                tool_dir / "tool.yaml",
            ):
                if cfg_cand.is_file():
                    rel = cfg_cand.relative_to(self.app_root).as_posix()
                    digest, sz = sha256_file(cfg_cand)
                    remote_hashes[rel] = {
                        "sha256": digest,
                        "size": sz,
                        "kind": PatchTargetKind.TOOL_CONFIG.value,
                    }

        for gr_dir_name in guardrail_map:
            gr_dir = self.app_root / "guardrails" / gr_dir_name
            for cfg_cand in (
                gr_dir / f"{gr_dir_name}.json",
                gr_dir / "guardrail.json",
                gr_dir / f"{gr_dir_name}.yaml",
                gr_dir / "guardrail.yaml",
            ):
                if cfg_cand.is_file():
                    rel = cfg_cand.relative_to(self.app_root).as_posix()
                    digest, sz = sha256_file(cfg_cand)
                    remote_hashes[rel] = {
                        "sha256": digest,
                        "size": sz,
                        "kind": PatchTargetKind.GUARDRAIL.value,
                    }

        return remote_hashes, live_app, agent_map, tool_map, guardrail_map

    def compile_diff(
        self,
        manifest: StateManifest | None = None,
    ) -> tuple[
        list[FileDelta],
        list[str],
        dict[str, dict[str, Any]],
        bool,
        types.App | None,
        dict[str, types.Agent] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        """Compiles SHA-256 deltas against manifest or live baseline."""
        current_files = scan_workspace_hashes(self.app_root)
        bootstrapped = False
        preloaded_app: types.App | None = None
        preloaded_agents: dict[str, types.Agent] | None = None
        preloaded_tools: dict[str, Any] | None = None
        preloaded_guardrails: dict[str, Any] | None = None

        if manifest is not None:
            baseline_files = manifest.files
        else:
            bootstrapped = True
            (
                baseline_files,
                preloaded_app,
                preloaded_agents,
                preloaded_tools,
                preloaded_guardrails,
            ) = self._bootstrap_remote_hashes()

        deltas: list[FileDelta] = []
        structural_warnings: list[str] = []

        for rel_posix, info in sorted(current_files.items()):
            classified = classify_workspace_file(rel_posix)
            if classified is None:
                continue
            kind, resource_key, mask_path = classified
            prev_info = baseline_files.get(rel_posix)
            prev_sha = prev_info.get("sha256") if prev_info else None
            curr_sha = info["sha256"]

            if kind == PatchTargetKind.STRUCTURAL_CONFIG:
                if manifest is not None and prev_sha != curr_sha:
                    structural_warnings.append(
                        f"{rel_posix} changed (structural schema/config edits "
                        "require a full 'cxas push' without --patch)."
                    )
                continue

            if prev_sha == curr_sha:
                continue

            deltas.append(
                FileDelta(
                    rel_path=rel_posix,
                    abs_path=self.app_root / rel_posix,
                    kind=kind,
                    resource_key=resource_key,
                    field_mask_path=mask_path,
                    old_sha256=prev_sha,
                    new_sha256=curr_sha,
                    size_bytes=int(info["size"]),
                    change_type="added" if prev_sha is None else "modified",
                )
            )

        return (
            deltas,
            structural_warnings,
            current_files,
            bootstrapped,
            preloaded_app,
            preloaded_agents,
            preloaded_tools,
            preloaded_guardrails,
        )

    def apply_patch(
        self,
        *,
        dry_run: bool = False,
        skip_lint: bool = False,
        force_etag: bool = False,
        expected_etag: str | None = None,
        post_patch_validator: Callable[[HotPatchReport], None] | None = None,
    ) -> HotPatchReport:
        """Executes differential hot-patching & DAG creation with rollback."""
        t_start = time.perf_counter()
        report = HotPatchReport(
            app_name=self.app_name,
            app_root=self.app_root,
            status="noop",
        )

        # ── Step 1: Compile SHA-256 Diff ─────────────────────────────────────
        t_diff0 = time.perf_counter()
        manifest = StateManifest.load(self.app_root, self.app_name)
        (
            deltas,
            structural_warnings,
            current_files,
            bootstrapped,
            preloaded_app,
            preloaded_agents,
            preloaded_tools,
            preloaded_guardrails,
        ) = self.compile_diff(manifest)
        report.deltas = deltas
        report.structural_warnings = structural_warnings
        report.bootstrapped_from_remote = bootstrapped
        report.diff_ms = (time.perf_counter() - t_diff0) * 1000.0

        seeded_agent_etags: dict[str, dict[str, str]] = (
            dict(manifest.agent_etags) if manifest else {}
        )
        seeded_agent_snaps: dict[str, AgentStateSnapshot] = (
            dict(manifest.agent_snapshots) if manifest else {}
        )
        if preloaded_agents:
            for dir_name, remote_ag in preloaded_agents.items():
                seeded_agent_etags[dir_name] = {
                    "resource_name": remote_ag.name,
                    "display_name": remote_ag.display_name,
                    "etag": getattr(remote_ag, "etag", "") or "",
                }
                seeded_agent_snaps[dir_name] = _agent_proto_to_snapshot(
                    remote_ag, dir_name
                )

        seeded_tool_etags: dict[str, dict[str, str]] = (
            dict(manifest.tool_etags) if manifest else {}
        )
        seeded_tool_snaps: dict[str, ToolStateSnapshot] = (
            dict(manifest.tool_snapshots) if manifest else {}
        )
        if preloaded_tools:
            for dir_name, remote_tl in preloaded_tools.items():
                seeded_tool_etags[dir_name] = {
                    "resource_name": remote_tl.name,
                    "display_name": getattr(
                        remote_tl, "display_name", dir_name
                    ),
                    "etag": getattr(remote_tl, "etag", "") or "",
                }
                seeded_tool_snaps[dir_name] = _tool_proto_to_snapshot(
                    remote_tl, dir_name
                )

        seeded_guardrail_etags: dict[str, dict[str, str]] = (
            dict(manifest.guardrail_etags) if manifest else {}
        )
        seeded_guardrail_snaps: dict[str, GuardrailStateSnapshot] = (
            dict(manifest.guardrail_snapshots) if manifest else {}
        )
        if preloaded_guardrails:
            for dir_name, remote_gr in preloaded_guardrails.items():
                seeded_guardrail_etags[dir_name] = {
                    "resource_name": remote_gr.name,
                    "display_name": getattr(
                        remote_gr, "display_name", dir_name
                    ),
                    "etag": getattr(remote_gr, "etag", "") or "",
                }
                seeded_guardrail_snaps[dir_name] = _guardrail_proto_to_snapshot(
                    remote_gr, dir_name
                )

        seeded_app_etag = (
            manifest.app_etag
            if manifest
            else (getattr(preloaded_app, "etag", "") or "")
        )
        seeded_app_display = (
            manifest.app_display_name
            if manifest
            else (getattr(preloaded_app, "display_name", "App") or "App")
        )
        seeded_global_inst = (
            manifest.global_instruction
            if manifest
            else (
                getattr(preloaded_app, "global_instruction", "") or ""
                if preloaded_app is not None
                else None
            )
        )
        seeded_app_guardrails = (
            manifest.app_guardrails
            if (manifest and manifest.app_guardrails is not None)
            else (
                list(getattr(preloaded_app, "guardrails", []) or [])
                if preloaded_app is not None
                else None
            )
        )
        seeded_app_root_agent = (
            manifest.app_root_agent
            if (manifest and manifest.app_root_agent is not None)
            else (
                getattr(preloaded_app, "root_agent", "") or ""
                if preloaded_app is not None
                else None
            )
        )
        seeded_app_description = (
            manifest.app_description
            if (manifest and manifest.app_description is not None)
            else (
                getattr(preloaded_app, "description", "") or ""
                if preloaded_app is not None
                else None
            )
        )

        if not deltas:
            if bootstrapped and not dry_run:
                StateManifest(
                    app_name=self.app_name,
                    app_display_name=seeded_app_display,
                    app_etag=seeded_app_etag,
                    global_instruction=seeded_global_inst,
                    app_guardrails=seeded_app_guardrails,
                    app_root_agent=seeded_app_root_agent,
                    app_description=seeded_app_description,
                    agent_etags=seeded_agent_etags,
                    tool_etags=seeded_tool_etags,
                    guardrail_etags=seeded_guardrail_etags,
                    agent_snapshots=seeded_agent_snaps,
                    tool_snapshots=seeded_tool_snaps,
                    guardrail_snapshots=seeded_guardrail_snaps,
                    files=current_files,
                ).save(self.app_root)
            report.status = "noop"
            report.total_ms = (time.perf_counter() - t_start) * 1000.0
            return report

        # ── Step 2: Scoped Pre-Flight Linter (<50ms) ─────────────────────────
        if not skip_lint:
            t_lint0 = time.perf_counter()
            linter = ScopedPreflightLinter(self.app_root)
            findings = linter.lint_deltas(deltas)
            report.lint_findings = findings
            report.lint_ms = (time.perf_counter() - t_lint0) * 1000.0
            blocking = [f for f in findings if f.severity == "error"]
            if blocking:
                report.status = "aborted"
                report.total_ms = (time.perf_counter() - t_start) * 1000.0
                report.error_message = "; ".join(
                    f.format_short() for f in blocking
                )
                raise ScopedLintError(blocking, report)

        if dry_run:
            report.status = "dry_run"
            report.total_ms = (time.perf_counter() - t_start) * 1000.0
            return report

        # ── Step 3: Fast In-Memory Snapshot & DAG Discovery ──────────────────
        t_snap0 = time.perf_counter()
        snapshot = PrePatchSnapshot(
            app_name=self.app_name,
            app_display_name=seeded_app_display,
        )

        needs_global_inst_patch = any(
            d.kind == PatchTargetKind.GLOBAL_INSTRUCTION for d in deltas
        )
        needs_app_cfg_patch = any(
            d.kind == PatchTargetKind.APP_CONFIG for d in deltas
        )
        needs_app_patch = needs_global_inst_patch or needs_app_cfg_patch

        touched_tool_keys_set = {
            d.resource_key
            for d in deltas
            if d.kind
            in (PatchTargetKind.PYTHON_TOOL, PatchTargetKind.TOOL_CONFIG)
        }
        touched_guardrail_keys_set = {
            d.resource_key
            for d in deltas
            if d.kind == PatchTargetKind.GUARDRAIL
        }
        touched_agent_keys_set = {
            d.resource_key
            for d in deltas
            if d.kind
            in (
                PatchTargetKind.AGENT_INSTRUCTION,
                PatchTargetKind.AGENT_CALLBACK,
                PatchTargetKind.AGENT_CONFIG,
            )
        }

        # Discover if any touched agent or app.json references a local tool,
        # guardrail, or sub-agent that hasn't been created on the server yet
        tool_alias_map = _discover_local_tool_alias_map(self.app_root)
        gr_alias_map = _discover_local_guardrail_alias_map(self.app_root)
        agent_alias_map = _discover_local_agent_alias_map(self.app_root)

        # Expand transitive local dependencies for touched agents
        queue_agents = list(touched_agent_keys_set)
        while queue_agents:
            ak = queue_agents.pop(0)
            ag_dir = self.app_root / "agents" / ak
            meta = _load_local_resource_metadata(ag_dir)
            for t_ref in meta.get("tools") or []:
                t_dir = tool_alias_map.get(str(t_ref)) or tool_alias_map.get(
                    _normalize_identifier(str(t_ref))
                )
                if t_dir and t_dir not in seeded_tool_snaps:
                    touched_tool_keys_set.add(t_dir)
            for g_ref in meta.get("guardrails") or []:
                g_dir = gr_alias_map.get(str(g_ref)) or gr_alias_map.get(
                    _normalize_identifier(str(g_ref))
                )
                if g_dir and g_dir not in seeded_guardrail_snaps:
                    touched_guardrail_keys_set.add(g_dir)
            for c_ref in (
                meta.get("childAgents") or meta.get("child_agents") or []
            ):
                c_dir = agent_alias_map.get(str(c_ref)) or agent_alias_map.get(
                    _normalize_identifier(str(c_ref))
                )
                if (
                    c_dir
                    and c_dir not in seeded_agent_snaps
                    and c_dir not in touched_agent_keys_set
                ):
                    touched_agent_keys_set.add(c_dir)
                    queue_agents.append(c_dir)

        if needs_app_cfg_patch:
            app_meta = _load_app_metadata(self.app_root)
            for g_ref in app_meta.get("guardrails") or []:
                g_dir = gr_alias_map.get(str(g_ref)) or gr_alias_map.get(
                    _normalize_identifier(str(g_ref))
                )
                if g_dir and g_dir not in seeded_guardrail_snaps:
                    touched_guardrail_keys_set.add(g_dir)
            root_ref = app_meta.get("rootAgent") or app_meta.get("root_agent")
            if root_ref:
                r_dir = agent_alias_map.get(
                    str(root_ref)
                ) or agent_alias_map.get(_normalize_identifier(str(root_ref)))
                if r_dir and r_dir not in seeded_agent_snaps:
                    touched_agent_keys_set.add(r_dir)

        touched_tool_keys = sorted(touched_tool_keys_set)
        touched_guardrail_keys = sorted(touched_guardrail_keys_set)
        ordered_agents_with_levels = _topological_sort_agents(
            self.app_root, sorted(touched_agent_keys_set)
        )

        if needs_app_patch:
            if (
                seeded_global_inst is not None
                and not expected_etag
                and (
                    not needs_app_cfg_patch or seeded_app_guardrails is not None
                )
            ):
                snapshot.app_etag = seeded_app_etag
                snapshot.global_instruction = seeded_global_inst
                snapshot.app_guardrails = (
                    list(seeded_app_guardrails)
                    if seeded_app_guardrails is not None
                    else []
                )
                snapshot.app_root_agent = seeded_app_root_agent
                snapshot.app_description = seeded_app_description
            else:
                live_app = preloaded_app or self.apps_client.get_app(
                    self.app_name
                )
                live_app_etag = getattr(live_app, "etag", "") or ""
                snapshot.app_display_name = (
                    getattr(live_app, "display_name", "App") or "App"
                )
                snapshot.app_etag = live_app_etag
                snapshot.global_instruction = (
                    getattr(live_app, "global_instruction", "") or ""
                )
                snapshot.app_guardrails = list(
                    getattr(live_app, "guardrails", []) or []
                )
                snapshot.app_root_agent = (
                    getattr(live_app, "root_agent", "") or ""
                )
                snapshot.app_description = (
                    getattr(live_app, "description", "") or ""
                )
                baseline_app_etag = expected_etag or seeded_app_etag
                if (
                    not force_etag
                    and baseline_app_etag
                    and live_app_etag
                    and baseline_app_etag != live_app_etag
                ):
                    report.status = "aborted"
                    report.snapshot_ms = (
                        time.perf_counter() - t_snap0
                    ) * 1000.0
                    report.total_ms = (time.perf_counter() - t_start) * 1000.0
                    raise EtagConflictError(
                        resource_name=self.app_name,
                        display_name=snapshot.app_display_name,
                        expected_etag=baseline_app_etag,
                        remote_etag=live_app_etag,
                        report=report,
                    )

        agent_map = preloaded_agents
        new_agent_keys: set[str] = set()
        for agent_key, _ in ordered_agents_with_levels:
            if agent_key in seeded_agent_snaps and not expected_etag:
                snapshot.agents[agent_key] = seeded_agent_snaps[agent_key]
                continue

            if agent_map is None:
                remote_agents = self.agents_client.list_agents()
                agent_map = match_local_agents_to_remote(
                    self.app_root, remote_agents
                )
                for k, r_ag in agent_map.items():
                    if k not in seeded_agent_snaps:
                        seeded_agent_snaps[k] = _agent_proto_to_snapshot(
                            r_ag, k
                        )
                        seeded_agent_etags[k] = {
                            "resource_name": r_ag.name,
                            "display_name": r_ag.display_name,
                            "etag": getattr(r_ag, "etag", "") or "",
                        }
            matched = (agent_map or {}).get(agent_key)
            if matched is None:
                # Brand-new sub-agent to be created via CreateAgent in the DAG!
                new_agent_keys.add(agent_key)
                continue

            fresh_agent = self.agents_client.get_agent(matched.name)
            live_etag = getattr(fresh_agent, "etag", "") or ""
            baseline_etag = expected_etag or seeded_agent_etags.get(
                agent_key, {}
            ).get("etag", "")
            if (
                not force_etag
                and baseline_etag
                and live_etag
                and baseline_etag != live_etag
            ):
                report.status = "aborted"
                report.snapshot_ms = (time.perf_counter() - t_snap0) * 1000.0
                report.total_ms = (time.perf_counter() - t_start) * 1000.0
                raise EtagConflictError(
                    resource_name=fresh_agent.name,
                    display_name=fresh_agent.display_name or agent_key,
                    expected_etag=baseline_etag,
                    remote_etag=live_etag,
                    report=report,
                )
            snap = _agent_proto_to_snapshot(fresh_agent, agent_key)
            snapshot.agents[agent_key] = snap
            seeded_agent_snaps[agent_key] = snap

        tool_map = preloaded_tools
        new_tool_keys: set[str] = set()
        for tool_key in touched_tool_keys:
            if tool_key in seeded_tool_snaps and not expected_etag:
                snapshot.tools[tool_key] = seeded_tool_snaps[tool_key]
                continue

            if tool_map is None:
                remote_tools = self.tools_client.list_tools()
                tool_map = _match_local_tools_to_remote(
                    self.app_root, remote_tools
                )
                for k, r_tl in tool_map.items():
                    if k not in seeded_tool_snaps:
                        seeded_tool_snaps[k] = _tool_proto_to_snapshot(r_tl, k)
                        seeded_tool_etags[k] = {
                            "resource_name": r_tl.name,
                            "display_name": getattr(r_tl, "display_name", k),
                            "etag": getattr(r_tl, "etag", "") or "",
                        }
            matched_tool = (tool_map or {}).get(tool_key)
            if matched_tool is None:
                # Brand-new tool to be created via CreateTool at DAG Level 1!
                new_tool_keys.add(tool_key)
                continue

            fresh_tool = self.tools_client.get_tool(matched_tool.name)
            live_tool_etag = getattr(fresh_tool, "etag", "") or ""
            baseline_tool_etag = expected_etag or seeded_tool_etags.get(
                tool_key, {}
            ).get("etag", "")
            if (
                not force_etag
                and baseline_tool_etag
                and live_tool_etag
                and baseline_tool_etag != live_tool_etag
            ):
                report.status = "aborted"
                report.snapshot_ms = (time.perf_counter() - t_snap0) * 1000.0
                report.total_ms = (time.perf_counter() - t_start) * 1000.0
                raise EtagConflictError(
                    resource_name=fresh_tool.name,
                    display_name=getattr(fresh_tool, "display_name", tool_key)
                    or tool_key,
                    expected_etag=baseline_tool_etag,
                    remote_etag=live_tool_etag,
                    report=report,
                )
            tsnap = _tool_proto_to_snapshot(fresh_tool, tool_key)
            snapshot.tools[tool_key] = tsnap
            seeded_tool_snaps[tool_key] = tsnap

        guardrail_map = preloaded_guardrails
        new_guardrail_keys: set[str] = set()
        for gr_key in touched_guardrail_keys:
            if gr_key in seeded_guardrail_snaps and not expected_etag:
                snapshot.guardrails[gr_key] = seeded_guardrail_snaps[gr_key]
                continue

            if guardrail_map is None:
                remote_grs = self.guardrails_client.list_guardrails()
                guardrail_map = _match_local_guardrails_to_remote(
                    self.app_root, remote_grs
                )
                for k, r_gr in guardrail_map.items():
                    if k not in seeded_guardrail_snaps:
                        seeded_guardrail_snaps[k] = (
                            _guardrail_proto_to_snapshot(r_gr, k)
                        )
                        seeded_guardrail_etags[k] = {
                            "resource_name": r_gr.name,
                            "display_name": getattr(r_gr, "display_name", k),
                            "etag": getattr(r_gr, "etag", "") or "",
                        }
            matched_gr = (guardrail_map or {}).get(gr_key)
            if matched_gr is None:
                # New guardrail created via CreateGuardrail at DAG Level 1.
                new_guardrail_keys.add(gr_key)
                continue

            fresh_gr = self.guardrails_client.get_guardrail(matched_gr.name)
            live_gr_etag = getattr(fresh_gr, "etag", "") or ""
            baseline_gr_etag = expected_etag or seeded_guardrail_etags.get(
                gr_key, {}
            ).get("etag", "")
            if (
                not force_etag
                and baseline_gr_etag
                and live_gr_etag
                and baseline_gr_etag != live_gr_etag
            ):
                report.status = "aborted"
                report.snapshot_ms = (time.perf_counter() - t_snap0) * 1000.0
                report.total_ms = (time.perf_counter() - t_start) * 1000.0
                raise EtagConflictError(
                    resource_name=fresh_gr.name,
                    display_name=getattr(fresh_gr, "display_name", gr_key)
                    or gr_key,
                    expected_etag=baseline_gr_etag,
                    remote_etag=live_gr_etag,
                    report=report,
                )
            gsnap = _guardrail_proto_to_snapshot(fresh_gr, gr_key)
            snapshot.guardrails[gr_key] = gsnap
            seeded_guardrail_snaps[gr_key] = gsnap

        # Build live resource lookup tables for DAG reference resolution
        known_tool_resources: dict[str, str] = {}
        for k, t_snap in seeded_tool_snaps.items():
            _register_resource_lookup(
                known_tool_resources,
                k,
                t_snap.display_name,
                t_snap.resource_name,
            )

        known_guardrail_resources: dict[str, str] = {}
        for k, g_snap in seeded_guardrail_snaps.items():
            _register_resource_lookup(
                known_guardrail_resources,
                k,
                g_snap.display_name,
                g_snap.resource_name,
            )

        known_agent_resources: dict[str, str] = {}
        for k, a_snap in seeded_agent_snaps.items():
            _register_resource_lookup(
                known_agent_resources,
                k,
                a_snap.display_name,
                a_snap.resource_name,
            )

        report.snapshot_ms = (time.perf_counter() - t_snap0) * 1000.0

        # ── Step 4: Topological DAG Execution + Server Etag + Atomic Rollback ─
        t_rpc_total0 = time.perf_counter()
        current_target_meta: tuple[str, str, str, str] | None = None

        def _execute_app_patch(dag_lvl: int) -> None:
            nonlocal \
                current_target_meta, \
                seeded_global_inst, \
                seeded_app_guardrails, \
                seeded_app_root_agent, \
                seeded_app_description
            app_patch = types.App(name=self.app_name)
            app_mask: list[str] = []
            payload_sz = 0

            if needs_global_inst_patch:
                new_global = (
                    self.app_root / "global_instruction.txt"
                ).read_text(encoding="utf-8")
                app_patch.global_instruction = new_global
                app_mask.append("global_instruction")
                payload_sz += len(new_global.encode("utf-8"))
                seeded_global_inst = new_global

            if needs_app_cfg_patch:
                app_meta = _load_app_metadata(self.app_root)
                if "guardrails" in app_meta:
                    resolved_app_grs = _resolve_ref_list(
                        app_meta.get("guardrails") or [],
                        known_guardrail_resources,
                        self.app_name,
                        "guardrails",
                    )
                    app_patch.guardrails.extend(resolved_app_grs)
                    app_mask.append("guardrails")
                    payload_sz += sum(len(g) for g in resolved_app_grs)
                    seeded_app_guardrails = resolved_app_grs
                root_ref = app_meta.get("rootAgent") or app_meta.get(
                    "root_agent"
                )
                if root_ref:
                    resolved_roots = _resolve_ref_list(
                        [root_ref],
                        known_agent_resources,
                        self.app_name,
                        "agents",
                    )
                    if resolved_roots and resolved_roots[0] != (
                        snapshot.app_root_agent or ""
                    ):
                        app_patch.root_agent = resolved_roots[0]
                        app_mask.append("root_agent")
                        payload_sz += len(resolved_roots[0])
                        seeded_app_root_agent = resolved_roots[0]
                if "description" in app_meta and app_meta.get(
                    "description", ""
                ) != (snapshot.app_description or ""):
                    app_patch.description = str(app_meta.get("description", ""))
                    app_mask.append("description")
                    payload_sz += len(app_patch.description.encode("utf-8"))
                    seeded_app_description = app_patch.description

            if not app_mask:
                return

            sent_etag = "" if force_etag else snapshot.app_etag
            if sent_etag:
                app_patch.etag = sent_etag
            current_target_meta = (
                "app",
                self.app_name,
                snapshot.app_display_name,
                sent_etag,
            )
            req = types.UpdateAppRequest(
                app=app_patch,
                update_mask=field_mask_pb2.FieldMask(paths=app_mask),
            )
            t0 = time.perf_counter()
            updated_app = self.apps_client.client.update_app(request=req)
            dt_ms = (time.perf_counter() - t0) * 1000.0
            new_etag = getattr(updated_app, "etag", "") or ""
            report.applied_patches.append(
                ResourcePatchResult(
                    resource_type="app",
                    resource_key="app",
                    resource_name=self.app_name,
                    display_name=snapshot.app_display_name,
                    field_mask_paths=app_mask,
                    old_etag=snapshot.app_etag,
                    new_etag=new_etag,
                    latency_ms=dt_ms,
                    payload_bytes=payload_sz,
                    operation="UPDATE",
                    dag_level=dag_lvl,
                )
            )

        try:
            # If only global_instruction.txt changed (no app.json deps),
            # apply it immediately at Level 1.
            if needs_app_patch and not needs_app_cfg_patch:
                _execute_app_patch(dag_lvl=1)

            # ── DAG Level 1A: Tools (CreateTool / UpdateTool) ────────────────
            for tool_key in touched_tool_keys:
                if tool_key in new_tool_keys:
                    (
                        tool_proto,
                        t_disp,
                        fn_name,
                        py_code,
                        fn_desc,
                        payload_sz,
                    ) = _build_tool_proto_from_local(self.app_root, tool_key)
                    tool_id = _sanitize_resource_id(tool_key)
                    current_target_meta = (
                        "tool",
                        f"{self.app_name}/tools/{tool_id}",
                        t_disp,
                        "",
                    )
                    c_req = types.CreateToolRequest(
                        parent=self.app_name,
                        tool_id=tool_id,
                        tool=tool_proto,
                    )
                    t0 = time.perf_counter()
                    created_tool = self.tools_client.client.create_tool(
                        request=c_req
                    )
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    created_name = (
                        getattr(created_tool, "name", "")
                        or f"{self.app_name}/tools/{tool_id}"
                    )
                    new_etag = getattr(created_tool, "etag", "") or ""
                    if not new_etag:
                        with contextlib.suppress(Exception):
                            new_etag = (
                                getattr(
                                    self.tools_client.get_tool(created_name),
                                    "etag",
                                    "",
                                )
                                or ""
                            )
                    _register_resource_lookup(
                        known_tool_resources, tool_key, t_disp, created_name
                    )
                    report.applied_patches.append(
                        ResourcePatchResult(
                            resource_type="tool",
                            resource_key=tool_key,
                            resource_name=created_name,
                            display_name=t_disp,
                            field_mask_paths=["CREATE (python_function)"],
                            old_etag="(new)",
                            new_etag=new_etag,
                            latency_ms=dt_ms,
                            payload_bytes=payload_sz,
                            operation="CREATE",
                            dag_level=1,
                        )
                    )
                    seeded_tool_snaps[tool_key] = ToolStateSnapshot(
                        resource_name=created_name,
                        display_name=t_disp,
                        etag=new_etag,
                        python_code=py_code,
                        function_name=fn_name,
                        description=fn_desc,
                    )
                else:
                    tool_snap = snapshot.tools[tool_key]
                    (
                        _,
                        t_disp,
                        fn_name,
                        new_code,
                        fn_desc,
                        payload_sz,
                    ) = _build_tool_proto_from_local(
                        self.app_root, tool_key, tool_snap.resource_name
                    )
                    py_fn_proto = types.PythonFunction(
                        name=tool_snap.function_name or fn_name,
                        python_code=new_code,
                    )
                    t_mask = ["python_function.python_code"]
                    if fn_desc and fn_desc != tool_snap.description:
                        py_fn_proto.description = fn_desc
                        t_mask.append("python_function.description")
                    tool_patch = types.Tool(
                        name=tool_snap.resource_name,
                        python_function=py_fn_proto,
                    )
                    sent_etag = "" if force_etag else tool_snap.etag
                    if sent_etag:
                        tool_patch.etag = sent_etag
                    current_target_meta = (
                        "tool",
                        tool_snap.resource_name,
                        tool_snap.display_name,
                        sent_etag,
                    )
                    t_req = types.UpdateToolRequest(
                        tool=tool_patch,
                        update_mask=field_mask_pb2.FieldMask(paths=t_mask),
                    )
                    t0 = time.perf_counter()
                    updated_tool = self.tools_client.client.update_tool(
                        request=t_req
                    )
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    new_etag = getattr(updated_tool, "etag", "") or ""
                    report.applied_patches.append(
                        ResourcePatchResult(
                            resource_type="tool",
                            resource_key=tool_key,
                            resource_name=tool_snap.resource_name,
                            display_name=tool_snap.display_name,
                            field_mask_paths=t_mask,
                            old_etag=tool_snap.etag,
                            new_etag=new_etag,
                            latency_ms=dt_ms,
                            payload_bytes=len(new_code.encode("utf-8")),
                            operation="UPDATE",
                            dag_level=1,
                        )
                    )
                    seeded_tool_snaps[tool_key] = ToolStateSnapshot(
                        resource_name=tool_snap.resource_name,
                        display_name=tool_snap.display_name,
                        etag=new_etag,
                        python_code=new_code,
                        function_name=tool_snap.function_name or fn_name,
                        description=fn_desc or tool_snap.description,
                    )

            # ── DAG Level 1B: Guardrails (CreateGuardrail / UpdateGuardrail) ─
            for gr_key in touched_guardrail_keys:
                gr_dir = self.app_root / "guardrails" / gr_key
                gr_meta = _load_local_resource_metadata(gr_dir)
                if gr_key in new_guardrail_keys:
                    gr_proto, _, payload_sz = _build_guardrail_proto_from_dict(
                        gr_meta, self.app_root, gr_key
                    )
                    gr_id = _sanitize_resource_id(gr_key)
                    current_target_meta = (
                        "guardrail",
                        f"{self.app_name}/guardrails/{gr_id}",
                        gr_proto.display_name,
                        "",
                    )
                    g_req = types.CreateGuardrailRequest(
                        parent=self.app_name,
                        guardrail_id=gr_id,
                        guardrail=gr_proto,
                    )
                    t0 = time.perf_counter()
                    created_gr = self.guardrails_client.client.create_guardrail(
                        request=g_req
                    )
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    created_name = (
                        getattr(created_gr, "name", "")
                        or f"{self.app_name}/guardrails/{gr_id}"
                    )
                    new_etag = getattr(created_gr, "etag", "") or ""
                    if not new_etag:
                        with contextlib.suppress(Exception):
                            new_etag = (
                                getattr(
                                    self.guardrails_client.get_guardrail(
                                        created_name
                                    ),
                                    "etag",
                                    "",
                                )
                                or ""
                            )
                    _register_resource_lookup(
                        known_guardrail_resources,
                        gr_key,
                        gr_proto.display_name,
                        created_name,
                    )
                    report.applied_patches.append(
                        ResourcePatchResult(
                            resource_type="guardrail",
                            resource_key=gr_key,
                            resource_name=created_name,
                            display_name=gr_proto.display_name,
                            field_mask_paths=["CREATE (guardrail)"],
                            old_etag="(new)",
                            new_etag=new_etag,
                            latency_ms=dt_ms,
                            payload_bytes=payload_sz,
                            operation="CREATE",
                            dag_level=1,
                        )
                    )
                    seeded_guardrail_snaps[gr_key] = GuardrailStateSnapshot(
                        resource_name=created_name,
                        display_name=gr_proto.display_name,
                        etag=new_etag,
                        guardrail_dict=gr_meta,
                    )
                else:
                    gr_snap = snapshot.guardrails[gr_key]
                    gr_proto, g_mask, payload_sz = (
                        _build_guardrail_proto_from_dict(
                            gr_meta,
                            self.app_root,
                            gr_key,
                            resource_name=gr_snap.resource_name,
                        )
                    )
                    sent_etag = "" if force_etag else gr_snap.etag
                    if sent_etag:
                        gr_proto.etag = sent_etag
                    current_target_meta = (
                        "guardrail",
                        gr_snap.resource_name,
                        gr_snap.display_name,
                        sent_etag,
                    )
                    ug_req = types.UpdateGuardrailRequest(
                        guardrail=gr_proto,
                        update_mask=field_mask_pb2.FieldMask(paths=g_mask),
                    )
                    t0 = time.perf_counter()
                    updated_gr = self.guardrails_client.client.update_guardrail(
                        request=ug_req
                    )
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    new_etag = getattr(updated_gr, "etag", "") or ""
                    report.applied_patches.append(
                        ResourcePatchResult(
                            resource_type="guardrail",
                            resource_key=gr_key,
                            resource_name=gr_snap.resource_name,
                            display_name=gr_snap.display_name,
                            field_mask_paths=g_mask,
                            old_etag=gr_snap.etag,
                            new_etag=new_etag,
                            latency_ms=dt_ms,
                            payload_bytes=payload_sz,
                            operation="UPDATE",
                            dag_level=1,
                        )
                    )
                    seeded_guardrail_snaps[gr_key] = GuardrailStateSnapshot(
                        resource_name=gr_snap.resource_name,
                        display_name=gr_proto.display_name,
                        etag=new_etag,
                        guardrail_dict=gr_meta,
                    )

            # ── DAG Level 2..N: Agents (Bottom-Up Topological Order) ─────────
            max_agent_dag_level = 1
            for agent_key, dag_lvl in ordered_agents_with_levels:
                max_agent_dag_level = max(max_agent_dag_level, dag_lvl)
                agent_dir = self.app_root / "agents" / agent_key
                meta = _load_local_resource_metadata(agent_dir)

                if agent_key in new_agent_keys:
                    disp_name = str(
                        meta.get("displayName")
                        or meta.get("display_name")
                        or meta.get("name")
                        or agent_key
                    )
                    desc_val = str(meta.get("description") or "")
                    inst_val = ""
                    for inst_fname in ("instruction.txt", "instructions.txt"):
                        inst_fpath = agent_dir / inst_fname
                        if inst_fpath.is_file():
                            inst_val = inst_fpath.read_text(encoding="utf-8")
                            break

                    raw_tools = list(meta.get("tools") or [])
                    for m in _TOOL_MACRO_RE.finditer(inst_val):
                        t_ref = m.group(1).strip()
                        if (
                            t_ref not in PLATFORM_SYSTEM_TOOLS
                            and t_ref not in raw_tools
                        ):
                            raw_tools.append(t_ref)
                    resolved_tools = _resolve_ref_list(
                        raw_tools,
                        known_tool_resources,
                        self.app_name,
                        "tools",
                    )

                    raw_grs = list(meta.get("guardrails") or [])
                    resolved_grs = _resolve_ref_list(
                        raw_grs,
                        known_guardrail_resources,
                        self.app_name,
                        "guardrails",
                    )

                    raw_children = list(
                        meta.get("childAgents")
                        or meta.get("child_agents")
                        or []
                    )
                    for m in _AGENT_MACRO_RE.finditer(inst_val):
                        a_ref = m.group(1).strip()
                        if a_ref not in raw_children:
                            raw_children.append(a_ref)
                    resolved_children = _resolve_ref_list(
                        raw_children,
                        known_agent_resources,
                        self.app_name,
                        "agents",
                    )

                    new_agent_proto = types.Agent(
                        display_name=disp_name,
                        description=desc_val,
                        instruction=inst_val,
                        tools=resolved_tools,
                        guardrails=resolved_grs,
                        child_agents=resolved_children,
                    )
                    new_cbs_map: dict[str, list[types.Callback]] = {}
                    for cb_field in CALLBACK_FIELD_SPECS:
                        built_cbs = _build_updated_callbacks_for_field(
                            self.app_root, agent_key, cb_field, []
                        )
                        if built_cbs:
                            getattr(new_agent_proto, cb_field).extend(built_cbs)
                        new_cbs_map[cb_field] = _clone_callbacks(built_cbs)

                    agent_id = _sanitize_resource_id(agent_key)
                    current_target_meta = (
                        "agent",
                        f"{self.app_name}/agents/{agent_id}",
                        disp_name,
                        "",
                    )
                    ca_req = types.CreateAgentRequest(
                        parent=self.app_name,
                        agent_id=agent_id,
                        agent=new_agent_proto,
                    )
                    t0 = time.perf_counter()
                    created_ag = self.agents_client.client.create_agent(
                        request=ca_req
                    )
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    created_name = (
                        getattr(created_ag, "name", "")
                        or f"{self.app_name}/agents/{agent_id}"
                    )
                    new_etag = getattr(created_ag, "etag", "") or ""
                    if not new_etag:
                        with contextlib.suppress(Exception):
                            new_etag = (
                                getattr(
                                    self.agents_client.get_agent(created_name),
                                    "etag",
                                    "",
                                )
                                or ""
                            )

                    _register_resource_lookup(
                        known_agent_resources,
                        agent_key,
                        disp_name,
                        created_name,
                    )
                    create_summary = ["CREATE (agent)"]
                    if resolved_tools:
                        create_summary.append(f"tools[{len(resolved_tools)}]")
                    if resolved_grs:
                        create_summary.append(
                            f"guardrails[{len(resolved_grs)}]"
                        )
                    if resolved_children:
                        create_summary.append(
                            f"child_agents[{len(resolved_children)}]"
                        )
                    payload_sz = (
                        len(inst_val.encode("utf-8"))
                        + len(disp_name.encode("utf-8"))
                        + len(desc_val.encode("utf-8"))
                    )
                    report.applied_patches.append(
                        ResourcePatchResult(
                            resource_type="agent",
                            resource_key=agent_key,
                            resource_name=created_name,
                            display_name=disp_name,
                            field_mask_paths=create_summary,
                            old_etag="(new)",
                            new_etag=new_etag,
                            latency_ms=dt_ms,
                            payload_bytes=payload_sz,
                            operation="CREATE",
                            dag_level=dag_lvl,
                        )
                    )
                    seeded_agent_snaps[agent_key] = AgentStateSnapshot(
                        resource_name=created_name,
                        display_name=disp_name,
                        etag=new_etag,
                        instruction=inst_val,
                        callbacks=new_cbs_map,
                        description=desc_val,
                        tools=resolved_tools,
                        child_agents=resolved_children,
                        guardrails=resolved_grs,
                    )
                    continue

                # Existing agent: compile FieldMask update
                agent_snap = snapshot.agents[agent_key]
                agent_deltas = [
                    d for d in deltas if d.resource_key == agent_key
                ]
                mask_paths: list[str] = []
                payload_bytes = 0

                agent_patch = types.Agent(name=agent_snap.resource_name)
                sent_etag = "" if force_etag else agent_snap.etag
                if sent_etag:
                    agent_patch.etag = sent_etag
                current_target_meta = (
                    "agent",
                    agent_snap.resource_name,
                    agent_snap.display_name,
                    sent_etag,
                )

                new_inst_val = agent_snap.instruction
                inst_deltas = [
                    d
                    for d in agent_deltas
                    if d.kind == PatchTargetKind.AGENT_INSTRUCTION
                ]
                if inst_deltas:
                    inst_text = inst_deltas[0].abs_path.read_text(
                        encoding="utf-8"
                    )
                    agent_patch.instruction = inst_text
                    new_inst_val = inst_text
                    mask_paths.append("instruction")
                    payload_bytes += len(inst_text.encode("utf-8"))

                new_cbs_map = {
                    k: _clone_callbacks(v)
                    for k, v in agent_snap.callbacks.items()
                }
                cb_fields = sorted(
                    {
                        d.field_mask_path
                        for d in agent_deltas
                        if d.kind == PatchTargetKind.AGENT_CALLBACK
                    }
                )
                for cb_field in cb_fields:
                    rebuilt_cbs = _build_updated_callbacks_for_field(
                        self.app_root,
                        agent_key,
                        cb_field,
                        agent_snap.callbacks.get(cb_field, []),
                    )
                    getattr(agent_patch, cb_field).extend(rebuilt_cbs)
                    new_cbs_map[cb_field] = _clone_callbacks(rebuilt_cbs)
                    mask_paths.append(cb_field)
                    payload_bytes += sum(
                        len(cb.python_code.encode("utf-8"))
                        for cb in rebuilt_cbs
                    )

                has_cfg_delta = any(
                    d.kind == PatchTargetKind.AGENT_CONFIG for d in agent_deltas
                )
                new_tools_list = list(agent_snap.tools)
                new_grs_list = list(agent_snap.guardrails)
                new_children_list = list(agent_snap.child_agents)
                new_desc_val = agent_snap.description
                new_disp_val = agent_snap.display_name

                if has_cfg_delta:
                    if "tools" in meta:
                        resolved_t = _resolve_ref_list(
                            meta.get("tools") or [],
                            known_tool_resources,
                            self.app_name,
                            "tools",
                        )
                        if resolved_t != agent_snap.tools:
                            agent_patch.tools.extend(resolved_t)
                            new_tools_list = resolved_t
                            mask_paths.append("tools")
                            payload_bytes += sum(len(x) for x in resolved_t)

                    if "guardrails" in meta:
                        resolved_g = _resolve_ref_list(
                            meta.get("guardrails") or [],
                            known_guardrail_resources,
                            self.app_name,
                            "guardrails",
                        )
                        if resolved_g != agent_snap.guardrails:
                            agent_patch.guardrails.extend(resolved_g)
                            new_grs_list = resolved_g
                            mask_paths.append("guardrails")
                            payload_bytes += sum(len(x) for x in resolved_g)

                    if "childAgents" in meta or "child_agents" in meta:
                        raw_c = (
                            meta.get("childAgents")
                            or meta.get("child_agents")
                            or []
                        )
                        resolved_c = _resolve_ref_list(
                            raw_c,
                            known_agent_resources,
                            self.app_name,
                            "agents",
                        )
                        if resolved_c != agent_snap.child_agents:
                            agent_patch.child_agents.extend(resolved_c)
                            new_children_list = resolved_c
                            mask_paths.append("child_agents")
                            payload_bytes += sum(len(x) for x in resolved_c)

                    if (
                        "description" in meta
                        and str(meta.get("description") or "")
                        != agent_snap.description
                    ):
                        new_desc_val = str(meta.get("description") or "")
                        agent_patch.description = new_desc_val
                        mask_paths.append("description")
                        payload_bytes += len(new_desc_val.encode("utf-8"))

                # Auto-bind new tools or sub-agents referenced via {@TOOL:...} /
                # {@AGENT:...} even if <agent>.json was not manually edited.
                if inst_deltas and new_tool_keys and "tools" not in mask_paths:
                    auto_tools = list(new_tools_list)
                    added_any_tool = False
                    for m in _TOOL_MACRO_RE.finditer(new_inst_val):
                        t_ref = m.group(1).strip()
                        t_dir = tool_alias_map.get(t_ref) or tool_alias_map.get(
                            _normalize_identifier(t_ref)
                        )
                        if t_dir and t_dir in new_tool_keys:
                            res_t = known_tool_resources.get(t_dir)
                            if res_t and res_t not in auto_tools:
                                auto_tools.append(res_t)
                                added_any_tool = True
                    if added_any_tool:
                        agent_patch.tools.extend(auto_tools)
                        new_tools_list = auto_tools
                        mask_paths.append("tools")
                        payload_bytes += sum(len(x) for x in auto_tools)

                if (
                    inst_deltas
                    and new_agent_keys
                    and "child_agents" not in mask_paths
                ):
                    auto_children = list(new_children_list)
                    added_any_child = False
                    for m in _AGENT_MACRO_RE.finditer(new_inst_val):
                        a_ref = m.group(1).strip()
                        a_dir = agent_alias_map.get(
                            a_ref
                        ) or agent_alias_map.get(_normalize_identifier(a_ref))
                        if a_dir and a_dir in new_agent_keys:
                            res_a = known_agent_resources.get(a_dir)
                            if res_a and res_a not in auto_children:
                                auto_children.append(res_a)
                                added_any_child = True
                    if added_any_child:
                        agent_patch.child_agents.extend(auto_children)
                        new_children_list = auto_children
                        mask_paths.append("child_agents")
                        payload_bytes += sum(len(x) for x in auto_children)

                if not mask_paths:
                    continue

                a_req = types.UpdateAgentRequest(
                    agent=agent_patch,
                    update_mask=field_mask_pb2.FieldMask(paths=mask_paths),
                )
                t0 = time.perf_counter()
                updated_agent = self.agents_client.client.update_agent(
                    request=a_req
                )
                dt_ms = (time.perf_counter() - t0) * 1000.0
                new_etag = getattr(updated_agent, "etag", "") or ""
                report.applied_patches.append(
                    ResourcePatchResult(
                        resource_type="agent",
                        resource_key=agent_key,
                        resource_name=agent_snap.resource_name,
                        display_name=new_disp_val,
                        field_mask_paths=mask_paths,
                        old_etag=agent_snap.etag,
                        new_etag=new_etag,
                        latency_ms=dt_ms,
                        payload_bytes=payload_bytes,
                        operation="UPDATE",
                        dag_level=dag_lvl,
                    )
                )
                seeded_agent_snaps[agent_key] = AgentStateSnapshot(
                    resource_name=agent_snap.resource_name,
                    display_name=new_disp_val,
                    etag=new_etag,
                    instruction=new_inst_val,
                    callbacks=new_cbs_map,
                    description=new_desc_val,
                    tools=new_tools_list,
                    child_agents=new_children_list,
                    guardrails=new_grs_list,
                )

            # ── Final DAG Level: Root App Config (when app.json changed) ─────
            if needs_app_cfg_patch:
                _execute_app_patch(dag_lvl=max_agent_dag_level + 1)

            report.rpc_ms = (time.perf_counter() - t_rpc_total0) * 1000.0

            if post_patch_validator is not None:
                post_patch_validator(report)

        except Exception as exc:
            report.rpc_ms = (time.perf_counter() - t_rpc_total0) * 1000.0

            # If CES rejected an optimistic etag mismatch before any mutation
            if (
                not report.applied_patches
                and not force_etag
                and current_target_meta is not None
                and current_target_meta[3]
                and _is_etag_conflict_exception(exc)
            ):
                rtype, rname, rdisp, exp_etag = current_target_meta
                remote_etag = "(changed on server)"
                try:
                    if rtype == "app":
                        remote_etag = (
                            getattr(self.apps_client.get_app(rname), "etag", "")
                            or remote_etag
                        )
                    elif rtype == "agent":
                        remote_etag = (
                            getattr(
                                self.agents_client.get_agent(rname), "etag", ""
                            )
                            or remote_etag
                        )
                    elif rtype == "tool":
                        remote_etag = (
                            getattr(
                                self.tools_client.get_tool(rname), "etag", ""
                            )
                            or remote_etag
                        )
                    elif rtype == "guardrail":
                        remote_etag = (
                            getattr(
                                self.guardrails_client.get_guardrail(rname),
                                "etag",
                                "",
                            )
                            or remote_etag
                        )
                except Exception:
                    pass
                report.status = "aborted"
                report.total_ms = (time.perf_counter() - t_start) * 1000.0
                raise EtagConflictError(
                    resource_name=rname,
                    display_name=rdisp,
                    expected_etag=exp_etag,
                    remote_etag=remote_etag,
                    report=report,
                ) from exc

            report.error_message = str(exc)
            if report.applied_patches:
                t_rb0 = time.perf_counter()
                report.rollback_actions = self._rollback_applied_patches(
                    report.applied_patches, snapshot
                )
                report.rollback_ms = (time.perf_counter() - t_rb0) * 1000.0
                report.status = "rolled_back"
            else:
                report.status = "aborted"
            report.total_ms = (time.perf_counter() - t_start) * 1000.0
            raise HotPatchApplyError(
                f"Hot-patch failed ({exc}). "
                + (
                    f"Atomically rolled back {len(report.rollback_actions)} "
                    "resource(s) in reverse topological order."
                    if report.rollback_actions
                    else "No remote resources were modified."
                ),
                report,
            ) from exc

        # ── Step 5: Persist Updated State Manifest & Snapshots ───────────────
        new_app_etag = seeded_app_etag
        new_agent_etags = dict(seeded_agent_etags)
        new_tool_etags = dict(seeded_tool_etags)
        new_guardrail_etags = dict(seeded_guardrail_etags)

        for patch_res in report.applied_patches:
            if patch_res.resource_type == "app":
                new_app_etag = patch_res.new_etag
            elif patch_res.resource_type == "agent":
                new_agent_etags[patch_res.resource_key] = {
                    "resource_name": patch_res.resource_name,
                    "display_name": patch_res.display_name,
                    "etag": patch_res.new_etag,
                }
            elif patch_res.resource_type == "tool":
                new_tool_etags[patch_res.resource_key] = {
                    "resource_name": patch_res.resource_name,
                    "display_name": patch_res.display_name,
                    "etag": patch_res.new_etag,
                }
            elif patch_res.resource_type == "guardrail":
                new_guardrail_etags[patch_res.resource_key] = {
                    "resource_name": patch_res.resource_name,
                    "display_name": patch_res.display_name,
                    "etag": patch_res.new_etag,
                }

        updated_manifest = StateManifest(
            app_name=self.app_name,
            app_display_name=seeded_app_display,
            app_etag=new_app_etag,
            global_instruction=seeded_global_inst,
            app_guardrails=seeded_app_guardrails,
            app_root_agent=seeded_app_root_agent,
            app_description=seeded_app_description,
            agent_etags=new_agent_etags,
            tool_etags=new_tool_etags,
            guardrail_etags=new_guardrail_etags,
            agent_snapshots=seeded_agent_snaps,
            tool_snapshots=seeded_tool_snaps,
            guardrail_snapshots=seeded_guardrail_snaps,
            files=current_files,
        )
        updated_manifest.save(self.app_root)

        report.status = "patched"
        report.total_ms = (time.perf_counter() - t_start) * 1000.0
        return report

    def _rollback_applied_patches(
        self,
        applied: list[ResourcePatchResult],
        snapshot: PrePatchSnapshot,
    ) -> list[RollbackAction]:
        """Reverts or deletes mutated resources in reverse topological order."""
        actions: list[RollbackAction] = []
        for patch_res in reversed(applied):
            t0 = time.perf_counter()
            rb_op = "DELETE" if patch_res.operation == "CREATE" else "REVERT"
            try:
                if patch_res.operation == "CREATE":
                    if patch_res.resource_type == "agent":
                        self.agents_client.client.delete_agent(
                            request=types.DeleteAgentRequest(
                                name=patch_res.resource_name,
                                force=True,
                            )
                        )
                    elif patch_res.resource_type == "guardrail":
                        self.guardrails_client.client.delete_guardrail(
                            request=types.DeleteGuardrailRequest(
                                name=patch_res.resource_name,
                                force=True,
                            )
                        )
                    elif patch_res.resource_type == "tool":
                        self.tools_client.client.delete_tool(
                            request=types.DeleteToolRequest(
                                name=patch_res.resource_name,
                                force=True,
                            )
                        )
                    actions.append(
                        RollbackAction(
                            resource_type=patch_res.resource_type,
                            resource_name=patch_res.resource_name,
                            display_name=patch_res.display_name,
                            field_mask_paths=[
                                f"DELETE ({patch_res.resource_type})"
                            ],
                            restored=True,
                            latency_ms=(time.perf_counter() - t0) * 1000.0,
                            operation="DELETE",
                        )
                    )
                    continue

                if patch_res.resource_type == "app":
                    revert_app = types.App(name=self.app_name)
                    if patch_res.new_etag:
                        revert_app.etag = patch_res.new_etag
                    for path in patch_res.field_mask_paths:
                        if (
                            path == "global_instruction"
                            and snapshot.global_instruction is not None
                        ):
                            revert_app.global_instruction = (
                                snapshot.global_instruction
                            )
                        elif (
                            path == "guardrails"
                            and snapshot.app_guardrails is not None
                        ):
                            revert_app.guardrails.extend(
                                snapshot.app_guardrails
                            )
                        elif (
                            path == "root_agent"
                            and snapshot.app_root_agent is not None
                        ):
                            revert_app.root_agent = snapshot.app_root_agent
                        elif (
                            path == "description"
                            and snapshot.app_description is not None
                        ):
                            revert_app.description = snapshot.app_description
                    req = types.UpdateAppRequest(
                        app=revert_app,
                        update_mask=field_mask_pb2.FieldMask(
                            paths=patch_res.field_mask_paths
                        ),
                    )
                    self.apps_client.client.update_app(request=req)

                elif patch_res.resource_type == "agent":
                    agent_snap = snapshot.agents[patch_res.resource_key]
                    revert_agent = types.Agent(name=agent_snap.resource_name)
                    if patch_res.new_etag:
                        revert_agent.etag = patch_res.new_etag
                    for path in patch_res.field_mask_paths:
                        if path == "instruction":
                            revert_agent.instruction = agent_snap.instruction
                        elif path in agent_snap.callbacks:
                            getattr(revert_agent, path).extend(
                                _clone_callbacks(agent_snap.callbacks[path])
                            )
                        elif path == "tools":
                            revert_agent.tools.extend(agent_snap.tools)
                        elif path == "child_agents":
                            revert_agent.child_agents.extend(
                                agent_snap.child_agents
                            )
                        elif path == "guardrails":
                            revert_agent.guardrails.extend(
                                agent_snap.guardrails
                            )
                        elif path == "description":
                            revert_agent.description = agent_snap.description
                        elif path == "display_name":
                            revert_agent.display_name = agent_snap.display_name
                    a_req = types.UpdateAgentRequest(
                        agent=revert_agent,
                        update_mask=field_mask_pb2.FieldMask(
                            paths=patch_res.field_mask_paths
                        ),
                    )
                    self.agents_client.client.update_agent(request=a_req)

                elif patch_res.resource_type == "tool":
                    tool_snap = snapshot.tools[patch_res.resource_key]
                    revert_tool = types.Tool(
                        name=tool_snap.resource_name,
                        python_function=types.PythonFunction(
                            name=tool_snap.function_name,
                            python_code=tool_snap.python_code,
                            description=tool_snap.description,
                        ),
                    )
                    if patch_res.new_etag:
                        revert_tool.etag = patch_res.new_etag
                    t_req = types.UpdateToolRequest(
                        tool=revert_tool,
                        update_mask=field_mask_pb2.FieldMask(
                            paths=patch_res.field_mask_paths
                        ),
                    )
                    self.tools_client.client.update_tool(request=t_req)

                elif patch_res.resource_type == "guardrail":
                    gr_snap = snapshot.guardrails[patch_res.resource_key]
                    revert_gr, _, _ = _build_guardrail_proto_from_dict(
                        gr_snap.guardrail_dict,
                        self.app_root,
                        patch_res.resource_key,
                        resource_name=gr_snap.resource_name,
                    )
                    if patch_res.new_etag:
                        revert_gr.etag = patch_res.new_etag
                    g_req = types.UpdateGuardrailRequest(
                        guardrail=revert_gr,
                        update_mask=field_mask_pb2.FieldMask(
                            paths=patch_res.field_mask_paths
                        ),
                    )
                    self.guardrails_client.client.update_guardrail(
                        request=g_req
                    )

                actions.append(
                    RollbackAction(
                        resource_type=patch_res.resource_type,
                        resource_name=patch_res.resource_name,
                        display_name=patch_res.display_name,
                        field_mask_paths=list(patch_res.field_mask_paths),
                        restored=True,
                        latency_ms=(time.perf_counter() - t0) * 1000.0,
                        operation=rb_op,
                    )
                )
            except Exception as rb_exc:
                actions.append(
                    RollbackAction(
                        resource_type=patch_res.resource_type,
                        resource_name=patch_res.resource_name,
                        display_name=patch_res.display_name,
                        field_mask_paths=list(patch_res.field_mask_paths),
                        restored=False,
                        latency_ms=(time.perf_counter() - t0) * 1000.0,
                        error=str(rb_exc),
                        operation=rb_op,
                    )
                )
        return actions
