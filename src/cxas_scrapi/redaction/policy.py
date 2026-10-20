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

"""Declarative redaction policy (``redaction_policy.yaml``).

The policy is the single, app-wide contract describing which kinds of data
must be redacted from persisted conversation data and which must be kept
intact. It is evaluated by ``cxas redaction`` and, optionally, by evals that
declare ``redaction_expectations``.

Example::

    version: 1
    categories:
      - id: payment_card
        action: must_redact
        match: digits
        values: ["4111 1111 1111 1111"]
        keys: [card_number]
      - id: crisis_line
        action: must_not_redact
        match: digits
        values: ["988", "911"]
"""

from __future__ import annotations

import enum
import pathlib
import re
from typing import Any

import pydantic
import yaml

DEFAULT_POLICY_FILENAME = "redaction_policy.yaml"

# How redacted spans usually appear in persisted data: ``[INFO_TYPE]``
# (replace-with-infoType) or masking characters.
DEFAULT_REDACTION_MARKERS = (
    r"\[[A-Z][A-Z0-9_/]*\]",
    r"[#*]{3,}",
)


class Action(str, enum.Enum):
    """What must happen to values of a category in persisted data."""

    MUST_REDACT = "must_redact"
    MUST_NOT_REDACT = "must_not_redact"
    REPORT_ONLY = "report_only"


class MatchMode(str, enum.Enum):
    """How category values are located in text."""

    EXACT = "exact"
    """Case-insensitive substring match."""

    DIGITS = "digits"
    """Digit-sequence match that tolerates spaces, dashes, filler words and
    spoken digits (``"four one one one"``, ``"double one"``, ``"niner"``)."""


class Surface(str, enum.Enum):
    """Parts of a persisted conversation that are checked."""

    USER_TEXT = "user_text"
    AGENT_TEXT = "agent_text"
    TOOL_ARGS = "tool_args"
    TOOL_RESPONSE = "tool_response"
    VARIABLES = "variables"


ALL_SURFACES = tuple(Surface)


