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


"""CXAS Agent Linter — framework, configuration, discovery, and runner.

Rule-based linting engine for validating CXAS agent apps against best
practices and structural requirements.  Inspired by pylint/ruff: rules
are first-class objects with IDs, configurable severity, per-file
overrides, and decorator-based auto-registration.

Configuration lives in ``cxaslint.yaml``.
"""

import fnmatch
import json
import re
import typing
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

# ── Severity ─────────────────────────────────────────────────────────────


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    OFF = "off"

    @classmethod
    def from_str(cls, s: typing.Any) -> "Severity":
        # YAML parses bare ``off`` as boolean False
        if isinstance(s, bool):
            return cls.OFF if not s else cls.ERROR
        return cls(str(s).lower())


# ── Toolset Resolution ────────────────────────────────────────────────────


class ToolsetValidationBehavior(Enum):
    STRICT = "strict"  # Enforce strict operation-level checks (OpenAPI)
    BYPASS = "bypass"  # Bypass operation-level checks (MCP/Connector)


@dataclass
class ToolsetResolution:
    """Structured result of resolving a toolset offline."""

    behavior: ToolsetValidationBehavior
    tools: list[str] = field(default_factory=list)


# ── Lint Result ──────────────────────────────────────────────────────────


@dataclass
class LintResult:
    file: str
    rule_id: str
    severity: Severity
    message: str
    line: int | None = None
    fix_suggestion: str = ""

    def __str__(self) -> typing.Any:
        prefix = {"error": "E", "warning": "W", "info": "I"}[
            self.severity.value
        ]
        loc = self.file
        if self.line:
            loc += f":{self.line}"
        return f"  [{prefix}] {loc} [{self.rule_id}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity.value,
            "rule_id": self.rule_id,
            "message": self.message,
            "fix_suggestion": self.fix_suggestion,
        }


# ── Lint Report ──────────────────────────────────────────────────────────


@dataclass
class LintReport:
    results: list = field(default_factory=list)

    @property
    def errors(self) -> typing.Any:
        return [r for r in self.results if r.severity == Severity.ERROR]

    @property
    def warnings(self) -> typing.Any:
        return [r for r in self.results if r.severity == Severity.WARNING]

    def add(self, result: LintResult) -> None:
        self.results.append(result)

    def add_all(self, results: list) -> None:
        self.results.extend(results)

    def print_summary(self, show_fixes: typing.Any = False) -> None:
        if not self.results:
            print("\n  All checks passed.")
            return

        for r in sorted(self.results, key=lambda x: (x.severity.value, x.file)):
            print(str(r))
            if show_fixes and r.fix_suggestion:
                print(f"         Fix: {r.fix_suggestion}")

        info_count = len(self.results) - len(self.errors) - len(self.warnings)
        print(
            f"\n  {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s), "
            f"{info_count} info"
        )

    def to_json(self) -> str:
        return json.dumps([r.to_dict() for r in self.results], indent=2)

    def print_and_exit(
        self, json_output: bool = False, show_fixes: bool = False
    ) -> None:
        """Print results and exit with code 1 if errors, 0 otherwise."""
        import sys  # noqa: PLC0415

        if json_output:
            print(self.to_json())
        else:
            print("\n" + "=" * 60)
            print("LINT RESULTS")
            print("=" * 60)
            self.print_summary(show_fixes=show_fixes)

            if self.errors:
                print(f"\nLint FAILED with {len(self.errors)} error(s).")
            else:
                print("\nLint PASSED (no errors).")

        sys.exit(1 if self.errors else 0)


# ── Rule Base Class ──────────────────────────────────────────────────────


class Rule(ABC):
    """Base class for all lint rules.

    Each rule has:
    - id: unique identifier (e.g., "I001")
    - name: human-readable name
    - description: what the rule checks
    - default_severity: severity when not overridden by config
    - category: set by the @rule decorator
    - target: which file type this rule operates on (used by the
      ``structure`` category to dispatch rules to the right files).
      Values: ``"app_config"``, ``"instruction"``, ``"agent_config"``.
      Rules that don't set ``target`` receive the default files for
      their category.
    - models: optional list of model names/patterns this rule applies to.
      If None or empty, the rule is model-agnostic and applies to all models.
    """

    id: str = ""
    name: str = ""
    description: str = ""
    default_severity: Severity = Severity.WARNING
    category: str = ""
    target: str = ""
    models: list[str] | None = None

    def is_applicable_for_model(self, model: str | None) -> bool:
        """Check if this rule is applicable for the given model.

        If `models` is None or empty, the rule is model-agnostic and
        applies to all models.
        If `models` is specified, it returns True only if `model` is
        provided and exactly matches one of the specified models
        (case-insensitive).
        """
        if not self.models:
            return True
        if not model:
            return False
        model_lower = model.strip().lower()
        return any(
            target.strip().lower() == model_lower for target in self.models
        )

    @abstractmethod
    def check(
        self, file_path: Path, content: str, context: "LintContext"
    ) -> list[LintResult]:
        """Run this rule against a file. Returns list of LintResults."""
        ...

    def make_result(
        self,
        file: str,
        message: str,
        severity: Severity | None = None,
        line: int | None = None,
        fix: str = "",
    ) -> LintResult:
        return LintResult(
            file=file,
            rule_id=self.id,
            severity=severity or self.default_severity,
            message=message,
            line=line,
            fix_suggestion=fix,
        )


# ── Decorator-Based Auto-Registration ───────────────────────────────────

_RULE_REGISTRY: dict[str, list[Rule]] = defaultdict(list)
# Dedup key is (rule_id, category) so the same id may register under
# multiple categories (e.g. V100 across callbacks/tools/evals) while
# repeated imports of the same rule still no-op.
_REGISTERED_IDS: set[tuple[str, str]] = set()


def rule(
    category: str,
    models: list[str] | str | None = None,
) -> typing.Any:
    """Class decorator that auto-registers a Rule into its category.

    Duplicate ``(rule_id, category)`` pairs are silently ignored so
    that repeated imports (or test-time ``@rule`` usage) never produce
    duplicates. The same ``rule_id`` MAY register under different
    categories — useful for cross-surface rules.

    Usage::

        @rule("config")
        class InvalidJson(Rule):
            id = "A001"
            ...

        @rule("instructions", models=["gemini-composite-v1"])
        class CompositeSpecificRule(Rule):
            id = "C001"
            ...
    """

    def decorator(cls) -> typing.Any:  # noqa: ANN001
        cls.category = category
        if models is not None:
            cls.models = [models] if isinstance(models, str) else list(models)
        elif isinstance(getattr(cls, "models", None), str):
            cls.models = [cls.models]
        instance = cls()
        key = (instance.id, category)
        if key not in _REGISTERED_IDS:
            _REGISTERED_IDS.add(key)
            _RULE_REGISTRY[category].append(instance)
        return cls

    return decorator


