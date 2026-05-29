"""CLI subcommands for GECX App Versions management."""

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

import argparse
import difflib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from typing import Any

from jinja2 import Template
from rich.console import Console
from rich.table import Table

from cxas_scrapi.cli.app import _resolve_app_args
from cxas_scrapi.core.versions import Versions

logger = logging.getLogger(__name__)


# --- Templates for output message and html report ---
CONSOLE_SUMMARY_TEMPLATE = """
[bold blue]Comparing versions for App:[/]
  [bold magenta]{{ display_name }}[/] [dim]({{ app_name }})[/]
  [dim]Source:[/] {{ source }} [bold cyan]({{ source_display_name }})[/]
  [dim]Target:[/] {{ target }} [bold cyan]({{ target_display_name }})[/]

[bold blue]============================================================
  📊 SUMMARY OF APP VERSION DIFF
============================================================[/]
⚙️ [bold]Global App Config[/]: {{ app_status }}
{% if total_agents > 0 -%}
📝 [bold]Agents ({{ total_agents }} changed)[/]:
{% if added_agents %}  [green]➕ Added   :[/] {{ added_agents }}
{% endif -%}
{% if removed_agents %}  [red]➖ Removed :[/] {{ removed_agents }}
{% endif -%}
{% if modified_agents %}  [yellow]🔄 Modified:[/] {{ modified_agents }}
{% endif -%}
{%- else -%}
📝 [bold]Agents[/]: [green]No changes detected[/]
{% endif %}
{% if total_tools > 0 -%}
🛠️ [bold]Tools ({{ total_tools }} changed)[/]:
{% if added_tools %}  [green]➕ Added   :[/] {{ added_tools }}
{% endif -%}
{% if removed_tools %}  [red]➖ Removed :[/] {{ removed_tools }}
{% endif -%}
{% if modified_tools %}  [yellow]🔄 Modified:[/] {{ modified_tools }}
{% endif -%}
{%- else -%}
🛠️ [bold]Tools[/]: [green]No changes detected[/]
{% endif %}
{% if total_guardrails > 0 -%}
🛡️ [bold]Guardrails ({{ total_guardrails }} changed)[/]:
{% if added_guardrails %}  [green]➕ Added   :[/] {{ added_guardrails }}
{% endif -%}
{% if removed_guardrails %}  [red]➖ Removed :[/] {{ removed_guardrails }}
{% endif -%}
{% if modified_guardrails %}  [yellow]🔄 Modified:[/] {{ modified_guardrails }}
{% endif -%}
{%- endif %}
{% if total_toolsets > 0 -%}
🧰 [bold]Toolsets ({{ total_toolsets }} changed)[/]:
{% if added_toolsets %}  [green]➕ Added   :[/] {{ added_toolsets }}
{% endif -%}
{% if removed_toolsets %}  [red]➖ Removed :[/] {{ removed_toolsets }}
{% endif -%}
{% if modified_toolsets %}  [yellow]🔄 Modified:[/] {{ modified_toolsets }}
{% endif -%}
{%- endif %}
[dim]* Use --verbose to print detailed line-by-line diffs to terminal[/]
[dim]* Use --web to force open/view the full interactive HTML diff report[/]
"""
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "utils")


def _load_template(filename: str) -> str:
    template_path = os.path.join(TEMPLATE_DIR, filename)
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


HTML_DIFF_BLOCK_TEMPLATE = _load_template("versions_diff_block_template.html")
HTML_REPORT_TEMPLATE = _load_template("versions_compare_report_template.html")


