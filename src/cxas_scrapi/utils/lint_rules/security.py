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

"""SAST security lint rules (SEC001-SEC005) for Python tools and callbacks.

Scans first-party Python code in exported CXAS apps (agent callbacks and
Python tools) for dangerous code execution, prohibited sandbox imports,
hardcoded credentials, unsafe deserialization, and sensitive data logging.
"""

import ast
import re
from pathlib import Path

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

# ── SEC001 Constants ─────────────────────────────────────────────────────

DANGEROUS_CALL_NAMES = frozenset({"eval", "exec", "compile", "__import__"})
SANDBOX_ESCAPE_DUNDERS = frozenset(
    {"__subclasses__", "__globals__", "__builtins__", "__code__"}
)

# ── SEC002 Constants ─────────────────────────────────────────────────────

PROHIBITED_MODULES = frozenset(
    {
        "subprocess",
        "socket",
        "ctypes",
        "multiprocessing",
        "pty",
        "fcntl",
        "resource",
        "pip",
        "venv",
        "ensurepip",
    }
)

BLOCKED_OS_CALLS = frozenset({"system", "popen", "fork", "kill"})
BLOCKED_OS_PREFIXES = ("spawn", "exec")
BLOCKED_SHUTIL_CALLS = frozenset({"rmtree"})

# ── SEC003 Constants ─────────────────────────────────────────────────────

SECRET_IDENTIFIERS = frozenset(
    {
        "api_key",
        "apikey",
        "x-api-key",
        "x_api_key",
        "secret_key",
        "client_secret",
        "access_token",
        "auth_token",
        "bearer_token",
        "authorization",
        "private_key",
        "password",
        "passwd",
    }
)

NON_SECRET_IDENTIFIER_SUFFIXES = (
    "_status",
    "_policy",
    "_header",
    "_header_name",
    "_type",
    "_format",
    "_name",
    "_field",
    "_key_name",
    "_url",
    "_endpoint",
    "_path",
    "_env",
    "_env_var",
    "_var",
    "_question",
    "_reset",
    "_verified",
    "_required",
    "_requested",
    "_count",
    "_enabled",
    "_disabled",
    "_message",
    "_error",
    "_prompt",
    "_label",
    "_flow",
    "_state",
    "_mode",
    "_pattern",
    "_regex",
)

TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("Google OAuth access token", re.compile(r"ya29\.[0-9A-Za-z\-_]+")),
    (
        "PEM private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("GitHub personal access token", re.compile(r"ghp_[0-9A-Za-z]{36}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]+")),
    (
        "JWT / Bearer token",
        re.compile(
            r"(?:Bearer\s+)?eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}"
            r"\.[A-Za-z0-9_-]{8,}"
        ),
    ),
    (
        "Bearer credential",
        re.compile(r"^Bearer\s+([A-Za-z0-9\-._~+/]{8,}=*)$"),
    ),
    (
        "API secret key",
        re.compile(r"sk-(?:live|test|proj)-[A-Za-z0-9_\-]{10,}"),
    ),
    ("AWS access key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
]

PLACEHOLDER_EXACT = frozenset(
    {
        "",
        "redacted",
        "[redacted]",
        "<redacted>",
        "<api_key>",
        "<secret>",
        "<token>",
        "<password>",
        "your_api_key_here",
        "your-api-key-here",
        "your_secret_here",
        "your_token_here",
        "dummy",
        "mock_token",
        "mock_key",
        "mock_secret",
        "test_key",
        "test_token",
        "test_secret",
        "test_password",
        "fake_key",
        "fake_token",
        "sample_key",
        "example_key",
        "placeholder",
        "changeme",
        "change_me",
        "none",
        "null",
        "nil",
        "todo",
        "tbd",
        "xxx",
        "xxxx",
    }
)

PLACEHOLDER_SUBSTRINGS = (
    "redacted",
    "your_",
    "your-",
    "dummy",
    "mock",
    "test_",
    "test-",
    "fake_",
    "fake-",
    "placeholder",
    "example",
    "sample_",
    "changeme",
    "replace_me",
    "insert_",
    "<",
)

# ── SEC004 Constants ─────────────────────────────────────────────────────

