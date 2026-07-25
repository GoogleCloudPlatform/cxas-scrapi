#!/usr/bin/env python3
"""
Dynamic, Universal Interactive Eval Report Generator for CXAS / GECX.

Runs simulation evaluations (optional), uses Gemini LLM to dynamically cluster
and categorize test failure modes without domain-specific hardcoding, 
dynamically extracts and builds filters for ALL session parameters, 
and generates a responsive single-page interactive HTML / MHTML report dashboard.
"""

import json
import argparse
import os
import sys
import subprocess
import re
from typing import List, Dict, Any, Set, Tuple

def get_app_metadata(app_name_arg: str | None = None) -> Dict[str, str]:
    """
    Dynamically resolves project_id, location, and app_id without hardcoding.
    Checks:
    1. --app-name argument if provided
    2. gecx-config.json in current directory or active project directory
    3. .active-project directory pointer
    4. Environment variables (GOOGLE_CLOUD_PROJECT / GCP_PROJECT)
    5. Interactive prompt fallback if running in a terminal
    """
    metadata = {
        "project_id": os.environ.get("GOOGLE_CLOUD_PROJECT", os.environ.get("GCP_PROJECT", "")),
        "location": "us",
        "app_id": ""
    }

    def parse_cfg_file(filepath: str):
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if cfg.get("gcp_project_id"):
                        metadata["project_id"] = cfg["gcp_project_id"]
                    if cfg.get("location"):
                        metadata["location"] = cfg["location"]
                    app_ref = cfg.get("deployed_app_id", cfg.get("app_id", ""))
                    if app_ref:
                        metadata["app_id"] = str(app_ref).split("/")[-1]
            except Exception:
                pass

    parse_cfg_file("gecx-config.json")

    if not metadata["app_id"] and os.path.exists(".active-project"):
        try:
            with open(".active-project", "r", encoding="utf-8") as f:
                active_dir = f.read().strip()
                if active_dir:
                    parse_cfg_file(os.path.join(active_dir, "gecx-config.json"))
        except Exception:
            pass

    if app_name_arg:
        parts = app_name_arg.split("/")
        if "projects" in parts:
            p_idx = parts.index("projects")
            if p_idx + 1 < len(parts):
                metadata["project_id"] = parts[p_idx + 1]
        if "locations" in parts:
            l_idx = parts.index("locations")
            if l_idx + 1 < len(parts):
                metadata["location"] = parts[l_idx + 1]
        if "apps" in parts:
            a_idx = parts.index("apps")
            if a_idx + 1 < len(parts):
                metadata["app_id"] = parts[a_idx + 1]
        elif "/" not in app_name_arg:
            metadata["app_id"] = app_name_arg

    if not metadata["project_id"] and sys.stdin.isatty():
        try:
            user_proj = input("Enter GCP Project ID: ").strip()
            if user_proj:
                metadata["project_id"] = user_proj
        except Exception:
            pass

    if not metadata["app_id"] and sys.stdin.isatty():
        try:
            user_app = input("Enter CES App ID: ").strip()
            if user_app:
                metadata["app_id"] = user_app.split("/")[-1]
        except Exception:
            pass

    return metadata

import concurrent.futures

USER_AGENT_EXTENSION = "skill/cxas-interactive-eval-reporter/generate_report"

