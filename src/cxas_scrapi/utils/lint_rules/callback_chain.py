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

"""Callback chain lint rules (C101-C109) integrated into `cxas lint`."""

from __future__ import annotations

from pathlib import Path

from cxas_scrapi.evals.callback_chain.lint import (
    CallbackChainLintFinding,
    lint_stage_chain,
)
from cxas_scrapi.evals.callback_chain.runner import CallbackChainRunner
from cxas_scrapi.evals.callback_chain.semantics import (
    STAGE_TO_FUNC_NAME,
    normalize_image_version,
    normalize_stage,
)
from cxas_scrapi.evals.callback_chain.traces import (
    CallbackSpec,
    ImageVersion,
    StageType,
)
from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)


def _find_agent_dir(file_path: Path) -> Path | None:
    """Finds the enclosing `agents/<agent_name>` dir for a callback file."""
    curr = file_path.parent
    for _ in range(5):
        if curr.parent.name == "agents":
            return curr
        if curr.parent == curr:
            break
        curr = curr.parent
    return None


def _infer_stage(file_path: Path, content: str) -> StageType | None:
    """Infers the lifecycle stage from callback path or function definition."""
    for part in reversed(file_path.parts):
        if part.endswith("_callbacks") or part in STAGE_TO_FUNC_NAME:
            try:
                return normalize_stage(part)
            except ValueError:
                continue
    for stage, func_name in STAGE_TO_FUNC_NAME.items():
        if f"def {func_name}" in content:
            return stage
    return None


def _get_image_version_from_context(
    context: LintContext, rule_id: str | None = None
) -> ImageVersion:
    """Extracts configured `image_version` from per-rule or global options."""
    opts = getattr(context, "options", None) or {}
    raw_ver = None
    cfg = getattr(context, "config", None)
    if cfg is not None and rule_id and hasattr(cfg, "get_options"):
        rule_opts = cfg.get_options(rule_id)
        if isinstance(rule_opts, dict):
            raw_ver = rule_opts.get("image_version")
    if raw_ver is None and rule_id and isinstance(opts.get(rule_id), dict):
        raw_ver = opts[rule_id].get("image_version")
    if raw_ver is None:
        raw_ver = opts.get("image_version")
    if raw_ver is None:
        for r_opt in opts.values():
            if isinstance(r_opt, dict) and r_opt.get("image_version"):
                raw_ver = r_opt.get("image_version")
                break
    try:
        return normalize_image_version(str(raw_ver or "v1"))
    except ValueError:
        return "v1"


def _run_agent_lint(
    agent_dir: Path,
    app_root: Path | None,
    image_version: ImageVersion,
    context: LintContext,
) -> list[CallbackChainLintFinding]:
    """Loads `CallbackChainRunner` for `agent_dir` and caches findings."""
    ctx_cache = getattr(context, "cache", None)
    if not isinstance(ctx_cache, dict):
        ctx_cache = {}
        context.cache = ctx_cache  # type: ignore[attr-defined]

    cache: dict[tuple[str, str], list[CallbackChainLintFinding]] = (
        ctx_cache.setdefault("callback_chain_lint_cache", {})
    )
    global_cache: list[CallbackChainLintFinding] = ctx_cache.setdefault(
        "callback_chain_global_findings", []
    )
    load_errors: dict[str, str] = ctx_cache.setdefault(
        "callback_chain_load_errors", {}
    )

    resolved_agent_path = agent_dir.resolve()
    resolved_agent = str(resolved_agent_path)
    cache_key = (resolved_agent, image_version)
    if cache_key not in cache:
        try:
            runner = CallbackChainRunner.from_agent_dir(
                agent_dir,
                app_dir=app_root,
                image_version=image_version,
            )
            # Fallback discovery for stage dirs when <agent>.json lacks callback
            # entries
            for st in STAGE_TO_FUNC_NAME:
                if not runner.get_callbacks(st, include_disabled=True):
                    stage_dir = agent_dir / f"{st}_callbacks"
                    if stage_dir.is_dir():
                        discovered_specs: list[CallbackSpec] = []
                        for cb_code_file in sorted(
                            stage_dir.glob("*/python_code.py")
                        ):
                            discovered_specs.append(
                                CallbackSpec(
                                    name=cb_code_file.parent.name,
                                    stage=st,
                                    python_code=cb_code_file.read_text(
                                        encoding="utf-8"
                                    ),
                                    path=str(cb_code_file.resolve()),
                                )
                            )
                        if discovered_specs:
                            runner.set_callbacks(st, discovered_specs)
            agent_findings = runner.lint(
                image_version=image_version,
                include_info=False,
            )
            cache[cache_key] = agent_findings
            for f in agent_findings:
                if f.path:
                    f_path_obj = Path(f.path).resolve()
                    f_resolved = str(f_path_obj)
                    if not f_path_obj.is_relative_to(resolved_agent_path):
                        dedup_key = (f.rule_id, f_resolved, f.line)
                        existing_keys = {
                            (
                                gf.rule_id,
                                str(Path(gf.path).resolve()),
                                gf.line,
                            )
                            for gf in global_cache
                            if gf.path
                        }
                        if dedup_key not in existing_keys:
                            global_cache.append(f)
        except Exception as exc:
            err_msg = (
                f"Failed to load callback chain for agent '{agent_dir.name}': "
                f"{type(exc).__name__}: {exc}"
            )
            load_errors[resolved_agent] = err_msg
            cache[cache_key] = []
    return cache[cache_key]


