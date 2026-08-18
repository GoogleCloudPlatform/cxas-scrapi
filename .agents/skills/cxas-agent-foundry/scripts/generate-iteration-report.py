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

USER_AGENT_EXTENSION = "skill/cxas-agent-foundry/generate-iteration-report"


ITERATIONS_DIR = get_project_path("eval-reports", "iterations")
DIFF_EXTENSIONS = {".txt", ".py"}


def _load_triage_module():
    """Load triage-results.py (hyphenated, can't be imported normally)."""
    try:
        import triage_results  # type: ignore
        return triage_results
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "triage_results",
            os.path.join(os.path.dirname(__file__), "triage-results.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


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
        client = Evaluations(app_name=app_name, user_agent_extension=USER_AGENT_EXTENSION)
    except Exception as e:
        print(f"  Warning: Could not initialize Evaluations client: {e}")
        return None

    # Import triage helpers — they live in the same scripts directory
    mod = _load_triage_module()
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


def _load_previous_per_eval(iteration: int) -> Dict[Tuple[str, str], Dict[str, int]]:
    """Load previous iteration's per_eval, keyed by (eval_type, eval_name).

    Returns ``{(eval_type, eval_name): {"pass": int, "total": int}}`` —
    only the fields needed to determine "was previously passing." Empty
    dict if no prior iteration exists or the results.json is malformed.
    """
    if iteration <= 1:
        return {}
    prev_results = os.path.join(_iteration_dir(iteration - 1), "results.json")
    if not os.path.isfile(prev_results):
        return {}
    try:
        with open(prev_results) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    out: Dict[Tuple[str, str], Dict[str, int]] = {}
    by_type = data.get("per_eval_by_type")
    if isinstance(by_type, dict):
        for eval_type, per_eval in by_type.items():
            if not isinstance(per_eval, dict):
                continue
            for name, info in per_eval.items():
                if not isinstance(info, dict):
                    continue
                out[(eval_type, name)] = {
                    "pass": int(info.get("pass", 0)),
                    "total": int(info.get("total", 0)),
                }
    else:
        # Backward compat: old results.json format only had golden per_eval.
        per_eval = data.get("per_eval", {})
        if isinstance(per_eval, dict):
            for name, info in per_eval.items():
                if not isinstance(info, dict):
                    continue
                out[("golden", name)] = {
                    "pass": int(info.get("pass", 0)),
                    "total": int(info.get("total", 0)),
                }
    return out


def _load_previous_typed_pass_rates(iteration: int) -> Dict[str, Tuple[int, int]]:
    """Load prior iteration's pass/total per eval type.

    Returns ``{type: (passed, total)}`` for each type present in the prior
    iteration's ``per_eval_by_type`` block. Empty dict on missing/legacy data.
    Used by ``_do_auto_revert`` to detect tool/callback test regressions
    (golden uses ``_get_prev_results`` for back-compat with older iterations).
    """
    if iteration <= 1:
        return {}
    prev_results = os.path.join(_iteration_dir(iteration - 1), "results.json")
    if not os.path.isfile(prev_results):
        return {}
    try:
        with open(prev_results) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    out: Dict[str, Tuple[int, int]] = {}
    by_type = data.get("per_eval_by_type")
    if not isinstance(by_type, dict):
        return out
    for eval_type, per_eval in by_type.items():
        if not isinstance(per_eval, dict):
            continue
        total = sum(int(info.get("total", 0)) for info in per_eval.values()
                    if isinstance(info, dict))
        passed = sum(int(info.get("pass", 0)) for info in per_eval.values()
                     if isinstance(info, dict))
        if total > 0:
            out[eval_type] = (passed, total)
    return out


def _extract_iteration_message(iteration: int) -> Optional[str]:
    """Pull the ``--message`` for ``iteration`` from experiment_log.md.

    The log is structured as ``## Iteration N — YYYY-MM-DD`` followed by a
    ``**Change:** <message>`` line. Returns the message text or None.
    """
    log_path = get_project_path("experiment_log.md")
    if not os.path.isfile(log_path):
        return None
    target_header = f"## Iteration {iteration} "
    capture_next = False
    try:
        with open(log_path) as f:
            for line in f:
                if line.startswith(target_header) or line.startswith(f"## Iteration {iteration}\n"):
                    capture_next = True
                    continue
                if capture_next:
                    stripped = line.strip()
                    if stripped.startswith("**Change:**"):
                        return stripped[len("**Change:**"):].strip()
                    # If we hit the next iteration header before finding Change:, give up.
                    if stripped.startswith("## Iteration "):
                        return None
    except OSError:
        return None
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
    rows = _load_callback_test_rows()
    if not rows:
        return None
    total = len(rows)
    passed = sum(1 for r in rows if not r.get("error_message"))
    return (passed, total) if total > 0 else None


def _get_latest_tool_test_results() -> Optional[Tuple[int, int]]:
    """Read tool test results and return (passed, total)."""
    rows = _load_tool_test_rows()
    if not rows:
        return None
    total = len(rows)
    passed = sum(1 for r in rows if r.get("passed", False) or r.get("status") == "PASSED")
    return (passed, total) if total > 0 else None


def _load_callback_test_rows() -> list:
    """Load raw callback_test_results.json rows for downstream triage."""
    path = get_project_path("eval-reports", "callback_test_results.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("results", [])
    except (json.JSONDecodeError, OSError):
        return []


def _load_tool_test_rows() -> list:
    """Load raw tool_test_results.json rows for downstream triage."""
    path = get_project_path("eval-reports", "tool_test_results.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("results", [])
    except (json.JSONDecodeError, OSError):
        return []


def _load_sim_rows() -> list:
    """Load the most recent sim_results_*.json results array."""
    reports_dir = get_project_path("eval-reports")
    if not os.path.isdir(reports_dir):
        return []
    sim_files = sorted(
        [f for f in os.listdir(reports_dir) if f.startswith("sim_results_") and f.endswith(".json")],
        reverse=True,
    )
    if not sim_files:
        return []
    try:
        with open(os.path.join(reports_dir, sim_files[0])) as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("results", [])
    except (json.JSONDecodeError, OSError):
        return []


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


def _format_regressions(
    golden_regression: bool, golden_prev_pct: float, golden_curr_pct: float, agent_failures: int,
    tool_regression: bool, tool_prev: Optional[Tuple[int, int]], tool_now: Optional[Tuple[int, int]],
    cb_regression: bool, cb_prev: Optional[Tuple[int, int]], cb_now: Optional[Tuple[int, int]],
) -> str:
    """Build a human-readable summary of which eval types regressed."""
    parts = []
    if golden_regression:
        parts.append(
            f"goldens {golden_prev_pct:.0f}% → {golden_curr_pct:.0f}% "
            f"({agent_failures} real failure(s))"
        )
    if tool_regression and tool_prev and tool_now:
        parts.append(f"tool tests {tool_prev[0]}/{tool_prev[1]} → {tool_now[0]}/{tool_now[1]}")
    if cb_regression and cb_prev and cb_now:
        parts.append(f"callback tests {cb_prev[0]}/{cb_prev[1]} → {cb_now[0]}/{cb_now[1]}")
    return ", ".join(parts) if parts else "(none)"


def _do_auto_revert(config: dict, iteration: int, triage: Optional[Dict[str, Any]]):
    """Revert cxas_app/ to previous iteration snapshot if a REAL regression occurred.

    Triggers on ANY of these regressions (each gated independently):
      - **Goldens** dropped AND failures are REAL agent issues (TOOL_MISSING,
        TEXT_MISMATCH, EXPECTATION_FAIL) — not platform issues (TIMEOUT,
        SCORES_PASS_BUT_FAIL, UNKNOWN).
      - **Tool tests** dropped (deterministic — no platform-issue exclusion).
      - **Callback tests** dropped (deterministic — same).

    Sims act as a counter-signal regardless of which type triggered: if sim
    pass rate improved while something else regressed, that's a mixed signal
    (the change probably helped real conversations) — investigate, don't
    revert. Sims do NOT trigger a revert on their own (too noisy: sim user
    is itself stochastic).

    Returns True if a revert was performed, False otherwise.
    """
    if not triage:
        return False

    prev = _get_prev_results(iteration)
    if prev is None:
        return False

    prev_passed, prev_total = prev
    passed = triage.get("passed", 0)
    total = triage.get("total", 0)
    prev_pct = 100 * prev_passed / prev_total if prev_total else 0
    curr_pct = 100 * passed / total if total else 0

    failures = triage.get("failures", {})
    agent_failures = (len(failures.get("TOOL_MISSING", []))
                      + len(failures.get("TEXT_MISMATCH", []))
                      + len(failures.get("EXPECTATION_FAIL", [])))

    golden_dropped = curr_pct < prev_pct
    golden_regression = golden_dropped and agent_failures > 0

    # Foundation regressions: any drop in pass count is a real bug — these
    # tests are deterministic, no platform-issue category to exclude.
    prev_typed = _load_previous_typed_pass_rates(iteration)
    tool_now = _get_latest_tool_test_results()
    cb_now = _get_latest_callback_results()
    tool_prev = prev_typed.get("tool_test")
    cb_prev = prev_typed.get("callback_test")
    tool_regression = bool(tool_now and tool_prev and tool_now[0] < tool_prev[0])
    cb_regression = bool(cb_now and cb_prev and cb_now[0] < cb_prev[0])

    if not (golden_regression or tool_regression or cb_regression):
        if golden_dropped and agent_failures == 0:
            platform_failures = (len(failures.get("TIMEOUT", []))
                                 + len(failures.get("SCORES_PASS_BUT_FAIL", []))
                                 + len(failures.get("UNKNOWN", [])))
            print(f"Goldens dropped ({prev_pct:.0f}% → {curr_pct:.0f}%) but all "
                  f"{platform_failures} failure(s) are platform issues. "
                  f"NOT reverting — not an agent regression.")
        return False

    # Sim counter-signal applies to ALL regression types.
    prev_sim = None
    prev_results_path = os.path.join(_iteration_dir(iteration - 1), "results.json")
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
            regs = _format_regressions(
                golden_regression, prev_pct, curr_pct, agent_failures,
                tool_regression, tool_prev, tool_now,
                cb_regression, cb_prev, cb_now,
            )
            print(f"{regs} regressed but sims improved "
                  f"({prev_sim_pct:.0f}% → {curr_sim_pct:.0f}%). "
                  f"NOT reverting — mixed signal. Investigate: the change may help real "
                  f"conversations but the test expectation may need updating.")
            return False

    # Real regression + no sim improvement → revert.
    prev_snapshot = _snapshot_dir(iteration - 1)
    app_dir = _get_app_dir(config)
    if not os.path.isdir(prev_snapshot):
        print(f"Warning: Previous snapshot not found at {prev_snapshot}. Cannot revert.")
        return False

    shutil.copytree(prev_snapshot, app_dir, dirs_exist_ok=True)
    regs = _format_regressions(
        golden_regression, prev_pct, curr_pct, agent_failures,
        tool_regression, tool_prev, tool_now,
        cb_regression, cb_prev, cb_now,
    )
    sim_note = ""
    if curr_sim is not None and prev_sim is not None:
        sim_note = f" Sims {'dropped' if curr_sim[0] < prev_sim[0] else 'flat'}."
    print(f"REGRESSION: {regs}.{sim_note} "
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


PLATFORM_FAILURE_CATEGORIES = ("TIMEOUT", "EVAL_ERROR", "SCORES_PASS_BUT_FAIL")


def _was_previously_passing(prev_per_eval: Dict[Tuple[str, str], Dict[str, int]],
                            eval_type: str, eval_name: str) -> bool:
    """True iff the eval existed in the prior iteration and ran 100% pass."""
    info = prev_per_eval.get((eval_type, eval_name))
    if info is None:
        return False
    total = info.get("total", 0)
    if total <= 0:
        return False
    return info.get("pass", 0) == total


def _split_clusters_by_regression(
    clusters: List[Dict[str, Any]],
    iteration: Optional[int],
) -> List[Dict[str, Any]]:
    """Tag clusters with regression metadata and split mixed clusters in two.

    A cluster member is a "regression" iff it passed every run in the prior
    iteration. Three outcomes per input cluster:

    - **Pure new failures** (none regressed): cluster is unchanged except for
      ``regression_status: "new"``.
    - **Pure regressions** (every member regressed): cluster is unchanged
      except for ``regression_status: "regression"`` + ``regression_context``,
      and ``priority_score`` is bumped (regressions signal an active fix
      conflict and deserve attention before new failures of the same severity).
    - **Mixed**: split into two clusters with the same discriminator — one
      "regression", one "new". Both retain the same category and discriminator;
      the main thread dispatches them as two distinct triage tasks.
    """
    if iteration is None or iteration <= 1:
        # Baseline iteration — nothing to compare against.
        for c in clusters:
            c["regression_status"] = "new"
        return clusters

    prev_per_eval = _load_previous_per_eval(iteration)
    if not prev_per_eval:
        for c in clusters:
            c["regression_status"] = "new"
        return clusters

    prior_message = _extract_iteration_message(iteration - 1)
    prior_snapshot = _snapshot_dir(iteration - 1)
    regression_context_template = {
        "previous_iteration": iteration - 1,
        "previous_message": prior_message,
        "previous_snapshot_dir": prior_snapshot if os.path.isdir(prior_snapshot) else None,
    }

    out: List[Dict[str, Any]] = []
    for cluster in clusters:
        eval_type = cluster.get("eval_type", "golden")
        regressed = [n for n in cluster.get("eval_names", [])
                     if _was_previously_passing(prev_per_eval, eval_type, n)]
        new_failing = [n for n in cluster.get("eval_names", []) if n not in regressed]

        if regressed and not new_failing:
            cluster["regression_status"] = "regression"
            cluster["regressed_evals"] = regressed
            cluster["regression_context"] = dict(regression_context_template)
            cluster["priority_score"] = cluster.get("priority_score", 0) + 50_000
            out.append(cluster)
        elif new_failing and not regressed:
            cluster["regression_status"] = "new"
            out.append(cluster)
        else:
            # Mixed: split into one regression cluster + one new-failure cluster.
            reg_cluster = dict(cluster)
            reg_cluster["eval_names"] = regressed
            reg_cluster["eval_count"] = len(regressed)
            reg_cluster["regression_status"] = "regression"
            reg_cluster["regressed_evals"] = regressed
            reg_cluster["regression_context"] = dict(regression_context_template)
            reg_cluster["priority_score"] = cluster.get("priority_score", 0) + 50_000
            if "eval_pass_rates" in cluster:
                reg_cluster["eval_pass_rates"] = {
                    k: v for k, v in cluster["eval_pass_rates"].items() if k in regressed
                }

            new_cluster = dict(cluster)
            new_cluster["eval_names"] = new_failing
            new_cluster["eval_count"] = len(new_failing)
            new_cluster["regression_status"] = "new"
            if "eval_pass_rates" in cluster:
                new_cluster["eval_pass_rates"] = {
                    k: v for k, v in cluster["eval_pass_rates"].items() if k in new_failing
                }

            out.append(reg_cluster)
            out.append(new_cluster)
    return out


def _build_run_summary(
    triage: Optional[Dict[str, Any]],
    reverted: bool,
    revert_reason: Optional[str],
    message: Optional[str],
    iteration: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the structured run summary that callers consume programmatically.

    Triages failures from all four eval types (golden, sim, tool_test,
    callback_test) into one unified `failure_clusters` pool tagged with
    `eval_type`, so the triage-failure sub-agent dispatch sees every failure
    mode regardless of type. Foundation tests (tool/callback) outrank
    application tests (golden/sim) of equivalent severity via the
    `category_priority` dict.
    """
    g_total = triage.get("total", 0) if triage else 0
    g_passed = triage.get("passed", 0) if triage else 0
    failures = triage.get("failures", {}) if triage else {}

    # Run typed-triage on the other three eval types. Each returns the same
    # shape as triage_results() so the merge below is uniform.
    tr = _load_triage_module()
    sim_rows = _load_sim_rows()
    tool_rows = _load_tool_test_rows()
    cb_rows = _load_callback_test_rows()
    sim_triage = tr.triage_sim_results(sim_rows) if sim_rows else None
    tool_triage = tr.triage_tool_test_results(tool_rows) if tool_rows else None
    cb_triage = tr.triage_callback_test_results(cb_rows) if cb_rows else None

    by_type = {
        "golden": {"passed": g_passed, "failed": g_total - g_passed, "total": g_total},
    }
    if sim_triage:
        by_type["sim"] = {
            "passed": sim_triage["passed"],
            "failed": sim_triage["total"] - sim_triage["passed"],
            "total": sim_triage["total"],
        }
    if tool_triage:
        by_type["tool_test"] = {
            "passed": tool_triage["passed"],
            "failed": tool_triage["total"] - tool_triage["passed"],
            "total": tool_triage["total"],
        }
    if cb_triage:
        by_type["callback_test"] = {
            "passed": cb_triage["passed"],
            "failed": cb_triage["total"] - cb_triage["passed"],
            "total": cb_triage["total"],
        }

    total = sum(t["total"] for t in by_type.values())
    passed = sum(t["passed"] for t in by_type.values())
    failed = total - passed

    # Foundation categories (TOOL_TEST_FAIL, CALLBACK_TEST_FAIL) sit just below
    # EVAL_ERROR — they're deterministic, isolated, easy to fix, and cascade
    # into golden/sim failures. Sim-specific categories sit lower; SIM_USER_OFF_SCRIPT
    # and SIM_TASK_INCOMPLETE are usually eval-side fixes.
    category_priority = {
        "EVAL_ERROR": 0,
        "TOOL_TEST_FAIL": 1,
        "CALLBACK_TEST_FAIL": 2,
        "TOOL_MISSING": 3,
        "EXPECTATION_FAIL": 4,
        "HALLUCINATION": 5,
        "TEXT_MISMATCH": 6,
        "EXTRA_TURNS": 7,
        "SIM_MAX_TURNS_EXCEEDED": 8,
        "SIM_USER_OFF_SCRIPT": 9,
        "SIM_TASK_INCOMPLETE": 10,
        "TIMEOUT": 11,
        "SCORES_PASS_BUT_FAIL": 12,
        "UNKNOWN": 13,
    }

    # Build flat_failures across all four eval types, tagging each with eval_type.
    # top_failures is an INDEX (eval_name + category + run_id + eval_type) for
    # prioritization and to feed into triage-failure dispatches. It deliberately
    # does NOT carry a "reason" / "detail" field — that field looked diagnostic
    # but was just a one-line classification, and models were treating it as a
    # real diagnosis and skipping the triage-failure sub-agent. Diagnoses live
    # in the triage-failure JSON output, not here.
    run_id = triage.get("run_short", "") if triage else ""
    flat_failures = []

    def _accumulate(eval_type: str, per_eval_dict: Dict[str, Any]):
        for eval_name, info in per_eval_dict.items():
            for category, _detail in info.get("failures", []):
                flat_failures.append({
                    "eval_name": eval_name,
                    "eval_type": eval_type,
                    "category": category,
                    "run_id": run_id,
                })

    _accumulate("golden", (triage or {}).get("per_eval", {}))
    if sim_triage:
        _accumulate("sim", sim_triage.get("per_eval", {}))
    if tool_triage:
        _accumulate("tool_test", tool_triage.get("per_eval", {}))
    if cb_triage:
        _accumulate("callback_test", cb_triage.get("per_eval", {}))
    flat_failures.sort(key=lambda f: category_priority.get(f["category"], 99))
    top_failures = flat_failures[:10]

    # failure_clusters: groups failures sharing a (category, discriminator).
    # Built from all four typed-triage outputs, tagged with eval_type so the
    # main thread knows which transcripts/files to point the sub-agent at.
    # priority_score puts higher-priority categories ahead of lower-priority
    # ones unconditionally, then sorts by cluster size within category.
    failure_clusters = []

    def _accumulate_clusters(eval_type: str, raw_clusters: Dict[str, list]):
        for category, clusters in raw_clusters.items():
            cat_pri = category_priority.get(category, 99)
            for c in clusters:
                eval_count = len(c.get("eval_names", []))
                entry = {
                    "category": category,
                    "eval_type": eval_type,
                    "discriminator": c.get("discriminator"),
                    "discriminator_kind": c.get("discriminator_kind", "none"),
                    "eval_count": eval_count,
                    "eval_names": c.get("eval_names", []),
                    "run_id": run_id,
                    "priority_score": (15 - cat_pri) * 1000 + min(eval_count, 999),
                }
                if c.get("eval_pass_rates"):
                    entry["eval_pass_rates"] = c["eval_pass_rates"]
                failure_clusters.append(entry)

    _accumulate_clusters("golden", (triage or {}).get("failure_clusters", {}) or {})
    if sim_triage:
        _accumulate_clusters("sim", sim_triage.get("failure_clusters", {}) or {})
    if tool_triage:
        _accumulate_clusters("tool_test", tool_triage.get("failure_clusters", {}) or {})
    if cb_triage:
        _accumulate_clusters("callback_test", cb_triage.get("failure_clusters", {}) or {})

    # Phase 3: regression detection. For each cluster member, look up the
    # previous iteration's pass rate. An eval was "previously passing" iff its
    # prior pass rate was 100% (all runs passed). When a cluster mixes
    # previously-passing (regression) and never-passing (new failure) members
    # with the same discriminator, split — they need different remediation
    # paths even though the surface symptom is identical. Regression clusters
    # carry `regression_context` so the triage subagent reads the prior
    # iteration's `--message` and instruction diff before flipping the fix.
    failure_clusters = _split_clusters_by_regression(failure_clusters, iteration)

    failure_clusters.sort(key=lambda c: -c["priority_score"])
    failure_clusters = failure_clusters[:10]

    platform_errors = [
        {"category": cat, "count": len(failures.get(cat, []))}
        for cat in PLATFORM_FAILURE_CATEGORIES
        if failures.get(cat)
    ]

    if triage is None:
        status = "errored"
    elif total == 0:
        status = "errored"
    elif platform_errors and passed == 0:
        status = "errored"
    elif platform_errors:
        status = "partial"
    else:
        status = "complete"

    return {
        "status": status,
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / total) if total else 0.0,
        "by_type": by_type,
        "top_failures": top_failures,
        "failure_clusters": failure_clusters,
        "total_failures": len(flat_failures),
        "platform_errors": platform_errors,
        "reverted": reverted,
        "revert_reason": revert_reason,
        "message": message,
    }


def do_report(config: dict, iteration: Optional[int] = None, message: Optional[str] = None, auto_revert: bool = False, json_summary: Optional[str] = None):
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

        # Capture per_eval for non-golden types too so future iterations can
        # detect regressions across all eval types (Phase 3 ping-pong defense).
        tr = _load_triage_module()
        sim_rows = _load_sim_rows()
        tool_rows = _load_tool_test_rows()
        cb_rows = _load_callback_test_rows()
        sim_triage = tr.triage_sim_results(sim_rows) if sim_rows else None
        tool_triage = tr.triage_tool_test_results(tool_rows) if tool_rows else None
        cb_triage = tr.triage_callback_test_results(cb_rows) if cb_rows else None

        def _ser_per_eval(p: Dict[str, Any]) -> Dict[str, Any]:
            return {
                name: {
                    "pass": info["pass"],
                    "total": info["total"],
                    "failures": [(cat, detail) for cat, detail in info.get("failures", [])],
                }
                for name, info in (p or {}).items()
            }

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
            "per_eval": _ser_per_eval(triage.get("per_eval", {})),
            # Phase 3: typed per_eval blocks so regression detection can span types.
            "per_eval_by_type": {
                "golden": _ser_per_eval(triage.get("per_eval", {})),
                "sim": _ser_per_eval(sim_triage.get("per_eval") if sim_triage else {}),
                "tool_test": _ser_per_eval(tool_triage.get("per_eval") if tool_triage else {}),
                "callback_test": _ser_per_eval(cb_triage.get("per_eval") if cb_triage else {}),
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
    reverted = False
    revert_reason = None
    if auto_revert and triage:
        total = triage.get("total", 0)
        passed = triage.get("passed", 0)
        reverted = _do_auto_revert(config, iteration, triage)
        if reverted:
            revert_reason = f"Golden pass rate regressed at iteration {iteration}; reverted to iteration {iteration - 1} snapshot."

    # Structured summary for programmatic callers (the iteration loop reads this
    # instead of parsing stdout).
    if json_summary:
        summary = _build_run_summary(triage, reverted, revert_reason, message, iteration=iteration)
        os.makedirs(os.path.dirname(os.path.abspath(json_summary)), exist_ok=True)
        with open(json_summary, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"\nJSON summary: {json_summary}")


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
    report_parser.add_argument(
        "--json-summary", default=None,
        help="Write a structured run summary (status, pass rate, by_type, top_failures, reverted) to this path. Used by the debug iteration loop to read results without parsing stdout."
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
        do_report(config, iteration=args.iteration, message=args.message, auto_revert=args.auto_revert, json_summary=args.json_summary)


if __name__ == "__main__":
    main()
