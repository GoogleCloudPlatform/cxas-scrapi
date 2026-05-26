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

"""Component path auto-discovery and template loading helpers for HTML
reporting.
"""

from __future__ import annotations

import functools
import os

# Resolve paths relative to this file
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
COMPONENTS_DIR = os.path.normpath(
    os.path.join(CURRENT_DIR, "../resources/components")
)


@functools.cache
def load_component(relative_path: str) -> str:
    """Helper to load raw component text from resources directory cached E2E."""
    full_path = os.path.join(COMPONENTS_DIR, relative_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()
