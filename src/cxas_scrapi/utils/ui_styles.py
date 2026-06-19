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

"""Common User Interface (UI) styling catalog.

Consolidates shared styles, color schemes, and keybindings for CLI,
Terminal User Interfaces (TUI), and notebook environments.
"""

from InquirerPy.utils import get_style

# Keybinding: map Escape to skip/cancel so fuzzy prompts can be aborted.
ESCAPE_KEYBINDINGS = {"skip": [{"key": "escape"}]}

# Style with a contrasting background for the highlighted row.
# Uses a steel-blue background with white text for the pointer/selected row,
# and magenta for fuzzy-matched characters so they pop against both
# light and dark terminal themes.
PROMPT_STYLE = get_style(
    {
        "pointer": "#ffffff bg:#4a6fa5",
        "fuzzy_match": "#ff79c6 bold",
        "fuzzy_prompt": "#6c99bb",
        "fuzzy_info": "#888888",
        "fuzzy_border": "#4a6fa5",
        "questionmark": "#6c99bb bold",
        "answer": "#61afef",
        "input": "#98c379",
        "marker": "#e5c07b",
    },
    style_override=False,
)