def _upload_to_codebin(title: str, content: str) -> str | None:
    """Uploads HTML content to Codebin using internal gosso and returns url."""
    gosso_path = "/google/bin/releases/gosso/gosso"
    if not os.path.exists(gosso_path):
        return None

    target_url = "https://codebin.googleplex.com/api/prototypes"
    post_data = {
        "title": title,
        "content": content,
    }

    # Write payload to temp file to avoid shell leakage or escaping issues
    with tempfile.NamedTemporaryFile(
        mode="w+", encoding="utf-8", delete=False, suffix=".json"
    ) as temp_file:
        json.dump(post_data, temp_file)
        temp_file_path = temp_file.name

    try:
        cmd = [
            gosso_path,
            "-method=POST",
            f"-url={target_url}",
            f"-data_file={temp_file_path}",
            "-header=Content-Type: application/json",
            "-header=Accept: application/json",
        ]

        res = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=15
        )

        # Clean up temp file
        try:
            os.remove(temp_file_path)
        except Exception:
            pass

        response_json = json.loads(res.stdout)
        if "id" in response_json:
            doc_id = response_json["id"]
            return f"https://codebin.googleplex.com/view/{doc_id}"
    except Exception as e:
        logger.warning("Codebin upload failed: %s", e)
        # Clean up temp file on error
        try:
            os.remove(temp_file_path)
        except Exception:
            pass
    return None


def app_versions_list(args: argparse.Namespace) -> None:
    """Handles the 'versions list' command."""
    apps_client, app_name, display_name = _resolve_app_args(args.app_name, args)
    console = Console()
    console.print(
        "\n[bold blue]Listing versions for App:[/] [bold"
        f" magenta]{display_name}[/] [dim]({app_name})[/]...\n"
    )

    try:
        v_client = Versions(app_name=app_name)
        versions = v_client.list_versions()

        if not versions:
            console.print("[yellow]No versions found for this app.[/]")
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Version ID", style="dim", width=38)
        table.add_column("Display Name", style="cyan")
        table.add_column("Description", style="yellow")
        table.add_column("Created At", style="green")
        table.add_column("Creator", style="blue")

        for v in versions:
            vd = type(v).to_dict(v) if not isinstance(v, dict) else v
            name = vd.get("name", "?")
            version_id = name.split("/")[-1] if name else "?"

            # Truncate description to fit nicely in standard terminal tables
            desc = vd.get("description", "N/A")
            if len(desc) > 45:
                desc = desc[:42] + "..."

            table.add_row(
                version_id,
                vd.get("display_name", "N/A"),
                desc,
                vd.get("create_time", "N/A"),
                vd.get("creator", "N/A"),
            )

        console.print(table)
        console.print()

    except Exception as e:
        console.print(f"[red]Failed to list app versions: {e}[/]")
        sys.exit(1)


def _print_console_summary(
    console: Console,
    display_name: str,
    app_name: str,
    args: argparse.Namespace,
    v1: Any,
    v2: Any,
    summary_stats: dict[str, Any],
) -> None:
    """Layer 1: Print high-level console summary of configuration drift."""
    app_ch = summary_stats["app_config_changed"]
    app_status = "[yellow]Modified[/]" if app_ch else "[green]Unchanged[/]"

    add_ag = summary_stats["added_agents"]
    rem_ag = summary_stats["removed_agents"]
    mod_ag = summary_stats["modified_agents"]
    total_agents = len(add_ag) + len(rem_ag) + len(mod_ag)

    add_tl = summary_stats["added_tools"]
    rem_tl = summary_stats["removed_tools"]
    mod_tl = summary_stats["modified_tools"]
    total_tools = len(add_tl) + len(rem_tl) + len(mod_tl)

    add_gr = summary_stats.get("added_guardrails", [])
    rem_gr = summary_stats.get("removed_guardrails", [])
    mod_gr = summary_stats.get("modified_guardrails", [])
    total_guardrails = len(add_gr) + len(rem_gr) + len(mod_gr)

    add_ts = summary_stats.get("added_toolsets", [])
    rem_ts = summary_stats.get("removed_toolsets", [])
    mod_ts = summary_stats.get("modified_toolsets", [])
    total_toolsets = len(add_ts) + len(rem_ts) + len(mod_ts)

    summary_rendered = Template(CONSOLE_SUMMARY_TEMPLATE).render(
        display_name=display_name,
        app_name=app_name,
        source=args.source,
        source_display_name=v1.display_name,
        target=args.target,
        target_display_name=v2.display_name,
        app_status=app_status,
        total_agents=total_agents,
        added_agents=add_ag,
        removed_agents=rem_ag,
        modified_agents=mod_ag,
        total_tools=total_tools,
        added_tools=add_tl,
        removed_tools=rem_tl,
        modified_tools=mod_tl,
        total_guardrails=total_guardrails,
        added_guardrails=add_gr,
        removed_guardrails=rem_gr,
        modified_guardrails=mod_gr,
        total_toolsets=total_toolsets,
        added_toolsets=add_ts,
        removed_toolsets=rem_ts,
        modified_toolsets=mod_ts,
    )
    console.print(summary_rendered)


