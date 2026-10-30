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


"""CES schema validation rules (V001-V007).

Validates that resource configs (app, agent, tool, toolset, guardrail,
evaluation, evaluation_expectation) conform to the CES protobuf schema.
Ported from ``utils/validator.py`` — the linter is now the single
validation tool.
"""

import json
import re
import typing
from pathlib import Path

import proto
import yaml
from google.cloud.ces_v1beta import types
from google.protobuf import json_format

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

# ── Shared helpers (ported from Validator) ───────────────────────────────


def _load_json_or_yaml(directory: Path, file_name: str) -> dict:
    """Load config from ``<file_name>.yaml`` or ``<file_name>.json``."""
    yaml_path = directory / f"{file_name}.yaml"
    json_path = directory / f"{file_name}.json"

    if yaml_path.exists():
        with open(yaml_path) as f:
            return yaml.safe_load(f)
    elif json_path.exists():
        with open(json_path) as f:
            return json.load(f)
    else:
        raise FileNotFoundError(
            f"Missing {file_name}.yaml or {file_name}.json in {directory}"
        )


_NON_PATH_KEYS: frozenset[str] = frozenset(
    {
        "default",
        "description",
        "displayName",
        "display_name",
        "name",
        "prompt",
        "text",
        "variableDeclarations",
        "variable_declarations",
        "variableOverrides",
        "variable_overrides",
    }
)


