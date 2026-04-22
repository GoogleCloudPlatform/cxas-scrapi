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
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

# ── Severity ─────────────────────────────────────────────────────────────


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    OFF = "off"

    @classmethod
    def from_str(cls, s) -> "Severity":
        # YAML parses bare ``off`` as boolean False
        if isinstance(s, bool):
            return cls.OFF if not s else cls.ERROR
        return cls(str(s).lower())


# ── Lint Result ──────────────────────────────────────────────────────────


@dataclass
class LintResult:
    file: str
    rule_id: str
    severity: Severity
    message: str
    line: Optional[int] = None
    fix_suggestion: str = ""

    def __str__(self):
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
    def errors(self):
        return [r for r in self.results if r.severity == Severity.ERROR]

    @property
    def warnings(self):
        return [r for r in self.results if r.severity == Severity.WARNING]

    def add(self, result: LintResult):
        self.results.append(result)

    def add_all(self, results: list):
        self.results.extend(results)

    def print_summary(self, show_fixes=False):
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
    """

    id: str = ""
    name: str = ""
    description: str = ""
    default_severity: Severity = Severity.WARNING
    category: str = ""
    target: str = ""

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
        severity: Optional[Severity] = None,
        line: Optional[int] = None,
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
_REGISTERED_IDS: set[str] = set()


def rule(category: str):
    """Class decorator that auto-registers a Rule into its category.

    Duplicate rule IDs are silently ignored so that repeated imports
    (or test-time ``@rule`` usage) never produce duplicates.

    Usage::

        @rule("config")
        class InvalidJson(Rule):
            id = "A001"
            ...
    """

    def decorator(cls):
        cls.category = category
        instance = cls()
        if instance.id not in _REGISTERED_IDS:
            _REGISTERED_IDS.add(instance.id)
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
    all_agent_names: set = field(default_factory=set)
    all_agent_display_names: set = field(default_factory=set)
    all_tool_names: set = field(default_factory=set)
    all_tool_dirs: dict = field(default_factory=dict)
    platform_tools: set = field(
        default_factory=lambda: {"end_session", "customize_response"}
    )
    options: dict = field(default_factory=dict)

    @property
    def all_known_tools(self) -> set:
        return self.all_tool_names | self.platform_tools


# ── Rule Registry ────────────────────────────────────────────────────────


class RuleRegistry:
    """Holds all registered rules and applies config overrides."""

    def __init__(self):
        self._rules: dict[str, Rule] = {}

    def register(self, rule_obj: Rule):
        self._rules[rule_obj.id] = rule_obj

    def register_all(self, rules: list[Rule]):
        for r in rules:
            self.register(r)

    def get(self, rule_id: str) -> Optional[Rule]:
        return self._rules.get(rule_id)

    def all_rules(self) -> list[Rule]:
        return sorted(self._rules.values(), key=lambda r: r.id)

    def rules_for_category(self, category: str) -> list[Rule]:
        return [r for r in self.all_rules() if r.category == category]

    def list_rules(self):
        """Print all registered rules."""
        current_cat = ""
        for r in self.all_rules():
            if r.category != current_cat:
                current_cat = r.category
                print(f"\n  {current_cat.upper()}")
            sev = r.default_severity.value.upper()
            print(f"    {r.id}  [{sev:7s}]  {r.name}: {r.description}")


# ── Configuration ────────────────────────────────────────────────────────


@dataclass
class LintConfig:
    """Linter configuration loaded from ``cxaslint.yaml``."""

    app_dir: str = "."
    evals_dir: str = "evals/"
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
            if fnmatch.fnmatch(file_path, pattern):
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

    def __init__(self, app_dir: Path, evals_dir: Path):
        self.app_dir = app_dir
        self.evals_dir = evals_dir
        self.app_root = self._find_app_root()

    def _find_app_root(self) -> Optional[Path]:
        """Find the actual app root directory.

        Handles two layouts:
        - Direct: ``app_dir`` itself contains ``app.json``/``app.yaml``
        - Nested: ``app_dir/<name>/`` contains the app (gecx-skills convention)
        """
        if not self.app_dir.exists():
            return None
        if (self.app_dir / "app.json").exists() or (
            self.app_dir / "app.yaml"
        ).exists():
            return self.app_dir
        for d in self.app_dir.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                if (d / "app.json").exists() or (d / "app.yaml").exists():
                    return d
        return None

    def discover_agents(self) -> dict[str, Path]:
        """Return ``{dir_name: instruction_or_config_path}`` for all agents."""
        if not self.app_root:
            return {}
        agents_dir = self.app_root / "agents"
        if not agents_dir.exists():
            return {}
        result = {}
        for d in sorted(agents_dir.iterdir()):
            if d.is_dir():
                inst = d / "instruction.txt"
                if inst.exists():
                    result[d.name] = inst
                else:
                    json_file = d / f"{d.name}.json"
                    if json_file.exists():
                        result[d.name] = json_file
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
                code = d / "python_function" / "python_code.py"
                if code.exists():
                    result[d.name] = code
                else:
                    json_files = list(d.glob("*.json"))
                    if json_files:
                        result[d.name] = json_files[0]
        return result

    def discover_callbacks(self) -> list[tuple[str, str, str, Path]]:
        """Return ``[(agent_name, cb_type, cb_name, code_path), ...]``."""
        if not self.app_root:
            return []
        agents_dir = self.app_root / "agents"
        if not agents_dir.exists():
            return []
        result = []
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
            for cb_type in cb_types:
                cb_dir = agent_dir / cb_type
                if not cb_dir.exists():
                    continue
                for cb in sorted(cb_dir.iterdir()):
                    code = cb / "python_code.py"
                    if code.exists():
                        result.append((agent_dir.name, cb_type, cb.name, code))
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

    def discover_app_config(self) -> Optional[Path]:
        """Return path to ``app.json`` or ``app.yaml``."""
        if not self.app_root:
            return None
        for name in ("app.json", "app.yaml"):
            p = self.app_root / name
            if p.exists():
                return p
        return None

    def discover_agent_configs(self) -> dict[str, Path]:
        """Return ``{agent_name: json_path}`` for all agent configs."""
        if not self.app_root:
            return {}
        agents_dir = self.app_root / "agents"
        if not agents_dir.exists():
            return {}
        result = {}
        for d in sorted(agents_dir.iterdir()):
            if d.is_dir():
                json_file = d / f"{d.name}.json"
                if json_file.exists():
                    result[d.name] = json_file
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


def build_context(
    project_root: Path,
    config: LintConfig,
    discovery: Discovery,
) -> LintContext:
    """Build the shared lint context from discovered app resources."""
    agents = discovery.discover_agents()
    tools = discovery.discover_tools()

    return LintContext(
        project_root=project_root,
        app_dir=discovery.app_dir,
        evals_dir=project_root / config.evals_dir,
        all_agent_names=set(agents.keys()),
        all_agent_display_names={
            discovery.dir_name_to_display(name) for name in agents
        },
        all_tool_names=set(tools.keys()),
        all_tool_dirs={name: path.parent for name, path in tools.items()},
        options=config.options,
    )


def run_rules(  # noqa: C901
    registry: RuleRegistry,
    config: LintConfig,
    context: LintContext,
    discovery: Discovery,
    report: LintReport,
    categories: Optional[list[str]] = None,
    specific_rules: Optional[set[str]] = None,
):
    """Run lint rules against discovered files."""

    def should_run(rule_obj):
        if specific_rules and rule_obj.id not in specific_rules:
            return False
        if categories and rule_obj.category not in categories:
            return False
        return True

    def _get_severity(rule_obj, file_rel):
        sev = config.get_severity(rule_obj, file_rel)
        return sev if sev != Severity.OFF else None

    def _lint_files(rules: list[Rule], files: dict[str, Path]):
        """Apply rules to a set of discovered files or directories."""
        for _name, file_path in files.items():
            rel = str(file_path.relative_to(context.project_root))
            if config.is_ignored(rel):
                continue
            content = file_path.read_text() if file_path.is_file() else ""
            for rule_obj in rules:
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

    # Instructions — only instruction.txt files
    instruction_files = {
        k: v
        for k, v in discovery.discover_agents().items()
        if v.name == "instruction.txt"
    }
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
        target_files = {
            "app_config": {},
            "instruction": instruction_files,
            "agent_config": discovery.discover_agent_configs(),
            "tool_config": discovery._discover_resource_dirs("tools"),
            "toolset_config": discovery.discover_toolsets(),
            "guardrail_config": discovery.discover_guardrails(),
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
