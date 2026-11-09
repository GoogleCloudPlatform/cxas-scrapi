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
Optional, temporary Rung 1 bridge for LlmAgent apps for kill switches.
Not required for brand identity (cxas build), incompatible with Guided Agents.
Python-free alternative today: Compile-time manifest exclusions (`cxas build`).
Platform sunset trigger: Agent routing policies.
"""

import json
from collections.abc import Mapping
from typing import Any


def check_disabled_agent(
    state: Mapping[str, Any], agent_name: str, *, fallback_message: str
) -> dict[str, Any] | None:
    """Checks disabled_agents and returns short-circuit response if disabled."""
    ctx = state.get("context", {})
    disabled = ctx.get("disabled_agents")

    disabled_set = set()
    if disabled:
        if isinstance(disabled, (list, set, tuple)):
            disabled_set = set(disabled)
        elif isinstance(disabled, str):
            try:
                parsed = json.loads(disabled)
                if isinstance(parsed, (list, tuple)):
                    disabled_set = set(parsed)
                else:
                    disabled_set = {str(parsed)}
            except json.JSONDecodeError:
                disabled_set = {
                    x.strip() for x in disabled.split(",") if x.strip()
                }

    if agent_name in disabled_set:
        return {
            "short_circuit": True,
            "agent": agent_name,
            "message": fallback_message,
        }
    return None
