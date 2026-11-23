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

"""Modular component lint rules (I021-I026, C020) for CXAS / CES apps.

All rules default to ``Severity.WARNING`` for non-breaking rollout; override
any rule to ``error`` or ``off`` in ``cxaslint.yaml``.

Complements I020 (``ces-jinja-subset-syntax``) in ``runtime_traps.py`` with
static checks targeting dynamic, modular, and polymorphic Jinja instructions
and one-time ``before_agent_callback`` initialization guards:

- I021: ``modular-callback-timing-race`` — Jinja templates reading state keys
  written in ``before_model_callback`` instead of ``before_agent_callback``.
- I022: ``modular-prefix-cache-hazard`` — volatile state variables used in
  ``{{ var }}`` or ``{% if %}`` near the top of a long instruction.
- I023: ``modular-unsupported-jinja-subset`` — unsupported filters (silent
  literal-text fallback), ``loop.*`` attributes other than ``loop.last``,
  unknown function/macro calls, unsupported block tags, nested curly braces,
  and unbalanced blocks.
- I024: ``modular-channel-modality-tag`` — ``{@startModality_*}`` /
  ``{@startChannel_*}`` allowlist validation, open/close stack parity, and
  advisory warning when tags wrap top-level XML sections.
- I025: ``modular-unmasked-modality-tool`` — ``{@TOOL: ...}`` or
  ``{@WIDGET: ...}`` referenced solely inside one modality without callback
  masking or an explicit prohibition in the opposite modality.
- I026: ``modular-hyphenated-var-in-expr`` — hyphenated variable names inside
  ``{% if %}/{% elif %}/{% for %}`` or filtered prints ``{{ var | filter }}``.
- C020: ``before-agent-missing-turn-guard`` — unconditional empty/zero state
  resets in ``before_agent_callback`` without a one-time init guard.
"""

import ast
import contextlib
import re
from dataclasses import dataclass
from pathlib import Path

from cxas_scrapi.utils.lint_rules.callbacks import _resolve_cb_type
from cxas_scrapi.utils.lint_rules.runtime_traps import (
    _line_of,
    _mask_regions,
    _parse_ast,
)
from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

# ── CES Template Engine & Tag Allowlists ─────────────────────────────────

MODALITY_MAP: dict[str, str] = {
    "TEXT": "TEXT",
    "Text": "TEXT",
    "VOICE": "VOICE",
    "Voice": "VOICE",
}

CHANNEL_MAP: dict[str, str] = {
    "API": "API",
    "GoogleTelephonyPlatform": "GOOGLE_TELEPHONY_PLATFORM",
    "GOOGLE_TELEPHONY_PLATFORM": "GOOGLE_TELEPHONY_PLATFORM",
    "GoogleCCaaSVoice": "CONTACT_CENTER_AS_A_SERVICE",
    "CONTACT_CENTER_AS_A_SERVICE": "CONTACT_CENTER_AS_A_SERVICE",
    "Twilio": "TWILIO",
    "TWILIO": "TWILIO",
    "Five9": "FIVE9",
    "FIVE9": "FIVE9",
    "AudioCodes": "CONTACT_CENTER_INTEGRATION",
    "CONTACT_CENTER_INTEGRATION": "CONTACT_CENTER_INTEGRATION",
    "Whatsapp": "WHATSAPP",
    "WhatsApp": "WHATSAPP",
    "WHATSAPP": "WHATSAPP",
    "Instagram": "INSTAGRAM",
    "INSTAGRAM": "INSTAGRAM",
    "GoogleCCaaSChat": "CONTACT_CENTER_AS_A_SERVICE_CHAT",
    "CONTACT_CENTER_AS_A_SERVICE_CHAT": "CONTACT_CENTER_AS_A_SERVICE_CHAT",
    "WEB_UI": "WEB_UI",
    "WebWidget": "WEB_UI",
}

SUPPORTED_FILTERS: frozenset[str] = frozenset({"length", "upper", "default"})
SUPPORTED_FUNCTIONS: frozenset[str] = frozenset(
    {"default", "length", "in_time_window"}
)
SUPPORTED_LOOP_ATTRS: frozenset[str] = frozenset({"last"})
SUPPORTED_BLOCK_KEYWORDS: frozenset[str] = frozenset(
    {"if", "elif", "else", "endif", "for", "endfor", "macro", "endmacro"}
)

JINJA_KEYWORDS: frozenset[str] = frozenset(
    {
        "if",
        "elif",
        "else",
        "endif",
        "for",
        "endfor",
        "in",
        "and",
        "or",
        "not",
        "is",
        "true",
        "false",
        "True",
        "False",
        "null",
        "none",
        "None",
        "defined",
        "undefined",
        "macro",
        "endmacro",
        "loop",
        "default",
        "length",
        "upper",
        "in_time_window",
    }
)

# Session-immutable variables exempt from I022 prefix-cache warnings.
DEFAULT_IMMUTABLE_KEYS: frozenset[str] = frozenset(
    {
        "_session",
        "brand",
        "locale",
        "country",
        "language",
        "caller_id",
        "current_date",
        "current_time",
        "session_metadata",
    }
)

# ── Regex Patterns ───────────────────────────────────────────────────────

_QUOTED_LITERAL_RE = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")

# Block tags: supports both {% ... %} and {{% ... %}} (dynamic-block syntax).
_BLOCK_TAG_RE = re.compile(
    r"\{\{?%-?\s*((?:(?!-?%\}\}?)(?!\{\{?%).)*?)\s*-?%\}\}?",
    re.DOTALL,
)
# Print tags: {{ ... }} (excluding {{% ... %}}, {@ ... }, and {{# ... #}}).
_PRINT_TAG_RE = re.compile(r"\{\{(?![%@#])([^{}]*?)\}\}")

_MODALITY_TAG_RE = re.compile(r"\{@(start|end)Modality_?([A-Za-z0-9_]+)\}")
_ANY_CES_TAG_RE = re.compile(
    r"\{@(start|end)(Modality|Channel)_?([A-Za-z0-9_]+)\}"
)

_TOOL_OR_WIDGET_REF_RE = re.compile(
    r"\{@(TOOL|WIDGET):\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\}"
)
_TOP_LEVEL_XML_RE = re.compile(
    r"<\s*(role|persona|constraints|taskflow|examples|goal)\b",
    re.IGNORECASE,
)