def call_llm_for_categories(failures: List[Dict[str, Any]], app_name_arg: str | None = None) -> Dict[int, List[str]]:
    """
    Use Gemini LLM to dynamically categorize failure instances into 
    3 to 8 domain-agnostic diagnostic categories using parallel batching 
    and native JSON mode.
    """
    if not failures:
        return {}
        
    try:
        from cxas_scrapi.utils.gemini import GeminiGenerate
        meta = get_app_metadata(app_name_arg)
        project_id = meta.get("project_id")
        client = GeminiGenerate(project_id=project_id) if project_id else GeminiGenerate()
        
        # Prepare failure list items with index
        failure_items = []
        for idx, item in enumerate(failures):
            failure_items.append({
                "index": idx,
                "eval_name": item.get("eval_name", ""),
                "expectation": item.get("expectation", ""),
                "justification": item.get("justification", "")
            })

        # Chunk failures into batches of 30 items for parallel execution
        batch_size = 30
        chunks = [failure_items[i:i + batch_size] for i in range(0, len(failure_items), batch_size)]
        
        def process_batch(chunk: List[Dict[str, Any]]) -> Dict[int, List[str]]:
            prompt = f"""You are an expert Conversational AI evaluation classifier.
Analyze the following test failure expectations and justifications from a conversational agent simulation:

{json.dumps(chunk, indent=2)}

Group these failures into 3 to 8 concise, professional diagnostic categories (e.g., "Tool Call Missing", "Safety Guardrail Violation", "Out-of-Scope Fallback", "Sim Infrastructure Timeout", "Instruction Violation", "Policy Disclaimer").

Return ONLY a valid JSON object mapping each failure 'index' (as a string) to an array of assigned category names.
Example output format:
{{
  "0": ["Tool Call Missing"],
  "1": ["Policy Disclaimer"]
}}
"""
            resp = client.generate(prompt, response_mime_type="application/json")
            if not resp:
                return {}
            
            clean_text = resp if isinstance(resp, str) else json.dumps(resp)
            clean_text = re.sub(r"^```json\s*", "", clean_text.strip(), flags=re.MULTILINE)
            clean_text = re.sub(r"^```\s*$", "", clean_text, flags=re.MULTILINE).strip()
            
            raw_mapping = json.loads(clean_text)
            batch_res = {}
            for k, v in raw_mapping.items():
                batch_res[int(k)] = v if isinstance(v, list) else [str(v)]
            return batch_res

        result = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(chunks))) as executor:
            future_to_chunk = {executor.submit(process_batch, chunk): chunk for chunk in chunks}
            for future in concurrent.futures.as_completed(future_to_chunk):
                try:
                    res = future.result()
                    result.update(res)
                except Exception as b_err:
                    print(f"[Warning] Batch classification error: {b_err}")

        return result
        
    except Exception as e:
        print(f"[Warning] LLM failure categorization skipped or offline ({e}). Using fallback clustering.")
        return {}

def fallback_categorize(expectation_text: str, justification: str) -> str:
    """Smart heuristic fallback when LLM is unavailable."""
    text_lower = (expectation_text + " " + justification).lower()
    
    if any(k in text_lower for k in ["sim ", "simulation", "timeout", "infra", "glitch", "flaky"]):
        return "Sim Related"
    if any(k in text_lower for k in ["disclaimer", "legal", "compliance", "policy"]):
        return "Policy & Compliance"
    if any(k in text_lower for k in ["one question", "single question", "constraint"]):
        return "Constraint Violation"
    if any(k in text_lower for k in ["tool", "function", "call", "parameter"]):
        return "Tool Calling Issue"
    if any(k in text_lower for k in ["out of scope", "fallback", "escalate"]):
        return "Out-of-Scope / Escalation"
    if any(k in text_lower for k in ["search", "grounding", "knowledge"]):
        return "Search Grounding"
        
    return "Other Failure"