def get_registered_rules() -> dict[str, list[Rule]]:
    """Return all auto-registered rules, keyed by category."""
    return dict(_RULE_REGISTRY)


def reset_registry() -> None:
    """Clear all registered rules.  Use in tests to get a clean state."""
    _RULE_REGISTRY.clear()
    _REGISTERED_IDS.clear()


# ── Lint Context ─────────────────────────────────────────────────────────


@dataclass
class LintContext:
    """Shared context passed to rules for cross-referencing."""

    project_root: Path
    app_dir: Path
    evals_dir: Path
    model: str | None = None
    agent_models: dict[str, str] = field(default_factory=dict)
    all_agent_names: set = field(default_factory=set)
    all_agent_display_names: set = field(default_factory=set)
    all_tool_names: set = field(default_factory=set)
    all_tool_dirs: dict = field(default_factory=dict)
    platform_tools: set = field(
        default_factory=lambda: {"end_session", "customize_response"}
    )
    options: dict = field(default_factory=dict)
    bypass_tool_prefixes: set = field(default_factory=set)
    app_root: Path | None = None
    env_file: Path | None = None
    agent_to_parents: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )

    @property
    def all_known_tools(self) -> set:
        return self.all_tool_names | self.platform_tools

    def get_model_for_agent(self, agent_name: str) -> str | None:
        """Get agent-configured model (or fallback to app-level model)."""
        return self.agent_models.get(agent_name, self.model)

    def get_model_for_file(self, file_path: Path) -> str | None:
        """Resolve the effective model for a specific file."""
        if self.app_root:
            try:
                rel = file_path.resolve().relative_to(self.app_root.resolve())
                parts = rel.parts
                if len(parts) >= 2 and parts[0] == "agents":
                    agent_name = parts[1]
                    return self.get_model_for_agent(agent_name)
            except ValueError:
                pass
        return self.model


VOICE_CHANNEL_TYPES = frozenset(
    {
        "GOOGLE_TELEPHONY_PLATFORM",
        "CONTACT_CENTER_AS_A_SERVICE",
        "CONTACT_CENTER_INTEGRATION",
        "VOICE_TELEPHONY",
        "TELEPHONY",
        "VOICE",
        "AUDIO",
        "CONTACT_CENTER",
        "TWILIO",
        "FIVE9",
    }
)


def is_voice_app(context: LintContext) -> bool:
    """Return True if deployment manifests or app config target a voice channel.

    When at least one ``deployments/*.yaml`` or ``deployments/*.yml`` manifest
    declares a channel (``channel`` or ``channelProfile.channelType``),
    deployment manifests are authoritative over ``app.json`` / ``app.yaml`` /
    ``app.yml`` ``audioProcessingConfig`` so digital builds that retain brand
    TTS settings are not misclassified as voice apps.
    """
    saw_deployment_channel = False
    seen_roots: set[Path] = set()
    for root in (context.app_root, context.app_dir, context.project_root):
        if root is None or root in seen_roots:
            continue
        seen_roots.add(root)
        deps_dir = root / "deployments"
        if not deps_dir.is_dir():
            continue
        dep_files = sorted({*deps_dir.glob("*.yaml"), *deps_dir.glob("*.yml")})
        for dep_file in dep_files:
            try:
                data = yaml.safe_load(dep_file.read_text("utf-8"))
            except (yaml.YAMLError, OSError, UnicodeDecodeError):
                continue
            if not isinstance(data, dict):
                continue

            candidates: list[object] = [data.get("channel")]
            for cp_key in ("channelProfile", "channel_profile"):
                cp = data.get(cp_key)
                if isinstance(cp, dict):
                    candidates.append(cp.get("channelType"))
                    candidates.append(cp.get("channel_type"))

            for raw in candidates:
                if isinstance(raw, str) and raw.strip():
                    saw_deployment_channel = True
                    if raw.strip().upper() in VOICE_CHANNEL_TYPES:
                        return True

    if saw_deployment_channel:
        return False

    app_root = context.app_root or context.app_dir
    if app_root is not None:
        app_json = app_root / "app.json"
        if app_json.is_file():
            try:
                data = json.loads(app_json.read_text("utf-8"))
                if isinstance(data, dict) and (
                    data.get("audioProcessingConfig")
                    or data.get("audio_processing_config")
                ):
                    return True
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass
        else:
            for yaml_name in ("app.yaml", "app.yml"):
                app_yaml = app_root / yaml_name
                if app_yaml.is_file():
                    try:
                        data = yaml.safe_load(app_yaml.read_text("utf-8"))
                        if isinstance(data, dict) and (
                            data.get("audioProcessingConfig")
                            or data.get("audio_processing_config")
                        ):
                            return True
                    except (yaml.YAMLError, OSError, UnicodeDecodeError):
                        pass
                    break

    return False


# ── Shared Config Loading, Local Resources & Agent Graph ────────────────

_CONFIG_EXTENSIONS: tuple[str, ...] = (".json", ".yaml", ".yml")
_APP_CONFIG_NAMES: tuple[str, ...] = ("app.json", "app.yaml", "app.yml")
_MAX_AGENT_CYCLES: int = 100
_CES_AGENT_REF_RE = re.compile(
    r"^(?:projects/[^/]+/locations/[^/]+/apps/[^/]+/)?agents/([^/]+)$"
)