def _print_verbose_diff(
    console: Console, diff_blocks: list[dict[str, Any]]
) -> None:
    """Layer 2: Print detailed syntax color-coded console diff."""
    console.print(
        "\n[bold blue]"
        + "=" * 60
        + "\n  📝 DETAILED LINE-BY-LINE CONSOLE DIFF\n"
        + "=" * 60
        + "[/]"
    )
    if not diff_blocks:
        console.print("[green]No differences found between versions.[/]")
        return

    for block in diff_blocks:
        console.print(f"\n[bold underline]{block['title']}[/]")
        for line in block["diff"].split("\n"):
            escaped = line.replace("[", "\\[").replace("]", "\\]")
            if line.startswith("+") and not line.startswith("+++"):
                console.print(f"[green]+ {escaped}[/]")
            elif line.startswith("-") and not line.startswith("---"):
                console.print(f"[red]- {escaped}[/]")
            elif line.startswith("@@"):
                console.print(f"[cyan]{escaped}[/]")
            else:
                console.print(f"[dim]  {escaped}[/]")


def _generate_html_report(
    console: Console,
    display_name: str,
    args: argparse.Namespace,
    v1: Any,
    v2: Any,
    diff_blocks: list[dict[str, Any]],
) -> None:
    """Layer 3: Generate collapsible HTML report and upload to Codebin."""

    def _resolve_report_path(args) -> str:
        if args.output and args.output.endswith(".html"):
            return os.path.abspath(args.output)

        workspace = os.getcwd()

        project_dir = "."
        pointer = os.path.join(workspace, ".active-project")
        if os.path.exists(pointer):
            with open(pointer) as f:
                name = f.read().strip()
            if name:
                project_dir = os.path.join(workspace, name)

        reports_dir = os.path.join(project_dir, "eval-reports", "comparisons")
        os.makedirs(reports_dir, exist_ok=True)
        return os.path.abspath(
            os.path.join(
                reports_dir, f"compare_{args.source[:8]}_{args.target[:8]}.html"
            )
        )

    # Render Diff Blocks HTML
    html_diff_blocks = []
    for block in diff_blocks:
        lines_html = []
        for line in block["diff"].split("\n"):
            escaped = (
                line.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            if line.startswith("+") and not line.startswith("+++"):
                lines_html.append(
                    f'<span class="line-addition">{escaped}</span>'
                )
            elif line.startswith("-") and not line.startswith("---"):
                lines_html.append(
                    f'<span class="line-deletion">{escaped}</span>'
                )
            elif line.startswith("@@"):
                lines_html.append(f'<span class="line-header">{escaped}</span>')
            else:
                lines_html.append(
                    f'<span class="line-context">{escaped}</span>'
                )

        content_html = "".join(lines_html)
        block_html = Template(HTML_DIFF_BLOCK_TEMPLATE).render(
            title=block["title"],
            path=block["path"],
            content=content_html,
        )
        html_diff_blocks.append(block_html)

    # Wrap in Webpage Template
    diff_blocks_joined = "".join(html_diff_blocks)
    if not diff_blocks_joined:
        diff_blocks_joined = (
            '<div class="card" style="text-align:center;'
            'color:#27ae60;font-weight:bold;">'
            "✅ No differences found between versions.</div>"
        )

    webpage = Template(HTML_REPORT_TEMPLATE).render(
        timestamp=time.strftime("%Y-%m-%d %H:%M"),
        display_name=display_name,
        source=args.source,
        source_display=v1.display_name,
        target=args.target,
        target_display=v2.display_name,
        diff_blocks=diff_blocks_joined,
    )

    gosso_path = "/google/bin/releases/gosso/gosso"
    use_codebin = os.path.exists(gosso_path)

    codebin_url = None
    if use_codebin:
        try:
            console.print("  [dim]Uploading report to Codebin...[/]")
            title = (
                f"Compare: {args.source[:8]} vs {args.target[:8]} "
                f"({display_name})"
            )
            codebin_url = _upload_to_codebin(title, webpage)
        except Exception as e:
            console.print(f"  [yellow]⚠️ Codebin upload failed: {e}[/]")

    # Set report URL and resolve disk writes only if required
    if codebin_url:
        report_url = codebin_url
        # If the user explicitly requested a local output HTML file,
        # we save it permanently.
        if args.output and args.output.endswith(".html"):
            try:
                report_path = _resolve_report_path(args)
                with open(report_path, "w") as f:
                    f.write(webpage)
            except Exception as e:
                console.print(
                    f"  [yellow]⚠️ Could not save local report: {e}[/]"
                )
    else:
        # Fallback: Codebin failed or not used,
        # so we write a local report on disk
        try:
            report_path = _resolve_report_path(args)
            with open(report_path, "w") as f:
                f.write(webpage)
            report_url = f"file://{report_path}"
            if use_codebin:
                console.print(
                    "  [yellow]⚠️ Upload failed. Saved fallback local report.[/]"
                )
        except Exception as e:
            report_url = "Failed to generate report"
            console.print(
                f"  [red]❌ Could not save fallback local report: {e}[/]"
            )

    console.print(
        "\n[bold green]🌐 Self-contained interactive HTML diff report "
        "generated successfully![/]"
    )

    # Terminal OSC 8 Link + dim fallback link
    console.print(
        f"👉 [bold underline cyan hyperlink={report_url}]View Full "
        "Interactive Diff Report[/]"
    )
    console.print(f"  [dim]({report_url})[/]\n")

    if args.web:
        try:
            import webbrowser  # noqa: PLC0415

            webbrowser.open(report_url)
        except Exception:
            pass


def app_versions_compare(args: argparse.Namespace) -> None:
    """Handles the 'versions compare' command."""
    console = Console()

    apps_client, app_name, display_name = _resolve_app_args(args.app_name, args)

    def get_clean_json(proto_message) -> str:
        d = (
            type(proto_message).to_dict(proto_message)
            if not isinstance(proto_message, dict)
            else proto_message
        )
        d.pop("etag", None)
        d.pop("name", None)
        return json.dumps(d, indent=2, sort_keys=True)

    def diff_strings(
        old_text: str,
        new_text: str,
        fromfile: str,
        tofile: str,
    ) -> str:
        diff_lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=fromfile,
                tofile=tofile,
                lineterm="",
            )
        )
        return "\n".join(diff_lines)

    try:
        v_client = Versions(app_name=app_name)
        v1 = v_client.get_version(args.source)
        v2 = v_client.get_version(args.target)

        snap1 = v1.snapshot
        snap2 = v2.snapshot

        summary_stats = {
            "app_config_changed": False,
            "added_agents": [],
            "removed_agents": [],
            "modified_agents": [],
            "added_tools": [],
            "removed_tools": [],
            "modified_tools": [],
            "added_guardrails": [],
            "removed_guardrails": [],
            "modified_guardrails": [],
            "added_toolsets": [],
            "removed_toolsets": [],
            "modified_toolsets": [],
        }

        diff_blocks = []

        # 1. App Config comparison
        app1_json = get_clean_json(snap1.app)
        app2_json = get_clean_json(snap2.app)
        if app1_json != app2_json:
            summary_stats["app_config_changed"] = True
            diff_blocks.append(
                {
                    "title": "⚙️ Global App Configuration Changes",
                    "path": "app.json",
                    "diff": diff_strings(
                        app1_json, app2_json, "a/app.json", "b/app.json"
                    ),
                }
            )

        # 2. Agents comparison
        agents1 = {a.display_name: a for a in snap1.agents}
        agents2 = {a.display_name: a for a in snap2.agents}

        added_agents = set(agents2.keys()) - set(agents1.keys())
        removed_agents = set(agents1.keys()) - set(agents2.keys())
        summary_stats["added_agents"] = list(added_agents)
        summary_stats["removed_agents"] = list(removed_agents)

        for name, agent2 in agents2.items():
            agent1 = agents1.get(name)
            if agent1:
                inst1 = agent1.instruction or ""
                inst2 = agent2.instruction or ""
                tools1 = sorted([t.split("/")[-1] for t in agent1.tools])
                tools2 = sorted([t.split("/")[-1] for t in agent2.tools])

                if inst1 != inst2 or tools1 != tools2:
                    summary_stats["modified_agents"].append(name)
                    agent_diff_text = []
                    if inst1 != inst2:
                        agent_diff_text.append(
                            diff_strings(
                                inst1,
                                inst2,
                                f"a/agents/{name}/instruction.txt",
                                f"b/agents/{name}/instruction.txt",
                            )
                        )
                    if tools1 != tools2:
                        agent_diff_text.append(
                            f"🛠️ Tool association changes for Agent `{name}`:\n"
                            f"  Before: {tools1}\n"
                            f"  After : {tools2}"
                        )
                    diff_blocks.append(
                        {
                            "title": (
                                f"📝 Instruction Changes for Agent: `{name}`"
                            ),
                            "path": f"agents/{name}/instruction.txt",
                            "diff": "\n".join(agent_diff_text),
                        }
                    )

        for name in added_agents:
            agent2 = agents2[name]
            inst2 = agent2.instruction or ""
            diff_blocks.append(
                {
                    "title": f"➕ Newly Added Agent: `{name}`",
                    "path": f"agents/{name}/instruction.txt",
                    "diff": diff_strings(
                        "",
                        inst2,
                        "/dev/null",
                        f"b/agents/{name}/instruction.txt",
                    ),
                }
            )

        for name in removed_agents:
            agent1 = agents1[name]
            inst1 = agent1.instruction or ""
            diff_blocks.append(
                {
                    "title": f"➖ Removed Agent: `{name}`",
                    "path": f"agents/{name}/instruction.txt",
                    "diff": diff_strings(
                        inst1,
                        "",
                        f"a/agents/{name}/instruction.txt",
                        "/dev/null",
                    ),
                }
            )

        # 3. Tools comparison
        tools1 = {t.display_name: t for t in snap1.tools}
        tools2 = {t.display_name: t for t in snap2.tools}

        added_tools = set(tools2.keys()) - set(tools1.keys())
        removed_tools = set(tools1.keys()) - set(tools2.keys())
        summary_stats["added_tools"] = list(added_tools)
        summary_stats["removed_tools"] = list(removed_tools)

        for name, tool2 in tools2.items():
            tool1 = tools1.get(name)
            if tool1:
                tool1_json = get_clean_json(tool1)
                tool2_json = get_clean_json(tool2)
                if tool1_json != tool2_json:
                    py1 = (
                        getattr(
                            getattr(tool1, "python_function", None),
                            "python_code",
                            "",
                        )
                        or ""
                    )
                    py2 = (
                        getattr(
                            getattr(tool2, "python_function", None),
                            "python_code",
                            "",
                        )
                        or ""
                    )

                    t1_meta = (
                        type(tool1).to_dict(tool1)
                        if not isinstance(tool1, dict)
                        else tool1.copy()
                    )
                    t2_meta = (
                        type(tool2).to_dict(tool2)
                        if not isinstance(tool2, dict)
                        else tool2.copy()
                    )
                    if "python_function" in t1_meta:
                        t1_meta["python_function"].pop("python_code", None)
                    if "python_function" in t2_meta:
                        t2_meta["python_function"].pop("python_code", None)
                    t1_meta_json = json.dumps(t1_meta, indent=2, sort_keys=True)
                    t2_meta_json = json.dumps(t2_meta, indent=2, sort_keys=True)

                    summary_stats["modified_tools"].append(name)
                    tool_diffs = []

                    if t1_meta_json != t2_meta_json:
                        tool_diffs.append(
                            diff_strings(
                                t1_meta_json,
                                t2_meta_json,
                                f"a/tools/{name}/{name}.json",
                                f"b/tools/{name}/{name}.json",
                            )
                        )

                    if py1 != py2:
                        tool_diffs.append(
                            diff_strings(
                                py1,
                                py2,
                                f"a/tools/{name}/python_code.py",
                                f"b/tools/{name}/python_code.py",
                            )
                        )

                    diff_blocks.append(
                        {
                            "title": (
                                f"🛠️ Config & Code Changes for Tool: `{name}`"
                            ),
                            "path": f"tools/{name}/",
                            "diff": "\n".join(tool_diffs),
                        }
                    )

        for name in added_tools:
            tool2 = tools2[name]
            py2 = (
                getattr(
                    getattr(tool2, "python_function", None),
                    "python_code",
                    "",
                )
                or ""
            )
            diff_blocks.append(
                {
                    "title": f"🛠️ Newly Added Tool: `{name}`",
                    "path": f"tools/{name}/python_code.py",
                    "diff": diff_strings(
                        "",
                        py2,
                        "/dev/null",
                        f"b/tools/{name}/python_code.py",
                    ),
                }
            )

        for name in removed_tools:
            tool1 = tools1[name]
            py1 = (
                getattr(
                    getattr(tool1, "python_function", None),
                    "python_code",
                    "",
                )
                or ""
            )
            diff_blocks.append(
                {
                    "title": f"🛠️ Removed Tool: `{name}`",
                    "path": f"tools/{name}/python_code.py",
                    "diff": diff_strings(
                        py1,
                        "",
                        f"a/tools/{name}/python_code.py",
                        "/dev/null",
                    ),
                }
            )

        # 5. Guardrails comparison
        gr1_list = getattr(snap1, "guardrails", [])
        gr2_list = getattr(snap2, "guardrails", [])
        guardrails1 = {g.display_name: g for g in gr1_list}
        guardrails2 = {g.display_name: g for g in gr2_list}

        added_gr = set(guardrails2.keys()) - set(guardrails1.keys())
        removed_gr = set(guardrails1.keys()) - set(guardrails2.keys())
        summary_stats["added_guardrails"] = list(added_gr)
        summary_stats["removed_guardrails"] = list(removed_gr)

        for name, gr2 in guardrails2.items():
            gr1 = guardrails1.get(name)
            if gr1:
                gr1_json = get_clean_json(gr1)
                gr2_json = get_clean_json(gr2)
                if gr1_json != gr2_json:
                    summary_stats["modified_guardrails"].append(name)
                    diff_blocks.append(
                        {
                            "title": (
                                f"🛡️ Guardrail Configuration Changes: `{name}`"
                            ),
                            "path": f"guardrails/{name}.json",
                            "diff": diff_strings(
                                gr1_json,
                                gr2_json,
                                f"a/guardrails/{name}.json",
                                f"b/guardrails/{name}.json",
                            ),
                        }
                    )

        for name in added_gr:
            gr2 = guardrails2[name]
            gr2_json = get_clean_json(gr2)
            diff_blocks.append(
                {
                    "title": f"🛡️ Newly Added Guardrail: `{name}`",
                    "path": f"guardrails/{name}.json",
                    "diff": diff_strings(
                        "",
                        gr2_json,
                        "/dev/null",
                        f"b/guardrails/{name}.json",
                    ),
                }
            )

        for name in removed_gr:
            gr1 = guardrails1[name]
            gr1_json = get_clean_json(gr1)
            diff_blocks.append(
                {
                    "title": f"🛡️ Removed Guardrail: `{name}`",
                    "path": f"guardrails/{name}.json",
                    "diff": diff_strings(
                        gr1_json,
                        "",
                        f"a/guardrails/{name}.json",
                        "/dev/null",
                    ),
                }
            )

        # 6. Toolsets comparison
        ts1_list = getattr(snap1, "toolsets", [])
        ts2_list = getattr(snap2, "toolsets", [])
        toolsets1 = {t.display_name: t for t in ts1_list}
        toolsets2 = {t.display_name: t for t in ts2_list}

        added_ts = set(toolsets2.keys()) - set(toolsets1.keys())
        removed_ts = set(toolsets1.keys()) - set(toolsets2.keys())
        summary_stats["added_toolsets"] = list(added_ts)
        summary_stats["removed_toolsets"] = list(removed_ts)

        for name, ts2 in toolsets2.items():
            ts1 = toolsets1.get(name)
            if ts1:
                ts1_json = get_clean_json(ts1)
                ts2_json = get_clean_json(ts2)
                if ts1_json != ts2_json:
                    summary_stats["modified_toolsets"].append(name)
                    diff_blocks.append(
                        {
                            "title": (
                                f"🧰 Toolset Configuration Changes: `{name}`"
                            ),
                            "path": f"toolsets/{name}.json",
                            "diff": diff_strings(
                                ts1_json,
                                ts2_json,
                                f"a/toolsets/{name}.json",
                                f"b/toolsets/{name}.json",
                            ),
                        }
                    )

        for name in added_ts:
            ts2 = toolsets2[name]
            ts2_json = get_clean_json(ts2)
            diff_blocks.append(
                {
                    "title": f"🧰 Newly Added Toolset: `{name}`",
                    "path": f"toolsets/{name}.json",
                    "diff": diff_strings(
                        "",
                        ts2_json,
                        "/dev/null",
                        f"b/toolsets/{name}.json",
                    ),
                }
            )

        for name in removed_ts:
            ts1 = toolsets1[name]
            ts1_json = get_clean_json(ts1)
            diff_blocks.append(
                {
                    "title": f"🧰 Removed Toolset: `{name}`",
                    "path": f"toolsets/{name}.json",
                    "diff": diff_strings(
                        ts1_json,
                        "",
                        f"a/toolsets/{name}.json",
                        "/dev/null",
                    ),
                }
            )

        # LAYER 1: High-Level Summary
        if not args.verbose and not args.web:
            _print_console_summary(
                console, display_name, app_name, args, v1, v2, summary_stats
            )

        # LAYER 2: Verbose Rich Console Diff
        if args.verbose:
            _print_verbose_diff(console, diff_blocks)

        # LAYER 3: Collapsible HTML Report & Codebin upload
        should_gen_html = (
            args.web
            or (args.output and args.output.endswith(".html"))
            or (not args.verbose and not args.output)
        )
        if should_gen_html:
            _generate_html_report(
                console, display_name, args, v1, v2, diff_blocks
            )

        # Save raw Markdown if explicitly requested
        if args.output and args.output.endswith(".md"):
            md_report = []
            md_report.append("# Version Comparison Report")
            md_report.append(
                f"Source Version  : `{args.source}` ({v1.display_name})"
            )
            md_report.append(
                f"Target Version: `{args.target}` ({v2.display_name})"
            )
            md_report.append("-" * 80)
            for block in diff_blocks:
                md_report.append(f"\n## {block['title']}")
                md_report.append("```diff")
                md_report.append(block["diff"])
                md_report.append("```")

            with open(args.output, "w") as f:
                f.write("\n".join(md_report))
            console.print(
                "\n[green]Successfully wrote Markdown comparison report to "
                f"{args.output}[/]"
            )

    except Exception as e:
        console.print(f"[red]Failed to compare app versions: {e}[/]")
        sys.exit(1)