def generate_dynamic_report(sim_data: List[Dict[str, Any]], title: str = "GECX Simulation Evaluation Report", app_name_arg: str | None = None) -> str:
    """Build dynamic interactive HTML report with universal filters."""
    
    meta = get_app_metadata(app_name_arg)
    gcp_project_id = meta.get("project_id", "")
    location = meta.get("location", "us")
    app_id = meta.get("app_id", "")

    total_runs = len(sim_data)
    passed_runs = sum(1 for item in sim_data if item.get("passed", False))
    
    # 1. Discover all dynamic session parameters
    session_param_keys: Set[str] = set()
    for item in sim_data:
        params = item.get("session_parameters", {})
        if isinstance(params, dict):
            session_param_keys.update(params.keys())
            
    sorted_param_keys = sorted(list(session_param_keys))
    
    # Values per parameter key
    param_values: Dict[str, Set[str]] = {k: set() for k in sorted_param_keys}
    for item in sim_data:
        params = item.get("session_parameters", {})
        for k in sorted_param_keys:
            val = str(params.get(k, "N/A")) if isinstance(params, dict) else "N/A"
            param_values[k].add(val)

    # 2. Extract failures for LLM categorization
    failure_list = []
    failure_map_ref = []  # tracks (item_idx, exp_idx)
    
    for item_idx, item in enumerate(sim_data):
        if not item.get("passed", False):
            for exp_idx, exp in enumerate(item.get("expectation_details", [])):
                if exp.get("status") not in ["Met", "PASSED"]:
                    failure_list.append({
                        "eval_name": item.get("name", ""),
                        "expectation": exp.get("expectation", ""),
                        "justification": exp.get("justification", "")
                    })
                    failure_map_ref.append((item_idx, exp_idx))
                    
    llm_cat_mapping = call_llm_for_categories(failure_list)
    
    # 3. Process items and assign categories
    all_categories: Set[str] = set()
    sim_failures_count = 0
    processed_evals = []
    
    # Map back LLM categories
    item_assigned_cats: Dict[int, Set[str]] = {i: set() for i in range(len(sim_data))}
    
    for f_idx, (item_idx, exp_idx) in enumerate(failure_map_ref):
        cats = llm_cat_mapping.get(f_idx, [])
        if not cats:
            exp_info = failure_list[f_idx]
            cat = fallback_categorize(exp_info["expectation"], exp_info["justification"])
            cats = [cat]
            
        for c in cats:
            item_assigned_cats[item_idx].add(c)
            
    for item_idx, item in enumerate(sim_data):
        passed = item.get("passed", False)
        item_cats = set()
        
        if passed:
            item_cats.add("Pass")
        else:
            item_cats = item_assigned_cats[item_idx]
            if not item_cats:
                item_cats.add("Other Failure")
                
            if "Sim Related" in item_cats or "Sim Infrastructure Timeout" in item_cats:
                sim_failures_count += 1
                
        all_categories.update(item_cats)
        
        # Build item parameter dict
        item_params = {}
        raw_params = item.get("session_parameters", {})
        for k in sorted_param_keys:
            item_params[k] = str(raw_params.get(k, "N/A")) if isinstance(raw_params, dict) else "N/A"
            
        processed_evals.append({
            "index": item_idx,
            "name": item.get("name", "Unknown Eval"),
            "run": item.get("run", 1),
            "passed": passed,
            "session_params": item_params,
            "categories": sorted(list(item_cats)),
            "raw": item
        })
        
    sorted_categories = sorted(list(all_categories))
    
    overall_pct = (passed_runs / total_runs * 100) if total_runs > 0 else 0
    adjusted_total = total_runs - sim_failures_count
    adjusted_pct = (passed_runs / adjusted_total * 100) if adjusted_total > 0 else overall_pct

    # 4. Generate HTML Content
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{
    font-family: 'Inter', 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 1250px;
    margin: 0 auto;
    padding: 24px;
    background: #f4f6f9;
    color: #2c3e50;
  }}
  h1 {{
    color: #1a2a6c;
    border-bottom: 4px solid #fdbb2d;
    padding-bottom: 12px;
    margin-top: 0;
    font-weight: 700;
  }}
  .summary-container {{
    display: flex;
    gap: 20px;
    margin-bottom: 25px;
  }}
  .summary-box {{
    flex: 1;
    color: white;
    padding: 22px;
    border-radius: 12px;
    box-shadow: 0 4px 15px rgba(0,0,0,0.08);
    text-align: center;
  }}
  .summary-box.overall {{
    background: linear-gradient(135deg, #1a2a6c, #b21f1f);
  }}
  .summary-box.adjusted {{
    background: linear-gradient(135deg, #11998e, #38ef7d);
  }}
  .summary-box .score {{
    font-size: 3em;
    font-weight: 800;
    line-height: 1.1;
  }}
  .summary-box .label {{
    font-size: 1.1em;
    opacity: 0.95;
    font-weight: 600;
    margin-top: 6px;
  }}
  .summary-box .subtext {{
    font-size: 0.88em;
    opacity: 0.85;
    margin-top: 4px;
  }}
  .filter-section {{
    background: white;
    padding: 20px;
    border-radius: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    margin-bottom: 25px;
  }}
  .filter-section h3 {{
    margin-top: 0;
    margin-bottom: 15px;
    color: #1a2a6c;
    font-size: 1.2em;
  }}
  .filter-group {{
    margin-bottom: 16px;
  }}
  .filter-group label {{
    font-weight: 700;
    display: block;
    margin-bottom: 8px;
    color: #34495e;
    font-size: 0.95em;
    text-transform: capitalize;
  }}
  .checkbox-grid {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }}
  .checkbox-item {{
    display: flex;
    align-items: center;
    background: #f8f9fa;
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 0.88em;
    cursor: pointer;
    border: 1px solid #dee2e6;
    user-select: none;
    transition: background 0.15s;
  }}
  .checkbox-item:hover {{
    background: #e9ecef;
  }}
  .checkbox-item input {{
    margin-right: 7px;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 15px 0;
    background: white;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
  }}
  th, td {{
    text-align: left;
    padding: 12px 16px;
    border-bottom: 1px solid #eef2f5;
  }}
  th {{
    background: #2c3e50;
    color: white;
    font-weight: 600;
    font-size: 0.92em;
  }}
  tr.eval-row:hover {{
    background: #f8f9fa;
  }}
  td.pass-text {{ color: #27ae60; font-weight: 700; }}
  td.fail-text {{ color: #e74c3c; font-weight: 700; }}
  .badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.76em;
    font-weight: 700;
    color: white;
    margin-right: 4px;
    margin-bottom: 3px;
  }}
  .badge.pass {{ background: #27ae60; }}
  .badge.sim-related {{ background: #e67e22; }}
  .badge.other {{ background: #4b6584; }}
  
  .eval-card {{
    background: white;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    margin: 15px 0;
    overflow: hidden;
  }}
  .eval-header {{
    padding: 14px 18px;
    font-weight: 700;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .eval-header.pass-bg {{
    background: #e8f8f5;
    border-left: 5px solid #27ae60;
    color: #196f3d;
  }}
  .eval-header.fail-bg {{
    background: #fadbd8;
    border-left: 5px solid #e74c3c;
    color: #78281f;
  }}
  .eval-body {{
    padding: 18px;
  }}
  .transcript {{
    background: #f8f9fa;
    border-radius: 6px;
    padding: 14px;
    margin: 12px 0;
    font-size: 0.9em;
    border: 1px solid #e9ecef;
    font-family: monospace;
    white-space: pre-wrap;
    line-height: 1.5;
  }}
  .transcript .user {{ color: #2980b9; font-weight: bold; margin: 4px 0; }}
  .transcript .agent {{ color: #27ae60; font-weight: bold; margin: 4px 0; }}
  .transcript .system {{ color: #d35400; margin: 4px 0; font-size: 0.88em; }}
  
  .run-detail {{
    margin: 10px 0;
    padding: 12px 14px;
    border-radius: 6px;
    border-left: 4px solid #ccc;
  }}
  .run-detail.pass {{ background: #f4f9f4; border-left-color: #27ae60; }}
  .run-detail.fail {{ background: #fdf2f0; border-left-color: #e74c3c; }}
  
  details summary {{
    cursor: pointer;
    outline: none;
  }}
</style>
</head>
<body>
<h1>{title}</h1>

<div class="summary-container">
  <div class="summary-box overall">
    <div class="score">{overall_pct:.1f}%</div>
    <div class="label">Overall Pass Rate</div>
    <div class="subtext">{passed_runs} / {total_runs} passed</div>
  </div>
  <div class="summary-box adjusted">
    <div class="score">{adjusted_pct:.1f}%</div>
    <div class="label">Adjusted Pass Rate</div>
    <div class="subtext">{passed_runs} / {adjusted_total}</div>
    <div class="subtext">(Excludes {sim_failures_count} sim failures)</div>
  </div>
</div>

<div class="filter-section">
  <h3>Interactive Filters</h3>
"""

    # Dynamic Filter Blocks for ALL session parameters
    for pkey in sorted_param_keys:
        pvals = sorted(list(param_values[pkey]))
        html += f"""
  <div class="filter-group">
    <label>Session Parameter: {pkey}</label>
    <div class="checkbox-grid">
      <div class="checkbox-item"><input type="checkbox" class="param-filter" data-param="{pkey}" value="all" checked> Select All</div>
"""
        for v in pvals:
            html += f'      <div class="checkbox-item"><input type="checkbox" class="param-filter" data-param="{pkey}" value="{v}" checked> {v}</div>\n'
        html += "    </div>\n  </div>\n"

    # Dynamic Category Filters
    html += """
  <div class="filter-group">
    <label>Failure & Evaluation Categories</label>
    <div class="checkbox-grid">
      <div class="checkbox-item"><input type="checkbox" class="cat-filter" value="all" checked> Select All</div>
"""
    for cat in sorted_categories:
        html += f'      <div class="checkbox-item"><input type="checkbox" class="cat-filter" value="{cat}" checked> {cat}</div>\n'

    html += """    </div>
  </div>
</div>

<h2>Results Summary Table</h2>
<table id="summary-table">
  <thead>
    <tr>
      <th>Status</th>
      <th>Eval Name</th>
      <th>Session ID</th>
      <th>Run #</th>
      <th>Duration</th>
"""
    for pkey in sorted_param_keys:
        html += f"      <th>{pkey}</th>\n"

    html += """      <th>Categories</th>
    </tr>
  </thead>
  <tbody>
"""

    for item in processed_evals:
        raw = item["raw"]
        session_id = raw.get("session_id", "N/A")
        if session_id != "N/A" and gcp_project_id and app_id:
            session_url = f"https://ces.cloud.google.com/projects/{gcp_project_id}/locations/{location}/apps/{app_id}?panel=conversation_list&id={session_id}&source=LIVE"
            session_cell = f'<a href="{session_url}" target="_blank" style="color: #2980b9; font-weight: 600; text-decoration: underline; font-family: monospace;">{session_id} ↗</a>'
        else:
            session_url = "#"
            session_cell = f'<code>{session_id}</code>'
            
        status_cls = "pass-text" if item["passed"] else "fail-text"
        status_str = "PASS" if item["passed"] else "FAIL"
        duration = f"{raw.get('duration_s', 0):.1f}s"
        cats_str = "|".join(item["categories"])
        
        # Build dataset data attributes
        data_attrs = f'data-cats="{cats_str}"'
        for pkey in sorted_param_keys:
            pval = item["session_params"].get(pkey, "N/A")
            data_attrs += f' data-param-{pkey}="{pval}"'
            
        badge_html = ""
        for cat in item["categories"]:
            bcls = "pass" if cat == "Pass" else ("sim-related" if "Sim" in cat else "other")
            badge_html += f'<span class="badge {bcls}">{cat}</span> '
            
        html += f"""    <tr class="eval-row" {data_attrs}>
      <td class="{status_cls}">{status_str}</td>
      <td><b>{item['name']}</b></td>
      <td>{session_cell}</td>
      <td>{item['run']}</td>
      <td>{duration}</td>
"""
        for pkey in sorted_param_keys:
            html += f"      <td>{item['session_params'].get(pkey, 'N/A')}</td>\n"

        html += f"""      <td>{badge_html}</td>
    </tr>
"""

    html += """  </tbody>
</table>

<h2>Detailed Eval Transcripts</h2>
<div id="eval-cards-container">
"""

    for item in processed_evals:
        raw = item["raw"]
        session_id = raw.get("session_id", "N/A")
        if session_id != "N/A" and gcp_project_id and app_id:
            session_url = f"https://ces.cloud.google.com/projects/{gcp_project_id}/locations/{location}/apps/{app_id}?panel=conversation_list&id={session_id}&source=LIVE"
            session_link_html = f'<a href="{session_url}" target="_blank" style="color: #1a5276; text-decoration: underline; font-weight: 700; background: #d4e6f1; padding: 2px 8px; border-radius: 4px;">{session_id} ↗ (Open Session in CES Agent Console)</a>'
        else:
            session_link_html = f'<code>{session_id}</code>'
        passed = item["passed"]
        bg_cls = "pass-bg" if passed else "fail-bg"
        status_str = "PASS" if passed else "FAIL"
        cats_str = "|".join(item["categories"])
        
        data_attrs = f'data-cats="{cats_str}"'
        param_summary = [f"Session: {session_id}"]
        for pkey in sorted_param_keys:
            pval = item["session_params"].get(pkey, "N/A")
            data_attrs += f' data-param-{pkey}="{pval}"'
            param_summary.append(f"{pkey}: {pval}")
            
        param_str = " | ".join(param_summary)
        
        transcript_lines = raw.get("detailed_trace", raw.get("transcript", "").split("\n"))
        formatted_trace = []
        for line in transcript_lines:
            if line.startswith("User:"):
                formatted_trace.append(f'<div class="user">{line}</div>')
            elif line.startswith("Agent Text:") or line.startswith("Agent:"):
                formatted_trace.append(f'<div class="agent">{line}</div>')
            else:
                formatted_trace.append(f'<div class="system">{line}</div>')
        formatted_transcript_html = "\n".join(formatted_trace)
        
        exp_details_html = ""
        for exp in raw.get("expectation_details", []):
            st = exp.get("status", "")
            e_cls = "pass" if st in ["Met", "PASSED"] else "fail"
            exp_details_html += f"""
        <div class="run-detail {e_cls}">
          <b>Expectation:</b> {exp.get('expectation','')}<br>
          <b>Status:</b> {st}<br>
          <b>Justification:</b> {exp.get('justification','')}
        </div>"""

        html += f"""
<div class="eval-card" {data_attrs}>
  <details>
    <summary class="eval-header {bg_cls}">
      <span>[{status_str}] {item['name']} (Run #{item['run']})</span>
      <span>{param_str} | {raw.get('duration_s',0):.1f}s</span>
    </summary>
    <div class="eval-body">
      <div class="session-link" style="margin-bottom: 14px; font-family: monospace; font-size: 0.92em; background: #eef2f7; padding: 10px 14px; border-radius: 6px; border-left: 4px solid #3498db;">
        <b>CES Session Link:</b> {session_link_html}
      </div>
      {exp_details_html}
      <h4>Transcript</h4>
      <div class="transcript">
{formatted_transcript_html}
      </div>
    </div>
  </details>
</div>
"""

    html += f"""
</div>

<script>
document.addEventListener('DOMContentLoaded', function() {{
  const paramKeys = {json.dumps(sorted_param_keys)};
  const catCheckboxes = document.querySelectorAll('.cat-filter');

  function updateFilter() {{
    const selectedCats = Array.from(catCheckboxes)
      .filter(cb => cb.checked && cb.value !== 'all')
      .map(cb => cb.value);
    const allCatsChecked = document.querySelector('.cat-filter[value="all"]')?.checked;

    // Build parameter selections map
    const paramSelections = {{}};
    paramKeys.forEach(pkey => {{
      const pCbs = document.querySelectorAll('.param-filter[data-param="' + pkey + '"]');
      const selectedVals = Array.from(pCbs)
        .filter(cb => cb.checked && cb.value !== 'all')
        .map(cb => cb.value);
      const allChecked = document.querySelector('.param-filter[data-param="' + pkey + '"][value="all"]')?.checked;
      paramSelections[pkey] = {{ selectedVals, allChecked }};
    }});

    document.querySelectorAll('.eval-row, .eval-card').forEach(el => {{
      const rowCats = el.getAttribute('data-cats') ? el.getAttribute('data-cats').split('|') : [];
      const matchesCat = allCatsChecked || rowCats.some(c => selectedCats.includes(c));

      let matchesParams = true;
      for (const pkey of paramKeys) {{
        const rowVal = el.getAttribute('data-param-' + pkey);
        const pInfo = paramSelections[pkey];
        if (!pInfo.allChecked && !pInfo.selectedVals.includes(rowVal)) {{
          matchesParams = false;
          break;
        }}
      }}

      if (matchesCat && matchesParams) {{
        el.style.display = '';
      }} else {{
        el.style.display = 'none';
      }}
    }});
  }}

  function handleSelectAll(allCb, groupCbs) {{
    if (!allCb) return;
    allCb.addEventListener('change', function() {{
      groupCbs.forEach(cb => cb.checked = allCb.checked);
      updateFilter();
    }});
    groupCbs.forEach(cb => {{
      if (cb.value !== 'all') {{
        cb.addEventListener('change', function() {{
          if (!this.checked) {{
            allCb.checked = false;
          }}
          updateFilter();
        }});
      }}
    }});
  }}

  paramKeys.forEach(pkey => {{
    const pCbs = document.querySelectorAll('.param-filter[data-param="' + pkey + '"]');
    const allCb = document.querySelector('.param-filter[data-param="' + pkey + '"][value="all"]');
    handleSelectAll(allCb, pCbs);
  }});

  const allCat = document.querySelector('.cat-filter[value="all"]');
  handleSelectAll(allCat, catCheckboxes);
}});
</script>
</body>
</html>
"""
    return html

def main():
    parser = argparse.ArgumentParser(description="Universal Interactive Evaluation Report Generator for GECX")
    parser.add_argument("--input", "-i", required=False, help="Path to sim_results.json file")
    parser.add_argument("--run", "--run-evals", action="store_true", help="Automatically run simulation evaluations first before generating report")
    parser.add_argument("--app-name", required=False, help="CXAS App Name/ID to run evals first")
    parser.add_argument("--output", "-o", required=False, help="Path to save output HTML report")
    parser.add_argument("--title", required=False, default="GECX Interactive Simulation Evaluation Report", help="Title for the report")
    
    args = parser.parse_args()
    
    input_file = args.input
    
    # Auto-run evals if requested or if no input file provided
    if (args.run or args.app_name) and not input_file:
        print("Executing evaluations prior to generating interactive report...")
        sim_runner_script = os.path.join(".agents", "skills", "cxas-agent-foundry", "scripts", "scrapi-sim-runner.py")
        if os.path.exists(sim_runner_script):
            cmd = ["uv", "run", "python", sim_runner_script, "run", "--channel", "text"]
            if args.app_name:
                cmd.extend(["--app-name", args.app_name])
            res = subprocess.run(cmd, text=True)
        else:
            app_arg = args.app_name or "."
            cmd = ["uv", "run", "cxas", "run", "--app-name", app_arg, "--wait"]
            res = subprocess.run(cmd, text=True)
            
        # Locate the newest sim_results_*.json file in eval-reports or current dir
        search_dirs = ["eval-reports", "."]
        found_files = []
        for sdir in search_dirs:
            if os.path.exists(sdir):
                for fname in os.listdir(sdir):
                    if fname.startswith("sim_results") and fname.endswith(".json"):
                        found_files.append(os.path.join(sdir, fname))
        if found_files:
            # Pick newest file
            input_file = max(found_files, key=os.path.getmtime)
            print(f"Found latest simulation results file: {input_file}")
        else:
            print("Error: Could not locate generated sim_results.json after running evaluations.")
            sys.exit(1)
            
    if not input_file or not os.path.exists(input_file):
        print("Error: Please provide a valid --input sim_results.json path or use --run to execute evaluations automatically.")
        sys.exit(1)
        
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    if isinstance(data, dict) and "results" in data:
        sim_data = data["results"]
    elif isinstance(data, list):
        sim_data = data
    else:
        sim_data = [data] if isinstance(data, dict) else []
        
    html_content = generate_dynamic_report(sim_data, title=args.title, app_name_arg=args.app_name)
    
    output_path = args.output
    if not output_path:
        out_dir = os.path.dirname(input_file) or "."
        output_path = os.path.join(out_dir, "interactive_simulation_report.html")
        
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"Successfully generated dynamic interactive report at: {output_path}")

if __name__ == "__main__":
    main()
