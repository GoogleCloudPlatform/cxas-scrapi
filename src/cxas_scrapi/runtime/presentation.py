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

"""Optional, interim runtime helpers for LlmAgent apps for presentation logic.

Not required for brand identity (`cxas build`), incompatible with Guided Agents.
Python-free alternative available today: Use declarative channel routing.
Platform sunset trigger: Declarative channels integration.
"""

import json
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any

DIGITAL_CHANNELS = frozenset(
    {
        "DIGITAL_APP",
        "WEB",
        "WEBCHAT",
        "MOBILE",
        "WHATSAPP",
        "INSTAGRAM",
    }
)


def adapt_presentation(
    state: Mapping[str, Any],
    spoken_text: str,
    widget_payload: Mapping[str, Any] | None = None,
    *,
    part_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Inspects channel_class and adapts presentation to voice or digital."""
    raw_ctx = state.get("context") if isinstance(state, Mapping) else None
    ctx = raw_ctx if isinstance(raw_ctx, Mapping) else {}
    channel_class = ctx.get("channel_class", "")
    text = "" if spoken_text is None else str(spoken_text)

    if isinstance(channel_class, str) and channel_class in DIGITAL_CHANNELS:
        json_str = (
            json.dumps(widget_payload, sort_keys=True)
            if widget_payload
            else None
        )
        part_obj = None
        if json_str:
            if part_factory:
                part_obj = part_factory(json_str)
            elif "cxas_sandbox" in sys.modules and hasattr(
                sys.modules["cxas_sandbox"], "Part"
            ):
                part_obj = sys.modules["cxas_sandbox"].Part.from_json(
                    data=json_str
                )
        return {
            "channel_mode": "digital",
            "text": text,
            "widget_json": json_str,
            "part": part_obj,
        }

    # Voice / telephony: scrub Markdown without mangling snake_case identifiers.
    cleaned_text = text
    cleaned_text = re.sub(
        r"```[a-zA-Z0-9_+-]*[ \t]*\r?\n?([\s\S]*?)```", r"\1", cleaned_text
    )
    cleaned_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned_text)
    cleaned_text = re.sub(r"https?://[^\s]+", "", cleaned_text)
    cleaned_text = re.sub(
        r"^[ \t]*#{1,6}[ \t]+", "", cleaned_text, flags=re.MULTILINE
    )
    cleaned_text = re.sub(
        r"^[ \t]*[-*+][ \t]+", "", cleaned_text, flags=re.MULTILINE
    )
    cleaned_text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", cleaned_text)
    cleaned_text = re.sub(
        r"(?<!\w)_{1,3}([^_]+)_{1,3}(?!\w)", r"\1", cleaned_text
    )
    cleaned_text = re.sub(r"`([^`]+)`", r"\1", cleaned_text)
    cleaned_text = cleaned_text.replace("`", "")
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

    return {
        "channel_mode": "voice",
        "text": cleaned_text,
        "widget_json": None,
        "part": None,
    }