def _get_agent_chain_findings(
    file_path: Path,
    content: str,
    context: LintContext,
    *,
    rule_id: str | None = None,
) -> list[CallbackChainLintFinding]:
    """Computes and caches callback chain findings for `file_path`."""
    image_version = _get_image_version_from_context(context, rule_id=rule_id)
    app_root = getattr(context, "app_root", None) or getattr(
        context, "project_root", None
    )
    agent_dir = _find_agent_dir(file_path)
    if (
        agent_dir is None
        and file_path.suffix == ".json"
        and file_path.parent.parent.name == "agents"
    ):
        agent_dir = file_path.parent

    try:
        target_resolved = str(file_path.resolve())
    except Exception:
        target_resolved = str(file_path)

    ctx_cache = getattr(context, "cache", None)
    if not isinstance(ctx_cache, dict):
        ctx_cache = {}
        context.cache = ctx_cache  # type: ignore[attr-defined]

    if agent_dir is not None and agent_dir.is_dir():
        findings = _run_agent_lint(agent_dir, app_root, image_version, context)
        load_errors: dict[str, str] = ctx_cache.get(
            "callback_chain_load_errors", {}
        )
        resolved_agent = str(agent_dir.resolve())
        if resolved_agent in load_errors:
            first_file_by_agent: dict[str, str] = ctx_cache.setdefault(
                "c109_first_file_by_agent", {}
            )
            if (
                first_file_by_agent.setdefault(resolved_agent, target_resolved)
                == target_resolved
            ):
                return [
                    CallbackChainLintFinding(
                        rule_id="C109",
                        severity="ERROR",
                        stage="before_agent",
                        callback_name=agent_dir.name,
                        message=load_errors[resolved_agent],
                        line=1,
                        path=target_resolved,
                    )
                ]
            return []

        if file_path.suffix == ".json":
            return []

        matched = [
            f
            for f in findings
            if f.path and str(Path(f.path).resolve()) == target_resolved
        ]
        if matched or file_path.is_file():
            return matched

    # If `file_path` is a global callback file inside `app_root`, analyze agents
    # in `app_root` first
    if app_root is not None and Path(app_root).is_dir():
        agents_root = Path(app_root) / "agents"
        if agents_root.is_dir():
            for ag_sub in sorted(agents_root.iterdir()):
                if ag_sub.is_dir():
                    _run_agent_lint(
                        ag_sub, Path(app_root), image_version, context
                    )
            global_cache: list[CallbackChainLintFinding] = ctx_cache.get(
                "callback_chain_global_findings", []
            )
            matched_global = [
                f
                for f in global_cache
                if f.path and str(Path(f.path).resolve()) == target_resolved
            ]
            if matched_global or file_path.is_file():
                return matched_global

    # Direct single-file fallback (e.g., unit test calling `rule.check` with
    # synthetic path)
    stage = _infer_stage(file_path, content)
    if stage is None:
        return []
    spec = CallbackSpec(
        name=file_path.parent.name or f"{stage}_callbacks_01",
        stage=stage,
        python_code=content,
        path=str(file_path),
    )
    return lint_stage_chain(
        stage,
        [spec],
        image_version=image_version,
        include_info=False,
    )


class _BaseCallbackChainRule(Rule):
    """Base adapter mapping `CallbackChainLintFinding` to `LintResult`."""

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        try:
            rel = str(file_path.relative_to(context.project_root))
        except ValueError:
            rel = str(file_path)

        findings = _get_agent_chain_findings(
            file_path, content, context, rule_id=self.id
        )
        results: list[LintResult] = []
        ctx_cache = getattr(context, "cache", None)
        if not isinstance(ctx_cache, dict):
            ctx_cache = {}
            context.cache = ctx_cache  # type: ignore[attr-defined]
        emitted_global: set[tuple[str, str, int | None]] = ctx_cache.setdefault(
            "emitted_global_callback_findings", set()
        )

        for f in findings:
            if f.rule_id != self.id:
                continue
            dedup_key = (self.id, rel, f.line)
            if dedup_key in emitted_global:
                continue
            emitted_global.add(dedup_key)
            sev = (
                Severity.ERROR
                if f.severity == "ERROR"
                else (
                    Severity.WARNING
                    if f.severity == "WARNING"
                    else Severity.INFO
                )
            )
            results.append(
                self.make_result(
                    file=rel,
                    line=f.line,
                    message=f.message,
                    severity=sev,
                )
            )

        # Also surface any global callback findings discovered during agent
        # chain analysis when global callbacks were not visited directly.
        global_cache: list[CallbackChainLintFinding] = ctx_cache.get(
            "callback_chain_global_findings", []
        )
        for gf in global_cache:
            if gf.rule_id != self.id or not gf.path:
                continue
            gf_path = Path(gf.path).resolve()
            try:
                gf_rel = str(gf_path.relative_to(context.project_root))
            except ValueError:
                gf_rel = str(gf_path)
            dedup_key = (self.id, gf_rel, gf.line)
            if dedup_key in emitted_global:
                continue
            emitted_global.add(dedup_key)
            sev = (
                Severity.ERROR
                if gf.severity == "ERROR"
                else (
                    Severity.WARNING
                    if gf.severity == "WARNING"
                    else Severity.INFO
                )
            )
            results.append(
                self.make_result(
                    file=gf_rel,
                    line=gf.line,
                    message=gf.message,
                    severity=sev,
                )
            )
        return results


