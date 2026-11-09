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
Optional, temporary Rung 1 bridge for LlmAgent apps for presentation logic.
Not required for brand identity (cxas build), incompatible with Guided Agents.
Python-free alternative available today: Use declarative channel routing.
Platform sunset trigger: Declarative channels integration.
"""

import json
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any

DIGITAL_CHANNELS = {
    "DIGITAL_APP",
    "WEB",
    "WEBCHAT",
    "MOBILE",
    "WHATSAPP",
    "INSTAGRAM",
}


def adapt_presentation(
    state: Mapping[str, Any],
    spoken_text: str,
    widget_payload: Mapping[str, Any] | None = None,
    *,
    part_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Inspects channel_class and adapts presentation to voice or digital."""
    ctx = state.get("context", {})
    channel_class = ctx.get("channel_class", "")

    if channel_class in DIGITAL_CHANNELS:
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
                )  # type: ignore
        return {
            "channel_mode": "digital",
            "text": spoken_text,
            "widget_json": json_str,
            "part": part_obj,
        }
    else:
        # Voice / telephony
        cleaned_text = spoken_text

        # strip markdown links
        cleaned_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned_text)

        # strip raw urls
        cleaned_text = re.sub(r"https?://[^\s]+", "", cleaned_text)

        # strip emphasis
        cleaned_text = re.sub(
            r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", cleaned_text
        )
        cleaned_text = re.sub(r"`([^`]+)`", r"\1", cleaned_text)

        # collapse extra whitespace
        cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

        return {
            "channel_mode": "voice",
            "text": cleaned_text,
            "widget_json": None,
            "part": None,
        }
