#!/usr/bin/env python3
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

"""Snapshot agent state and generate HTML iteration reports.

Usage:
  python scripts/generate-iteration-report.py snapshot
  python scripts/generate-iteration-report.py report
  python scripts/generate-iteration-report.py report --iteration 3
  python scripts/generate-iteration-report.py report --message "Fixed escalation by adding set_variables tool"
"""

import argparse
import difflib
import json
import os
import shutil
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))
from config import load_config, load_app_name, get_project_path


ITERATIONS_DIR = get_project_path("eval-reports", "iterations")
DIFF_EXTENSIONS = {".txt", ".py"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _get_app_dir(config: dict) -> str:
    """Return the app directory from config, resolved to the project path."""
    return get_project_path(config.get("app_dir", "cxas_app"))


def _detect_next_iteration() -> int:
    """Auto-detect the next iteration number from existing directories."""
    if not os.path.isdir(ITERATIONS_DIR):
        return 1
    existing = []
    for name in os.listdir(ITERATIONS_DIR):
        if name.startswith("iteration_"):
            try:
                existing.append(int(name.split("_", 1)[1]))
            except ValueError:
                pass
    return max(existing) + 1 if existing else 1


def _latest_iteration() -> Optional[int]:
    """Return the highest existing iteration number, or None."""
    if not os.path.isdir(ITERATIONS_DIR):
        return None
    existing = []
    for name in os.listdir(ITERATIONS_DIR):
        if name.startswith("iteration_"):
            try:
                existing.append(int(name.split("_", 1)[1]))
            except ValueError:
                pass
    return max(existing) if existing else None


def _iteration_dir(n: int) -> str:
    return os.path.join(ITERATIONS_DIR, f"iteration_{n}")


def _snapshot_dir(n: int) -> str:
    return os.path.join(_iteration_dir(n), "snapshot")


def _collect_diffable_files(directory: str) -> Dict[str, str]:
    """Collect contents of .txt and .py files under directory, keyed by relative path."""
    files = {}
    if not os.path.isdir(directory):
        return files
    for root, _dirs, filenames in os.walk(directory):
        for fname in filenames:
            _, ext = os.path.splitext(fname)
            if ext not in DIFF_EXTENSIONS:
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, directory)
            try:
                with open(full, encoding="utf-8", errors="replace") as f:
                    files[rel] = f.read()
            except OSError:
                pass
    return files


def _compute_diffs(old_files: Dict[str, str], new_files: Dict[str, str]) -> List[Dict[str, Any]]:
    """Compute unified diffs between two sets of files.

    Returns a list of dicts: {"path": str, "diff": str, "status": "added"|"removed"|"modified"}
    """
    all_paths = sorted(set(old_files.keys()) | set(new_files.keys()))
    diffs = []
    for path in all_paths:
        old = old_files.get(path)
        new = new_files.get(path)
        if old is None:
            # New file
            diff_lines = list(difflib.unified_diff(
                [], new.splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
            ))
            diffs.append({"path": path, "diff": "\n".join(diff_lines), "status": "added"})
        elif new is None:
            # Removed file
            diff_lines = list(difflib.unified_diff(
                old.splitlines(keepends=True), [],
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
            ))
            diffs.append({"path": path, "diff": "\n".join(diff_lines), "status": "removed"})
        elif old != new:
            diff_lines = list(difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
            ))
            diffs.append({"path": path, "diff": "\n".join(diff_lines), "status": "modified"})
    return diffs


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def do_snapshot(config: dict) -> int:
    """Copy app_dir to the next iteration snapshot. Returns the iteration number."""
    app_dir = _get_app_dir(config)
    if not os.path.isdir(app_dir):
        print(f"Error: app directory '{app_dir}' not found.")
        sys.exit(1)

    iteration = _detect_next_iteration()
    dest = _snapshot_dir(iteration)
    os.makedirs(dest, exist_ok=True)
    shutil.copytree(app_dir, dest, dirs_exist_ok=True)
    print(f"Snapshot saved for iteration {iteration}")
    return iteration


# ---------------------------------------------------------------------------
# Eval results (triage)
# ---------------------------------------------------------------------------