def _load_config_dict(path: Path) -> dict[str, typing.Any] | None:
    """Load a JSON or YAML mapping from ``path``, returning None on error."""
    if path.is_symlink() or not path.is_file():
        return None
    suffix = path.suffix.lower()
    if suffix not in _CONFIG_EXTENSIONS:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if suffix == ".json" else yaml.safe_load(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class LocalResource:
    """A locally defined resource under ``toolsets/`` or ``guardrails/``."""

    key: str
    report_path: Path
    identifiers: frozenset[str]
    normalized_identifiers: frozenset[str]


def _find_dir_config(resource_dir: Path) -> Path | None:
    """Return ``<dir>.json``, ``<dir>.yaml``, or ``<dir>.yml`` if present."""
    for ext in _CONFIG_EXTENSIONS:
        candidate = resource_dir / f"{resource_dir.name}{ext}"
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def discover_local_resources(
    app_root: Path, subdir: str
) -> list[LocalResource]:
    """Discover directory and flat configs under ``app_root/<subdir>``."""
    parent = app_root / subdir
    if parent.is_symlink() or not parent.is_dir():
        return []

    resources: list[LocalResource] = []
    for entry in sorted(parent.iterdir()):
        if (
            entry.is_symlink()
            or entry.name.startswith(".")
            or entry.name == "__pycache__"
        ):
            continue

        cfg: dict[str, typing.Any] | None = None
        if entry.is_dir():
            key = entry.name
            cfg_path = _find_dir_config(entry)
            report_path = cfg_path or entry
            if cfg_path is not None:
                cfg = _load_config_dict(cfg_path)
        elif entry.is_file() and entry.suffix.lower() in _CONFIG_EXTENSIONS:
            key = entry.stem
            report_path = entry
            cfg = _load_config_dict(entry)
        else:
            continue

        ids: set[str] = {key, key.replace("_", " ")}
        if isinstance(cfg, dict):
            disp = cfg.get("displayName") or cfg.get("display_name")
            if isinstance(disp, str) and disp.strip():
                ids.add(disp.strip())
            cfg_name = cfg.get("name")
            if isinstance(cfg_name, str) and cfg_name.strip():
                clean_name = cfg_name.strip()
                ids.add(clean_name)
                ids.add(clean_name.rsplit("/", 1)[-1])

        norm_ids = {i.replace(" ", "_").lower() for i in ids}
        resources.append(
            LocalResource(
                key=key,
                report_path=report_path,
                identifiers=frozenset(ids),
                normalized_identifiers=frozenset(norm_ids),
            )
        )
    return resources


def extract_resource_ref_candidates(ref: str, subdir: str) -> set[str]:
    """Return raw and short-ID lookup candidates for a CES resource ref."""
    raw = ref.strip()
    if not raw:
        return set()
    cands = {raw}
    m = re.match(
        rf"^(?:projects/[^/]+/locations/[^/]+/apps/[^/]+/)?{re.escape(subdir)}/([^/]+)$",
        raw,
    )
    if m:
        cands.add(m.group(1))
    return cands


def match_local_resources(
    ref: str,
    subdir: str,
    resources: list[LocalResource],
) -> list[LocalResource]:
    """Match a reference string against discovered ``LocalResource`` entries."""
    cands = extract_resource_ref_candidates(ref, subdir)
    if not cands:
        return []
    spaced_cands = {c.replace(" ", "_") for c in cands}
    exact = [
        res
        for res in resources
        if (cands & res.identifiers) or (spaced_cands & res.identifiers)
    ]
    if exact:
        return exact
    norm_cands = {c.replace(" ", "_").lower() for c in cands}
    return [res for res in resources if norm_cands & res.normalized_identifiers]


@dataclass
class AgentGraph:
    """Resolved agent hierarchy graph for a CES app."""

    app_config: dict[str, typing.Any] | None = None
    app_config_path: Path | None = None
    root_agent_ref: str | None = None
    root_agent_dir: str | None = None
    agent_dirs: dict[str, Path] = field(default_factory=dict)
    agent_configs: dict[str, dict[str, typing.Any]] = field(
        default_factory=dict
    )
    agent_config_paths: dict[str, Path] = field(default_factory=dict)
    agent_display_names: dict[str, str] = field(default_factory=dict)
    adjacency: dict[str, list[str]] = field(default_factory=dict)
    reachable: set[str] = field(default_factory=set)
    cycles: list[list[str]] = field(default_factory=list)


def _build_agent_ref_resolver(
    agent_names: set[str],
    agent_configs: dict[str, dict[str, typing.Any]],
) -> typing.Callable[[typing.Any], str | None]:
    """Build a deterministic resolver from agent ref strings to dir names."""
    sorted_names = sorted(agent_names)
    name_set = set(sorted_names)
    exact_name_map: dict[str, str] = {}
    short_name_map: dict[str, str] = {}
    exact_display_map: dict[str, str] = {}
    norm_dir_map: dict[str, str] = {}
    norm_name_map: dict[str, str] = {}
    norm_display_map: dict[str, str] = {}

    for dir_name in sorted_names:
        norm_dir_map.setdefault(dir_name.replace(" ", "_").lower(), dir_name)
        cfg = agent_configs.get(dir_name)
        if not isinstance(cfg, dict):
            continue
        cfg_name = cfg.get("name")
        if isinstance(cfg_name, str) and cfg_name.strip():
            clean_name = cfg_name.strip()
            short_id = clean_name.rsplit("/", 1)[-1]
            exact_name_map.setdefault(clean_name, dir_name)
            short_name_map.setdefault(short_id, dir_name)
            norm_name_map.setdefault(
                clean_name.replace(" ", "_").lower(), dir_name
            )
            norm_name_map.setdefault(
                short_id.replace(" ", "_").lower(), dir_name
            )
        disp = cfg.get("displayName") or cfg.get("display_name")
        if isinstance(disp, str) and disp.strip():
            clean_disp = disp.strip()
            exact_display_map.setdefault(clean_disp, dir_name)
            norm_display_map.setdefault(
                clean_disp.replace(" ", "_").lower(), dir_name
            )

    def _resolve(ref: typing.Any) -> str | None:
        if not isinstance(ref, str) or not ref.strip():
            return None
        raw = ref.strip()
        if raw in name_set:
            return raw
        underscored = raw.replace(" ", "_")
        if underscored in name_set:
            return underscored
        if raw in exact_name_map:
            return exact_name_map[raw]
        if raw in exact_display_map:
            return exact_display_map[raw]

        candidates = [raw]
        m = _CES_AGENT_REF_RE.match(raw)
        if m:
            short_ref = m.group(1)
            if short_ref in name_set:
                return short_ref
            short_u = short_ref.replace(" ", "_")
            if short_u in name_set:
                return short_u
            if short_ref in short_name_map:
                return short_name_map[short_ref]
            if short_ref in exact_display_map:
                return exact_display_map[short_ref]
            candidates.append(short_ref)

        if raw in short_name_map:
            return short_name_map[raw]

        for cand in candidates:
            norm = cand.replace(" ", "_").lower()
            for lookup in (norm_dir_map, norm_name_map, norm_display_map):
                if norm in lookup:
                    return lookup[norm]
        return None

    return _resolve


def _tarjan_sccs(
    nodes: list[str], adj: dict[str, list[str]]
) -> list[list[str]]:
    """Return strongly connected components via iterative Tarjan's algorithm."""
    index_counter = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    tarjan_stack: list[str] = []
    sccs: list[list[str]] = []

    for start in nodes:
        if start in indices:
            continue
        indices[start] = lowlink[start] = index_counter
        index_counter += 1
        tarjan_stack.append(start)
        on_stack.add(start)
        call_stack: list[list[typing.Any]] = [[start, 0]]

        while call_stack:
            frame = call_stack[-1]
            v: str = frame[0]
            idx: int = frame[1]
            nbrs = adj.get(v, [])

            if idx < len(nbrs):
                frame[1] = idx + 1
                w = nbrs[idx]
                if w not in indices:
                    indices[w] = lowlink[w] = index_counter
                    index_counter += 1
                    tarjan_stack.append(w)
                    on_stack.add(w)
                    call_stack.append([w, 0])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])
            else:
                call_stack.pop()
                if call_stack:
                    parent: str = call_stack[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
                if lowlink[v] == indices[v]:
                    comp: list[str] = []
                    while True:
                        member = tarjan_stack.pop()
                        on_stack.remove(member)
                        comp.append(member)
                        if member == v:
                            break
                    sccs.append(sorted(comp))

    return sccs


def _unblock_johnson(
    node: str,
    blocked: set[str],
    blocked_map: dict[str, set[str]],
) -> None:
    """Iteratively unblock vertices in Johnson's elementary cycle search."""
    work = [node]
    while work:
        cur = work.pop()
        if cur in blocked:
            blocked.remove(cur)
            work.extend(blocked_map.pop(cur, ()))


def _find_elementary_cycles(
    adjacency: dict[str, list[str]],
    root_agent_dir: str | None = None,
    max_cycles: int = _MAX_AGENT_CYCLES,
) -> list[list[str]]:
    """Find deduplicated, canonicalized elementary cycles in ``adjacency``."""
    nodes = sorted(adjacency.keys())
    cycles: list[list[str]] = []

    # 1. Self-loops (u -> u)
    for u in nodes:
        if u in adjacency.get(u, ()):
            cycles.append([u, u])
            if len(cycles) >= max_cycles:
                break

    # 2. Multi-node cycles via iterative Johnson's algorithm
    no_self_adj: dict[str, list[str]] = {
        u: [v for v in adjacency.get(u, ()) if v != u and v in adjacency]
        for u in nodes
    }
    top_sccs = [
        c
        for c in _tarjan_sccs(nodes, no_self_adj)
        if len(c) >= 2  # noqa: PLR2004
    ]
    for scc in top_sccs:
        for start_idx, start in enumerate(scc):
            if len(cycles) >= max_cycles:
                break
            tail_nodes = scc[start_idx:]
            tail_set = set(tail_nodes)
            tail_adj = {
                u: [v for v in no_self_adj[u] if v in tail_set]
                for u in tail_nodes
            }
            start_scc: set[str] | None = None
            for comp in _tarjan_sccs(tail_nodes, tail_adj):
                if len(comp) >= 2 and start in comp:  # noqa: PLR2004
                    start_scc = set(comp)
                    break
            if not start_scc:
                continue

            sub_adj = {
                u: [v for v in tail_adj[u] if v in start_scc] for u in start_scc
            }
            blocked: set[str] = {start}
            blocked_map: dict[str, set[str]] = defaultdict(set)
            path: list[str] = [start]
            stack: list[list[typing.Any]] = [[start, 0, False]]

            while stack and len(cycles) < max_cycles:
                frame = stack[-1]
                v: str = frame[0]
                idx: int = frame[1]
                nbrs = sub_adj[v]

                if idx < len(nbrs):
                    frame[1] = idx + 1
                    w = nbrs[idx]
                    if w == start:
                        cycles.append([*path, start])
                        frame[2] = True
                    elif w not in blocked:
                        blocked.add(w)
                        path.append(w)
                        stack.append([w, 0, False])
                else:
                    _, _, found = stack.pop()
                    path.pop()
                    if found:
                        _unblock_johnson(v, blocked, blocked_map)
                        if stack:
                            stack[-1][2] = True
                    else:
                        for w in nbrs:
                            blocked_map[w].add(v)

    canonical: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for cyc in cycles:
        core = cyc[:-1]
        if root_agent_dir and root_agent_dir in core:
            pos = core.index(root_agent_dir)
            core = core[pos:] + core[:pos]
        elif len(core) > 1:
            min_val = min(core)
            pos = core.index(min_val)
            core = core[pos:] + core[:pos]
        full = [*core, core[0]]
        key = tuple(full)
        if key not in seen:
            seen.add(key)
            canonical.append(full)

    canonical.sort()
    return canonical


def resolve_agent_graph(app_root: Path) -> AgentGraph:
    """Resolve app config, agent directories, reachability, and cycles."""
    app_config_path: Path | None = None
    app_config: dict[str, typing.Any] | None = None
    for name in _APP_CONFIG_NAMES:
        candidate = app_root / name
        if candidate.is_file() and not candidate.is_symlink():
            app_config_path = candidate
            app_config = _load_config_dict(candidate)
            break

    agent_dirs: dict[str, Path] = {}
    agent_configs: dict[str, dict[str, typing.Any]] = {}
    agent_config_paths: dict[str, Path] = {}
    agent_display_names: dict[str, str] = {}

    agents_dir = app_root / "agents"
    if agents_dir.is_dir() and not agents_dir.is_symlink():
        for entry in sorted(agents_dir.iterdir()):
            if (
                entry.is_symlink()
                or not entry.is_dir()
                or entry.name.startswith(".")
                or entry.name == "__pycache__"
            ):
                continue
            agent_dirs[entry.name] = entry
            cfg_path = _find_dir_config(entry)
            if cfg_path is not None:
                agent_config_paths[entry.name] = cfg_path
                cfg = _load_config_dict(cfg_path)
                if cfg is not None:
                    agent_configs[entry.name] = cfg
                    disp = cfg.get("displayName") or cfg.get("display_name")
                    if isinstance(disp, str) and disp.strip():
                        agent_display_names[entry.name] = disp.strip()

    resolve_ref = _build_agent_ref_resolver(
        set(agent_dirs.keys()), agent_configs
    )

    root_agent_ref: str | None = None
    root_agent_dir: str | None = None
    if isinstance(app_config, dict):
        raw_root = app_config.get("rootAgent")
        if isinstance(raw_root, str) and raw_root.strip():
            root_agent_ref = raw_root.strip()
            root_agent_dir = resolve_ref(root_agent_ref)

    adjacency: dict[str, list[str]] = {name: [] for name in sorted(agent_dirs)}
    for agent_name in sorted(agent_dirs):
        cfg = agent_configs.get(agent_name)
        if not isinstance(cfg, dict):
            continue
        raw_children = (
            cfg.get("childAgents")
            if "childAgents" in cfg
            else cfg.get("child_agents")
        )
        if not isinstance(raw_children, list):
            continue
        seen_children: set[str] = set()
        for child_ref in raw_children:
            resolved = resolve_ref(child_ref)
            if resolved is not None and resolved not in seen_children:
                seen_children.add(resolved)
                adjacency[agent_name].append(resolved)

    reachable: set[str] = set()
    if root_agent_dir is not None:
        queue: deque[str] = deque([root_agent_dir])
        reachable.add(root_agent_dir)
        while queue:
            curr = queue.popleft()
            for nxt in adjacency.get(curr, ()):
                if nxt not in reachable:
                    reachable.add(nxt)
                    queue.append(nxt)

    cycles = _find_elementary_cycles(adjacency, root_agent_dir=root_agent_dir)

    return AgentGraph(
        app_config=app_config,
        app_config_path=app_config_path,
        root_agent_ref=root_agent_ref,
        root_agent_dir=root_agent_dir,
        agent_dirs=agent_dirs,
        agent_configs=agent_configs,
        agent_config_paths=agent_config_paths,
        agent_display_names=agent_display_names,
        adjacency=adjacency,
        reachable=reachable,
        cycles=cycles,
    )


def collect_attached_guardrails(
    graph: AgentGraph,
    resources: list[LocalResource],
) -> tuple[set[LocalResource], dict[LocalResource, set[str]]]:
    """Return ``(app_referenced, agent_refs_by_resource)`` for guardrails."""
    app_referenced: set[LocalResource] = set()
    agent_refs: dict[LocalResource, set[str]] = defaultdict(set)

    if isinstance(graph.app_config, dict):
        raw_app_grs = graph.app_config.get("guardrails")
        if isinstance(raw_app_grs, list):
            for entry in raw_app_grs:
                gr_ref = (
                    entry
                    if isinstance(entry, str)
                    else (
                        entry.get("guardrail")
                        if isinstance(entry, dict)
                        else None
                    )
                )
                if isinstance(gr_ref, str) and gr_ref.strip():
                    for matched in match_local_resources(
                        gr_ref, "guardrails", resources
                    ):
                        app_referenced.add(matched)

    for agent_name, cfg in sorted(graph.agent_configs.items()):
        raw_grs = cfg.get("guardrails")
        if not isinstance(raw_grs, list):
            continue
        for entry in raw_grs:
            gr_ref = (
                entry
                if isinstance(entry, str)
                else (
                    entry.get("guardrail") if isinstance(entry, dict) else None
                )
            )
            if isinstance(gr_ref, str) and gr_ref.strip():
                for matched in match_local_resources(
                    gr_ref, "guardrails", resources
                ):
                    agent_refs[matched].add(agent_name)

    return app_referenced, agent_refs


# ── Rule Registry ────────────────────────────────────────────────────────


class RuleRegistry:
    """Holds all registered rules and applies config overrides.

    Internally keyed by ``(rule_id, category)`` so the same id may
    appear under multiple categories. ``get(rule_id)`` returns the
    first match by id for back-compat with callers that expect
    unique ids (V001-V007 single-resource validation).
    """

    def __init__(self) -> None:
        self._rules: dict[tuple[str, str], Rule] = {}

    def register(self, rule_obj: Rule) -> None:
        self._rules[(rule_obj.id, rule_obj.category)] = rule_obj

    def register_all(self, rules: list[Rule]) -> None:
        for r in rules:
            self.register(r)

    def get(self, rule_id: str) -> Rule | None:
        for (rid, _cat), r in self._rules.items():
            if rid == rule_id:
                return r
        return None

    def all_rules(self) -> list[Rule]:
        return sorted(self._rules.values(), key=lambda r: (r.id, r.category))

    def rules_for_category(self, category: str) -> list[Rule]:
        return [r for r in self.all_rules() if r.category == category]

    def rules_for_model(
        self, model: str, model_only: bool = False
    ) -> list[Rule]:
        """Return rules applicable to a specific model.

        If `model_only` is True, returns only rules that are specific to
        that model (i.e. rules with non-empty `models` that match).
        If `model_only` is False, returns all rules applicable to that model
        (including model-agnostic rules).
        """
        if model_only:
            return [
                r
                for r in self.all_rules()
                if r.models and r.is_applicable_for_model(model)
            ]
        return [r for r in self.all_rules() if r.is_applicable_for_model(model)]

    def list_rules(
        self, model: str | None = None, model_only: bool = False
    ) -> None:
        """Print all registered rules, optionally filtered by model."""
        current_cat = ""
        if model:
            rules = self.rules_for_model(model, model_only=model_only)
        elif model_only:
            rules = [r for r in self.all_rules() if r.models]
        else:
            rules = self.all_rules()

        for r in rules:
            if r.category != current_cat:
                current_cat = r.category
                print(f"\n  {current_cat.upper()}")
            sev = r.default_severity.value.upper()
            model_info = f" [models: {', '.join(r.models)}]" if r.models else ""
            print(
                f"    {r.id}  [{sev:7s}]{model_info}  {r.name}: {r.description}"
            )


# ── Configuration ────────────────────────────────────────────────────────


@dataclass
class LintConfig:
    """Linter configuration loaded from ``cxaslint.yaml``."""

    app_dir: str = "."
    evals_dir: str = "evals/"
    model: str | None = None
    rules: dict[str, Severity] = field(default_factory=dict)
    options: dict[str, dict] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=list)
    per_file: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, project_root: Path) -> "LintConfig":
        config = cls()

        lint_config_path = project_root / "cxaslint.yaml"
        if lint_config_path.exists():
            with open(lint_config_path) as f:
                data = yaml.safe_load(f) or {}

            config.app_dir = data.get("app_dir", config.app_dir)
            config.evals_dir = data.get("evals_dir", config.evals_dir)
            config.model = data.get("model", config.model)

            for rule_id, severity_str in (data.get("rules") or {}).items():
                config.rules[rule_id] = Severity.from_str(severity_str)

            config.options = data.get("options") or {}
            config.ignore = data.get("ignore") or []
            config.per_file = data.get("per_file") or {}

        # Fall back to gecx-config.json for app_dir if not set by cxaslint.yaml
        if config.app_dir == ".":
            gecx_config_path = project_root / "gecx-config.json"
            if gecx_config_path.exists():
                with open(gecx_config_path) as f:
                    gecx = json.load(f)
                config.app_dir = gecx.get("app_dir", config.app_dir)

        return config

    def get_severity(self, rule_obj: Rule, file_path: str = "") -> Severity:
        """Get the effective severity for a rule.

        Considers per-file overrides.
        """
        for pattern, overrides in self.per_file.items():
            if fnmatch.fnmatch(file_path, pattern):  # noqa: SIM102
                if rule_obj.id in overrides:
                    return Severity.from_str(overrides[rule_obj.id])

        if rule_obj.id in self.rules:
            return self.rules[rule_obj.id]

        return rule_obj.default_severity

    def is_ignored(self, file_path: str) -> bool:
        """Check if a file matches any ignore pattern."""
        return any(fnmatch.fnmatch(file_path, p) for p in self.ignore)

    def get_options(self, rule_id: str) -> dict:
        """Get rule-specific options."""
        return self.options.get(rule_id, {})