# Hyphenated variable patterns.
_HYPHENATED_NAME = r"[A-Za-z_][\w.:]*(?:-[\w.:]+)+"
_HYPHENATED_IN_EXPR_RE = re.compile(rf"(?:^|[^\w.:/-])({_HYPHENATED_NAME})")
_HYPHENATED_EXACT_RE = re.compile(rf"^{_HYPHENATED_NAME}$")
_FILTERED_PRINT_RE = re.compile(r"\{\{(?![%@#])([^{}|%#]+)\|[^{}]*\}\}")

_IDENT_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
_LOOP_ATTR_RE = re.compile(r"\bloop\.([A-Za-z_][A-Za-z0-9_]*)\b")
_FUNC_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_NESTED_BRACE_RE = re.compile(r"[{}]")
_STATE_PREFIX_RE = re.compile(r"^(?:app|user|temp):")
_NON_VAR_PRINT_RE = re.compile(r"^[0-9#+\-*/\s:.,]+$")
_FILTER_NAME_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)")
_MACRO_SIG_RE = re.compile(r"^([A-Za-z_]\w*)\s*\(([^)]*)\)$")
_MACRO_HEAD_RE = re.compile(r"^([A-Za-z_]\w*)\s*\(")


# ── Shared Helpers ───────────────────────────────────────────────────────


def _strip_quotes(expr: str) -> str:
    return _QUOTED_LITERAL_RE.sub(" '' ", expr)


