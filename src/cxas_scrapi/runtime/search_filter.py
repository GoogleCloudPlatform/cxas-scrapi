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

"""
Optional, temporary Rung 1 bridge for LlmAgent apps for building search filters.
Not required for brand identity (cxas build), incompatible with Guided Agents.
Python-free alternative available today: Use declarative search components.
Platform sunset trigger: Declarative search integration.
"""

from collections.abc import Mapping, Sequence
from typing import Any


def escape_search_filter_literal(raw: str) -> str:
    """Escapes `\\` -> `\\\\` and `"` -> `\\\"`."""
    if not isinstance(raw, str):
        raise ValueError("raw must be a string")
    for ch in raw:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise ValueError(f"Invalid character in filter literal: {ch!r}")

    return raw.replace("\\", "\\\\").replace('"', '\\"')


def quoted_filter_literal(raw: str) -> str:
    """Returns quoted escaped filter literal."""
    return '"' + escape_search_filter_literal(raw) + '"'


def build_scoped_datastore_filter(
    state: Mapping[str, Any], *, extra_clauses: Sequence[str] = ()
) -> str:
    """Reads context keys and emits scoped datastore filter."""
    ctx = state.get("context") or {}
    brand = ctx.get("brand_id")
    country = ctx.get("country_code")
    lang = ctx.get("lang")

    if not brand or not country or not lang:
        raise ValueError(f"Missing required context keys: {ctx}")

    brand_q = quoted_filter_literal(brand)
    country_q = quoted_filter_literal(country)
    lang_q = quoted_filter_literal(lang)

    base = (
        f"brand: ANY({brand_q}) AND country: ANY({country_q})"
        f" AND language: ANY({lang_q})"
    )

    if extra_clauses:
        extra_str = " AND ".join(f"({c})" for c in extra_clauses)
        base = f"{base} AND {extra_str}"

    return base