class Category(pydantic.BaseModel):
    """A class of data with a single redaction decision."""

    model_config = pydantic.ConfigDict(extra="forbid")

    id: str
    action: Action
    description: str = ""
    match: MatchMode = MatchMode.EXACT
    values: list[str] = pydantic.Field(default_factory=list)
    patterns: list[str] = pydantic.Field(default_factory=list)
    keys: list[str] = pydantic.Field(default_factory=list)
    allow_last: int = 0
    """Trailing digits that may remain visible (e.g. ``4`` for last-four)."""

    @pydantic.field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", v):
            raise ValueError(
                f"category id '{v}' may only contain letters, digits, "
                "'_', '-' and '.'"
            )
        return v

    @pydantic.field_validator("patterns")
    @classmethod
    def _valid_patterns(cls, v: list[str]) -> list[str]:
        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid regex '{pattern}': {e}") from e
        return v

    @pydantic.field_validator("values", mode="before")
    @classmethod
    def _stringify_values(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [str(x) for x in v]
        return v

    @pydantic.field_validator("allow_last")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("allow_last must be >= 0")
        return v


class Thresholds(pydantic.BaseModel):
    """Pass/fail limits applied to a redaction report."""

    model_config = pydantic.ConfigDict(extra="forbid")

    max_leaks: int = 0
    max_partial_leaks: int = 0
    max_over_redactions: int = 0


class RedactionPolicy(pydantic.BaseModel):
    """The app-wide redaction contract."""

    model_config = pydantic.ConfigDict(extra="forbid")

    version: int = 1
    description: str = ""
    categories: list[Category]
    surfaces: list[Surface] = pydantic.Field(
        default_factory=lambda: list(ALL_SURFACES)
    )
    redaction_markers: list[str] = pydantic.Field(
        default_factory=lambda: list(DEFAULT_REDACTION_MARKERS)
    )
    thresholds: Thresholds = pydantic.Field(default_factory=Thresholds)

    @pydantic.field_validator("version")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported policy version {v}; expected 1")
        return v

    @pydantic.field_validator("redaction_markers")
    @classmethod
    def _valid_markers(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("redaction_markers must not be empty")
        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(
                    f"invalid marker regex '{pattern}': {e}"
                ) from e
        return v

    @pydantic.model_validator(mode="after")
    def _unique_ids(self) -> RedactionPolicy:
        seen: set[str] = set()
        for category in self.categories:
            if category.id in seen:
                raise ValueError(f"duplicate category id '{category.id}'")
            seen.add(category.id)
        return self

    def category(self, category_id: str) -> Category | None:
        for c in self.categories:
            if c.id == category_id:
                return c
        return None

    def with_values(
        self, extra_values: dict[str, list[str]] | None
    ) -> RedactionPolicy:
        """Returns a copy with additional per-category sample values."""
        if not extra_values:
            return self
        unknown = sorted(set(extra_values) - {c.id for c in self.categories})
        if unknown:
            raise ValueError(
                f"redaction_expectations.values references unknown "
                f"categories: {', '.join(unknown)}"
            )
        updated = self.model_copy(deep=True)
        for c in updated.categories:
            for value in extra_values.get(c.id, []):
                if str(value) not in c.values:
                    c.values.append(str(value))
        return updated

    @property
    def marker_regex(self) -> re.Pattern[str]:
        return re.compile("|".join(f"(?:{m})" for m in self.redaction_markers))


def load_policy(path: str | pathlib.Path) -> RedactionPolicy:
    """Loads and validates a redaction policy YAML file."""
    p = pathlib.Path(path)
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected a YAML mapping at the top level")
    return RedactionPolicy.model_validate(data)


class RedactionExpectations(pydantic.BaseModel):
    """Optional ``redaction_expectations`` block on an eval test case.

    ``policy`` points at the app-wide policy file (relative paths resolve
    against the eval file's directory). ``values`` declares the sensitive
    values this eval seeds into the conversation, keyed by category id.
    ``require`` lists categories this eval is expected to exercise.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    policy: str | None = None
    values: dict[str, list[str]] = pydantic.Field(default_factory=dict)
    require: list[str] = pydantic.Field(default_factory=list)

    @pydantic.field_validator("values", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return {
                str(k): [str(x) for x in (vals or [])]
                if isinstance(vals, list)
                else [str(vals)]
                for k, vals in v.items()
            }
        return v

    @classmethod
    def parse(cls, raw: Any) -> RedactionExpectations | None:
        """Parses the YAML value (``true``, a path string, or a mapping)."""
        if raw is None or raw is False:
            return None
        if raw is True:
            return cls()
        if isinstance(raw, str):
            return cls(policy=raw)
        if isinstance(raw, dict):
            return cls.model_validate(raw)
        raise ValueError(
            "redaction_expectations must be true, a policy path, or a mapping"
        )

    def resolve_policy(
        self, base_dir: str | pathlib.Path | None = None
    ) -> RedactionPolicy:
        """Loads the referenced policy and merges this eval's values."""
        policy_path = find_policy_file(self.policy, base_dir)
        policy = load_policy(policy_path).with_values(self.values)
        unknown = sorted(set(self.require) - {c.id for c in policy.categories})
        if unknown:
            raise ValueError(
                f"redaction_expectations.require references unknown "
                f"categories: {', '.join(unknown)}"
            )
        return policy


def merge_redaction_expectations(common: Any, case: Any) -> Any:
    """Merges file-level and per-eval ``redaction_expectations`` blocks.

    A per-eval ``false`` opts out of a file-wide block. Mappings are merged:
    ``values`` and ``require`` are unioned and ``policy`` is overridden by
    the eval. Returns ``None`` when neither level opts in.
    """
    if case is False:
        return None
    if common is None or common is False:
        return case
    if case is None:
        return common

    def as_dict(v: Any) -> dict[str, Any]:
        if v is True:
            return {}
        if isinstance(v, str):
            return {"policy": v}
        return dict(v) if isinstance(v, dict) else {}

    base, override = as_dict(common), as_dict(case)
    merged: dict[str, Any] = {**base, **override}
    values: dict[str, list[Any]] = {}
    for src in (base.get("values") or {}, override.get("values") or {}):
        for cat, vals in src.items():
            vals_list = vals if isinstance(vals, list) else [vals]
            bucket = values.setdefault(cat, [])
            bucket.extend(v for v in vals_list if v not in bucket)
    if values:
        merged["values"] = values
    require = list(
        dict.fromkeys(
            [*(base.get("require") or []), *(override.get("require") or [])]
        )
    )
    if require:
        merged["require"] = require
    return merged or True


def find_policy_file(
    explicit: str | None = None,
    base_dir: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Resolves a policy path.

    An explicit path is resolved against ``base_dir`` when relative. Without
    one, ``redaction_policy.yaml`` is searched for in ``base_dir`` and its
    parents, then in the current working directory.
    """
    base = pathlib.Path(base_dir) if base_dir else pathlib.Path.cwd()
    if explicit:
        candidate = pathlib.Path(explicit)
        if not candidate.is_absolute():
            candidate = base / candidate
        if not candidate.exists():
            raise FileNotFoundError(f"redaction policy not found: {candidate}")
        return candidate
    for directory in [base, *base.resolve().parents, pathlib.Path.cwd()]:
        candidate = directory / DEFAULT_POLICY_FILENAME
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"no {DEFAULT_POLICY_FILENAME} found from {base} upwards; pass a "
        "policy path explicitly"
    )