# ── Discovery ────────────────────────────────────────────────────────────


class Discovery:
    """Discovers agents, tools, callbacks, evals, and configs.

    Scans an app directory for all resources.
    """

    def __init__(
        self,
        app_dir: Path,
        evals_dir: Path,
        limit_agents: set[str] | None = None,
        limit_tools: set[str] | None = None,
    ) -> None:
        self.app_dir = app_dir
        self.evals_dir = evals_dir
        self.limit_agents = limit_agents
        self.limit_tools = limit_tools
        self.app_root = self._find_app_root()

    def _find_app_root(self) -> Path | None:
        """Find the actual app root directory.

        Handles two layouts:
        - Direct: ``app_dir`` itself contains ``app.{json,yaml,yml}``
        - Nested: ``app_dir/<name>/`` contains the app (gecx-skills convention)
        """
        if not self.app_dir.exists():
            return None
        if any((self.app_dir / name).exists() for name in _APP_CONFIG_NAMES):
            return self.app_dir
        for d in sorted(self.app_dir.iterdir()):
            if (
                not d.is_symlink()
                and d.is_dir()
                and not d.name.startswith(".")
                and any((d / name).exists() for name in _APP_CONFIG_NAMES)
            ):
                return d
        return None

    def discover_global_instruction(self) -> Path | None:
        """Return path to ``global_instruction.txt`` if it exists."""
        if not self.app_root:
            return None
        p = self.app_root / "global_instruction.txt"
        return p if p.exists() else None

    def discover_agents(self) -> dict[str, Path]:
        """Return ``{dir_name: instruction_or_config_path}`` for all agents."""
        if not self.app_root:
            return {}
        agents_dir = self.app_root / "agents"
        if not agents_dir.exists() or agents_dir.is_symlink():
            return {}
        result = {}
        for d in sorted(agents_dir.iterdir()):
            if (
                d.is_symlink()
                or not d.is_dir()
                or d.name.startswith(".")
                or d.name == "__pycache__"
            ):
                continue
            if (
                self.limit_agents is not None
                and d.name not in self.limit_agents
            ):
                continue
            inst = d / "instruction.txt"
            if inst.exists():
                result[d.name] = inst
            else:
                cfg_file = _find_dir_config(d)
                if cfg_file is not None:
                    result[d.name] = cfg_file
        return result

    def discover_tools(self) -> dict[str, Path]:
        """Return ``{tool_name: code_path}`` for all tools."""
        if not self.app_root:
            return {}
        tools_dir = self.app_root / "tools"
        if not tools_dir.exists():
            return {}
        result = {}
        for d in sorted(tools_dir.iterdir()):
            if d.is_dir():
                if (
                    self.limit_tools is not None
                    and d.name not in self.limit_tools
                ):
                    continue
                code = d / "python_function" / "python_code.py"
                if code.exists():
                    result[d.name] = code
                else:
                    config_files = (
                        sorted(d.glob("*.json"))
                        or sorted(d.glob("*.yaml"))
                        or sorted(d.glob("*.yml"))
                    )
                    if config_files:
                        result[d.name] = config_files[0]
        return result

    def discover_callbacks(self) -> list[tuple[str, str, str, Path]]:
        """Return ``[(agent_name, cb_type, cb_name, code_path), ...]``."""
        if not self.app_root:
            return []
        agents_dir = self.app_root / "agents"
        if not agents_dir.exists():
            return []
        result = []
        seen_paths: set[Path] = set()
        cb_types = [
            "before_model_callbacks",
            "after_model_callbacks",
            "before_agent_callbacks",
            "after_agent_callbacks",
            "before_tool_callbacks",
            "after_tool_callbacks",
        ]
        for agent_dir in sorted(agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            if (
                self.limit_agents is not None
                and agent_dir.name not in self.limit_agents
            ):
                continue
            for cb_type in cb_types:
                cb_dir = agent_dir / cb_type
                if not cb_dir.exists():
                    continue
                for cb in sorted(cb_dir.iterdir()):
                    code = cb / "python_code.py"
                    if code.exists() and code not in seen_paths:
                        seen_paths.add(code)
                        result.append((agent_dir.name, cb_type, cb.name, code))
            for d in sorted(agent_dir.iterdir()):
                if not d.is_dir():
                    continue
                for cb_type in cb_types:
                    if d.name.startswith(f"{cb_type}_"):
                        code = d / "python_code.py"
                        if code.exists() and code not in seen_paths:
                            seen_paths.add(code)
                            result.append(
                                (agent_dir.name, cb_type, d.name, code)
                            )
                        break
        return result

    def discover_evals(self) -> dict[str, Path]:
        """Return ``{filename: path}`` for all eval YAMLs."""
        result = {}
        if not self.evals_dir.exists():
            return result
        for yaml_path in sorted(self.evals_dir.rglob("*.yaml")):
            rel = str(yaml_path.relative_to(self.evals_dir))
            result[rel] = yaml_path
        return result

    def discover_app_config(self) -> Path | None:
        """Return path to ``app.json``, ``app.yaml``, or ``app.yml``."""
        if not self.app_root:
            return None
        for name in _APP_CONFIG_NAMES:
            p = self.app_root / name
            if p.is_file() and not p.is_symlink():
                return p
        return None

    def discover_app_model(self) -> str | None:
        """Return model specified in app config under ``modelSettings``."""
        app_cfg = self.discover_app_config()
        if not app_cfg:
            return None
        data = _load_config_dict(app_cfg)
        if not data:
            return None
        model_settings = (
            data.get("modelSettings") or data.get("model_settings") or {}
        )
        if isinstance(model_settings, dict):
            return model_settings.get("model")
        return None

    def discover_agent_configs(self) -> dict[str, Path]:
        """Return ``{agent_name: config_path}`` for all agent configs."""
        if not self.app_root:
            return {}
        agents_dir = self.app_root / "agents"
        if not agents_dir.exists() or agents_dir.is_symlink():
            return {}
        result = {}
        for d in sorted(agents_dir.iterdir()):
            if (
                d.is_symlink()
                or not d.is_dir()
                or d.name.startswith(".")
                or d.name == "__pycache__"
            ):
                continue
            if (
                self.limit_agents is not None
                and d.name not in self.limit_agents
            ):
                continue
            cfg_file = _find_dir_config(d)
            if cfg_file is not None:
                result[d.name] = cfg_file
        return result

    def discover_agent_models(self) -> dict[str, str]:
        """Return ``{agent_name: model}`` for agents with ``modelSettings``."""
        configs = self.discover_agent_configs()
        result = {}
        for name, path in configs.items():
            data = _load_config_dict(path)
            if not data:
                continue
            model_settings = (
                data.get("modelSettings") or data.get("model_settings") or {}
            )
            if isinstance(model_settings, dict) and model_settings.get("model"):
                result[name] = model_settings["model"]
        return result

    def _discover_resource_dirs(self, subdir: str) -> dict[str, Path]:
        """Return ``{name: dir_path}`` for subdirs.

        Scans ``app_root/<subdir>/``.
        """
        if not self.app_root:
            return {}
        parent = self.app_root / subdir
        if not parent.exists():
            return {}
        return {d.name: d for d in sorted(parent.iterdir()) if d.is_dir()}

    def discover_toolsets(self) -> dict[str, Path]:
        return self._discover_resource_dirs("toolsets")

    def discover_guardrails(self) -> dict[str, Path]:
        return self._discover_resource_dirs("guardrails")

    def discover_evaluations(self) -> dict[str, Path]:
        return self._discover_resource_dirs("evaluations")

    def discover_evaluation_expectations(self) -> dict[str, Path]:
        return self._discover_resource_dirs("evaluation_expectations")

    def dir_name_to_display(self, dir_name: str) -> str:
        """Convert directory name to display name."""
        return dir_name.replace("_", " ")


# Maps CLI per-resource flags to their schema rule IDs.
SINGLE_RESOURCE_RULES = {
    "agent": "V002",
    "tool": "V003",
    "toolset": "V004",
    "guardrail": "V005",
    "evaluation": "V006",
    "evaluation_expectations": "V007",
}


# ── Runner ───────────────────────────────────────────────────────────────


def build_registry() -> RuleRegistry:
    """Build the rule registry by importing all rule modules.

    Importing ``cxas_scrapi.utils.lint_rules`` triggers the ``@rule``
    decorator on every rule class, populating ``_RULE_REGISTRY``.  We
    then copy those into a ``RuleRegistry`` for config-aware lookups.
    """
    import cxas_scrapi.utils.lint_rules  # noqa: F401,PLC0415

    registry = RuleRegistry()
    for _category, rules in get_registered_rules().items():
        registry.register_all(rules)
    return registry


def get_toolset_tools(
    app_root: Path,
    toolset_name: str,
    allowed_tool_ids: list[str] | None = None,
) -> ToolsetResolution:
    """Parses toolset config and resolves its validation behavior and tools.

    Returns a ToolsetResolution object.
    """
    # If allowed_tool_ids is explicitly empty, it means NO tools are assigned.
    # We enforce STRICT validation with an empty tools list (no tools allowed).
    if allowed_tool_ids is not None and not allowed_tool_ids:
        return ToolsetResolution(behavior=ToolsetValidationBehavior.STRICT)

    toolset_dir = app_root / "toolsets" / toolset_name
    json_file = toolset_dir / f"{toolset_name}.json"
    if not json_file.exists():
        return ToolsetResolution(behavior=ToolsetValidationBehavior.STRICT)

    try:
        config = json.loads(json_file.read_text())
    except Exception:
        return ToolsetResolution(behavior=ToolsetValidationBehavior.STRICT)

    # Handle OpenAPI toolset strictly
    if "openApiToolset" in config or "open_api_toolset" in config:
        schema_file = toolset_dir / "open_api_toolset" / "open_api_schema.yaml"
        if not schema_file.exists():
            return ToolsetResolution(behavior=ToolsetValidationBehavior.STRICT)

        http_methods = {
            "get",
            "post",
            "put",
            "delete",
            "patch",
            "options",
            "head",
            "trace",
        }
        tools = []
        try:
            schema = yaml.safe_load(schema_file.read_text()) or {}
            for path_str, methods in (schema.get("paths") or {}).items():
                if not isinstance(methods, dict):
                    continue
                for method_name, details in methods.items():
                    if not isinstance(details, dict):
                        continue
                    op_id = details.get("operationId")
                    if not op_id and str(method_name).lower() in http_methods:
                        sanitized = re.sub(
                            r"[^A-Za-z0-9_]",
                            "_",
                            str(path_str).strip("/"),
                        )
                        op_id = f"{str(method_name).upper()}_{sanitized}"
                    if op_id:
                        if allowed_tool_ids and op_id not in allowed_tool_ids:
                            continue
                        tools.append(f"{toolset_name}_{op_id}")
        except Exception:
            pass
        return ToolsetResolution(
            behavior=ToolsetValidationBehavior.STRICT, tools=tools
        )

    # Default for all other toolsets (MCP, Connector, etc.)
    # where tools cannot be resolved offline.
    # We return BYPASS to signal that this prefix should bypass
    # strict operation checks.
    return ToolsetResolution(behavior=ToolsetValidationBehavior.BYPASS)


def build_context(
    project_root: Path,
    config: LintConfig,
    discovery: Discovery,
    model_override: str | None = None,
    env_file: Path | None = None,
) -> LintContext:
    """Build the shared lint context from discovered app resources."""
    agents = discovery.discover_agents()
    tools = discovery.discover_tools()
    toolsets = discovery.discover_toolsets()

    all_tool_names = set(tools.keys())
    bypass_tool_prefixes = set()
    app_root = discovery.app_root

    if app_root:
        for ts_name in toolsets:
            res = get_toolset_tools(app_root, ts_name)
            if res.behavior == ToolsetValidationBehavior.BYPASS:
                # MCP/Connector toolset -> bypass validation of operation suffix
                bypass_tool_prefixes.add(f"{ts_name}_")
            elif res.tools:
                all_tool_names.update(res.tools)

    # Discover agent configs to build parent-child map
    agent_configs = discovery.discover_agent_configs()
    loaded_agent_configs: dict[str, dict[str, typing.Any]] = {}
    for agent_name, config_path in agent_configs.items():
        loaded = _load_config_dict(config_path)
        if loaded is not None:
            loaded_agent_configs[agent_name] = loaded

    known_agent_names = set(agents.keys()) | set(agent_configs.keys())
    resolve_agent_ref = _build_agent_ref_resolver(
        known_agent_names, loaded_agent_configs
    )
    agent_to_parents: dict[str, set[str]] = defaultdict(set)

    for parent_name, agent_config in loaded_agent_configs.items():
        child_agents = (
            agent_config.get("childAgents")
            if "childAgents" in agent_config
            else agent_config.get("child_agents")
        )
        if not isinstance(child_agents, list):
            continue
        for child_ref in child_agents:
            child_name = resolve_agent_ref(child_ref)
            if child_name:
                agent_to_parents[child_name].add(parent_name)

    all_agent_display_names = {
        discovery.dir_name_to_display(name) for name in known_agent_names
    }
    for agent_config in loaded_agent_configs.values():
        disp = agent_config.get("displayName") or agent_config.get(
            "display_name"
        )
        if isinstance(disp, str) and disp.strip():
            all_agent_display_names.add(disp.strip())

    resolved_model = (
        model_override or config.model or discovery.discover_app_model()
    )
    agent_models = discovery.discover_agent_models()

    return LintContext(
        project_root=project_root,
        app_dir=discovery.app_dir,
        evals_dir=project_root / config.evals_dir,
        model=resolved_model,
        agent_models=agent_models,
        all_agent_names=known_agent_names,
        all_agent_display_names=all_agent_display_names,
        all_tool_names=all_tool_names,
        all_tool_dirs={name: path.parent for name, path in tools.items()},
        options=config.options,
        bypass_tool_prefixes=bypass_tool_prefixes,
        app_root=app_root,
        env_file=env_file,
        agent_to_parents=agent_to_parents,
    )


def run_rules(
    registry: RuleRegistry,
    config: LintConfig,
    context: LintContext,
    discovery: Discovery,
    report: LintReport,
    categories: list[str] | None = None,
    specific_rules: set[str] | None = None,
    model_only: bool = False,
) -> None:
    """Run lint rules against discovered files."""

    def should_run(rule_obj: typing.Any) -> bool:
        if specific_rules and rule_obj.id not in specific_rules:
            return False
        if model_only and not rule_obj.models:
            return False
        return not (categories and rule_obj.category not in categories)

    def _get_severity(rule_obj: typing.Any, file_rel: typing.Any) -> typing.Any:
        sev = config.get_severity(rule_obj, file_rel)
        return sev if sev != Severity.OFF else None

    def _lint_files(rules: list[Rule], files: dict[str, Path]) -> None:
        """Apply rules to a set of discovered files or directories."""
        for _name, file_path in files.items():
            rel = str(file_path.relative_to(context.project_root))
            if config.is_ignored(rel):
                continue
            file_model = context.get_model_for_file(file_path)
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
            else:
                content = ""
            for rule_obj in rules:
                if not rule_obj.is_applicable_for_model(file_model):
                    continue
                sev = _get_severity(rule_obj, rel)
                if sev is None:
                    continue
                for result in rule_obj.check(file_path, content, context):
                    result.severity = sev
                    report.add(result)

    def _get_rules(category: str) -> list[Rule]:
        if categories and category not in categories:
            return []
        return [
            r for r in registry.rules_for_category(category) if should_run(r)
        ]

    # Instructions — instruction.txt files + global_instruction.txt
    instruction_files = {
        k: v
        for k, v in discovery.discover_agents().items()
        if v.name == "instruction.txt"
    }
    global_inst = discovery.discover_global_instruction()
    if global_inst:
        instruction_files["global_instruction"] = global_inst
    _lint_files(_get_rules("instructions"), instruction_files)

    # Callbacks
    cb_files = {
        f"{agent}_{cb_type}_{cb_name}": code_path
        for agent, cb_type, cb_name, code_path in discovery.discover_callbacks()
    }
    _lint_files(_get_rules("callbacks"), cb_files)

    # Tools
    _lint_files(_get_rules("tools"), discovery.discover_tools())

    # Evals
    _lint_files(_get_rules("evals"), discovery.discover_evals())

    # Config — app config + agent configs
    config_rules = _get_rules("config")
    if config_rules:
        app_cfg = discovery.discover_app_config()
        config_files = {}
        if app_cfg:
            config_files["app"] = app_cfg
        config_files.update(discovery.discover_agent_configs())
        _lint_files(config_rules, config_files)

    # Target-dispatched rules (structure + schema categories)
    # Rules with a ``target`` property get matched to discovered files.
    target_dispatched = _get_rules("structure") + _get_rules("schema")
    if target_dispatched:
        deployment_configs: dict[str, Path] = {}
        seen_dep_roots: set[Path] = set()
        for dep_root in (
            discovery.app_root,
            discovery.app_dir,
            context.project_root,
        ):
            if dep_root is None or dep_root in seen_dep_roots:
                continue
            seen_dep_roots.add(dep_root)
            for p in sorted(dep_root.glob("deployments/*.yaml")):
                deployment_configs.setdefault(p.stem, p)

        target_files = {
            "app_config": {},
            "instruction": instruction_files,
            "agent_config": discovery.discover_agent_configs(),
            "tool_config": discovery._discover_resource_dirs("tools"),
            "toolset_config": discovery.discover_toolsets(),
            "guardrail_config": discovery.discover_guardrails(),
            "deployment_config": deployment_configs,
            "evaluation_config": discovery.discover_evaluations(),
            "eval_expectation_config": (
                discovery.discover_evaluation_expectations()
            ),
        }
        app_cfg = discovery.discover_app_config()
        if app_cfg:
            target_files["app_config"] = {"app": app_cfg}

        by_target: dict[str, list[Rule]] = {}
        for r in target_dispatched:
            by_target.setdefault(r.target, []).append(r)

        for target_name, rules in by_target.items():
            files = target_files.get(target_name, {})
            if files:
                _lint_files(rules, files)