def _resolve_paths(
    data: typing.Any,
    extra_prefixes: typing.Any = (),
    base_path: typing.Any = None,
) -> typing.Any:
    """Recursively replace file-path strings with their contents."""
    if isinstance(data, dict):
        return {
            k: (
                v
                if k in _NON_PATH_KEYS
                else _resolve_paths(v, extra_prefixes, base_path)
            )
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [
            _resolve_paths(item, extra_prefixes, base_path) for item in data
        ]
    elif (
        isinstance(data, str)
        and "://" not in data
        and not any(c.isspace() for c in data)
        and data.endswith((".txt", ".py", ".yaml", ".json"))
    ):
        path_to_check = Path(data)
        resolved = False

        if base_path:
            alt = Path(base_path) / data
            if alt.exists():
                path_to_check = alt
                resolved = True

        if not resolved and extra_prefixes:
            for prefix in extra_prefixes:
                if (
                    data.startswith(prefix)
                    and base_path
                    and prefix in base_path
                ):
                    parts = base_path.rsplit(prefix, 1)
                    if parts:
                        alt = Path(parts[0]) / data
                        if alt.exists():
                            path_to_check = alt
                            resolved = True
                            break

        if resolved or path_to_check.exists():
            with open(path_to_check) as f:
                return f.read()
        else:
            raise FileNotFoundError(f"Referenced file not found: {data}")
    return data


def _to_camel_case(snake_str: str) -> str:
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def _get_required_fields(cls) -> list[str]:  # noqa: ANN001
    """Parse the docstring of a proto class to find required fields."""
    doc = cls.__doc__
    if not doc:
        return []
    lines = doc.split("\n")
    required = []
    cls_name = getattr(cls, "__name__", "")
    for i, line in enumerate(lines):
        match = re.match(r"^\s+(\w+)\s+\([^)]+\):$", line)
        if match and i + 1 < len(lines):  # noqa: SIM102
            if lines[i + 1].strip().startswith("Required."):
                field_name = match.group(1)
                if (
                    cls_name == "VariableDeclaration"
                    and field_name == "description"
                ):
                    continue
                if cls_name == "LlmPolicy" and field_name == "policy_scope":
                    continue
                required.append(field_name)
    return required


def _validate_fields(data: dict, cls, path: str = "") -> None:  # noqa: ANN001
    """Validate required fields and recurse into nested proto messages."""
    required = _get_required_fields(cls)
    cls_name = getattr(cls, "__name__", str(cls))
    if cls_name == "EngineSource" and any(
        bool(data.get(k)) for k in ("dataStoreSources", "data_store_sources")
    ):
        required = [f for f in required if f != "engine"]
    if cls_name == "Scenario" and any(
        k in data
        for k in (
            "evaluationExpectations",
            "evaluation_expectations",
            "taskCompletionBehavior",
            "task_completion_behavior",
            "userGoalBehavior",
            "user_goal_behavior",
            "milestones",
        )
    ):
        required = [
            f for f in required if f not in ("rubrics", "scenario_expectations")
        ]
    missing = [
        f for f in required if f not in data and _to_camel_case(f) not in data
    ]
    if missing:
        raise ValueError(
            f"Missing required fields for {path} {cls_name}: {missing}"
        )

    if hasattr(cls, "meta") and hasattr(cls.meta, "fields"):
        for name, field in cls.meta.fields.items():
            if not (hasattr(field, "message") and field.message is not None):
                continue
            camel_name = _to_camel_case(name)
            field_data = data[name] if name in data else data.get(camel_name)
            if not field_data:
                continue
            sub_path = f"{path}.{name}" if path else name
            if isinstance(field, proto.fields.RepeatedField):
                for item in field_data if isinstance(field_data, list) else []:
                    if isinstance(item, dict) and item:
                        _validate_fields(item, field.message, path=sub_path)
            elif isinstance(field, proto.fields.Field) and isinstance(
                field_data, dict
            ):
                _validate_fields(field_data, field.message, path=sub_path)


def _pop_missing_descriptor_fields(
    msg_dict: typing.Any,
    proto_cls: typing.Any,
    candidate_keys: tuple[str, ...],
) -> None:
    """Remove v1main fields that are not yet present in the v1beta wheel."""
    if not isinstance(msg_dict, dict):
        return
    known = set()
    if hasattr(proto_cls, "meta") and hasattr(proto_cls.meta, "fields"):
        for fname in proto_cls.meta.fields:
            known.add(fname)
            known.add(_to_camel_case(fname))
    for key in candidate_keys:
        if key not in known:
            msg_dict.pop(key, None)


def _strip_v1main_only_fields(data: dict, target: str) -> None:
    """Strip CES v1main fields that lag in the google-cloud-ces v1beta wheel."""
    if target in ("app_config", "agent_config"):
        model_settings = data.get("modelSettings") or data.get("model_settings")
        _pop_missing_descriptor_fields(
            model_settings,
            types.ModelSettings,
            ("thinkingLevel", "thinking_level"),
        )
    elif target == "tool_config":
        ds_tool = data.get("dataStoreTool") or data.get("data_store_tool")
        if isinstance(ds_tool, dict):
            mod_configs = ds_tool.get("modalityConfigs") or ds_tool.get(
                "modality_configs"
            )
            if isinstance(mod_configs, list):
                for mc in mod_configs:
                    _pop_missing_descriptor_fields(
                        mc,
                        types.DataStoreTool.ModalityConfig,
                        ("snippetsConfig", "snippets_config"),
                    )
        remote_tool = data.get("remoteAgentTool") or data.get(
            "remote_agent_tool"
        )
        _pop_missing_descriptor_fields(
            remote_tool,
            types.RemoteAgentTool,
            (
                "apiAuthentication",
                "api_authentication",
                "statefulAgent",
                "stateful_agent",
                "inputVariableMapping",
                "input_variable_mapping",
                "outputVariableMapping",
                "output_variable_mapping",
            ),
        )
    elif target == "guardrail_config":
        action = data.get("action")
        _pop_missing_descriptor_fields(
            action,
            types.TriggerAction,
            (
                "endSession",
                "end_session",
                "toolCall",
                "tool_call",
                "retry",
            ),
        )
    elif target == "evaluation_config":
        scenario = data.get("scenario") or data.get("Scenario")
        _pop_missing_descriptor_fields(
            scenario,
            types.Evaluation.Scenario,
            ("milestones", "initialEvent", "initial_event"),
        )


# ── Schema validation rule ───────────────────────────────────────────────

# Each tuple: (rule_id, name, description, target, proto_type,
#               config_name, extra_prefixes, resolve_paths)
_RESOURCE_SCHEMAS = [
    (
        "V001",
        "schema-app-valid",
        "App config conforms to CES proto schema",
        "app_config",
        types.App,
        "app",
        (),
        True,
    ),
    (
        "V002",
        "schema-agent-valid",
        "Agent config conforms to CES proto schema",
        "agent_config",
        types.Agent,
        None,
        ("agents/",),
        True,
    ),
    (
        "V003",
        "schema-tool-valid",
        "Tool config conforms to CES proto schema",
        "tool_config",
        types.Tool,
        None,
        ("tools/",),
        True,
    ),
    (
        "V004",
        "schema-toolset-valid",
        "Toolset config conforms to CES proto schema",
        "toolset_config",
        types.Toolset,
        None,
        ("toolsets/",),
        True,
    ),
    (
        "V005",
        "schema-guardrail-valid",
        "Guardrail config conforms to CES proto schema",
        "guardrail_config",
        types.Guardrail,
        None,
        (),
        False,
    ),
    (
        "V006",
        "schema-evaluation-valid",
        "Evaluation config conforms to CES proto schema",
        "evaluation_config",
        types.Evaluation,
        None,
        (),
        False,
    ),
    (
        "V007",
        "schema-eval-expectation-valid",
        "Evaluation expectation config conforms to CES proto schema",
        "eval_expectation_config",
        types.EvaluationExpectation,
        None,
        (),
        False,
    ),
]


class SchemaValid(Rule):
    """Validates a resource directory against its CES proto schema.

    Parameterized per resource type — instantiated once per entry in
    ``_RESOURCE_SCHEMAS``.
    """

    default_severity = Severity.ERROR

    def __init__(
        self,
        rule_id: typing.Any,
        rule_name: typing.Any,
        desc: typing.Any,
        rule_target: typing.Any,
        proto_type: typing.Any,
        config_name: typing.Any,
        extra_prefixes: typing.Any,
        do_resolve: typing.Any,
    ) -> None:
        self.id = rule_id
        self.name = rule_name
        self.description = desc
        self.target = rule_target
        self._proto_type = proto_type
        self._config_name = config_name
        self._extra_prefixes = extra_prefixes
        self._do_resolve = do_resolve

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        resource_dir = file_path if file_path.is_dir() else file_path.parent
        config_name = self._config_name or resource_dir.name
        rel = str(resource_dir)

        try:
            data = _load_json_or_yaml(resource_dir, config_name)
        except FileNotFoundError as e:
            return [self.make_result(rel, f"Missing config: {e}")]

        if self._do_resolve:
            try:
                data = _resolve_paths(
                    data, self._extra_prefixes, str(resource_dir)
                )
            except FileNotFoundError as e:
                return [self.make_result(rel, f"Missing referenced file: {e}")]

        # Pop custom tools field from app_config to avoid proto validation
        # failure
        if self.target == "app_config" and "tools" in data:
            data.pop("tools")

        # Pop any custom _comment_ fields used for inline documentation
        for key in list(data.keys()):
            if key.startswith("_comment_"):
                data.pop(key)

        if self.target == "evaluation_config":
            lower_keys = {k.lower() for k in data}
            if "turns" in lower_keys or "expectations" in lower_keys:
                # TODO: Deprecate this block of logic if the backend no longer
                # supports these legacy root-level keys (turns/expectations).
                turns = data.pop("turns", None) or data.pop("Turns", None) or []
                expectations = (
                    data.pop("expectations", None)
                    or data.pop("Expectations", None)
                    or []
                )
                data["golden"] = {
                    "turns": turns,
                    "evaluationExpectations": expectations,
                }
                lower_keys.add("golden")

            # Bypass scenario validation if it's a deterministic
            # (golden) evaluation
            if "golden" in lower_keys:
                data.pop("scenario", None)
                data.pop("Scenario", None)

        try:
            _validate_fields(data, self._proto_type, path=str(resource_dir))
        except ValueError as e:
            return [self.make_result(rel, str(e))]

        _strip_v1main_only_fields(data, self.target)

        try:
            obj = self._proto_type()
            json_format.ParseDict(data, obj._pb, ignore_unknown_fields=False)
        except Exception as e:
            return [
                self.make_result(rel, f"Proto schema validation failed: {e}")
            ]

        return []


# Register one rule per resource type
for (
    _id,
    _name,
    _desc,
    _target,
    _proto,
    _cfg,
    _pfx,
    _resolve,
) in _RESOURCE_SCHEMAS:

    def _make_init(
        rid: typing.Any,
        rn: typing.Any,
        rd: typing.Any,
        rt: typing.Any,
        rp: typing.Any,
        rc: typing.Any,
        rpfx: typing.Any,
        rr: typing.Any,
    ) -> typing.Any:
        def __init__(self) -> None:  # noqa: ANN001
            SchemaValid.__init__(
                self,
                rid,
                rn,
                rd,
                rt,
                rp,
                rc,
                rpfx,
                rr,
            )

        return __init__

    cls = type(
        f"SchemaValid_{_id}",
        (SchemaValid,),
        {
            "__init__": _make_init(
                _id,
                _name,
                _desc,
                _target,
                _proto,
                _cfg,
                _pfx,
                _resolve,
            )
        },
    )
    rule("schema")(cls)