UNSAFE_DESERIALIZATION_MODULES = frozenset(
    {"pickle", "_pickle", "cPickle", "marshal", "dill"}
)
UNSAFE_DESERIALIZATION_FUNCS = frozenset({"load", "loads"})
UNCONDITIONAL_UNSAFE_YAML_FUNCS = frozenset(
    {"unsafe_load", "full_load", "unsafe_load_all", "full_load_all"}
)
LOADER_CHECKED_YAML_FUNCS = frozenset({"load", "load_all"})
SAFE_YAML_LOADERS = frozenset({"SafeLoader", "CSafeLoader"})

# ── SEC005 Constants ─────────────────────────────────────────────────────

LOGGING_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "critical", "exception"}
)

SENSITIVE_LOG_IDENTIFIERS = frozenset(
    {
        "password",
        "passcode",
        "pin",
        "ssn",
        "social_security",
        "cvv",
        "cvc",
        "card_number",
        "credit_card",
        "api_key",
        "secret",
        "auth_token",
        "access_token",
    }
)

SAFE_LOG_PREFIXES = ("is_", "has_", "needs_", "can_", "should_")

SAFE_LOG_TOKENS = frozenset(
    {
        "verified",
        "valid",
        "invalid",
        "ok",
        "status",
        "state",
        "requested",
        "required",
        "reset",
        "asked",
        "question",
        "present",
        "missing",
        "exists",
        "enabled",
        "disabled",
        "count",
        "length",
        "len",
        "attempts",
        "retries",
        "limit",
        "expired",
        "type",
        "mode",
        "header",
        "policy",
        "name",
        "field",
        "key_name",
        "error",
        "message",
        "prompt",
        "flow",
        "flag",
        "masked",
        "redacted",
        "hash",
    }
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _parse_python_ast(file_path: Path, content: str) -> ast.Module | None:
    """Parse Python source into an AST, or None for non-py/syntax error."""
    if file_path.suffix != ".py":
        return None
    try:
        return ast.parse(content)
    except SyntaxError:
        return None


def _matches_secret_identifier(name: str) -> bool:
    """Return True if a variable or dict key name indicates a secret."""
    lower = name.strip().lower()
    if not lower:
        return False
    if lower in SECRET_IDENTIFIERS:
        return True
    normalized = lower.replace("-", "_")
    if normalized.endswith(NON_SECRET_IDENTIFIER_SUFFIXES):
        return False
    # Match suffix/prefix bounded by underscore or hyphen (e.g. my_api_key)
    return any(
        lower.endswith(f"_{ident}")
        or lower.startswith(f"{ident}_")
        or lower.endswith(f"-{ident}")
        or lower.startswith(f"{ident}-")
        for ident in SECRET_IDENTIFIERS
    )


def _is_placeholder_secret(value: str) -> bool:
    """Return True if literal looks like an empty/placeholder/dummy value."""
    stripped = value.strip()
    if not stripped:
        return True
    if stripped.lower().startswith("bearer "):
        token_part = stripped[7:].strip()
        if not token_part or token_part.lower() in {"test", "token"}:
            return True
        return _is_placeholder_secret(token_part)
    lower = stripped.lower()
    if lower in PLACEHOLDER_EXACT:
        return True
    # Strings composed solely of masking characters like "***" or "••••"
    if all(ch in "*•.#-_xX?" for ch in stripped):
        return True
    # Masked strings like "****1234"
    if stripped.startswith(("***", "•••")):
        return True
    if stripped.startswith("<") and stripped.endswith(">"):
        return True
    if stripped.startswith("${") or (
        stripped.startswith("{") and stripped.endswith("}")
    ):
        return True
    return any(sub in lower for sub in PLACEHOLDER_SUBSTRINGS)


def _matches_sensitive_log_name(name: str) -> str | None:
    """Return matched identifier if ``name`` references PII or secrets."""
    lower = name.strip().lower()
    if not lower:
        return None
    if lower in SENSITIVE_LOG_IDENTIFIERS:
        return lower
    normalized = lower.replace("-", "_")
    if normalized.startswith(SAFE_LOG_PREFIXES):
        return None
    tokens = set(re.split(r"[^a-z0-9]+", lower))
    if tokens & SAFE_LOG_TOKENS:
        return None
    for ident in SENSITIVE_LOG_IDENTIFIERS:
        if "_" in ident:
            if ident in lower:
                return ident
        elif ident in tokens:
            return ident
    return None


def _is_os_dangerous_func(func_name: str) -> bool:
    """Return True if ``os.<func_name>`` is a prohibited process/system call."""
    if func_name in BLOCKED_OS_CALLS:
        return True
    return any(
        func_name == pfx or func_name.startswith(pfx)
        for pfx in BLOCKED_OS_PREFIXES
    )


def _iter_non_docstring_Load_strings(
    tree: ast.Module,
) -> list[tuple[int, str]]:
    """Collect ``(lineno, value)`` for string constants excluding docstrings."""
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(
                node,
                (
                    ast.Module,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                ),
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            docstring_nodes.add(id(node.body[0].value))

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
        ):
            out.append((node.lineno, node.value))
    return out


# ── SEC001: dangerous-code-execution ─────────────────────────────────────


def _check_sec001(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    tree = _parse_python_ast(file_path, content)
    if tree is None:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results: list[LintResult] = []
    seen: set[tuple[int, str]] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn_name: str | None = None
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in DANGEROUS_CALL_NAMES
            ):
                fn_name = node.func.id
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"builtins", "__builtins__"}
                and node.func.attr in DANGEROUS_CALL_NAMES
            ):
                fn_name = node.func.attr

            if fn_name and (node.lineno, fn_name) not in seen:
                seen.add((node.lineno, fn_name))
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=node.lineno,
                        message=(
                            f"Dangerous dynamic code execution call "
                            f"'{fn_name}()' is prohibited in CES Python "
                            "callbacks and tools"
                        ),
                        fix=(
                            f"Remove '{fn_name}()' and use explicit "
                            "data structures or static function dispatch"
                        ),
                    )
                )
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in SANDBOX_ESCAPE_DUNDERS
        ):
            if (node.lineno, node.attr) not in seen:
                seen.add((node.lineno, node.attr))
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=node.lineno,
                        message=(
                            f"Access to sandbox-escape dunder attribute "
                            f"'{node.attr}' is prohibited"
                        ),
                        fix=f"Remove access to '{node.attr}'",
                    )
                )

    return sorted(results, key=lambda r: r.line or 0)


