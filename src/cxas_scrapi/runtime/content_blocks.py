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

"""Optional, interim runtime helpers for LlmAgent apps for content blocks.

Not required for brand identity (`cxas build`), incompatible with Guided Agents.
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
    if not isinstance(catalog, Mapping):
        raise MissingContentBlock(f"{key}/{brand_id}/{country_code}/{lang}")

    attempts = [
        (brand_id, country_code, lang),
        (brand_id, "DEFAULT", lang),
        ("default", country_code, lang),
        ("default", "DEFAULT", lang),
    ]

    for b, c, lang_code in attempts:
        brand_map = catalog.get(b)
        if not isinstance(brand_map, Mapping):
            continue
        country_map = brand_map.get(c)
        if not isinstance(country_map, Mapping):
            continue
        lang_map = country_map.get(lang_code)
        if not isinstance(lang_map, Mapping):
            continue
        val = lang_map.get(key)
        if isinstance(val, str):
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
    if not isinstance(state, MutableMapping):
        return fallback_utterance

    raw_ctx = state.get("context")
    ctx: MutableMapping[str, Any]
    if isinstance(raw_ctx, MutableMapping):
        ctx = raw_ctx
    else:
        ctx = {}
        state["context"] = ctx

    brand = ctx.get("brand_id")
    country = ctx.get("country_code")
    lang = ctx.get("lang")

    if (
        not isinstance(brand, str)
        or not brand
        or not isinstance(country, str)
        or not country
        or not isinstance(lang, str)
        or not lang
    ):
        ctx["handoff_reason"] = handoff_reason
        return fallback_utterance

    try:
        return get_verbatim_content_block(catalog, brand, country, lang, key)
    except MissingContentBlock:
        ctx["handoff_reason"] = handoff_reason
        return fallback_utterance
