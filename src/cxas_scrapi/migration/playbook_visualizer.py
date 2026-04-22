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

"""Playbook-level Rich tree visualizer for DFCX agents."""

import re
from typing import Any, Dict

from rich.markup import escape
from rich.text import Text
from rich.tree import Tree


class PlaybookTreeVisualizer:
    """Generates a detailed Rich Tree for a single Playbook."""

    def __init__(self, playbook_data: Dict[str, Any]):
        self.pb = playbook_data

    def _render_steps(self, parent_node, steps):
        for step in steps:
            text = step.get("text", "")
            if text:
                safe_text = escape(text)
                safe_text = re.sub(
                    r"(\${FLOW:[^}]+})",
                    r"[bold magenta]\1[/]",
                    safe_text,
                )
                safe_text = re.sub(
                    r"(\${TOOL:[^}]+})",
                    r"[bold orange3]\1[/]",
                    safe_text,
                )
                safe_text = re.sub(
                    r"(\${PLAYBOOK:[^}]+})",
                    r"[bold blue]\1[/]",
                    safe_text,
                )
                safe_text = re.sub(
                    r"(\$session\.params\.[a-zA-Z0-9_]+)",
                    r"[bold cyan]\1[/]",
                    safe_text,
                )
                step_node = parent_node.add(f"▪ {safe_text}")
                if "steps" in step:
                    self._render_steps(step_node, step["steps"])

    def build_tree(self) -> Tree:
        """Build and return the Rich Tree for this playbook."""
        root = Tree(
            f"📘 [bold blue]Playbook:[/] "
            f"{self.pb.get('displayName', 'Unnamed')}"
        )
        if "goal" in self.pb:
            root.add(f"[bold]Goal:[/] [dim]{escape(self.pb['goal'])}[/]")

        in_params = self.pb.get("inputParameterDefinitions", [])
        out_params = self.pb.get("outputParameterDefinitions", [])
        if in_params or out_params:
            p_node = root.add("📦 [bold]Parameters[/]")
            if in_params:
                in_node = p_node.add("📥 [green]Input[/]")
                for p in in_params:
                    p_type = (
                        p.get("typeSchema", {})
                        .get("inlineSchema", {})
                        .get("type", "UNKNOWN")
                    )
                    in_node.add(f"[cyan]{p['name']}[/] ([dim]{p_type}[/])")
            if out_params:
                out_node = p_node.add("📤 [magenta]Output[/]")
                for p in out_params:
                    p_type = (
                        p.get("typeSchema", {})
                        .get("inlineSchema", {})
                        .get("type", "UNKNOWN")
                    )
                    out_node.add(f"[cyan]{p['name']}[/] ([dim]{p_type}[/])")

        if "instruction" in self.pb and "steps" in self.pb["instruction"]:
            i_node = root.add("📝 [bold]Instructions & Logic[/]")
            self._render_steps(i_node, self.pb["instruction"]["steps"])

        if (
            "codeBlock" in self.pb
            and "code" in self.pb["codeBlock"]
            and self.pb["codeBlock"]["code"]
        ):
            code_node = root.add("💻 [bold]Code Block[/]")
            code_node.add(Text(self.pb["codeBlock"]["code"], style="dim"))

        return root