def _fetch_eval_results() -> Optional[Dict[str, Any]]:
    """Fetch latest golden eval results using triage-results logic.

    Returns a triage dict or None if results unavailable.
    """
    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print("  Warning: cxas-scrapi not installed. Skipping eval results.")
        return None

    try:
        from cxas_scrapi.core.evaluations import Evaluations
    except ImportError:
        print("  Warning: Could not import Evaluations. Skipping eval results.")
        return None

    try:
        app_name = load_app_name()
        client = Evaluations(app_name=app_name)
    except Exception as e:
        print(f"  Warning: Could not initialize Evaluations client: {e}")
        return None

    # Import triage helpers — they live in the same scripts directory
    try:
        from triage_results import (
            get_golden_evals,
            get_results_for_eval,
            get_latest_run_results,
            triage_results,
        )
    except ImportError:
        # Fall back to importing with hyphenated module workaround
        import importlib
        spec = importlib.util.spec_from_file_location(
            "triage_results",
            os.path.join(os.path.dirname(__file__), "triage-results.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        get_golden_evals = mod.get_golden_evals
        get_results_for_eval = mod.get_results_for_eval
        get_latest_run_results = mod.get_latest_run_results
        triage_results = mod.triage_results

    # Build eval name lookup
    try:
        evals_map = client.get_evaluations_map(reverse=False)
    except Exception as e:
        print(f"  Warning: Failed to fetch evaluations map: {e}")
        return None

    name_lookup = {}
    for cat in ["goldens", "scenarios"]:
        for resource, display in evals_map.get(cat, {}).items():
            name_lookup[resource] = display

    golden_evals = get_golden_evals(client)
    if not golden_evals:
        print("  Warning: No golden evals found.")
        return None

    all_results = []
    run_short = ""
    time_str = ""
    for display_name in golden_evals:
        try:
            results = get_results_for_eval(client, display_name)
            rs, ts, latest = get_latest_run_results(results)
            all_results.extend(latest)
            if ts > time_str:
                time_str = ts
                run_short = rs
        except Exception as e:
            print(f"  Warning: Failed to fetch {display_name}: {e}")

    if not all_results:
        print("  Warning: No eval results found.")
        return None

    triage = triage_results(all_results, name_lookup)
    triage["run_short"] = run_short
    triage["time_str"] = time_str
    return triage


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _render_diff_html(diffs: List[Dict[str, Any]]) -> str:
    """Render diffs as syntax-highlighted HTML blocks."""
    if not diffs:
        return '<p style="color:#888;">No changes detected (baseline iteration or identical files).</p>'

    html = ""
    for d in diffs:
        status_badge = {
            "added": '<span style="background:#d4edda;color:#155724;padding:2px 8px;border-radius:4px;font-size:0.8em;">ADDED</span>',
            "removed": '<span style="background:#f8d7da;color:#721c24;padding:2px 8px;border-radius:4px;font-size:0.8em;">REMOVED</span>',
            "modified": '<span style="background:#fff3cd;color:#856404;padding:2px 8px;border-radius:4px;font-size:0.8em;">MODIFIED</span>',
        }.get(d["status"], "")

        lines_html = ""
        for line in d["diff"].split("\n"):
            escaped = _escape(line)
            if line.startswith("+") and not line.startswith("+++"):
                lines_html += f'<span style="color:#155724;background:#d4edda;display:block;">{escaped}</span>'
            elif line.startswith("-") and not line.startswith("---"):
                lines_html += f'<span style="color:#721c24;background:#f8d7da;display:block;">{escaped}</span>'
            elif line.startswith("@@"):
                lines_html += f'<span style="color:#0366d6;display:block;">{escaped}</span>'
            else:
                lines_html += f'<span style="display:block;">{escaped}</span>'

        html += f"""<details style="margin:8px 0;">
<summary style="cursor:pointer;font-weight:bold;padding:4px 0;">{_escape(d["path"])} {status_badge}</summary>
<pre style="background:#f6f8fa;padding:12px;border-radius:6px;font-size:0.85em;overflow-x:auto;margin:4px 0;line-height:1.4;">{lines_html}</pre>
</details>
"""
    return html


def _render_triage_html(triage: Dict[str, Any]) -> str:
    """Render triage failure groups as HTML."""
    failures = triage.get("failures", {})
    if not failures:
        return '<p style="color:#27ae60;font-weight:bold;">No failures detected.</p>'

    category_order = ["TIMEOUT", "SCORES_PASS_BUT_FAIL", "EXTRA_TURNS", "EXPECTATION_FAIL", "TOOL_MISSING", "TEXT_MISMATCH", "UNKNOWN"]
    html = ""
    for cat in category_order:
        items = failures.get(cat)
        if not items:
            continue
        html += f'<div style="background:#fff;border-left:4px solid #e74c3c;padding:10px 14px;margin:8px 0;border-radius:4px;">'
        html += f'<b style="color:#c0392b;">{_escape(cat)}</b> ({len(items)})<ul style="margin:4px 0 0 0;padding-left:20px;">'
        for eval_name, detail in items:
            html += f'<li><b>{_escape(eval_name)}</b>: {_escape(detail)}</li>'
        html += '</ul></div>\n'
    return html


def _render_per_eval_table(per_eval: Dict[str, Any]) -> str:
    """Render per-eval pass/fail table as HTML."""
    if not per_eval:
        return '<p style="color:#888;">No eval data available.</p>'

    html = '<table style="border-collapse:collapse;width:100%;margin:10px 0;">'
    html += '<tr style="background:#2c3e50;color:white;"><th style="padding:8px 12px;text-align:left;">Eval</th><th style="padding:8px 12px;text-align:center;">Pass</th><th style="padding:8px 12px;text-align:center;">Total</th><th style="padding:8px 12px;text-align:center;">Rate</th><th style="padding:8px 12px;text-align:left;">Status</th></tr>\n'

    for name in sorted(per_eval.keys()):
        info = per_eval[name]
        p, t = info["pass"], info["total"]
        rate = 100 * p / t if t else 0
        if p == t:
            status = '<span style="color:#27ae60;font-weight:bold;">PASS</span>'
            row_bg = ""
        else:
            status = '<span style="color:#e74c3c;font-weight:bold;">FAIL</span>'
            row_bg = ' style="background:#fef5f5;"'
        html += f'<tr{row_bg}><td style="padding:8px 12px;">{_escape(name)}</td><td style="padding:8px 12px;text-align:center;">{p}</td><td style="padding:8px 12px;text-align:center;">{t}</td><td style="padding:8px 12px;text-align:center;">{rate:.0f}%</td><td style="padding:8px 12px;">{status}</td></tr>\n'

    html += '</table>'
    return html


def build_report_html(
    iteration: int,
    config: dict,
    diffs: List[Dict[str, Any]],
    triage: Optional[Dict[str, Any]],
    message: Optional[str] = None,
) -> str:
    """Build a self-contained HTML report."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    app_id = config.get("deployed_app_id", "unknown")

    # Summary stats
    if triage:
        total = triage.get("total", 0)
        passed = triage.get("passed", 0)
        pct = 100 * passed / total if total else 0
        run_short = triage.get("run_short", "")
        time_str = triage.get("time_str", "")
    else:
        total = passed = 0
        pct = 0
        run_short = ""
        time_str = ""

    pct_color = "#27ae60" if pct >= 90 else ("#f57c00" if pct >= 70 else "#e74c3c")

    message_html = ""
    if message:
        message_html = f'<div style="background:#e8eaf6;padding:12px 16px;border-radius:6px;margin:12px 0;border-left:4px solid #3f51b5;"><b>Rationale:</b> {_escape(message)}</div>'

    eval_summary_html = ""
    if triage:
        eval_summary_html = f"""
    <div style="display:flex;gap:24px;align-items:center;margin:12px 0;">
      <div style="font-size:2.2em;font-weight:bold;color:{pct_color};">{pct:.0f}%</div>
      <div>
        <b>{passed}/{total}</b> evals passed<br>
        <span style="color:#666;font-size:0.85em;">Run: {_escape(run_short)} | {_escape(time_str)}</span>
      </div>
    </div>"""
    else:
        eval_summary_html = '<p style="color:#888;">Eval results not available.</p>'

    diff_html = _render_diff_html(diffs)
    triage_html = _render_triage_html(triage) if triage else '<p style="color:#888;">No triage data available.</p>'
    per_eval_html = _render_per_eval_table(triage.get("per_eval", {})) if triage else '<p style="color:#888;">No per-eval data available.</p>'

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Iteration {iteration} Report - {_escape(app_id)}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    max-width: 1000px;
    margin: 0 auto;
    padding: 20px;
    background: #fff;
    color: #333;
  }}
  h1 {{
    color: #1a1a2e;
    border-bottom: 3px solid #3f51b5;
    padding-bottom: 10px;
  }}
  h2 {{
    color: #1a1a2e;
    margin-top: 30px;
    border-bottom: 1px solid #ddd;
    padding-bottom: 6px;
  }}
  .header-meta {{
    color: #666;
    font-size: 0.9em;
    margin: 4px 0 16px 0;
  }}
  .section {{
    margin: 16px 0;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
  }}
  th, td {{
    text-align: left;
    padding: 8px 12px;
    border-bottom: 1px solid #ddd;
  }}
  th {{
    background: #2c3e50;
    color: white;
  }}
  details {{
    margin: 4px 0;
  }}
  summary {{
    cursor: pointer;
  }}
  pre {{
    margin: 0;
  }}
</style>
</head>
<body>

<h1>Iteration {iteration}</h1>
<div class="header-meta">
  App: <b>{_escape(app_id)}</b> | Generated: {ts}
</div>

{message_html}

<h2>Summary</h2>
<div class="section">
{eval_summary_html}
</div>

<h2>Changes from Previous Iteration</h2>
<div class="section">
{diff_html}
</div>

<h2>Failure Triage</h2>
<div class="section">
{triage_html}
</div>

<h2>Per-Eval Results</h2>
<div class="section">
{per_eval_html}
</div>

</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Experiment log & results tracking
# ---------------------------------------------------------------------------

def _get_prev_results(iteration: int) -> Optional[Tuple[int, int]]:
    """Load passed/total from the previous iteration's results.json."""
    prev_dir = _iteration_dir(iteration - 1)
    prev_results = os.path.join(prev_dir, "results.json")
    if not os.path.isfile(prev_results):
        return None
    try:
        with open(prev_results) as f:
            data = json.load(f)
        total = data.get("total", 0)
        passed = data.get("passed", 0)
        if total == 0:
            return None
        return (passed, total)
    except (json.JSONDecodeError, OSError):
        return None


def _compute_status(iteration: int, passed: int, total: int) -> Tuple[str, int, Optional[str]]:
    """Compute status, delta, and comparison string.

    Returns (status, delta, comparison_str).
    """
    prev = _get_prev_results(iteration)
    if prev is None:
        return ("baseline", 0, None)
    prev_passed, prev_total = prev
    delta = passed - prev_passed
    prev_pct = 100 * prev_passed / prev_total if prev_total else 0
    comparison = f"{prev_passed}/{prev_total} ({prev_pct:.1f}%)"
    if delta > 0:
        return ("improved", delta, comparison)
    elif delta < 0:
        return ("regressed", delta, comparison)
    else:
        return ("unchanged", 0, comparison)


def _get_latest_callback_results() -> Optional[Tuple[int, int]]:
    """Read callback test results and return (passed, total)."""
    path = get_project_path("eval-reports", "callback_test_results.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        results = data if isinstance(data, list) else data.get("results", [])
        total = len(results)
        passed = sum(1 for r in results if not r.get("error_message"))
        return (passed, total) if total > 0 else None
    except (json.JSONDecodeError, OSError):
        return None


def _get_latest_tool_test_results() -> Optional[Tuple[int, int]]:
    """Read tool test results and return (passed, total)."""
    path = get_project_path("eval-reports", "tool_test_results.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        results = data if isinstance(data, list) else data.get("results", [])
        total = len(results)
        passed = sum(1 for r in results if r.get("passed", False) or r.get("status") == "PASSED")
        return (passed, total) if total > 0 else None
    except (json.JSONDecodeError, OSError):
        return None


def _next_log_iteration() -> int:
    """Determine the next iteration number for the experiment log.

    Reads the log file and returns max_iteration + 1, so iteration numbers
    always increase even if report is run multiple times on the same snapshot.
    """
    log_path = get_project_path("experiment_log.md")
    if not os.path.isfile(log_path):
        return 1
    max_iter = 0
    with open(log_path) as f:
        for line in f:
            if line.startswith("## Iteration "):
                try:
                    num = int(line.split("Iteration ")[1].split(" ")[0])
                    max_iter = max(max_iter, num)
                except (ValueError, IndexError):
                    pass
    return max_iter + 1


def _append_experiment_log(iteration: int, triage: Optional[Dict[str, Any]], message: Optional[str]):
    """Append a structured entry to <project>/experiment_log.md."""
    log_path = get_project_path("experiment_log.md")
    ts = datetime.now().strftime("%Y-%m-%d")

    # Use the log's own iteration counter to avoid duplicates
    iteration = _next_log_iteration()

    # Golden results from triage
    if triage:
        g_total = triage.get("total", 0)
        g_passed = triage.get("passed", 0)
    else:
        g_total = g_passed = 0

    status, delta, comparison = _compute_status(iteration, g_passed, g_total)

    # Sim, callback, tool results
    sim = _get_latest_sim_pass_rate()
    cb = _get_latest_callback_results()
    tool = _get_latest_tool_test_results()

    # Create file with header if it doesn't exist
    if not os.path.isfile(log_path):
        with open(log_path, "w") as f:
            f.write("# Experiment Log\n\nTracking what was tried, results across all eval types, and failure details.\n\n")

    # Build the entry
    change_text = message or "(no description)"

    lines = []
    lines.append(f"## Iteration {iteration} — {ts}")
    lines.append(f"**Change:** {change_text}")
    lines.append("")

    # Results table
    lines.append("| Eval Type | Pass Rate |")
    lines.append("|-----------|-----------|")
    g_pct = 100 * g_passed / g_total if g_total else 0
    lines.append(f"| Goldens | {g_passed}/{g_total} ({g_pct:.0f}%) |")
    if sim:
        s_pct = 100 * sim[0] / sim[1] if sim[1] else 0
        lines.append(f"| Simulations | {sim[0]}/{sim[1]} ({s_pct:.0f}%) |")
    if tool:
        t_pct = 100 * tool[0] / tool[1] if tool[1] else 0
        lines.append(f"| Tool Tests | {tool[0]}/{tool[1]} ({t_pct:.0f}%) |")
    if cb:
        c_pct = 100 * cb[0] / cb[1] if cb[1] else 0
        lines.append(f"| Callback Tests | {cb[0]}/{cb[1]} ({c_pct:.0f}%) |")

    # Status vs previous
    if comparison:
        lines.append(f"\n**Status:** {status} from {comparison}")

    # Golden failure breakdown
    if triage and triage.get("failures"):
        lines.append("")
        lines.append("**Golden failures:**")
        for cat, items in triage["failures"].items():
            # Group by eval name
            eval_counts = {}
            for eval_name, detail in items:
                if eval_name not in eval_counts:
                    eval_counts[eval_name] = {"count": 0, "detail": detail}
                eval_counts[eval_name]["count"] += 1
            for eval_name, info in eval_counts.items():
                count_str = f" x{info['count']}" if info['count'] > 1 else ""
                detail_str = f": {info['detail'][:100]}" if info['detail'] else ""
                lines.append(f"- `{cat}` {eval_name}{count_str}{detail_str}")

    # Sim failure breakdown
    if sim and sim[0] < sim[1]:
        reports_dir = get_project_path("eval-reports")
        sim_files = sorted(
            [f for f in os.listdir(reports_dir) if f.startswith("sim_results_") and f.endswith(".json")],
            reverse=True,
        )
        if sim_files:
            try:
                with open(os.path.join(reports_dir, sim_files[0])) as f:
                    sim_data = json.load(f)
                sim_results = sim_data if isinstance(sim_data, list) else sim_data.get("results", [])
                failed_sims = [r for r in sim_results if not r.get("passed", False) and not r.get("error")]
                if failed_sims:
                    lines.append("")
                    lines.append("**Sim failures:**")
                    for r in failed_sims:
                        name = r.get("name", "?")
                        exp_details = r.get("expectation_details", [])
                        failed_exps = [e for e in exp_details if e.get("status") != "Met"]
                        for fe in failed_exps:
                            lines.append(f"- `{name}`: {fe.get('expectation', '?')[:80]} — {fe.get('justification', '?')[:80]}")
            except (json.JSONDecodeError, OSError):
                pass

    lines.append("")
    lines.append("")

    with open(log_path, "a") as f:
        f.write("\n".join(lines))
    print(f"Experiment log: {log_path}")


def _append_results_tsv(iteration: int, triage: Optional[Dict[str, Any]], message: Optional[str]):
    """Append a row to <project>/results.tsv."""
    tsv_path = get_project_path("results.tsv")
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Use the latest iteration from the log (written just before this call)
    log_iter = _next_log_iteration() - 1
    if log_iter >= 1:
        iteration = log_iter

    if triage:
        g_total = triage.get("total", 0)
        g_passed = triage.get("passed", 0)
    else:
        g_total = g_passed = 0

    sim = _get_latest_sim_pass_rate()
    cb = _get_latest_callback_results()
    tool = _get_latest_tool_test_results()

    status, delta, _ = _compute_status(iteration, g_passed, g_total)
    msg = (message or "").replace("\t", " ").replace("\n", " ")

    def _rate(passed, total):
        return f"{passed}/{total}" if total else "-"

    # Create file with header if it doesn't exist
    if not os.path.isfile(tsv_path):
        with open(tsv_path, "w") as f:
            f.write("iteration\ttimestamp\tgoldens\tsims\ttool_tests\tcallback_tests\tstatus\tmessage\n")

    row = (f"{iteration}\t{ts}\t{_rate(g_passed, g_total)}\t"
           f"{_rate(sim[0], sim[1]) if sim else '-'}\t"
           f"{_rate(tool[0], tool[1]) if tool else '-'}\t"
           f"{_rate(cb[0], cb[1]) if cb else '-'}\t"
           f"{status}\t{msg}\n")
    with open(tsv_path, "a") as f:
        f.write(row)
    print(f"Results TSV: {tsv_path}")


# ---------------------------------------------------------------------------
# Report command
# ---------------------------------------------------------------------------

def _get_latest_sim_pass_rate() -> Optional[Tuple[int, int]]:
    """Read the latest sim results from eval-reports/ and return (passed, total)."""
    reports_dir = get_project_path("eval-reports")
    if not os.path.isdir(reports_dir):
        return None
    # Find the most recent sim_results_*.json
    sim_files = sorted(
        [f for f in os.listdir(reports_dir) if f.startswith("sim_results_") and f.endswith(".json")],
        reverse=True,
    )
    if not sim_files:
        return None
    try:
        with open(os.path.join(reports_dir, sim_files[0])) as f:
            data = json.load(f)
        results = data if isinstance(data, list) else data.get("results", [])
        total = len(results)
        passed = sum(1 for r in results if r.get("passed", False) or r.get("status") == "PASSED")
        return (passed, total) if total > 0 else None
    except (json.JSONDecodeError, OSError):
        return None


def _do_auto_revert(config: dict, iteration: int, triage: Optional[Dict[str, Any]]):
    """Revert cxas_app/ to previous iteration snapshot if a REAL regression occurred.

    REVERT CONDITIONS (ALL must be true):
    1. Golden pass rate dropped compared to previous iteration
    2. The golden failures are REAL agent issues (TOOL_MISSING, TEXT_MISMATCH, EXPECTATION_FAIL),
       not platform issues (TIMEOUT, SCORES_PASS_BUT_FAIL)
    3. Sim pass rate also dropped or stayed the same (if sim data is available)

    DO NOT REVERT when:
    - First iteration (baseline)
    - Golden pass rate improved or stayed the same
    - All golden failures are platform issues
    - Goldens regressed BUT sims improved (mixed signal — investigate, don't revert)
    - No triage data available

    Returns True if a revert was performed, False otherwise.
    """
    if not triage:
        return False

    passed = triage.get("passed", 0)
    total = triage.get("total", 0)

    prev = _get_prev_results(iteration)
    if prev is None:
        return False

    prev_passed, prev_total = prev
    prev_pct = 100 * prev_passed / prev_total if prev_total else 0
    curr_pct = 100 * passed / total if total else 0

    if curr_pct >= prev_pct:
        return False

    # Check failure categories — only revert on REAL agent failures
    failures = triage.get("failures", {})
    agent_failures = (len(failures.get("TOOL_MISSING", []))
                      + len(failures.get("TEXT_MISMATCH", []))
                      + len(failures.get("EXPECTATION_FAIL", [])))
    platform_failures = (len(failures.get("TIMEOUT", []))
                        + len(failures.get("SCORES_PASS_BUT_FAIL", []))
                        + len(failures.get("UNKNOWN", [])))

    if agent_failures == 0:
        print(f"Goldens dropped ({prev_pct:.0f}% → {curr_pct:.0f}%) but all "
              f"{platform_failures} failure(s) are platform issues. "
              f"NOT reverting — not an agent regression.")
        return False

    # Check sims — if sims improved while goldens regressed, don't auto-revert
    # (mixed signal means the change helped real conversations but broke a golden expectation)
    prev_results_path = os.path.join(_iteration_dir(iteration - 1), "results.json")
    prev_sim = None
    if os.path.isfile(prev_results_path):
        try:
            with open(prev_results_path) as f:
                prev_data = json.load(f)
            prev_sim = prev_data.get("sim_pass_rate")
        except (json.JSONDecodeError, OSError):
            pass

    curr_sim = _get_latest_sim_pass_rate()
    if prev_sim is not None and curr_sim is not None:
        prev_sim_pct = 100 * prev_sim[0] / prev_sim[1] if prev_sim[1] else 0
        curr_sim_pct = 100 * curr_sim[0] / curr_sim[1] if curr_sim[1] else 0
        if curr_sim_pct > prev_sim_pct:
            print(f"Goldens regressed ({prev_pct:.0f}% → {curr_pct:.0f}%) but sims improved "
                  f"({prev_sim_pct:.0f}% → {curr_sim_pct:.0f}%). "
                  f"NOT reverting — mixed signal. Investigate: the change may help real "
                  f"conversations but the golden expectation may need updating.")
            return False

    # Both regressed (or sims unavailable) + real agent failures — revert
    prev_snapshot = _snapshot_dir(iteration - 1)
    app_dir = _get_app_dir(config)
    if not os.path.isdir(prev_snapshot):
        print(f"Warning: Previous snapshot not found at {prev_snapshot}. Cannot revert.")
        return False

    shutil.copytree(prev_snapshot, app_dir, dirs_exist_ok=True)
    sim_note = ""
    if curr_sim is not None:
        sim_note = f" Sims also {'dropped' if prev_sim and curr_sim[0] < prev_sim[0] else 'unavailable for comparison'}."
    print(f"AGENT REGRESSION: Goldens dropped {prev_pct:.0f}% → {curr_pct:.0f}% "
          f"with {agent_failures} real failure(s).{sim_note} "
          f"Reverted {app_dir}/ to iteration {iteration - 1} snapshot.")

    # Update experiment_log.md — replace the status line for this iteration
    log_path = get_project_path("experiment_log.md")
    if os.path.isfile(log_path):
        with open(log_path, "r") as f:
            content = f.read()
        # Find and update the status line for this iteration
        import re
        content = re.sub(
            rf"(## Iteration {iteration} .*?\n\*\*Change:\*\*.*?\n\*\*Result:\*\*.*?\n\*\*Status:\*\*) .+",
            r"\1 reverted",
            content,
        )
        with open(log_path, "w") as f:
            f.write(content)

    # Update results.tsv — replace the status in the last line for this iteration
    tsv_path = get_project_path("results.tsv")
    if os.path.isfile(tsv_path):
        with open(tsv_path, "r") as f:
            lines = f.readlines()
        for i in range(len(lines) - 1, -1, -1):
            parts = lines[i].split("\t")
            if parts and parts[0] == str(iteration):
                # status is column index 4
                if len(parts) > 4:
                    parts[4] = "reverted"
                    lines[i] = "\t".join(parts)
                break
        with open(tsv_path, "w") as f:
            f.writelines(lines)

    return True


def do_report(config: dict, iteration: Optional[int] = None, message: Optional[str] = None, auto_revert: bool = False):
    """Generate an iteration report, auto-snapshotting if needed."""
    app_dir = _get_app_dir(config)

    if iteration is not None:
        # Regenerating a specific iteration
        if not os.path.isdir(_snapshot_dir(iteration)):
            print(f"Error: No snapshot found for iteration {iteration}.")
            sys.exit(1)
    else:
        # Auto-detect: if a snapshot already exists for the next iteration, use it;
        # otherwise, take a snapshot first.
        latest = _latest_iteration()
        if latest is not None and os.path.isdir(_snapshot_dir(latest)):
            # Check if the latest snapshot directory has content
            # (it might have been created by a prior snapshot command)
            iteration = latest
        else:
            # No iterations at all — snapshot first
            if not os.path.isdir(app_dir):
                print(f"Error: app directory '{app_dir}' not found.")
                sys.exit(1)
            iteration = do_snapshot(config)

    iter_dir = _iteration_dir(iteration)
    snapshot = _snapshot_dir(iteration)

    # Compute diffs against previous iteration
    prev = iteration - 1
    if prev >= 1 and os.path.isdir(_snapshot_dir(prev)):
        print(f"Diffing iteration {prev} -> {iteration}...")
        old_files = _collect_diffable_files(_snapshot_dir(prev))
        new_files = _collect_diffable_files(snapshot)
        diffs = _compute_diffs(old_files, new_files)
        print(f"  {len(diffs)} file(s) changed.")
    else:
        print(f"Iteration {iteration} is the baseline (no previous iteration to diff against).")
        diffs = []

    # Fetch eval results
    print("Fetching eval results...")
    triage = _fetch_eval_results()

    # Save raw results
    results_path = os.path.join(iter_dir, "results.json")
    if triage:
        # Serialize triage to JSON-safe format
        # Also capture sim pass rate for cross-comparison in auto-revert
        sim_pass_rate = _get_latest_sim_pass_rate()

        serializable = {
            "total": triage["total"],
            "passed": triage["passed"],
            "sim_pass_rate": list(sim_pass_rate) if sim_pass_rate else None,
            "run_short": triage.get("run_short", ""),
            "time_str": triage.get("time_str", ""),
            "failures": {
                cat: [(name, detail) for name, detail in items]
                for cat, items in triage.get("failures", {}).items()
            },
            "per_eval": {
                name: {
                    "pass": info["pass"],
                    "total": info["total"],
                    "failures": [(cat, detail) for cat, detail in info.get("failures", [])],
                }
                for name, info in triage.get("per_eval", {}).items()
            },
        }
        with open(results_path, "w") as f:
            json.dump(serializable, f, indent=2)
        print(f"  Results saved to {results_path}")
    else:
        # Save empty results to mark that we tried
        with open(results_path, "w") as f:
            json.dump({"total": 0, "passed": 0, "note": "Eval results not available"}, f, indent=2)
        print(f"  No eval results available. Empty results saved to {results_path}")

    # Generate HTML
    html = build_report_html(iteration, config, diffs, triage, message=message)
    report_path = os.path.join(iter_dir, "report.html")
    with open(report_path, "w") as f:
        f.write(html)
    print(f"\nReport: {report_path}")

    # Append to experiment log and results.tsv
    _append_experiment_log(iteration, triage, message)
    _append_results_tsv(iteration, triage, message)

    # Auto-revert if regression detected
    if auto_revert and triage:
        total = triage.get("total", 0)
        passed = triage.get("passed", 0)
        _do_auto_revert(config, iteration, triage)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Snapshot agent state and generate iteration reports"
    )
    subparsers = parser.add_subparsers(dest="command")

    # snapshot
    subparsers.add_parser("snapshot", help="Save current app state as a new iteration snapshot")

    # report
    report_parser = subparsers.add_parser("report", help="Generate an iteration report")
    report_parser.add_argument(
        "--iteration", type=int, default=None,
        help="Regenerate report for a specific iteration number"
    )
    report_parser.add_argument(
        "--message", default=None,
        help="Add a rationale / change description to the report"
    )
    report_parser.add_argument(
        "--auto-revert", action="store_true", default=False,
        help="Automatically revert cxas_app/ to previous snapshot if pass rate regressed"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        config = load_config()
    except SystemExit:
        print("Error: Could not load gecx-config.json. Ensure you are in the project root.")
        sys.exit(1)

    if args.command == "snapshot":
        do_snapshot(config)
    elif args.command == "report":
        do_report(config, iteration=args.iteration, message=args.message, auto_revert=args.auto_revert)


if __name__ == "__main__":
    main()