def _approx_tokens(text: str) -> int:
    """Fast dependency-free token estimate (~4 chars/token)."""
    return max(0, (len(text) + 3) // 4)


def _rel_path(file_path: Path, context: LintContext) -> str:
    try:
        return str(file_path.relative_to(context.project_root))
    except ValueError:
        return str(file_path)


def _normalize_state_key(raw_key: str) -> str:
    return _STATE_PREFIX_RE.sub("", raw_key.split(".", 1)[0])


@dataclass(frozen=True)
class _CallbackWrite:
    key: str
    stage: str  # "before_agent" | "before_model" | "after_model" | "tool"
    file_path: str
    line: int
    one_time_guarded: bool = False


_INIT_GUARD_TOKENS: tuple[str, ...] = (
    "init",
    "loaded",
    "called",
    "done",
    "once",
    "bootstrap",
    "started",
)


def _is_init_key_name(key: str) -> bool:
    low = key.lower()
    return any(tok in low for tok in _INIT_GUARD_TOKENS)


def _extract_state_read_key(
    expr: ast.expr, state_aliases: set[str]
) -> str | None:
    """Return key if ``expr`` reads session state (``.get('k')``, ``['k']``)."""
    if isinstance(expr, ast.Call):
        fn = expr.func
        is_state_get = (
            isinstance(fn, ast.Attribute)
            and fn.attr == "get"
            and _is_state_expr(fn.value, state_aliases)
        )
        is_get_var = (isinstance(fn, ast.Name) and fn.id == "get_variable") or (
            isinstance(fn, ast.Attribute) and fn.attr == "get_variable"
        )
        if (
            (is_state_get or is_get_var)
            and expr.args
            and isinstance(expr.args[0], ast.Constant)
            and isinstance(expr.args[0].value, str)
        ):
            return _normalize_state_key(expr.args[0].value)
    elif (
        isinstance(expr, ast.Subscript)
        and _is_state_expr(expr.value, state_aliases)
        and isinstance(expr.slice, ast.Constant)
        and isinstance(expr.slice.value, str)
    ):
        return _normalize_state_key(expr.slice.value)
    return None


def _is_truthy_literal(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Constant):
        val = expr.value
        if val is True:
            return True
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return val != 0
    return False


def _is_init_guard(test: ast.expr) -> bool:
    """Detect simple one-time init guard (Way 1: ``if not state.get(...):``)."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        src = ast.dump(test.operand).lower()
        return any(tok in src for tok in _INIT_GUARD_TOKENS)
    if isinstance(test, ast.Compare):
        src = ast.dump(test).lower()
        if "notin" in src or "isnone" in src or "none" in src:
            return True
        if len(test.ops) == 1 and isinstance(test.ops[0], ast.NotEq):
            return any(tok in src for tok in _INIT_GUARD_TOKENS)
    return False


def _early_return_guard(
    stmt: ast.stmt, state_aliases: set[str]
) -> tuple[str | None, bool]:
    """Inspect ``if <guard>: return ...`` for one-time early-return guards.

    Returns ``(guarded_key, is_session_wide_guard)``. When the tested key looks
    like a session init flag (e.g. ``_initialized``), ``is_session_wide_guard``
    is ``True`` so every write written *below* the guard is treated as set-once.
    """
    if not isinstance(stmt, ast.If) or not stmt.body:
        return (None, False)
    if not any(isinstance(s, ast.Return) for s in stmt.body):
        return (None, False)
    test = stmt.test

    # `if state.get("_initialized"): return None`
    key = _extract_state_read_key(test, state_aliases)
    if key is not None:
        return (key, _is_init_key_name(key))

    # Comparisons:
    # - `if state.get("_initialized") == "true": return None`
    # - `if state.get("_initialized") is True: return None`
    # - `if "_initialized" in state: return None`
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and len(test.comparators) == 1
    ):
        op = test.ops[0]
        comp = test.comparators[0]
        if isinstance(op, ast.In) and _is_state_expr(comp, state_aliases):
            if isinstance(test.left, ast.Constant) and isinstance(
                test.left.value, str
            ):
                k = _normalize_state_key(test.left.value)
                return (k, _is_init_key_name(k))
        elif isinstance(op, (ast.Eq, ast.Is)):
            lk = _extract_state_read_key(test.left, state_aliases)
            if lk is not None and _is_truthy_literal(comp):
                return (lk, _is_init_key_name(lk))
            rk = _extract_state_read_key(comp, state_aliases)
            if rk is not None and _is_truthy_literal(test.left):
                return (rk, _is_init_key_name(rk))
            # Handle `str(state.get("_initialized")).lower() == "true"`
            for sub in ast.walk(test.left):
                if isinstance(sub, ast.expr):
                    sk = _extract_state_read_key(sub, state_aliases)
                    if sk is not None and _is_truthy_literal(comp):
                        return (sk, _is_init_key_name(sk))
        elif isinstance(op, (ast.IsNot, ast.NotEq)):
            lk = _extract_state_read_key(test.left, state_aliases)
            if (
                lk is not None
                and isinstance(comp, ast.Constant)
                and comp.value in (None, False, "")
            ):
                return (lk, _is_init_key_name(lk))

    return (None, False)


def _is_state_expr(node: ast.expr, state_aliases: set[str]) -> bool:
    if isinstance(node, ast.Name) and node.id in state_aliases:
        return True
    return isinstance(node, ast.Attribute) and node.attr in (
        "state",
        "variables",
    )


def _is_default_reset_literal(node: ast.expr) -> bool:
    """Return True for empty/zero literal defaults (``, 0, False, [], {})."""
    if isinstance(node, ast.Constant):
        val = node.value
        if val is False or val == "":
            return True
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return val == 0
        return False
    if isinstance(node, ast.List):
        return len(node.elts) == 0
    if isinstance(node, ast.Dict):
        return len(node.keys) == 0
    return False


def _extract_python_state_writes(
    py_file: Path, stage: str, root: Path
) -> list[_CallbackWrite]:
    """Parse Python AST for session state/variable writes."""
    try:
        src = py_file.read_text(encoding="utf-8")
    except OSError:
        return []
    tree = _parse_ast(src)
    if tree is None:
        return []

    try:
        rel = str(py_file.relative_to(root))
    except ValueError:
        rel = str(py_file)

    writes: list[_CallbackWrite] = []
    state_aliases: set[str] = {"state", "variables"}

    def _visit_block(
        stmts: list[ast.stmt],
        in_guard: bool,
        guarded_keys: frozenset[str],
    ) -> None:
        block_guarded = in_guard
        local_guarded = set(guarded_keys)
        for stmt in stmts:
            _visit(stmt, block_guarded, frozenset(local_guarded))
            gk, session_wide = _early_return_guard(stmt, state_aliases)
            if session_wide:
                block_guarded = True
            if gk is not None:
                local_guarded.add(gk)

    def _visit(
        node: ast.AST,
        in_guard: bool,
        guarded_keys: frozenset[str],
    ) -> None:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)
        ):
            _visit_block(node.body, in_guard, guarded_keys)
            return

        if isinstance(node, ast.If):
            guarded = in_guard or _is_init_guard(node.test)
            _visit_block(node.body, guarded, guarded_keys)
            _visit_block(node.orelse, in_guard, guarded_keys)
            return

        if isinstance(node, ast.Assign):
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and _is_state_expr(node.value, state_aliases)
            ):
                state_aliases.add(node.targets[0].id)
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and _is_state_expr(target.value, state_aliases)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    key = _normalize_state_key(target.slice.value)
                    writes.append(
                        _CallbackWrite(
                            key,
                            stage,
                            rel,
                            node.lineno,
                            in_guard or key in guarded_keys,
                        )
                    )
        elif isinstance(node, ast.AugAssign):
            target = node.target
            if (
                isinstance(target, ast.Subscript)
                and _is_state_expr(target.value, state_aliases)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                key = _normalize_state_key(target.slice.value)
                writes.append(
                    _CallbackWrite(
                        key,
                        stage,
                        rel,
                        node.lineno,
                        in_guard or key in guarded_keys,
                    )
                )
        elif isinstance(node, ast.Call):
            fn = node.func
            is_set_var = (
                isinstance(fn, ast.Name) and fn.id == "set_variable"
            ) or (isinstance(fn, ast.Attribute) and fn.attr == "set_variable")
            if is_set_var:
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    key = _normalize_state_key(node.args[0].value)
                    writes.append(
                        _CallbackWrite(
                            key,
                            stage,
                            rel,
                            node.lineno,
                            in_guard or key in guarded_keys,
                        )
                    )
            elif isinstance(fn, ast.Attribute) and _is_state_expr(
                fn.value, state_aliases
            ):
                if fn.attr == "setdefault" and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(
                        arg0.value, str
                    ):
                        key = _normalize_state_key(arg0.value)
                        writes.append(
                            _CallbackWrite(key, stage, rel, node.lineno, True)
                        )
                elif fn.attr == "update" and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Dict):
                        for k in arg0.keys:
                            if isinstance(k, ast.Constant) and isinstance(
                                k.value, str
                            ):
                                key = _normalize_state_key(k.value)
                                writes.append(
                                    _CallbackWrite(
                                        key,
                                        stage,
                                        rel,
                                        node.lineno,
                                        in_guard or key in guarded_keys,
                                    )
                                )
                    for kw in node.keywords:
                        if kw.arg:
                            writes.append(
                                _CallbackWrite(
                                    kw.arg,
                                    stage,
                                    rel,
                                    node.lineno,
                                    in_guard or kw.arg in guarded_keys,
                                )
                            )

        for child in ast.iter_child_nodes(node):
            _visit(child, in_guard, guarded_keys)

    _visit(tree, False, frozenset())
    return writes


def _iter_agent_callback_files(agent_dir: Path, cb_type: str) -> list[Path]:
    """Return callback Python files supporting all CES export layouts.

    Handles:
    - ``<agent>/<cb_type>/<cb_name>/python_code.py`` (standard ``cxas pull``)
    - ``<agent>/<cb_type>_<suffix>/python_code.py`` (legacy flat prefix)
    - ``<agent>/<cb_type>/*.py`` (simple direct script layout)
    """
    found: list[Path] = []
    seen: set[Path] = set()

    cb_dir = agent_dir / cb_type
    if cb_dir.is_dir():
        for entry in sorted(cb_dir.iterdir()):
            if entry.is_dir():
                code = entry / "python_code.py"
                if code.is_file() and code not in seen:
                    seen.add(code)
                    found.append(code)
            elif (
                entry.is_file() and entry.suffix == ".py" and entry not in seen
            ):
                seen.add(entry)
                found.append(entry)

    prefix = f"{cb_type}_"
    for entry in sorted(agent_dir.iterdir()):
        if entry.is_dir() and entry.name.startswith(prefix):
            code = entry / "python_code.py"
            if code.is_file() and code not in seen:
                seen.add(code)
                found.append(code)

    return found


def _find_agent_dir(instruction_path: Path) -> Path | None:
    if instruction_path.name == "global_instruction.txt":
        return None
    parent = instruction_path.parent
    if not parent.is_dir():
        return None
    cb_prefixes = ("before_model_callbacks", "before_agent_callbacks")
    for p in cb_prefixes:
        if (parent / p).is_dir():
            return parent
    for entry in parent.iterdir():
        if entry.is_dir() and entry.name.startswith(
            ("before_model_callbacks_", "before_agent_callbacks_")
        ):
            return parent
    if (
        instruction_path.name == "instruction.txt"
        and parent.parent.name == "agents"
    ):
        return parent
    return None


def _collect_agent_callbacks(
    agent_dir: Path, root: Path
) -> list[_CallbackWrite]:
    writes: list[_CallbackWrite] = []
    stage_dirs = (
        ("before_agent", "before_agent_callbacks"),
        ("before_model", "before_model_callbacks"),
        ("after_model", "after_model_callbacks"),
    )
    for stage, folder in stage_dirs:
        for py_file in _iter_agent_callback_files(agent_dir, folder):
            writes.extend(_extract_python_state_writes(py_file, stage, root))
    return writes


def _collect_tool_writes(context: LintContext) -> list[_CallbackWrite]:
    writes: list[_CallbackWrite] = []
    search_root = context.app_root or context.app_dir
    if search_root is None:
        return writes
    tools_dir = search_root / "tools"
    if not tools_dir.is_dir():
        return writes
    for py_file in sorted(tools_dir.rglob("*.py")):
        writes.extend(
            _extract_python_state_writes(py_file, "tool", context.project_root)
        )
    return writes


def _extract_template_var_refs(
    masked: str,
) -> tuple[list[tuple[str, int, str]], set[str]]:
    """Return ``(root_var, offset, kind)`` refs read by Jinja & macro names."""
    refs: list[tuple[str, int, str]] = []
    local_vars: set[str] = set()
    macros: set[str] = set()

    for m in _BLOCK_TAG_RE.finditer(masked):
        body = m.group(1).strip()
        parts = body.split(None, 2)
        if not parts:
            continue
        kw = parts[0]
        if kw == "macro" and len(parts) >= 2:
            header = body[len("macro") :].strip()
            mm = _MACRO_SIG_RE.match(header)
            if mm:
                macros.add(mm.group(1))
                for raw_arg in mm.group(2).split(","):
                    arg = raw_arg.strip()
                    if arg:
                        local_vars.add(arg)
            continue
        if kw == "for" and len(parts) >= 3 and " in " in body:
            after_for = body[len("for") :].strip()
            item_part, iter_part = after_for.split(" in ", 1)
            local_vars.add(item_part.strip())
            clean = _strip_quotes(iter_part)
            for im in _IDENT_RE.finditer(clean):
                root = im.group(1)
                if root not in JINJA_KEYWORDS and root not in local_vars:
                    refs.append((root, m.start(), "for"))
            continue
        if kw in ("if", "elif") or (
            kw == "else" and len(parts) >= 2 and parts[1] == "if"
        ):
            expr = body[len(kw) :].strip()
            if kw == "else":
                expr = expr[len("if") :].strip()
            clean = _strip_quotes(expr)
            for im in _IDENT_RE.finditer(clean):
                root = im.group(1)
                if (
                    root not in JINJA_KEYWORDS
                    and root not in local_vars
                    and root not in macros
                ):
                    refs.append((root, m.start(), "if"))

    for m in _PRINT_TAG_RE.finditer(masked):
        expr = m.group(1).strip()
        if not expr:
            continue
        clean = _strip_quotes(expr)
        for im in _IDENT_RE.finditer(clean):
            root = im.group(1)
            if (
                root not in JINJA_KEYWORDS
                and root not in local_vars
                and root not in macros
                and root not in SUPPORTED_FILTERS
            ):
                refs.append((root, m.start(), "print"))

    return refs, macros


def _defined_macro_names(masked: str) -> set[str]:
    macros: set[str] = set()
    for m in _BLOCK_TAG_RE.finditer(masked):
        body = m.group(1).strip()
        if body.startswith(("macro ", "macro\t")):
            header = body[len("macro") :].strip()
            mm = _MACRO_HEAD_RE.match(header)
            if mm:
                macros.add(mm.group(1))
    return macros


# ── I021: Callback Timing Race ───────────────────────────────────────────


@rule("instructions")
class ModularCallbackTimingRace(Rule):
    id = "I021"
    name = "modular-callback-timing-race"
    description = (
        "State variables read in Jinja templates should be derived in "
        "before_agent_callback rather than before_model_callback "
        "(instructions render BEFORE before_model executes)"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        agent_dir = _find_agent_dir(file_path)
        if agent_dir is None:
            return []

        writes = _collect_agent_callbacks(agent_dir, context.project_root)
        before_agent_keys = {w.key for w in writes if w.stage == "before_agent"}
        before_model_writes: dict[str, _CallbackWrite] = {}
        for w in writes:
            if w.stage == "before_model" and w.key not in before_model_writes:
                before_model_writes[w.key] = w

        if not before_model_writes:
            return []

        masked = _mask_regions(content)
        refs, _ = _extract_template_var_refs(masked)
        rel = _rel_path(file_path, context)
        results: list[LintResult] = []
        seen: set[tuple[str, int]] = set()

        for var, offset, kind in refs:
            if var in before_model_writes and var not in before_agent_keys:
                line = _line_of(masked, offset)
                if (var, line) in seen:
                    continue
                seen.add((var, line))
                w = before_model_writes[var]
                if kind == "print":
                    fix_msg = (
                        f"Move derivation of '{var}' into "
                        f"before_agent_callback so '{{{{{var}}}}}' renders on "
                        "the current turn (note: single-brace "
                        f"'{{{var}}}' stays literal in system_instruction and "
                        "tracks deltas via <state_update> in history instead)."
                    )
                else:
                    fix_msg = (
                        f"Move derivation of '{var}' into "
                        "before_agent_callback so the value is set before CES "
                        "evaluates the template block."
                    )
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Template {kind} references '{var}', which is "
                            f"written in before_model callback "
                            f"({w.file_path}:{w.line}) and not in "
                            "before_agent_callback — causes a silent 1-turn "
                            "lag because CES renders instructions BEFORE "
                            "before_model executes"
                        ),
                        fix=fix_msg,
                    )
                )
        return results


# ── I022: Prefix-Cache Placement Advisory ────────────────────────────────


@rule("instructions")
class ModularPrefixCacheHazard(Rule):
    id = "I022"
    name = "modular-prefix-cache-hazard"
    description = (
        "Warn when volatile (turn-varying) variables appear in {{var}} or "
        "{% if %} near the top of a long instruction, risking KV prefix-cache "
        "invalidation across turns"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not content.strip():
            return []

        cfg = context.options.get("I022", {})
        top_fraction = float(cfg.get("top_fraction", 0.40))
        min_trailing_tokens = int(cfg.get("min_trailing_tokens", 500))
        extra_immutable = set(cfg.get("immutable_keys", ()))
        immutable = DEFAULT_IMMUTABLE_KEYS | extra_immutable

        volatile_keys: set[str] = set()
        agent_dir = _find_agent_dir(file_path)
        if agent_dir is not None:
            for w in _collect_agent_callbacks(agent_dir, context.project_root):
                if not w.one_time_guarded:
                    volatile_keys.add(w.key)
        for w in _collect_tool_writes(context):
            if not w.one_time_guarded:
                volatile_keys.add(w.key)

        if not volatile_keys:
            return []

        masked = _mask_regions(content)
        total_len = len(masked)
        cutoff = int(total_len * top_fraction)
        refs, _ = _extract_template_var_refs(masked)
        rel = _rel_path(file_path, context)
        results: list[LintResult] = []
        reported: set[str] = set()

        for var, offset, kind in refs:
            if var in immutable or var not in volatile_keys or var in reported:
                continue
            if offset > cutoff:
                continue
            trailing_tokens = _approx_tokens(masked[offset:])
            if trailing_tokens < min_trailing_tokens:
                continue
            reported.add(var)
            pct = int((offset / max(1, total_len)) * 100)
            if kind == "print":
                fix_msg = (
                    f"Move '{{{{{var}}}}}' lower in the instruction so "
                    "earlier static tokens stay cached, or (for small scalar "
                    f"flags) use single-brace '{{{var}}}' — note that CES "
                    f"keeps '{{{var}}}' literal in system_instruction and "
                    "tracks deltas via <state_update> turns in history."
                )
            else:
                fix_msg = (
                    f"Move the volatile '{{% {kind} ... %}}' block referencing "
                    f"'{var}' toward the bottom of the instruction so earlier "
                    "static tokens stay cached."
                )
            results.append(
                self.make_result(
                    file=rel,
                    line=_line_of(masked, offset),
                    message=(
                        f"Volatile variable '{var}' used in template {kind} at "
                        f"~{pct}% into instruction (~{trailing_tokens} tokens "
                        "follow below); mutating it invalidates prompt prefix "
                        "caching on every turn"
                    ),
                    fix=fix_msg,
                )
            )
        return results


# ── I023: Unsupported CES Jinja Subset (filters, loop.*, funcs, balance) ─


@rule("instructions")
class ModularUnsupportedJinjaSubset(Rule):
    id = "I023"
    name = "modular-unsupported-jinja-subset"
    description = (
        "Complements I020: flags unsupported filters (silent literal-text "
        "fallback), unsupported loop.* attrs, unknown functions/macros, "
        "unsupported tags, nested braces, and unbalanced blocks offline"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        masked = _mask_regions(content)
        rel = _rel_path(file_path, context)
        results: list[LintResult] = []
        macros = _defined_macro_names(masked)
        allowed_funcs = SUPPORTED_FUNCTIONS | macros

        # 1. Control block checks ({% ... %} and {{% ... %}})
        block_stack: list[tuple[str, int]] = []
        for m in _BLOCK_TAG_RE.finditer(masked):
            raw_body = m.group(1).strip()
            line = _line_of(masked, m.start())
            if not raw_body:
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message="Empty Jinja control block tag '{% %}'",
                        fix=(
                            "Remove empty block tag or supply a valid "
                            "directive."
                        ),
                    )
                )
                continue

            parts = raw_body.split(None, 2)
            kw = parts[0]
            if kw == "else" and len(parts) >= 2 and parts[1] == "if":
                kw = "elif"
                rest = parts[2] if len(parts) > 2 else ""
            else:
                rest = raw_body[len(kw) :].strip()

            if kw not in SUPPORTED_BLOCK_KEYWORDS:
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Unsupported CES Jinja block tag "
                            f"'{{% {kw} ... %}}'; supported tags are: if, "
                            "elif, else, endif, for, endfor, macro, endmacro"
                        ),
                        fix=(
                            "Move variable assignments ({% set %}) to "
                            "before_agent_callback or rewrite using supported "
                            "blocks."
                        ),
                    )
                )
                continue

            rest_no_quotes = _strip_quotes(rest)
            if _NESTED_BRACE_RE.search(rest_no_quotes):
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            "Nested curly braces are not allowed inside "
                            f"control block tag '{{% {raw_body[:40]} %}}'"
                        ),
                        fix=(
                            "Reference variables directly without braces "
                            "inside {% ... %} (e.g., {% if balance > 100 %})."
                        ),
                    )
                )

            if kw in ("if", "elif", "for"):
                self._check_expr(
                    rest_no_quotes,
                    line,
                    rel,
                    allowed_funcs,
                    results,
                    is_control=True,
                )

            if kw in ("if", "for", "macro"):
                block_stack.append((kw, line))
            elif kw in ("elif", "else"):
                if not block_stack or block_stack[-1][0] != "if":
                    results.append(
                        self.make_result(
                            file=rel,
                            line=line,
                            message=(
                                f"Unexpected '{{% {kw} %}}' outside an open "
                                "'if' block"
                            ),
                            fix=(
                                "Ensure every elif/else belongs to a matching "
                                "{% if %}."
                            ),
                        )
                    )
            elif kw == "endif":
                if not block_stack or block_stack[-1][0] != "if":
                    results.append(
                        self.make_result(
                            file=rel,
                            line=line,
                            message=(
                                "Unexpected '{% endif %}' without a matching "
                                "'{% if %}'"
                            ),
                            fix=(
                                "Remove extra {% endif %} or close the inner "
                                "block first."
                            ),
                        )
                    )
                else:
                    block_stack.pop()
            elif kw == "endfor":
                if not block_stack or block_stack[-1][0] != "for":
                    results.append(
                        self.make_result(
                            file=rel,
                            line=line,
                            message=(
                                "Unexpected '{% endfor %}' without a matching "
                                "'{% for %}'"
                            ),
                            fix=(
                                "Remove extra {% endfor %} or close the inner "
                                "block first."
                            ),
                        )
                    )
                else:
                    block_stack.pop()
            elif kw == "endmacro":
                if not block_stack or block_stack[-1][0] != "macro":
                    results.append(
                        self.make_result(
                            file=rel,
                            line=line,
                            message=(
                                "Unexpected '{% endmacro %}' without a "
                                "matching '{% macro %}'"
                            ),
                            fix="Remove extra {% endmacro %}.",
                        )
                    )
                else:
                    block_stack.pop()

        for open_kw, open_line in block_stack:
            end_kw = f"end{open_kw}"
            results.append(
                self.make_result(
                    file=rel,
                    line=open_line,
                    message=(
                        f"Unclosed '{{% {open_kw} ... %}}' block "
                        f"(missing '{{% {end_kw} %}}')"
                    ),
                    fix=f"Add matching '{{% {end_kw} %}}'.",
                )
            )

        # 2. Print tag checks ({{ ... }}) — catches silent literal fallbacks.
        for m in _PRINT_TAG_RE.finditer(masked):
            raw_expr = m.group(1).strip()
            if not raw_expr:
                continue
            line = _line_of(masked, m.start())
            expr_no_quotes = _strip_quotes(raw_expr)
            if _NON_VAR_PRINT_RE.match(expr_no_quotes):
                continue
            self._check_expr(
                expr_no_quotes,
                line,
                rel,
                allowed_funcs,
                results,
                is_control=False,
            )

        return results

    def _check_expr(
        self,
        expr_no_quotes: str,
        line: int,
        rel: str,
        allowed_funcs: set[str] | frozenset[str],
        results: list[LintResult],
        *,
        is_control: bool,
    ) -> None:
        # Unsupported filters (`| foo`) degrade silently to literal text.
        if "|" in expr_no_quotes:
            parts = expr_no_quotes.split("|")[1:]
            for fseg in parts:
                fm = _FILTER_NAME_RE.match(fseg)
                if fm and fm.group(1) not in SUPPORTED_FILTERS:
                    fname = fm.group(1)
                    results.append(
                        self.make_result(
                            file=rel,
                            line=line,
                            message=(
                                f"Unsupported CES Jinja filter '| {fname}' "
                                "(silently degrades to literal '{{ ... }}' "
                                "text at runtime)"
                            ),
                            fix=(
                                "Supported filters are: length, upper, "
                                f"default. Move '{fname}' transformation into "
                                "before_agent_callback."
                            ),
                        )
                    )

        # Unsupported loop.* attributes (only loop.last is supported).
        for lm in _LOOP_ATTR_RE.finditer(expr_no_quotes):
            attr = lm.group(1)
            if attr not in SUPPORTED_LOOP_ATTRS:
                if attr == "length":
                    fix = "Use `collection | length` for total count."
                else:
                    fix = (
                        "Only `loop.last` is supported in the CES template "
                        "engine. Pre-compute index/is_first inside "
                        "before_agent_callback or use a plain bullet list."
                    )
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Unsupported loop attribute 'loop.{attr}' "
                            "(silently resolves to null at runtime; "
                            "only 'loop.last' is supported)"
                        ),
                        fix=fix,
                    )
                )

        # Unknown function/macro calls (skip filter calls like `| default()`).
        expr_no_filters = (
            expr_no_quotes if is_control else expr_no_quotes.split("|", 1)[0]
        )
        for cm in _FUNC_CALL_RE.finditer(expr_no_filters):
            fn_name = cm.group(1)
            if fn_name in allowed_funcs or fn_name in JINJA_KEYWORDS:
                continue
            preceding = expr_no_filters[: cm.start()].rstrip()
            if preceding.endswith("|") and fn_name in SUPPORTED_FILTERS:
                continue
            results.append(
                self.make_result(
                    file=rel,
                    line=line,
                    message=(
                        f"Unknown CES template function/macro "
                        f"'{fn_name}(...)' (built-ins: default, length, "
                        "in_time_window)"
                    ),
                    fix=(
                        f"Define '{{% macro {fn_name}(...) %}}' in this same "
                        "instruction file or move logic to "
                        "before_agent_callback."
                    ),
                )
            )


# ── I024: Modality & Channel Block Tag Validator ─────────────────────────


@rule("instructions")
class ModularChannelModalityTag(Rule):
    id = "I024"
    name = "modular-channel-modality-tag"
    description = (
        "Validates {@startModality_*}/{@startChannel_*} tag names against CES "
        "allowlists, checks open/close stack parity, and warns when tags wrap "
        "top-level XML sections"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        masked = _mask_regions(content)
        rel = _rel_path(file_path, context)
        results: list[LintResult] = []

        # Stack of (kind, canonical_value, raw_key, raw_tag, body_start, line).
        stack: list[tuple[str, str, str, str, int, int]] = []

        for m in _ANY_CES_TAG_RE.finditer(masked):
            direction, kind, raw_key = m.group(1), m.group(2), m.group(3)
            raw_tag = m.group(0)
            line = _line_of(masked, m.start())

            if kind == "Modality":
                canonical = MODALITY_MAP.get(raw_key)
                if canonical is None:
                    results.append(
                        self.make_result(
                            file=rel,
                            line=line,
                            message=(
                                f"Invalid modality tag '{raw_tag}'. Supported: "
                                "Text/TEXT, Voice/VOICE"
                            ),
                            fix=(
                                "Replace with a valid modality tag "
                                "(e.g., {@startModality_Voice})."
                            ),
                        )
                    )
                    continue
            else:
                canonical = CHANNEL_MAP.get(raw_key)
                if canonical is None:
                    results.append(
                        self.make_result(
                            file=rel,
                            line=line,
                            message=(
                                f"Invalid channel tag '{raw_tag}'. Supported "
                                "include API, GoogleTelephonyPlatform, "
                                "GoogleCCaaSVoice, Twilio, Five9, AudioCodes, "
                                "WhatsApp, Instagram, GoogleCCaaSChat, WEB_UI, "
                                "WebWidget (and uppercase enum aliases)"
                            ),
                            fix="Check exact CES channel tag spelling.",
                        )
                    )
                    continue

            if direction == "start":
                stack.append((kind, canonical, raw_key, raw_tag, m.end(), line))
            elif not stack:
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Unexpected closing tag '{raw_tag}' with no "
                            "matching start tag"
                        ),
                        fix=(
                            "Remove stray closing tag or add its matching "
                            "start tag."
                        ),
                    )
                )
            else:
                (
                    open_kind,
                    open_canon,
                    open_raw_key,
                    open_tag,
                    body_start,
                    open_line,
                ) = stack.pop()
                if (open_kind, open_canon) != (kind, canonical):
                    results.append(
                        self.make_result(
                            file=rel,
                            line=line,
                            message=(
                                f"Mismatched closer '{raw_tag}' for "
                                f"'{open_tag}' opened at line {open_line} "
                                "(CES translates all closers identically to "
                                "{% endif %}, masking mismatched tags)"
                            ),
                            fix=(
                                f"Close '{open_tag}' with "
                                f"{{@end{open_kind}_{open_raw_key}}}."
                            ),
                        )
                    )
                else:
                    body = masked[body_start : m.start()]
                    xml_m = _TOP_LEVEL_XML_RE.search(body)
                    if xml_m:
                        xml_tag = xml_m.group(1)
                        results.append(
                            self.make_result(
                                file=rel,
                                line=_line_of(
                                    masked, body_start + xml_m.start()
                                ),
                                severity=Severity.WARNING,
                                message=(
                                    f"Top-level XML section '<{xml_tag}>' is "
                                    f"wrapped inside conditional '{open_tag}' "
                                    "(keep structural XML static)"
                                ),
                                fix=(
                                    f"Move '{open_tag}...{raw_tag}' inside "
                                    f"<{xml_tag}>...</{xml_tag}>."
                                ),
                            )
                        )

        for open_kind, _canon, open_key, open_tag, _start, open_line in stack:
            results.append(
                self.make_result(
                    file=rel,
                    line=open_line,
                    message=(
                        f"Unclosed tag '{open_tag}' "
                        f"(missing matching {{@end{open_kind}_{open_key}}})"
                    ),
                    fix=f"Add matching '{{@end{open_kind}_{open_key}}}'.",
                )
            )
        return results


# ── I025: Unmasked Modality-Scoped Tools / Widgets ───────────────────────


@rule("instructions")
class ModularUnmaskedModalityTool(Rule):
    id = "I025"
    name = "modular-unmasked-modality-tool"
    description = (
        "Warn when a {@TOOL: ...} or {@WIDGET: ...} is referenced strictly "
        "inside a single modality block without callback masking or an "
        "explicit prohibition in the opposite modality branch"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        masked = _mask_regions(content)
        text_spans: list[tuple[int, int]] = []
        voice_spans: list[tuple[int, int]] = []
        stack: list[tuple[str, int]] = []

        for m in _MODALITY_TAG_RE.finditer(masked):
            direction, raw_key = m.group(1), m.group(2)
            canon = MODALITY_MAP.get(raw_key)
            if canon is None:
                continue
            if direction == "start":
                stack.append((canon, m.end()))
            elif stack and stack[-1][0] == canon:
                _, start_idx = stack.pop()
                if canon == "TEXT":
                    text_spans.append((start_idx, m.start()))
                else:
                    voice_spans.append((start_idx, m.start()))

        if not text_spans and not voice_spans:
            return []

        def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
            return any(s <= pos < e for s, e in spans)

        refs_by_mod: dict[tuple[str, str], set[str]] = {}
        first_offset: dict[tuple[str, str], int] = {}
        for m in _TOOL_OR_WIDGET_REF_RE.finditer(masked):
            kind, name = m.group(1), m.group(2)
            key = (kind, name)
            first_offset.setdefault(key, m.start())
            bucket = refs_by_mod.setdefault(key, set())
            if _in_spans(m.start(), text_spans):
                bucket.add("TEXT")
            elif _in_spans(m.start(), voice_spans):
                bucket.add("VOICE")
            else:
                bucket.add("SHARED")

        callback_text = ""
        agent_dir = _find_agent_dir(file_path)
        if agent_dir is not None:
            chunks: list[str] = []
            for py_file in _iter_agent_callback_files(
                agent_dir, "before_model_callbacks"
            ):
                with contextlib.suppress(OSError):
                    chunks.append(py_file.read_text(encoding="utf-8"))
            callback_text = "\n".join(chunks)

        has_modality_mask = (
            "_session" in callback_text and "modality" in callback_text
        )
        voice_body = "\n".join(masked[s:e] for s, e in voice_spans).lower()
        text_body = "\n".join(masked[s:e] for s, e in text_spans).lower()
        rel = _rel_path(file_path, context)
        results: list[LintResult] = []

        for (kind, name), mods in sorted(refs_by_mod.items()):
            if mods not in ({"TEXT"}, {"VOICE"}):
                continue
            scoped_mod = next(iter(mods))
            opposite = "VOICE" if scoped_mod == "TEXT" else "TEXT"
            opp_body = voice_body if opposite == "VOICE" else text_body

            if has_modality_mask and name in callback_text:
                continue
            prohib_re = re.compile(
                rf"(?:do not|never|forbid|avoid|must not)[^.\n]*"
                rf"(?:{re.escape(name.lower())}|widget)"
            )
            if prohib_re.search(opp_body):
                continue

            results.append(
                self.make_result(
                    file=rel,
                    line=_line_of(masked, first_offset[(kind, name)]),
                    message=(
                        f"{{@{kind}: {name}}} is referenced only inside "
                        f"{scoped_mod} blocks, but remains unmasked in "
                        f"{opposite} (no _session.modality mask in "
                        "before_model_callback or opposite-modality "
                        "prohibition)"
                    ),
                    fix=(
                        f"Mask '{name}' for {opposite} in "
                        "before_model_callback using "
                        "callback_context.get_variable('_session', {})"
                        ".get('modality'), or add an explicit prohibition "
                        f"inside {{@startModality_{opposite.title()}}}."
                    ),
                )
            )
        return results


# ── I026: Hyphenated Variable Names ──────────────────────────────────────


@rule("instructions")
class ModularHyphenatedVarInExpr(Rule):
    id = "I026"
    name = "modular-hyphenated-var-in-expr"
    description = (
        "Flags hyphenated variable names inside {% if %}/{% elif %}/{% for %} "
        "expressions or filtered prints {{ var | filter }}"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        masked = _mask_regions(content)
        rel = _rel_path(file_path, context)
        results: list[LintResult] = []

        seen_block: set[str] = set()
        for m in _BLOCK_TAG_RE.finditer(masked):
            body = m.group(1).strip()
            parts = body.split(None, 2)
            if not parts:
                continue
            kw = parts[0]
            if kw in ("if", "elif", "for"):
                expr = body[len(kw) :].strip()
            elif kw == "else" and len(parts) >= 2 and parts[1] == "if":
                expr = parts[2] if len(parts) > 2 else ""
            else:
                continue
            expr_clean = _strip_quotes(expr)
            line = _line_of(masked, m.start())
            for hm in _HYPHENATED_IN_EXPR_RE.finditer(expr_clean):
                name = hm.group(1)
                if name in seen_block:
                    continue
                seen_block.add(name)
                results.append(
                    self.make_result(
                        file=rel,
                        line=line,
                        message=(
                            f"Variable '{name}' contains '-', which the CES "
                            "template engine cannot parse inside "
                            "{% if %}/{% elif %}/{% for %} expressions "
                            "(causes fallback to plain variable substitution)"
                        ),
                        fix=(
                            f"Rename '{name}' to '{name.replace('-', '_')}' "
                            "(or copy/alias it inside before_agent_callback)."
                        ),
                    )
                )

        seen_filter: set[str] = set()
        for m in _FILTERED_PRINT_RE.finditer(masked):
            target = m.group(1).strip()
            if _HYPHENATED_EXACT_RE.match(target) and target not in seen_filter:
                seen_filter.add(target)
                results.append(
                    self.make_result(
                        file=rel,
                        line=_line_of(masked, m.start()),
                        message=(
                            f"Variable '{target}' contains '-', so filters on "
                            "it (e.g., '| upper', '| default') are ignored and "
                            f"emitted as literal text (plain "
                            f"'{{{{{target}}}}}' without filters works)"
                        ),
                        fix=(
                            f"Use underscored name "
                            f"'{target.replace('-', '_')}' to apply filters."
                        ),
                    )
                )
        return results


# ── C020: Missing Turn Guard in before_agent_callback ────────────────────


@rule("callbacks")
class BeforeAgentMissingTurnGuard(Rule):
    id = "C020"
    name = "before-agent-missing-turn-guard"
    description = (
        "Warn when before_agent_callback assigns empty/zero default literals "
        "to session state unconditionally without a one-time init guard "
        "(overwrites conversation memory on Turn 2+)"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        if _resolve_cb_type(file_path) != "before_agent_callbacks":
            return []

        tree = _parse_ast(content)
        if tree is None:
            return []

        entry_fns = [
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "before_agent_callback"
        ]
        if not entry_fns:
            return []

        rel = _rel_path(file_path, context)
        results: list[LintResult] = []
        fix_msg = (
            "Guard one-time initialization so Turn 2+ does not reset state: "
            'add `if state.get("_initialized") == "true": return None` at the '
            "top of `before_agent_callback`, wrap writes in `if not "
            'state.get("_initialized"):`, or use `state.setdefault(key, val)`.'
        )

        for fn in entry_fns:
            state_aliases: set[str] = {"state", "variables"}
            local_guarded: set[str] = set()
            seen_keys: set[str] = set()

            for stmt in fn.body:
                gk, session_wide = _early_return_guard(stmt, state_aliases)
                if session_wide:
                    break
                if gk is not None:
                    local_guarded.add(gk)
                    continue

                if isinstance(stmt, ast.Assign):
                    if (
                        len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)
                        and _is_state_expr(stmt.value, state_aliases)
                    ):
                        state_aliases.add(stmt.targets[0].id)
                        continue
                    if not _is_default_reset_literal(stmt.value):
                        continue
                    for target in stmt.targets:
                        if (
                            isinstance(target, ast.Subscript)
                            and _is_state_expr(target.value, state_aliases)
                            and isinstance(target.slice, ast.Constant)
                            and isinstance(target.slice.value, str)
                        ):
                            key = _normalize_state_key(target.slice.value)
                            if key in local_guarded or key in seen_keys:
                                continue
                            seen_keys.add(key)
                            results.append(
                                self.make_result(
                                    file=rel,
                                    line=stmt.lineno,
                                    message=(
                                        f"before_agent_callback assigns a "
                                        f"default/empty literal to state key "
                                        f"'{key}' with no one-time init guard "
                                        "— resets '{key}' on every turn"
                                    ),
                                    fix=fix_msg,
                                )
                            )
                elif isinstance(stmt, ast.Expr) and isinstance(
                    stmt.value, ast.Call
                ):
                    call = stmt.value
                    fn_node = call.func
                    is_set_var = (
                        isinstance(fn_node, ast.Name)
                        and fn_node.id == "set_variable"
                    ) or (
                        isinstance(fn_node, ast.Attribute)
                        and fn_node.attr == "set_variable"
                    )
                    if (
                        is_set_var
                        and len(call.args) >= 2
                        and isinstance(call.args[0], ast.Constant)
                        and isinstance(call.args[0].value, str)
                        and _is_default_reset_literal(call.args[1])
                    ):
                        key = _normalize_state_key(call.args[0].value)
                        if key not in local_guarded and key not in seen_keys:
                            seen_keys.add(key)
                            results.append(
                                self.make_result(
                                    file=rel,
                                    line=stmt.lineno,
                                    message=(
                                        f"before_agent_callback assigns a "
                                        f"default/empty literal to state key "
                                        f"'{key}' with no one-time init guard "
                                        "— resets '{key}' on every turn"
                                    ),
                                    fix=fix_msg,
                                )
                            )
                    elif (
                        isinstance(fn_node, ast.Attribute)
                        and fn_node.attr == "update"
                        and _is_state_expr(fn_node.value, state_aliases)
                        and call.args
                        and isinstance(call.args[0], ast.Dict)
                    ):
                        d = call.args[0]
                        for k_node, v_node in zip(
                            d.keys, d.values, strict=False
                        ):
                            if (
                                isinstance(k_node, ast.Constant)
                                and isinstance(k_node.value, str)
                                and _is_default_reset_literal(v_node)
                            ):
                                key = _normalize_state_key(k_node.value)
                                if (
                                    key not in local_guarded
                                    and key not in seen_keys
                                ):
                                    seen_keys.add(key)
                                    results.append(
                                        self.make_result(
                                            file=rel,
                                            line=stmt.lineno,
                                            message=(
                                                "before_agent_callback resets "
                                                f"state key '{key}' via "
                                                "state.update() with no "
                                                "one-time init guard — resets "
                                                f"'{key}' on every turn"
                                            ),
                                            fix=fix_msg,
                                        )
                                    )
        return results
