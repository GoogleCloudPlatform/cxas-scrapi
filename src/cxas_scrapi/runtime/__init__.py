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
Optional, temporary Rung 1 bridge for LlmAgent apps.
Not required for brand identity (cxas build), incompatible with Guided Agents.
"""

from .content_blocks import (
    MissingContentBlock,
    get_verbatim_content_block,
    resolve_content_block_or_handoff,
)
from .kill_switch import check_disabled_agent
from .presentation import adapt_presentation
from .search_filter import (
    build_scoped_datastore_filter,
    escape_search_filter_literal,
    quoted_filter_literal,
)

__all__ = [
    "MissingContentBlock",
    "adapt_presentation",
    "build_scoped_datastore_filter",
    "check_disabled_agent",
    "escape_search_filter_literal",
    "get_verbatim_content_block",
    "quoted_filter_literal",
    "resolve_content_block_or_handoff",
]