@rule("callbacks")
class CallbackDangerousCodeExecution(Rule):
    id = "SEC001"
    name = "dangerous-code-execution"
    description = (
        "Prohibits eval(), exec(), compile(), __import__(), and "
        "sandbox-escape dunder attributes"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec001(self, file_path, content, context)


@rule("tools")
class ToolDangerousCodeExecution(Rule):
    id = "SEC001"
    name = "dangerous-code-execution"
    description = (
        "Prohibits eval(), exec(), compile(), __import__(), and "
        "sandbox-escape dunder attributes"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec001(self, file_path, content, context)


# ── SEC002: prohibited-sandbox-import ────────────────────────────────────


def _check_sec002(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    tree = _parse_python_ast(file_path, content)
    if tree is None:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results: list[LintResult] = []
    seen: set[tuple[int, str]] = set()

    os_aliases: set[str] = {"os"}
    shutil_aliases: set[str] = {"shutil"}
    direct_os_funcs: dict[str, str] = {}
    direct_shutil_funcs: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_mod = alias.name.split(".")[0]
                bound_name = alias.asname or top_mod
                if top_mod == "os":
                    os_aliases.add(bound_name)
                elif top_mod == "shutil":
                    shutil_aliases.add(bound_name)
                if (
                    top_mod in PROHIBITED_MODULES
                    and (node.lineno, top_mod) not in seen
                ):
                    seen.add((node.lineno, top_mod))
                    results.append(
                        rule_obj.make_result(
                            file=rel,
                            line=node.lineno,
                            message=(
                                f"Import of prohibited module '{alias.name}' "
                                "violates the CES Python sandbox"
                            ),
                            fix=(
                                f"Remove 'import {alias.name}' and use "
                                "approved CES toolsets or HTTP APIs instead"
                            ),
                        )
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_mod = node.module.split(".")[0]
            if (
                top_mod in PROHIBITED_MODULES
                and (node.lineno, top_mod) not in seen
            ):
                seen.add((node.lineno, top_mod))
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=node.lineno,
                        message=(
                            f"Import from prohibited module '{node.module}' "
                            "violates the CES Python sandbox"
                        ),
                        fix=(
                            f"Remove 'from {node.module} import ...' and use "
                            "approved CES toolsets or HTTP APIs instead"
                        ),
                    )
                )
            elif top_mod == "os":
                for alias in node.names:
                    if _is_os_dangerous_func(alias.name):
                        bound = alias.asname or alias.name
                        direct_os_funcs[bound] = f"os.{alias.name}"
                        if (node.lineno, f"os.{alias.name}") not in seen:
                            seen.add((node.lineno, f"os.{alias.name}"))
                            results.append(
                                rule_obj.make_result(
                                    file=rel,
                                    line=node.lineno,
                                    message=(
                                        f"Import of prohibited OS process call "
                                        f"'os.{alias.name}' violates the CES "
                                        "Python sandbox"
                                    ),
                                    fix=f"Remove 'os.{alias.name}' import",
                                )
                            )
            elif top_mod == "shutil":
                for alias in node.names:
                    if alias.name in BLOCKED_SHUTIL_CALLS:
                        bound = alias.asname or alias.name
                        direct_shutil_funcs[bound] = f"shutil.{alias.name}"
                        if (node.lineno, f"shutil.{alias.name}") not in seen:
                            seen.add((node.lineno, f"shutil.{alias.name}"))
                            results.append(
                                rule_obj.make_result(
                                    file=rel,
                                    line=node.lineno,
                                    message=(
                                        f"Import of prohibited filesystem call "
                                        f"'shutil.{alias.name}' violates the "
                                        "CES Python sandbox"
                                    ),
                                    fix=f"Remove 'shutil.{alias.name}' import",
                                )
                            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target_call: str | None = None
        if isinstance(node.func, ast.Attribute) and isinstance(
            node.func.value, ast.Name
        ):
            mod_name = node.func.value.id
            attr_name = node.func.attr
            if mod_name in os_aliases and _is_os_dangerous_func(attr_name):
                target_call = f"os.{attr_name}"
            elif (
                mod_name in shutil_aliases and attr_name in BLOCKED_SHUTIL_CALLS
            ):
                target_call = f"shutil.{attr_name}"
        elif isinstance(node.func, ast.Name):
            if node.func.id in direct_os_funcs:
                target_call = direct_os_funcs[node.func.id]
            elif node.func.id in direct_shutil_funcs:
                target_call = direct_shutil_funcs[node.func.id]

        if target_call and (node.lineno, target_call) not in seen:
            seen.add((node.lineno, target_call))
            results.append(
                rule_obj.make_result(
                    file=rel,
                    line=node.lineno,
                    message=(
                        f"Prohibited system/filesystem call '{target_call}()' "
                        "is not allowed in the CES Python sandbox"
                    ),
                    fix=f"Remove '{target_call}()'",
                )
            )

    return sorted(results, key=lambda r: r.line or 0)


@rule("callbacks")
class CallbackProhibitedSandboxImport(Rule):
    id = "SEC002"
    name = "prohibited-sandbox-import"
    description = (
        "Prohibits OS/process/raw-socket module imports and dangerous "
        "os/shutil system calls"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec002(self, file_path, content, context)


@rule("tools")
class ToolProhibitedSandboxImport(Rule):
    id = "SEC002"
    name = "prohibited-sandbox-import"
    description = (
        "Prohibits OS/process/raw-socket module imports and dangerous "
        "os/shutil system calls"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec002(self, file_path, content, context)


# ── SEC003: hardcoded-secret-or-token ────────────────────────────────────


def _extract_constant_str(node: ast.AST | None) -> str | None:
    """Return string value if ``node`` is a literal string Constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _check_sec003(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    tree = _parse_python_ast(file_path, content)
    if tree is None:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results: list[LintResult] = []
    flagged_lines: set[int] = set()

    # 1. Check high-confidence token prefixes in non-docstring literals
    for lineno, str_val in _iter_non_docstring_Load_strings(tree):
        if lineno in flagged_lines or _is_placeholder_secret(str_val):
            continue
        for token_label, pattern in TOKEN_PATTERNS:
            if pattern.search(str_val):
                flagged_lines.add(lineno)
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=lineno,
                        message=(
                            f"Hardcoded {token_label} detected in string "
                            "literal"
                        ),
                        fix=(
                            "Remove the hardcoded credential and retrieve it "
                            "at runtime from session state, Secret Manager, "
                            "or environment variables"
                        ),
                    )
                )
                break

    # 2. Check assignments and dict keys whose names indicate secrets
    def _check_secret_binding(
        key_name: str, value_node: ast.AST | None, lineno: int
    ) -> None:
        if lineno in flagged_lines:
            return
        if not _matches_secret_identifier(key_name):
            return
        str_val = _extract_constant_str(value_node)
        if str_val is None or _is_placeholder_secret(str_val):
            return
        flagged_lines.add(lineno)
        results.append(
            rule_obj.make_result(
                file=rel,
                line=lineno,
                message=(
                    f"Hardcoded secret or credential assigned to '{key_name}'"
                ),
                fix=(
                    f"Do not hardcode '{key_name}'; read it from "
                    "context.state, environment variables, or Secret Manager"
                ),
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _check_secret_binding(target.id, node.value, node.lineno)
                elif isinstance(target, ast.Subscript):
                    slice_str = _extract_constant_str(target.slice)
                    if slice_str is not None:
                        _check_secret_binding(
                            slice_str, node.value, node.lineno
                        )
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                _check_secret_binding(node.target.id, node.value, node.lineno)
        elif isinstance(node, ast.Dict):
            for k_node, v_node in zip(node.keys, node.values, strict=False):
                k_str = _extract_constant_str(k_node)
                if k_str is not None and v_node is not None:
                    _check_secret_binding(k_str, v_node, v_node.lineno)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg is not None:
                    _check_secret_binding(kw.arg, kw.value, kw.lineno)

    return sorted(results, key=lambda r: r.line or 0)


@rule("callbacks")
class CallbackHardcodedSecretOrToken(Rule):
    id = "SEC003"
    name = "hardcoded-secret-or-token"
    description = (
        "Prohibits hardcoded API keys, tokens, private keys, and passwords"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec003(self, file_path, content, context)


@rule("tools")
class ToolHardcodedSecretOrToken(Rule):
    id = "SEC003"
    name = "hardcoded-secret-or-token"
    description = (
        "Prohibits hardcoded API keys, tokens, private keys, and passwords"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec003(self, file_path, content, context)


# ── SEC004: unsafe-deserialization ───────────────────────────────────────


def _is_safe_yaml_loader(loader_node: ast.AST) -> bool:
    """Return True if ``Loader=...`` references SafeLoader or CSafeLoader."""
    if isinstance(loader_node, ast.Name):
        return loader_node.id in SAFE_YAML_LOADERS
    if isinstance(loader_node, ast.Attribute):
        return loader_node.attr in SAFE_YAML_LOADERS
    return False


def _check_sec004(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    tree = _parse_python_ast(file_path, content)
    if tree is None:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results: list[LintResult] = []
    seen: set[tuple[int, str]] = set()

    # Track aliases from `from pickle import loads` or `import pickle as p`
    mod_aliases: dict[str, str] = {
        "pickle": "pickle",
        "_pickle": "_pickle",
        "cPickle": "cPickle",
        "marshal": "marshal",
        "dill": "dill",
        "shelve": "shelve",
        "yaml": "yaml",
    }
    direct_unsafe_funcs: dict[str, str] = {}
    direct_yaml_load: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_mod = alias.name.split(".")[0]
                if top_mod in mod_aliases and alias.asname:
                    mod_aliases[alias.asname] = top_mod
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_mod = node.module.split(".")[0]
            for alias in node.names:
                bound = alias.asname or alias.name
                if (
                    top_mod in UNSAFE_DESERIALIZATION_MODULES
                    and alias.name in UNSAFE_DESERIALIZATION_FUNCS
                ):
                    direct_unsafe_funcs[bound] = f"{top_mod}.{alias.name}"
                elif top_mod == "shelve" and alias.name == "open":
                    direct_unsafe_funcs[bound] = "shelve.open"
                elif top_mod == "yaml":
                    if alias.name in UNCONDITIONAL_UNSAFE_YAML_FUNCS:
                        direct_unsafe_funcs[bound] = f"yaml.{alias.name}"
                    elif alias.name in LOADER_CHECKED_YAML_FUNCS:
                        direct_yaml_load[bound] = f"yaml.{alias.name}"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        unsafe_call: str | None = None
        yaml_load_call: str | None = None

        if isinstance(node.func, ast.Attribute) and isinstance(
            node.func.value, ast.Name
        ):
            canonical_mod = mod_aliases.get(node.func.value.id)
            attr = node.func.attr
            if (
                canonical_mod in UNSAFE_DESERIALIZATION_MODULES
                and attr in UNSAFE_DESERIALIZATION_FUNCS
            ):
                unsafe_call = f"{canonical_mod}.{attr}"
            elif canonical_mod == "shelve" and attr == "open":
                unsafe_call = "shelve.open"
            elif canonical_mod == "yaml":
                if attr in UNCONDITIONAL_UNSAFE_YAML_FUNCS:
                    unsafe_call = f"yaml.{attr}"
                elif attr in LOADER_CHECKED_YAML_FUNCS:
                    yaml_load_call = f"yaml.{attr}"
        elif isinstance(node.func, ast.Name):
            if node.func.id in direct_unsafe_funcs:
                unsafe_call = direct_unsafe_funcs[node.func.id]
            elif node.func.id in direct_yaml_load:
                yaml_load_call = direct_yaml_load[node.func.id]

        if unsafe_call and (node.lineno, unsafe_call) not in seen:
            seen.add((node.lineno, unsafe_call))
            results.append(
                rule_obj.make_result(
                    file=rel,
                    line=node.lineno,
                    message=(
                        f"Unsafe deserialization call '{unsafe_call}()' can "
                        "execute arbitrary code"
                    ),
                    fix=(
                        f"Replace '{unsafe_call}()' with safe serialization "
                        "formats such as 'json.loads()' or 'yaml.safe_load()'"
                    ),
                )
            )
        elif yaml_load_call:
            loader_node: ast.AST | None = None
            if len(node.args) >= 2:
                loader_node = node.args[1]
            else:
                for kw in node.keywords:
                    if kw.arg == "Loader":
                        loader_node = kw.value
                        break
            if (
                loader_node is None or not _is_safe_yaml_loader(loader_node)
            ) and (node.lineno, yaml_load_call) not in seen:
                seen.add((node.lineno, yaml_load_call))
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=node.lineno,
                        message=(
                            f"Unsafe '{yaml_load_call}()' without SafeLoader "
                            "can execute arbitrary Python objects"
                        ),
                        fix=(
                            "Use 'yaml.safe_load()' or pass "
                            "'Loader=yaml.SafeLoader'"
                        ),
                    )
                )

    return sorted(results, key=lambda r: r.line or 0)


@rule("callbacks")
class CallbackUnsafeDeserialization(Rule):
    id = "SEC004"
    name = "unsafe-deserialization"
    description = (
        "Prohibits pickle/cPickle/marshal/shelve deserialization and "
        "yaml.load() without SafeLoader"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec004(self, file_path, content, context)


@rule("tools")
class ToolUnsafeDeserialization(Rule):
    id = "SEC004"
    name = "unsafe-deserialization"
    description = (
        "Prohibits pickle/cPickle/marshal/shelve deserialization and "
        "yaml.load() without SafeLoader"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec004(self, file_path, content, context)


# ── SEC005: sensitive-data-logging ───────────────────────────────────────


def _find_sensitive_ref_in_expr(expr: ast.AST) -> str | None:
    """Return sensitive identifier name if ``expr`` references PII/credentials.

    Inspects variables, attribute accesses, dict/state subscripts, and
    f-string interpolated expressions (ignoring constant string literals).
    """
    if isinstance(expr, ast.Constant):
        return None
    if isinstance(expr, ast.JoinedStr):
        for val in expr.values:
            if isinstance(val, ast.FormattedValue):
                hit = _find_sensitive_ref_in_expr(val.value)
                if hit:
                    return hit
        return None
    if isinstance(expr, ast.Name):
        return _matches_sensitive_log_name(expr.id)
    if isinstance(expr, ast.Attribute):
        hit = _matches_sensitive_log_name(expr.attr)
        if hit:
            return hit
        return _find_sensitive_ref_in_expr(expr.value)
    if isinstance(expr, ast.Subscript):
        key_str = _extract_constant_str(expr.slice)
        if key_str:
            hit = _matches_sensitive_log_name(key_str)
            if hit:
                return hit
        else:
            hit = _find_sensitive_ref_in_expr(expr.slice)
            if hit:
                return hit
        return _find_sensitive_ref_in_expr(expr.value)
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "get"
        and expr.args
    ):
        key_str = _extract_constant_str(expr.args[0])
        if key_str:
            hit = _matches_sensitive_log_name(key_str)
            if hit:
                return hit
    if isinstance(expr, ast.Dict):
        for k_node, v_node in zip(expr.keys, expr.values, strict=False):
            if v_node is not None:
                hit = _find_sensitive_ref_in_expr(v_node)
                if hit:
                    return hit
            k_str = _extract_constant_str(k_node)
            if (
                k_str
                and v_node is not None
                and not isinstance(v_node, ast.Constant)
            ):
                hit = _matches_sensitive_log_name(k_str)
                if hit:
                    return hit
        return None

    for child in ast.iter_child_nodes(expr):
        hit = _find_sensitive_ref_in_expr(child)
        if hit:
            return hit
    return None


def _is_logging_or_print_call(node: ast.Call) -> str | None:
    """Return call label if ``node`` is ``print`` or ``logging.<level>``."""
    if isinstance(node.func, ast.Name) and node.func.id == "print":
        return "print()"
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in LOGGING_METHODS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"logging", "logger", "log", "_logger"}
    ):
        return f"{node.func.value.id}.{node.func.attr}()"
    return None


def _check_sec005(
    rule_obj: Rule, file_path: Path, content: str, context: LintContext
) -> list[LintResult]:
    tree = _parse_python_ast(file_path, content)
    if tree is None:
        return []
    rel = str(file_path.relative_to(context.project_root))
    results: list[LintResult] = []
    seen_lines: set[int] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_desc = _is_logging_or_print_call(node)
        if not call_desc or node.lineno in seen_lines:
            continue

        all_exprs = [
            *node.args,
            *(kw.value for kw in node.keywords if kw.value is not None),
        ]
        for arg in all_exprs:
            # Skip plain constant string literals (e.g. print("Enter your pin"))
            if isinstance(arg, ast.Constant):
                continue
            sensitive_ident = _find_sensitive_ref_in_expr(arg)
            if sensitive_ident:
                seen_lines.add(node.lineno)
                results.append(
                    rule_obj.make_result(
                        file=rel,
                        line=node.lineno,
                        message=(
                            f"Sensitive data '{sensitive_ident}' logged via "
                            f"'{call_desc}'"
                        ),
                        fix=(
                            f"Redact or remove '{sensitive_ident}' before "
                            f"calling '{call_desc}'"
                        ),
                    )
                )
                break

    return sorted(results, key=lambda r: r.line or 0)


@rule("callbacks")
class CallbackSensitiveDataLogging(Rule):
    id = "SEC005"
    name = "sensitive-data-logging"
    description = (
        "Warns when print() or logging calls output sensitive PII or "
        "credential identifiers"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec005(self, file_path, content, context)


@rule("tools")
class ToolSensitiveDataLogging(Rule):
    id = "SEC005"
    name = "sensitive-data-logging"
    description = (
        "Warns when print() or logging calls output sensitive PII or "
        "credential identifiers"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return _check_sec005(self, file_path, content, context)


# ── Helper for scanning an exported app directory ────────────────────────


def iter_agent_callback_files(app_dir: Path) -> list[Path]:
    """Yield all callback ``python_code.py`` files under ``app_dir/agents``."""
    from cxas_scrapi.utils.linter import Discovery  # noqa: PLC0415

    discovery = Discovery(app_dir, app_dir / "evals")
    return [code_path for _, _, _, code_path in discovery.discover_callbacks()]
