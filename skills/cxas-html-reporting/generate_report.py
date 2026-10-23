import json
import urllib.request
import os
import zipfile
import io
import base64
import time
import argparse
from datetime import datetime
import sys

import subprocess
import shutil
import sqlite3
import urllib.parse

def get_gcloud_access_token() -> str:
    """Retrieves live OAuth Bearer access token without hardcoded user workstation paths."""
    env_token = os.environ.get("GCLOUD_ACCESS_TOKEN") or os.environ.get("ACCESS_TOKEN")
    if env_token:
        return env_token.strip()

    # Priority 1: Check ADC refresh token exchange
    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    if os.path.exists(adc_path):
        try:
            with open(adc_path, "r", encoding="utf-8") as f:
                adc_data = json.load(f)
            if all(k in adc_data for k in ("refresh_token", "client_id", "client_secret")):
                payload = urllib.parse.urlencode({
                    "client_id": adc_data["client_id"],
                    "client_secret": adc_data["client_secret"],
                    "refresh_token": adc_data["refresh_token"],
                    "grant_type": "refresh_token",
                }).encode("utf-8")
                req = urllib.request.Request("https://oauth2.googleapis.com/token", data=payload)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    token_resp = json.loads(resp.read().decode("utf-8"))
                    access_token = token_resp.get("access_token")
                    if access_token:
                        return access_token.strip()
        except Exception:
            pass

    # Priority 2: Check live user SQLite access token cache
    db_path = os.path.expanduser("~/.config/gcloud/access_tokens.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT access_token FROM access_tokens WHERE access_token IS NOT NULL AND access_token != '' ORDER BY token_expiry DESC"
            ).fetchall()
            for (token,) in rows:
                if token and token.startswith("ya29."):
                    return token.strip()
        except Exception:
            pass

    # Priority 3: Fall back to gcloud binary in PATH or standard internal release paths
    gcloud_candidates = [
        shutil.which("gcloud"),
        "/google/bin/releases/gcloud/gcloud",
        "/usr/bin/gcloud",
        "/usr/local/bin/gcloud",
        os.path.expanduser("~/google-cloud-sdk/bin/gcloud"),
    ]
    gcloud_cmd = next((p for p in gcloud_candidates if p and os.path.exists(p)), None)
    if gcloud_cmd:
        try:
            token = subprocess.check_output([gcloud_cmd, "auth", "print-access-token"], text=True).strip()
            if token:
                return token
        except Exception:
            pass

    raise RuntimeError("Could not acquire OAuth Bearer token. Please set GCLOUD_ACCESS_TOKEN or run `gcloud auth login`.")