@rule("callbacks")
class UnconditionalShortCircuitRule(_BaseCallbackChainRule):
    """C101: Callback unconditionally returns a non-None override."""

    id = "C101"
    name = "unconditional-callback-short-circuit"
    description = (
        "Callback unconditionally returns a non-None value, making subsequent "
        "callbacks in the same stage chain unreachable."
    )
    default_severity = Severity.WARNING


@rule("callbacks")
class SharedStateReferenceAliasingRule(_BaseCallbackChainRule):
    """C102: Mutable local variable assigned to multiple state keys."""

    id = "C102"
    name = "shared-state-reference-aliasing"
    description = (
        "Mutable local variable is assigned to multiple session state keys "
        "without `copy`/`deepcopy`, creating a shared reference aliasing "
        "hazard in 'combined' mode."
    )
    default_severity = Severity.WARNING


@rule("callbacks")
class UndeclaredStateSubscriptReadRule(_BaseCallbackChainRule):
    """C103: Direct subscript read `state['k']` on an undeclared state key."""

    id = "C103"
    name = "undeclared-state-subscript-read"
    description = (
        "Direct subscript read `state['k']` on a key not declared in "
        "`app.json` and not written by any earlier or same-agent callback."
    )
    default_severity = Severity.WARNING


@rule("callbacks")
class StateNoneAssignmentDeletionRule(_BaseCallbackChainRule):
    """C104: Assigning `None` to `state['k']` deletes the key."""

    id = "C104"
    name = "state-none-deletes-key"
    description = (
        "Assigning `None` to `state['k']` deletes the key in the CES runtime, "
        "affecting `'k' in state` or `state['k']` subscript reads in the same "
        "agent."
    )
    default_severity = Severity.WARNING


@rule("callbacks")
class InvalidToolCallbackReturnRule(_BaseCallbackChainRule):
    """C105: Tool callback returns empty dict `{}` or non-dict literal."""

    id = "C105"
    name = "invalid-tool-callback-return"
    description = (
        "Tool callback returns empty dict `{}` (which diverges between "
        "'per_callback' and 'combined' modes) or a non-dict literal."
    )
    default_severity = Severity.WARNING


@rule("callbacks")
class UnreturnedLlmResponseMutationRule(_BaseCallbackChainRule):
    """C106: `after_model` callback mutates `llm_response` without return."""

    id = "C106"
    name = "unreturned-llm-response-mutation"
    description = (
        "`after_model` callback mutates `llm_response` in-place on a "
        "control-flow path that returns `None`; in-place mutations are "
        "discarded unless the response is returned."
    )
    default_severity = Severity.WARNING


@rule("callbacks")
class UnsupportedLlmRequestMutationRule(_BaseCallbackChainRule):
    """C107: `before_model` callback mutates non-persisted `llm_request`."""

    id = "C107"
    name = "unsupported-llm-request-mutation"
    description = (
        "`before_model` callback mutates `llm_request` attributes other than "
        "`config.system_instruction`, `config.excluded_tools`, or `contents`."
    )
    default_severity = Severity.WARNING


@rule("callbacks")
class NonStringStateDictKeyV2Rule(_BaseCallbackChainRule):
    """C108: State assignment contains a non-string dict key under V2 image."""

    id = "C108"
    name = "non-string-state-dict-key-v2"
    description = (
        "State assignment contains a non-string dict key, which raises "
        "`TypeError: Dict key must be str` under V2 image (`orjson`)."
    )
    default_severity = Severity.ERROR


@rule("callbacks")
class CallbackChainLoadErrorRule(_BaseCallbackChainRule):
    """C109: Failed to load agent or callback chain configuration."""

    id = "C109"
    name = "callback-chain-load-error"
    description = (
        "Failed to load callback chain configuration or referenced "
        "`pythonCode` file for an agent."
    )
    default_severity = Severity.ERROR
    target = "agent_config_or_callback"
