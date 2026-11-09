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
Optional, temporary Rung 1 bridge for LlmAgent apps for content blocks.
Not required for brand identity (cxas build), incompatible with Guided Agents.
Python-free alternative available today: Use `cxas build` content macros.
Platform sunset trigger: Content blocks native integration.
"""

from collections.abc import Mapping, MutableMapping
from typing import Any


class MissingContentBlock(LookupError):
    pass


def get_verbatim_content_block(
    catalog: Mapping[str, Any],
    brand_id: str,
    country_code: str,
    lang: str,
    key: str,
) -> str:
    """Deterministic content block lookup."""
    attempts = [
        (brand_id, country_code, lang),
        (brand_id, "DEFAULT", lang),
        ("default", country_code, lang),
        ("default", "DEFAULT", lang),
    ]

    for b, c, lang_code in attempts:
        val = catalog.get(b, {}).get(c, {}).get(lang_code, {}).get(key)
        if val is not None:
            return val

    raise MissingContentBlock(f"{key}/{brand_id}/{country_code}/{lang}")


def resolve_content_block_or_handoff(
    state: MutableMapping[str, Any],
    catalog: Mapping[str, Any],
    key: str,
    *,
    fallback_utterance: str,
    handoff_reason: str = "missing_statutory_copy",
) -> str:
    """Resolves context keys, returning verbatim copy or fail-closed handoff."""
    ctx = state.get("context") or {}
    brand = ctx.get("brand_id")
    country = ctx.get("country_code")
    lang = ctx.get("lang")

    if not brand or not country or not lang:
        state.setdefault("context", {})["handoff_reason"] = handoff_reason
        return fallback_utterance

    try:
        return get_verbatim_content_block(catalog, brand, country, lang, key)
    except MissingContentBlock:
        state.setdefault("context", {})["handoff_reason"] = handoff_reason
        return fallback_utterance