def find_scrapi_cli_script() -> str:
    """Dynamically resolves scrapi_cli.py script without hardcoded user workstation paths."""
    env_script = os.environ.get("SCRAPI_CLI_PATH")
    if env_script and os.path.exists(env_script):
        return env_script
    candidates = [
        shutil.which("scrapi_cli.py"),
        os.path.join(os.path.dirname(__file__), "scrapi_cli.py"),
        os.path.join(os.path.dirname(__file__), "..", "cxas-scrapi", "scripts", "scrapi_cli.py"),
        os.path.expanduser("~/.gemini/jetski/builtin/skills/cxas-scrapi/scripts/scrapi_cli.py"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CXAS Evaluation Report</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .collapsible-content {{ display: none; }}
        .expanded .collapsible-content {{ display: block; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
    </style>
    <script>
        function toggleCollapse(id) {{
            document.getElementById(id).classList.toggle('expanded');
        }}
        function showTab(tabId) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            
            document.querySelectorAll('.tab-btn').forEach(el => {{
                el.classList.remove('border-indigo-500', 'text-indigo-600');
                el.classList.add('border-transparent', 'text-gray-500');
            }});
            const btn = document.getElementById('btn-' + tabId);
            if(btn) {{
                btn.classList.remove('border-transparent', 'text-gray-500');
                btn.classList.add('border-indigo-500', 'text-indigo-600');
            }}
        }}
    </script>
</head>
<body class="bg-gray-50 text-gray-800 font-sans p-8">
    <div class="max-w-6xl mx-auto">
        <h1 class="text-4xl font-bold mb-2 text-indigo-700">CXAS Evaluation Report</h1>
        <p class="text-gray-600 mb-8">Generated on: {timestamp}</p>

        <!-- Tabs Navigation -->
        {tab_btns_html}

        <!-- TAB: SUMMARY -->
        <div id="tab-summary" class="tab-content active">
            <!-- Summary Cards -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-10">
                <div class="bg-white p-6 rounded-lg shadow-sm border-l-4 border-blue-500">
                    <h3 class="text-lg font-semibold text-gray-500">Total Tests</h3>
                    <p class="text-3xl font-bold">{total_tests}</p>
                </div>
                <div class="bg-white p-6 rounded-lg shadow-sm border-l-4 border-green-500">
                    <h3 class="text-lg font-semibold text-gray-500">Passed</h3>
                    <p class="text-3xl font-bold text-green-600">{passed_tests}</p>
                </div>
                <div class="bg-white p-6 rounded-lg shadow-sm border-l-4 border-red-500">
                    <h3 class="text-lg font-semibold text-gray-500">Failed</h3>
                    <p class="text-3xl font-bold text-red-600">{failed_tests}</p>
                </div>
            </div>

            {global_warnings_html}

            <div class="bg-indigo-50 border-l-4 border-indigo-500 p-4 mb-10 rounded-r-lg">
                <h2 class="text-xl font-bold text-indigo-800 mb-2">🤖 AI Prescriptive Insights</h2>
                <div id="ai-insights" class="bg-white p-4 rounded border border-indigo-200 text-sm overflow-hidden">
                    {insights_text}
                </div>
            </div>

            <h2 class="text-2xl font-bold mb-6 text-gray-800 border-b pb-2">All Test Cases Overview</h2>
            <div class="space-y-4">
                {summary_cards_html}
            </div>
        </div>

        <!-- TAB: TOOL CALLS -->
        <div id="tab-tool-calls" class="tab-content">
            <h2 class="text-2xl font-bold mb-6 text-gray-800 border-b pb-2">Tool Call Execution Issues</h2>
            <p class="mb-4 text-gray-600 text-sm">Tests below encountered schema validation errors, hallucinated endpoints, or missing required attributes on tool execution.</p>
            <div class="space-y-4">
                {tool_cards_html}
            </div>
        </div>
        
        <!-- TAB: VARIABLES -->
        <div id="tab-variables" class="tab-content">
            <h2 class="text-2xl font-bold mb-6 text-gray-800 border-b pb-2">State & Variables</h2>
            <p class="mb-4 text-gray-600 text-sm">Tests below failed to substitute `{{variable}}` patterns, either due to incorrect session context setup or agent omission.</p>
            <div class="space-y-4">
                {variable_cards_html}
            </div>
        </div>

        <!-- TAB: SEMANTIC SIMILARITY -->
        <div id="tab-semantic" class="tab-content">
            <h2 class="text-2xl font-bold mb-6 text-gray-800 border-b pb-2">Generative & Phrasing</h2>
            <p class="mb-4 text-gray-600 text-sm">Tests below generated unscripted text, hallucinatory tone, or failed to perfectly match golden phrasing constraints.</p>
            <div class="space-y-4">
                {semantic_cards_html}
            </div>
        </div>

        <!-- TAB: AGENT HANDOVERS -->
        <div id="tab-handovers" class="tab-content">
            <h2 class="text-2xl font-bold mb-6 text-gray-800 border-b pb-2">Subagent Handovers & Routing</h2>
            <p class="mb-4 text-gray-600 text-sm">Tests below failed expected subagent handoffs, made unauthorized horizontal transfers, or unexpectedly escalated to root/main.</p>
            <div class="space-y-4">
                {handover_cards_html}
            </div>
        </div>
        
        <!-- TAB: SYSTEM & INFRASTRUCTURE -->
        <div id="tab-system" class="tab-content">
            <h2 class="text-2xl font-bold mb-6 text-gray-800 border-b pb-2">System, Runtime & Quota Errors</h2>
            <p class="mb-4 text-gray-600 text-sm">Tests below encountered platform execution failures, API quota exhaustion, conversation retrieval timeouts, or runtime crashes.</p>
            <div class="space-y-4">
                {system_cards_html}
            </div>
        </div>

        {linter_tab_content}
        
    </div>
</body>
</html>
"""

SUMMARY_ROW_TEMPLATE = """
<div id="test-{index}" class="bg-white rounded-lg shadow-sm border {border_color} overflow-hidden">
    <div class="p-4 cursor-pointer hover:bg-gray-50 flex justify-between items-center" onclick="toggleCollapse('test-{index}')">
        <div>
            <span class="inline-block px-2 py-1 text-xs font-semibold rounded-full {badge_color} mr-2">
                {status}
            </span>
            <span class="font-semibold text-lg">{test_name}</span>
        </div>
        <div class="flex items-center space-x-2">
            <a href="{eval_url}" target="_blank" onclick="event.stopPropagation();" title="View in CXAS Evaluate Tab" class="text-blue-500 hover:text-blue-700 bg-blue-50 hover:bg-blue-100 p-1.5 rounded-full transition-colors">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
            </a>
            <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
        </div>
    </div>
    
    <div class="collapsible-content border-t bg-gray-50 p-6">
        {scores_badge_bar}
        <h4 class="font-semibold text-gray-700 mb-2">Diagnostic Summary</h4>
        <ul class="list-disc list-inside text-sm text-gray-700 space-y-1">
            {all_issues_html}
        </ul>
    </div>
</div>
"""

ISSUE_CARD_TEMPLATE = """
<div class="bg-white p-4 rounded shadow-sm border-l-4 border-red-400 mb-4">
    <div class="flex justify-between items-start mb-2">
        <h3 class="font-bold text-gray-800">{test_name} <span class="ml-2 inline-block px-2 py-0.5 text-[0.65rem] font-bold rounded bg-gray-200 text-gray-600">{status}</span></h3>
        <a href="{eval_url}" target="_blank" class="inline-flex items-center text-xs text-blue-600 hover:text-blue-800 whitespace-nowrap bg-blue-50 px-2 py-1 rounded">View in CXAS ↗</a>
    </div>
    <ul class="list-disc pl-5 text-sm text-red-700 space-y-1 bg-red-50 p-3 rounded">
        {issues_list}
    </ul>
</div>
"""

def categorize_errors(errors):
    cats = {
        "Tool Calls": [],
        "State & Variables": [],
        "Generative & Phrasing": [],
        "Agent Handovers": [],
        "System & Infrastructure": []
    }
    for er in errors:
        if any(w in er for w in ["System Error", "QUOTA_EXHAUSTED", "RUNTIME_FAILURE", "CONVERSATION_RETRIEVAL", "USER_SIMULATION"]):
            cats["System & Infrastructure"].append(er)
        elif any(w in er for w in ["Routing/Transfer", "Agent Transfer", "agentTransfer"]):
            cats["Agent Handovers"].append(er)
        elif any(w in er for w in ["Missing parameter", "Schema validation", "Missing state", "TypeError"]):
            cats["State & Variables"].append(er)
        elif any(w in er for w in ["Hallucination", "Goal Failure", "Semantic Similarity", "Rubric Failure", "Task Completion Failure", "unscripted", "factually inaccurate"]):
            cats["Generative & Phrasing"].append(er)
        else:
            cats["Tool Calls"].append(er)
            
    for k in cats:
        cats[k] = list(set(cats[k]))
    return cats

def parse_evaluation_schema_details(eval_data, conv_data=None):
    """Parses all evaluation fields according to Google Cloud CES v1beta evaluation.proto schema."""
    findings = []
    telemetry = {
        "semanticSimilarity": [],
        "toolInvocation": [],
        "hallucination": [],
        "userGoalSatisfaction": None,
        "taskCompletion": None,
        "agentTransfers": [],
        "systemErrors": [],
        "rubrics": [],
        "metricScores": {}
    }

    # 1. Top-level evaluation error_info (e.g. Quota, Runtime, User Simulation)
    top_err = eval_data.get("errorInfo") or eval_data.get("error")
    if top_err and isinstance(top_err, dict):
        etype = top_err.get("errorType", top_err.get("code", "RUNTIME_FAILURE"))
        emsg = top_err.get("errorMessage", top_err.get("userFacingErrorMessage", str(top_err)))
        findings.append(f"[System Error - {etype}]: {emsg}")
        telemetry["systemErrors"].append({"errorType": etype, "errorMessage": emsg, "scope": "evaluation"})

    # Check goldenResult vs scenarioResult
    golden_res = eval_data.get("goldenResult", {})
    scenario_res = eval_data.get("scenarioResult", {})

    # Process TurnReplayResults in Golden evaluation
    turn_results = golden_res.get("turnReplayResults", [])
    for t_idx, turn in enumerate(turn_results):
        # Semantic Similarity check
        sem = turn.get("semanticSimilarityResult")
        if sem and isinstance(sem, dict):
            score = sem.get("score")
            label = sem.get("label", "Unknown Consistency")
            explanation = sem.get("explanation", "").strip()
            outcome = sem.get("outcome", "PASS")
            telemetry["semanticSimilarity"].append({
                "turn": t_idx + 1, "score": score, "label": label, "outcome": outcome, "explanation": explanation
            })
            if outcome == "FAIL" or (score is not None and score < 3):
                findings.append(f"[Semantic Similarity Failure (Turn {t_idx + 1}, Score {score}/4 - {label})]: {explanation}")

        # Overall Tool Invocation check
        oti = turn.get("overallToolInvocationResult")
        t_outcome = "PASS"
        if oti and isinstance(oti, dict):
            t_score = oti.get("toolInvocationScore")
            t_outcome = oti.get("outcome", "PASS")
            telemetry["metricScores"][f"turn_{t_idx + 1}_tool_invocation_score"] = t_score
            if t_outcome == "FAIL" or (t_score is not None and t_score < 1.0):
                findings.append(f"[Tool Invocation Failure (Turn {t_idx + 1})]: Invocation score was {t_score if t_score is not None else '0'}.")

        # Tool ordered invocation score (only flag if overall tool invocation failed)
        order_score = turn.get("toolOrderedInvocationScore")
        if order_score is not None:
            telemetry["metricScores"][f"turn_{t_idx + 1}_tool_order_score"] = order_score
            if order_score < 1.0 and t_outcome == "FAIL":
                findings.append(f"[Tool Order Failure (Turn {t_idx + 1})]: Only {int(order_score * 100)}% of tools invoked in exact golden order.")

        # Turn-level ErrorInfo
        turn_err = turn.get("errorInfo")
        if turn_err and isinstance(turn_err, dict):
            etype = turn_err.get("errorType", "RUNTIME_FAILURE")
            emsg = turn_err.get("errorMessage", "Unknown execution error")
            findings.append(f"[System Error - Turn {t_idx + 1} {etype}]: {emsg}")
            telemetry["systemErrors"].append({"turn": t_idx + 1, "errorType": etype, "errorMessage": emsg})

        # Expectation Outcomes (Parameter correctness & Observed Transfers)
        exp_outcomes = turn.get("expectationOutcome", turn.get("expectationOutcomes", []))
        for exp in exp_outcomes:
            e_outcome = exp.get("outcome")
            t_inv = exp.get("toolInvocationResult")
            tool_err_flagged = False
            if t_inv and isinstance(t_inv, dict):
                param_score = t_inv.get("parameterCorrectnessScore")
                expl = t_inv.get("explanation", "")
                if t_inv.get("outcome") == "FAIL" or (param_score is not None and param_score < 1.0):
                    findings.append(f"[Tool Parameter Correctness Failure (Turn {t_idx + 1})]: Parameter accuracy at {int((param_score or 0) * 100)}%. {expl}")
                    tool_err_flagged = True
                telemetry["toolInvocation"].append({"turn": t_idx + 1, "parameterCorrectnessScore": param_score, "explanation": expl})

            agent_transfer = exp.get("observedAgentTransfer")
            expected_transfer = exp.get("expectation", {}).get("agentTransfer") if isinstance(exp.get("expectation"), dict) else None
            routing_flagged = False

            if e_outcome == "FAIL":
                if agent_transfer and isinstance(agent_transfer, dict):
                    t_name = agent_transfer.get("displayName") or agent_transfer.get("targetAgent", "main").split("/")[-1]
                    findings.append(f"[Routing/Transfer Failure (Turn {t_idx + 1})]: Agent transfer to '{t_name}' failed expectation (Outcome: FAIL).")
                    routing_flagged = True
                elif expected_transfer and isinstance(expected_transfer, dict):
                    exp_name = expected_transfer.get("displayName", expected_transfer.get("targetAgent", "target_agent")).split("/")[-1]
                    findings.append(f"[Routing/Transfer Failure (Turn {t_idx + 1})]: Expected agent transfer to '{exp_name}' was not triggered.")
                    routing_flagged = True
                elif not tool_err_flagged:
                    exp_desc = "observed response expectation" if exp.get("observedAgentResponse") else "conversation expectation"
                    findings.append(f"[Expectation Failure (Turn {t_idx + 1})]: {exp_desc.capitalize()} did not satisfy golden criteria (Outcome: FAIL).")

            if agent_transfer:
                telemetry["agentTransfers"].append({"turn": t_idx + 1, "transfer": agent_transfer, "outcome": e_outcome})

        # Turn Hallucination
        h_res = turn.get("hallucinationResult")
        if h_res and isinstance(h_res, dict):
            h_score = h_res.get("score")
            h_label = h_res.get("label", "")
            h_expl = h_res.get("explanation", "").strip()
            telemetry["hallucination"].append({"turn": t_idx + 1, "score": h_score, "label": h_label, "explanation": h_expl})
            if h_score == 0:
                findings.append(f"[Hallucination (Turn {t_idx + 1})]: {h_expl}")

    # Process ScenarioResult in dynamic scenario evaluation
    if scenario_res:
        # Hallucination results (per turn)
        for idx, h in enumerate(scenario_res.get("hallucinationResult", [])):
            h_score = h.get("score")
            h_expl = h.get("explanation", "").strip()
            if h_expl.startswith("Justification:"):
                h_expl = h_expl.replace("Justification:", "").strip()
            telemetry["hallucination"].append({"turn": idx + 1, "score": h_score, "explanation": h_expl})
            if h_score == 0:
                findings.append(f"[Hallucination (Turn {idx + 1})]: {h_expl}")

        # User Goal Satisfaction
        ug = scenario_res.get("userGoalSatisfactionResult", {})
        if ug:
            u_score = ug.get("score")
            u_expl = ug.get("explanation", "").strip()
            telemetry["userGoalSatisfaction"] = {"score": u_score, "label": ug.get("label"), "explanation": u_expl}
            if u_score == 0:
                findings.append(f"[Goal Failure]: {u_expl}")

        # Task Completion Result
        tc_res = scenario_res.get("taskCompletionResult", {})
        if tc_res:
            tc_score = tc_res.get("score")
            tc_expl = tc_res.get("explanation", "").strip()
            telemetry["taskCompletion"] = {"score": tc_score, "label": tc_res.get("label"), "explanation": tc_expl}
            if tc_score == 0:
                findings.append(f"[Task Completion Failure]: {tc_expl}")

        # Rubric outcomes
        for r in scenario_res.get("rubricOutcomes", []):
            telemetry["rubrics"].append(r)
            if r.get("score") is not None and r.get("score") < 0.7:
                findings.append(f"[Rubric Failure - {r.get('rubric', 'Rubric')}]: Score {r.get('score')}. {r.get('scoreExplanation', '')}")

        # Scenario expectation outcomes
        for idx, s_exp in enumerate(scenario_res.get("expectationOutcomes", [])):
            if s_exp.get("outcome") == "FAIL":
                findings.append(f"[Scenario Expectation Failure (Step {idx + 1})]: Expectation check did not pass.")

        if scenario_res.get("taskCompleted") is False:
            telemetry["metricScores"]["taskCompleted"] = False

    return findings, telemetry

def audit_cloud_project_linter(app_id, env="prod"):
    """Fetches all live agent definitions for app_id and runs SCRAPI static production linter checks."""
    token = get_gcloud_access_token()
    project_id = app_id.split("/")[1]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project_id,
    }
    api_domain = "ces.googleapis.com" if env == "prod" else "autopush-ces.sandbox.googleapis.com"
    url = f"https://{api_domain}/v1beta/{app_id}/agents?pageSize=100"
    req = urllib.request.Request(url, headers=headers)
    linter_errors = []
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            agents = data.get("agents", [])
        
        import re
        for ag in agents:
            ag_name = ag.get("displayName", ag.get("name", "").split("/")[-1])
            instr = ag.get("instruction", "")
            tools = [t.split("/")[-1] for t in ag.get("tools", [])]
            child_agents = [c.split("/")[-1] for c in ag.get("childAgents", [])]
            is_root = (ag_name.lower() in ("main", "root_agent", "rootagent"))

            # Check 1: Inactive backticked pills
            if re.search(r"\`\{@TOOL:[^}]+\}\`", instr):
                linter_errors.append(f"Agent [{ag_name}]: Found inactive backticked tool reference (`{{@TOOL: ...}}`). Remove backticks to activate UI pill.")
            if re.search(r"\`\{@AGENT:[^}]+\}\`", instr):
                linter_errors.append(f"Agent [{ag_name}]: Found inactive backticked agent reference (`{{@AGENT: ...}}`). Remove backticks to activate UI pill.")

            # Check 2: All registered tools must be referenced via active pill {@TOOL: name}
            for tname in tools:
                if f"{{@TOOL: {tname}}}" not in instr and tname != "end_session":
                    linter_errors.append(f"Agent [{ag_name}]: Tool '{tname}' is registered in JSON tools[] but missing active reference '{{@TOOL: {tname}}}' in instruction prompt.")

            # Check 3: Variable Reference Definition Rule
            raw_vars = sorted(set(re.findall(r"\{(?![@])[a-zA-Z0-9_.]+\}", instr)))
            if raw_vars and "<context_and_session_variables>" not in instr:
                linter_errors.append(f"Agent [{ag_name}]: References variables {raw_vars} but missing `<context_and_session_variables>` definition block.")

            # Check 4: Single-Parent Directed Tree Topology Rule
            if not is_root:
                if child_agents:
                    linter_errors.append(f"Agent [{ag_name}]: Non-root subagent illegally declares childAgents '{child_agents}' in configuration. Subagents must form a strict Single-Parent Tree rooted at main/root.")
                agent_pills = re.findall(r"\{@AGENT:\s*([^}]+)\}", instr)
                for target in agent_pills:
                    t_clean = target.strip()
                    if t_clean.lower() not in ("root_agent", "rootagent", "main", ag_name.lower()):
                        linter_errors.append(f"Agent [{ag_name}]: Illegal lateral transfer pill '{{@AGENT: {t_clean}}}'. Specialist subagents have ONLY ONE parent ('main') and must return exclusively to that parent.")

            # Check 5: Markup-Safe Inequality Check
            if re.search(r"<\s*\d+", instr):
                linter_errors.append(f"Agent [{ag_name}]: Found unescaped inequality markup ('<' followed by digit) in instruction text. Use plain text ('less than', 'under') to prevent XML parsing issues.")

            # Check 6: Forbidden legacy phrases
            forbidden = ["ow do", "ey up", "telcobrand"]
            instr_lower = instr.lower()
            for fp in forbidden:
                if fp in instr_lower:
                    linter_errors.append(f"Agent [{ag_name}]: Found forbidden phrase/brand '{fp}' in instruction text.")
    except Exception as e:
        linter_errors.append(f"Failed running live cloud agent audit: {e}")

    return linter_errors

def fetch_results(app_id, env="prod"):
    token = get_gcloud_access_token()
    project_id = app_id.split("/")[1]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }
    
    api_domain = "ces.googleapis.com" if env == "prod" else "autopush-ces.sandbox.googleapis.com"
    lro_domain = "ces.clients6.google.com" if env == "prod" else "autopush-ces.sandbox.googleapis.com"
    
    print("Listing evaluations...")
    list_url = f"https://{api_domain}/v1beta/{app_id}/evaluations?pageSize=200"
    req = urllib.request.Request(list_url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        all_evals = data.get("evaluations", [])
        
    results = []
    print(f"Fetching lastCompletedResult for {len(all_evals)} evaluations...")
    for ev in all_evals:
        ename = ev["name"]
        dn = ev.get("displayName", ev["name"].split("/")[-1])
        req_eval = urllib.request.Request(f"https://{api_domain}/v1beta/{ename}", headers=headers)
        try:
            with urllib.request.urlopen(req_eval) as eval_resp:
                eval_obj = json.loads(eval_resp.read().decode("utf-8"))
                last_res = eval_obj.get("lastCompletedResult")
                if last_res:
                    results.append({"displayName": dn, "result": last_res, "evalName": ename})
        except Exception as e:
            print(f"Failed fetching {dn}: {e}")
            
    print(f"Triggering {len(results)} export LROs to fetch full traces...")
    lro_map = {}
    for item in results:
        eval_name = item["evalName"]
        res_name = item["result"]["name"]
        url = f"https://{lro_domain}/v1beta/{eval_name}/results:export?alt=json"
        payload = {"exportOptions": {"exportFormat": "JSON"}, "names": [res_name]}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                lro = json.loads(resp.read().decode())
                lro_map[eval_name] = lro["name"]
        except Exception as e:
            print(f"Failed to trigger export for eval {eval_name}: {e}")

    lro_results = {}
    pending = list(lro_map.keys())
    while pending:
        time.sleep(2)
        for eval_name in pending[:]:
            op_name = lro_map[eval_name]
            lro_req = urllib.request.Request(f"https://{lro_domain}/v1beta/{op_name}", headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(lro_req) as lro_resp:
                    lro = json.loads(lro_resp.read().decode())
                    if lro.get("done"):
                        lro_results[eval_name] = lro
                        pending.remove(eval_name)
            except Exception as e:
                print(f"Polling error for {eval_name}: {e}")

    for item in results:
        item["toolErrors"] = []
        item["detailedTelemetry"] = {}
        lro = lro_results.get(item["evalName"], {})
        if "error" in lro or not lro:
            continue
            
        try:
            b64 = lro["response"]["evaluationResultsContent"]
            z_data = base64.b64decode(b64)
            errors_found = []
            conv_data = {}
            eval_data = {}
            with zipfile.ZipFile(io.BytesIO(z_data), "r") as z:
                conv_file = next((n for n in z.namelist() if n.startswith("conversations/") and n.endswith(".json")), None)
                if conv_file:
                    conv_data = json.loads(z.read(conv_file).decode("utf-8"))
                    tc_map = {}
                    for t_idx, turn in enumerate(conv_data.get("turns", [])):
                        for m_idx, msg in enumerate(turn.get("messages", [])):
                            for chunk in msg.get("chunks", []):
                                if "toolCall" in chunk:
                                    tc = chunk["toolCall"]
                                    if "id" in tc and "tool" in tc:
                                        tc_map[tc["id"]] = tc["tool"].split("/")[-1]
                                if "toolResponse" in chunk:
                                    tr = chunk["toolResponse"]
                                    tc_id = tr.get("id")
                                    tc_name = tc_map.get(tc_id, "unknown_tool")
                                    err = tr.get("response", {}).get("error", "No Error Field")
                                    if err != "No Error Field":
                                        errors_found.append(f"[{tc_name}]: {err}")
                                    elif "content" in tr.get("response", {}):
                                        content_str = str(tr["response"]["content"])
                                        if "Missing parameter" in content_str or "Schema validation" in content_str or "TypeError" in content_str:
                                            errors_found.append(f"[{tc_name}]: {content_str}")

                        root_span = turn.get("rootSpan", {})
                        for span in root_span.get("childSpans", []):
                            attrs = span.get("attributes", {})
                            if "undeclared tool references" in attrs:
                                u_refs = attrs["undeclared tool references"]
                                if isinstance(u_refs, list):
                                    for u_tool in u_refs:
                                        errors_found.append(f"[undeclared_tool]: {u_tool}")
                                elif isinstance(u_refs, str):
                                    errors_found.append(f"[undeclared_tool]: {u_refs}")
                            
                            if "errorInfo" in attrs:
                                err = attrs["errorInfo"]
                                errors_found.append(f"[ActionError]: {err.get('errorMessage', 'unknown error')}")
                
                eval_file = next((n for n in z.namelist() if n.startswith("evaluationResults/") and n.endswith(".json")), None)
                if eval_file:
                    eval_data = json.loads(z.read(eval_file).decode("utf-8"))
                    schema_findings, schema_telemetry = parse_evaluation_schema_details(eval_data, conv_data)
                    errors_found.extend(schema_findings)
                    item["detailedTelemetry"] = schema_telemetry

                    if eval_data.get("evaluationStatus") == "FAIL" and not errors_found:
                        errors_found.append("[Unknown Evaluation Failure]")

            item["toolErrors"] = list(set(errors_found))
        except Exception as e:
            print(f"Error parsing ZIP for eval {item['evalName']}: {e}")
            
    return results

def build_issues_html(issues_list):
    if not issues_list:
        return "<li class='text-green-600'>No issues detected.</li>"
    return "".join(f"<li>{i}</li>" for i in issues_list)

def build_score_badges(telemetry):
    """Builds visual pills for semantic similarity, tool correctness, and goal satisfaction."""
    badges = []
    sem_list = telemetry.get("semanticSimilarity", [])
    if sem_list:
        lowest_sem = min((s.get("score") for s in sem_list if s.get("score") is not None), default=None)
        if lowest_sem is not None:
            color = "bg-green-100 text-green-800" if lowest_sem >= 3 else "bg-amber-100 text-amber-800" if lowest_sem == 2 else "bg-red-100 text-red-800"
            badges.append(f'<span class="inline-block px-2 py-0.5 text-xs font-semibold rounded {color} mr-2">Semantic Similarity: {lowest_sem}/4</span>')

    goal = telemetry.get("userGoalSatisfaction")
    if goal and goal.get("score") is not None:
        g_score = goal.get("score")
        g_color = "bg-green-100 text-green-800" if g_score == 1 else "bg-red-100 text-red-800"
        badges.append(f'<span class="inline-block px-2 py-0.5 text-xs font-semibold rounded {g_color} mr-2">Goal Satisfaction: {goal.get("label", g_score)}</span>')

    sys_errs = telemetry.get("systemErrors", [])
    if sys_errs:
        badges.append(f'<span class="inline-block px-2 py-0.5 text-xs font-semibold rounded bg-purple-100 text-purple-800 mr-2">System/Quota Errors: {len(sys_errs)}</span>')

    if not badges:
        return ""
    return f'<div class="mb-3 flex flex-wrap gap-1">{"".join(badges)}</div>'

def main():
    parser = argparse.ArgumentParser(description="CXAS Standalone HTML & Machine-Readable Agentic Report Generator")
    parser.add_argument("--app-id", required=True, help="Full Cloud Resource name of the application, e.g. 'projects/PROJECT_ID/locations/LOCATION/apps/APP_ID'")
    parser.add_argument("--output", required=True, help="Destination filepath for generated HTML or JSON report output")
    parser.add_argument("--format", choices=["html", "json"], default="html", help="Output format (default: html)")
    parser.add_argument("--env", type=str, choices=["prod", "dev"], default="prod", help="Which environment the App resides in (prod: ces-console.corp vs dev: ces-console-dev.corp)")
    parser.add_argument("--app-dir", type=str, default="", help="Optional local directory path to app bundle bundle for offline static linter checks")
    args = parser.parse_args()
    
    results = fetch_results(args.app_id, env=args.env)
    
    total = len(results)
    passed = 0
    
    summary_cards_html = []
    tool_cards_html = []
    variable_cards_html = []
    semantic_cards_html = []
    handover_cards_html = []
    system_cards_html = []
    
    for item in results:
        res = item["result"]
        status = res.get("evaluationStatus", "UNKNOWN")
        if status == "PASS" or res.get("passed", False):
            item["evaluationStatus"] = "PASS"
            passed += 1
        else:
            item["evaluationStatus"] = "FAIL"
            
        c = categorize_errors(item["toolErrors"])
        item["categorizedIssues"] = c
        item["globalIssues"] = []
        
        all_hints = c["Tool Calls"] + c["State & Variables"] + c["Generative & Phrasing"] + c["Agent Handovers"] + c["System & Infrastructure"]
        
        display_status = item["evaluationStatus"]
        if item["evaluationStatus"] == "PASS" and len(all_hints) > 0:
            display_status = "PASS (WARNINGS)"
            
        badge_color = "bg-green-100 text-green-800"
        border_color = "border-green-200"
        if item["evaluationStatus"] == "FAIL":
            badge_color = "bg-red-100 text-red-800"
            border_color = "border-red-200"
        elif display_status == "PASS (WARNINGS)":
            badge_color = "bg-yellow-100 text-yellow-800"
            border_color = "border-yellow-200"
            
        test_name_full = f"{item['displayName']} (Result ID: {res.get('name', '').split('/')[-1]})"
        parts = item.get("evalName", "").split("/")
        project_id = parts[1] if len(parts) > 1 else ""
        location_id = parts[3] if len(parts) > 3 else ""
        app_id_part = parts[5] if len(parts) > 5 else ""
        eval_id = parts[7] if len(parts) > 7 else ""
        result_id = res.get('name', '').split('/')[-1]
        
        console_domain = "ces-console-dev.corp.google.com" if args.env == "dev" else "ces-console.corp.google.com"
        kind = "goldens" if "goldenResult" in res else "scenarios"
        eval_url = f"https://{console_domain}/projects/{project_id}/locations/{location_id}/apps/{app_id_part}/evaluate/{kind}/{eval_id}/results/{result_id}"
        
        summary_cards_html.append(SUMMARY_ROW_TEMPLATE.format(
            index=item['evalName'], status=display_status, test_name=test_name_full,
            badge_color=badge_color, border_color=border_color,
            scores_badge_bar=build_score_badges(item.get("detailedTelemetry", {})),
            all_issues_html=build_issues_html(all_hints), eval_url=eval_url
        ))
        
        if c["Tool Calls"]:
            tool_cards_html.append(ISSUE_CARD_TEMPLATE.format(test_name=test_name_full, status=display_status, issues_list=build_issues_html(c["Tool Calls"]), eval_url=eval_url))
        if c["State & Variables"]:
            variable_cards_html.append(ISSUE_CARD_TEMPLATE.format(test_name=test_name_full, status=display_status, issues_list=build_issues_html(c["State & Variables"]), eval_url=eval_url))
        if c["Generative & Phrasing"]:
            semantic_cards_html.append(ISSUE_CARD_TEMPLATE.format(test_name=test_name_full, status=display_status, issues_list=build_issues_html(c["Generative & Phrasing"]), eval_url=eval_url))
        if c["Agent Handovers"]:
            handover_cards_html.append(ISSUE_CARD_TEMPLATE.format(test_name=test_name_full, status=display_status, issues_list=build_issues_html(c["Agent Handovers"]), eval_url=eval_url))
        if c["System & Infrastructure"]:
            system_cards_html.append(ISSUE_CARD_TEMPLATE.format(test_name=test_name_full, status=display_status, issues_list=build_issues_html(c["System & Infrastructure"]), eval_url=eval_url))

    cloud_linter_issues = audit_cloud_project_linter(args.app_id, env=args.env)
    linter_output = "No local app-dir provided for bundle linting."
    if args.app_dir:
        linter_script = find_scrapi_cli_script()
        if linter_script and os.path.exists(linter_script):
            try:
                res = subprocess.run(["python3", linter_script, "check-lint", args.app_dir], capture_output=True, text=True)
                linter_output = res.stdout + "\n" + res.stderr
            except Exception as e:
                linter_output = f"Error running linter: {e}"
        else:
            linter_output = "Scrapi CLI script not found."

    if args.format == "json":
        with open(args.output, "w") as f:
            json.dump({
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "schemaVersion": "ces.v1beta.evaluation.proto",
                "projectLinterAudit": {
                    "totalIssues": len(cloud_linter_issues),
                    "issues": cloud_linter_issues
                },
                "linterAnalysis": linter_output if args.app_dir else None,
                "results": results
            }, f, indent=2)
        print(f"Metrics saved to {args.output}")
        return

    category_totals = {"Tool Calls": 0, "State & Variables": 0, "Generative & Phrasing": 0, "Agent Handovers": 0, "System & Infrastructure": 0}
    unique_intents = set()
    total_distinct_errors = 0
    for item in results:
        unique_intents.add(item["evalName"])
        c = categorize_errors(item["toolErrors"])
        total_distinct_errors += len(item["toolErrors"])
        for k in c: category_totals[k] += len(c[k])
    
    system_tab_btn = f'<button onclick="showTab(\'tab-system\')" id="btn-tab-system" class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300">System & Infrastructure ({category_totals["System & Infrastructure"]})</button>' if category_totals["System & Infrastructure"] > 0 else ""

    linter_count_badge = f'<span class="ml-1 px-1.5 py-0.5 text-xs font-bold rounded bg-red-100 text-red-700">{len(cloud_linter_issues)}</span>' if cloud_linter_issues else '<span class="ml-1 px-1.5 py-0.5 text-xs font-bold rounded bg-green-100 text-green-700">0</span>'

    tab_btns_html = """
        <div class="border-b border-gray-200 mb-6 flex overflow-x-auto">
            <nav class="-mb-px flex space-x-8 cursor-pointer" aria-label="Tabs">
                <button onclick="showTab('tab-summary')" id="btn-tab-summary" class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm border-indigo-500 text-indigo-600">
                    Summary Dashboard
                </button>
                <button onclick="showTab('tab-tool-calls')" id="btn-tab-tool-calls" class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300">
                    Tool Calls ({num_tools})
                </button>
                <button onclick="showTab('tab-variables')" id="btn-tab-variables" class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300">
                    State & Variables ({num_vars})
                </button>
                <button onclick="showTab('tab-semantic')" id="btn-tab-semantic" class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300">
                    Generative & Phrasing ({num_semantic})
                </button>
                <button onclick="showTab('tab-handovers')" id="btn-tab-handovers" class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300">
                    Agent Handovers ({num_handovers})
                </button>
                <button onclick="showTab('tab-linter')" id="btn-tab-linter" class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300">
                    Project Linter Audit {linter_count_badge}
                </button>
                {system_tab_btn}
            </nav>
        </div>
    """.format(
        num_tools=category_totals["Tool Calls"],
        num_vars=category_totals["State & Variables"],
        num_semantic=category_totals["Generative & Phrasing"],
        num_handovers=category_totals["Agent Handovers"],
        linter_count_badge=linter_count_badge,
        system_tab_btn=system_tab_btn
    )

    cloud_linter_items_html = "".join([f'<li class="mb-1">{err}</li>' for err in cloud_linter_issues]) if cloud_linter_issues else '<li class="text-green-600 font-semibold">All agents follow production Agent Studio design guidelines. Zero architectural violations found.</li>'

    local_bundle_section = f"""
        <div class="mt-6">
            <h3 class="font-bold text-gray-700 mb-2">Local Bundle File Linter Output:</h3>
            <div class="bg-gray-900 text-green-400 p-4 rounded font-mono text-sm whitespace-pre-wrap overflow-x-auto">
{linter_output}
            </div>
        </div>
    """ if args.app_dir else ""

    linter_tab_content = f"""
        <div id="tab-linter" class="tab-content">
            <h2 class="text-2xl font-bold mb-2 text-gray-800 border-b pb-2">SCRAPI Project Architecture & Prompt Linter Audit</h2>
            <p class="mb-4 text-gray-600 text-sm">Direct REST audit of all live subagent prompts, tool configurations, and parent-child hierarchy rules in cloud project <code class="bg-gray-100 px-1 py-0.5 rounded text-red-600 font-mono text-xs">{args.app_id}</code>.</p>
            
            <div class="bg-white p-5 rounded-lg shadow-sm border border-gray-200 mb-6">
                <div class="flex items-center justify-between mb-3">
                    <h3 class="font-bold text-lg text-gray-800">Agent Studio Best-Practices Audit ({len(cloud_linter_issues)} Issues)</h3>
                    <span class="px-2.5 py-1 text-xs font-semibold rounded-full {'bg-red-100 text-red-800' if cloud_linter_issues else 'bg-green-100 text-green-800'}">
                        {'ACTION REQUIRED' if cloud_linter_issues else 'COMPLIANT'}
                    </span>
                </div>
                <ul class="list-disc pl-5 text-sm text-gray-800 space-y-1.5">
                    {cloud_linter_items_html}
                </ul>
            </div>
            {local_bundle_section}
        </div>
    """

    insights_text = f"""
    <p class="text-sm text-gray-700 mb-2"><b>Agent Status Overview:</b> Your suite executed {total} test cases across {len(unique_intents)} target evaluation goldens, with {passed} passing natively.</p>
    <p class="text-sm text-gray-700 mb-2"><b>Comprehensive Schema Breakdown:</b> After analyzing full turn-replay telemetry, semantic similarity scores, tool correctness, subagent handovers, and system error objects, {total_distinct_errors} evaluation failure findings were identified:</p>
    <ul class="list-disc list-inside text-sm text-gray-700 mb-2 mt-1 ml-2">
       <li><b>{category_totals['Tool Calls']}</b> API schema, parameter accuracy, or invocation order errors.</li>
       <li><b>{category_totals['State & Variables']}</b> missing parameter assignments or session context failures.</li>
       <li><b>{category_totals['Generative & Phrasing']}</b> generative hallucination or semantic similarity drops.</li>
       <li><b>{category_totals['Agent Handovers']}</b> subagent routing, unexpected escalations to main, or missed handoffs.</li>
       <li><b>{category_totals['System & Infrastructure']}</b> platform runtime errors, quota exhaustion, or retrieval failures.</li>
       <li><b>{len(cloud_linter_issues)}</b> project-wide prompt & architectural design linter flags identified in live Cloud agents (see <b>Project Linter Audit</b> tab).</li>
    </ul>
    """
    if args.app_dir:
        insights_text += f"<p class=\"text-sm text-gray-700 mt-2\">Static Instructions Linter was executed on {args.app_dir}. Check the <b>Project Linter Audit</b> tab for details.</p>"

    final_html = HTML_TEMPLATE.format(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_tests=total, passed_tests=passed, failed_tests=total - passed,
        tab_btns_html=tab_btns_html,
        linter_tab_content=linter_tab_content,
        global_warnings_html="", insights_text=insights_text,
        summary_cards_html="\n".join(summary_cards_html),
        tool_cards_html="\n".join(tool_cards_html),
        variable_cards_html="\n".join(variable_cards_html),
        semantic_cards_html="\n".join(semantic_cards_html),
        handover_cards_html="\n".join(handover_cards_html),
        system_cards_html="\n".join(system_cards_html)
    )
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(final_html)
    print(f"Report saved to {args.output}")

if __name__ == "__main__":
    main()

