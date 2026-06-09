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

"""Generate TurnEvals probes from SimulationEvals runs.

Run the (expensive) simulation loop ONCE, then slice each scenario's
trajectory into single-turn, deterministic, AUDIO turn-eval probes you can
iterate on cheaply. Expectations are harvested from what the agent actually
did (tool calls + transfers) so observed-good behavior gets locked in.

This script changes NO harness code; it only consumes the public
``SimulationEvals`` API and emits a YAML the ``TurnEvals`` runner accepts
(run it with ``cxas sxs``).

IMPORTANT: generation bakes in observed behavior, INCLUDING bugs. Always
review the harvested assertions (use --review) and keep only the ones that
represent *required* behavior before trusting the file. Never generate from a
failing run without curating.

Examples
--------
# Re-use a prior `cxas evals report` run, slice into audio probes:
python .agents/skills/cxas-turn-eval-curator/scripts/sim_to_turn_evals.py \
    --app-name projects/.../apps/<id> \
    --sim-results eval-out/sim_results.json \
    --output evals/turn_tests/from_sims.yaml --review

# Capture fresh (audio) then convert:
python .agents/skills/cxas-turn-eval-curator/scripts/sim_to_turn_evals.py \
    --app-name projects/.../apps/<id> \
    --eval-file evals/simulations/simulations.yaml --run \
    --capture-modality audio \
    --output evals/turn_tests/from_sims.yaml --review
"""

# Force urllib3 to use python's standard ssl module even if pyopenssl is installed.
# This prevents ValueError: "Context has already been used to create a Connection".
import os
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

try:
    import urllib3.contrib.pyopenssl
    urllib3.contrib.pyopenssl.inject_into_urllib3 = lambda: None
    urllib3.contrib.pyopenssl.extract_from_urllib3()
except ImportError:
    pass

import argparse
import ast
import json
import re
import sys
import time
from typing import Any, Dict, List, Optional


# Transfers are surfaced by the harness as this synthetic tool call.
_TRANSFER_ACTION = "transfer_to_agent"
_TRANSFER_TARGET_KEYS = ("agent", "agent_name", "target_agent")
# Default arg keys worth pinning as tool_input assertions (stable identifiers).
_DEFAULT_INPUT_KEYS = ("account_id", "customer_id", "ticket_id", "line_id")

# The local sim trace embeds tool calls / transfers as lines INSIDE the
# multi-line "Agent Text: ... blocks, which the library's own line-start
# parser misses. We parse them directly so conversion works without the
# platform conversation fetch (which may be unavailable / not retained).
_TOOLCALL_RE = re.compile(r"Tool Call:\s*(?P<name>\S+)\s+with args\s+(?P<args>\{.*\})\s*$")
_TOOLRESPONSE_RE = re.compile(r"Tool Response:\s*(?P<name>\S+)\s+with result\s+(?P<result>\{.*\})\s*$")
_TRANSFER_RE = re.compile(r"Agent Transfer:\s*(?P<target>.+?)\s*$")


def _parse_local_trace(trace_lines):
    """Parse SimulationEvals detailed_trace into Turn objects WITH tool calls and outputs.

    Richer than SimulationEvals._get_turns_from_local_trace, which only reads
    line starts and therefore drops embedded ``Tool Call:`` lines.
    """
    from cxas_scrapi.utils.eval_utils import Turn, ToolCall  # noqa: PLC0415

    turns: List[Any] = []
    cur = None
    for block in trace_lines:
        for line in str(block).split("\n"):
            line = line.rstrip()
            if line.startswith("User: "):
                cur = Turn(user=line[6:].strip(), tool_calls=[])
                turns.append(cur)
                continue
            if line.startswith("Agent Text: "):  # excludes "Agent Text (Diag):"
                if cur is None:
                    cur = Turn(tool_calls=[])
                    turns.append(cur)
                txt = line[len("Agent Text: "):].strip()
                cur.agent = f"{cur.agent} {txt}".strip() if cur.agent else txt
                continue
            if cur is None:
                continue
            m = _TOOLCALL_RE.search(line)
            if m:
                try:
                    args = ast.literal_eval(m.group("args"))
                except (ValueError, SyntaxError):
                    args = {}
                cur.tool_calls.append(
                    ToolCall(
                        action=m.group("name"),
                        args=args if isinstance(args, dict) else {},
                    )
                )
                continue
            mr = _TOOLRESPONSE_RE.search(line)
            if mr:
                name = mr.group("name")
                try:
                    res_val = ast.literal_eval(mr.group("result"))
                except (ValueError, SyntaxError):
                    res_val = {}
                match = next(
                    (t for t in reversed(cur.tool_calls)
                     if t.action == name and t.output is None),
                    None,
                )
                if match:
                    if isinstance(res_val, dict) and "result" in res_val:
                        match.output = res_val["result"]
                    else:
                        match.output = res_val
                continue
            mt = _TRANSFER_RE.search(line)
            if mt:
                target = mt.group("target").removeprefix("Transferred to ").strip()
                cur.tool_calls.append(
                    ToolCall(action=_TRANSFER_ACTION, args={"agent": target})
                )
                continue
    return turns


def _agent_text(turn) -> str:
    """Join a Turn.agent value (str | list | None) into one string."""
    a = getattr(turn, "agent", None)
    if a is None:
        return ""
    if isinstance(a, list):
        return " ".join(str(x) for x in a if x).strip()
    return str(a).strip()


def _as_utterance(turn) -> List[Dict[str, Any]]:
    """Render a reconstructed Turn as historical_contexts.utterances entries."""
    out = []
    user = getattr(turn, "user", None)
    if user:
        user = str(user).strip()
        # Normalize "event: welcome" back to the <event>...</event> form.
        if user.lower().startswith("event:"):
            user = f"<event>{user.split(':', 1)[1].strip()}</event>"
        out.append({"user": user})
    text = _agent_text(turn)
    if text:
        out.append({"agent": text})
    return out


def _harvest_expectations(
    turn, input_keys, output_keys, want_output
) -> List[Dict[str, Any]]:
    """Turn observed tool calls / transfers into deterministic expectations.

    ``want_output`` emits tool_output assertions when the captured turn carries
    tool responses (only available from the real platform record, not the local
    trace). ``output_keys`` pins specific response keys; empty -> existence-only.
    """
    exps: List[Dict[str, Any]] = []
    seen = set()
    for tc in getattr(turn, "tool_calls", []) or []:
        action = tc.action
        args = tc.args or {}
        if action == _TRANSFER_ACTION:
            target = next(
                (args[k] for k in _TRANSFER_TARGET_KEYS if args.get(k)),
                "unknown",
            )
            key = ("agent_transfer", target)
            if key not in seen:
                seen.add(key)
                exps.append({"type": "agent_transfer", "value": target})
            continue

        key = ("tool_called", action)
        if key not in seen:
            seen.add(key)
            exps.append({"type": "tool_called", "value": action})

        if "*" in input_keys:
            pinned = args
        else:
            pinned = {k: args[k] for k in input_keys if k in args}
        if pinned:
            pkey = ("tool_input", json.dumps(pinned, sort_keys=True, default=str))
            if pkey not in seen:
                seen.add(pkey)
                exps.append({"type": "tool_input", "value": pinned})

        out = getattr(tc, "output", None)
        if want_output and isinstance(out, dict):
            if "*" in output_keys:
                pinned_out = out
            else:
                pinned_out = {k: out[k] for k in output_keys if k in out}
            value = {action: pinned_out}  # {} => assert the tool returned
            okey = ("tool_output", json.dumps(value, sort_keys=True, default=str))
            if okey not in seen:
                seen.add(okey)
                exps.append({"type": "tool_output", "value": value})
    return exps


def _fetch_conversation_dict(app_name, creds, sid, attempts, delay):
    """Fetch the real platform conversation, retrying past the read-after-write
    eventual-consistency window (a fresh sim's conversation can briefly 404)."""
    from cxas_scrapi.core.conversation_history import ConversationHistory  # noqa: PLC0415

    ch = ConversationHistory(app_name=app_name, creds=creds)
    last = None
    for a in range(attempts):
        try:
            conv = ch.get_conversation(sid)
            return type(conv).to_dict(conv)
        except Exception as e:  # noqa: BLE001
            last = e
            if a < attempts - 1:
                time.sleep(delay * (a + 1))
    raise last


def _parse_conversation_turns(conv_dict):
    """Parse a platform conversation dict into native turns (1:1 with
    conv_dict['turns'], so the index doubles as turn_count) WITH tool outputs."""
    from cxas_scrapi.core.sessions import Sessions  # noqa: PLC0415
    from cxas_scrapi.utils.eval_utils import Turn, ToolCall  # noqa: PLC0415

    turns = []
    for p_turn in conv_dict.get("turns", []):
        users, agents, tcs = [], [], []
        for m in p_turn.get("messages", []):
            role = m.get("role", "")
            for ch in m.get("chunks", []):
                if ch.get("text"):
                    (users if role == "user" else agents).append(ch["text"])
                elif "tool_call" in ch:
                    tc = ch["tool_call"]
                    nm = tc.get("display_name") or tc.get("tool")
                    args = Sessions._expand_pb_struct(tc.get("args", {}))  # noqa: SLF001
                    tcs.append(ToolCall(action=nm, args=args))
                elif "tool_response" in ch:
                    tr = ch["tool_response"]
                    nm = tr.get("display_name") or tr.get("tool")
                    resp = Sessions._expand_pb_struct(tr.get("response", {}))  # noqa: SLF001
                    match = next(
                        (t for t in reversed(tcs)
                         if t.action == nm and t.output is None),
                        None,
                    )
                    if match:
                        match.output = resp
                    else:
                        tcs.append(ToolCall(action=nm, args={}, output=resp))
                elif "agent_transfer" in ch:
                    at = ch["agent_transfer"]
                    tgt = at.get("display_name") or at.get(
                        "target_agent", "unknown"
                    )
                    tcs.append(
                        ToolCall(action=_TRANSFER_ACTION, args={"agent": tgt})
                    )
        turns.append(
            Turn(
                user=" ".join(users).strip() or None,
                agent=" ".join(agents).strip() or None,
                tool_calls=tcs,
            )
        )
    return turns


def _reconstruct(app_name, creds, res, args):
    """Return (turns, source). Prefer the real platform conversation
    (full tool args+outputs) to get rich expectations; fall back to the local
    trace (text + tool args + transfers, no outputs) only if the fetch fails or
    no session_id is available."""
    sid = res.get("session_id")
    if sid:
        try:
            cd = _fetch_conversation_dict(
                app_name, creds, sid, args.fetch_retries, args.fetch_delay
            )
            return _parse_conversation_turns(cd), "platform"
        except Exception as e:  # noqa: BLE001
            print(
                f"  ! platform fetch failed for {res.get('name')} after "
                f"{args.fetch_retries} tries ({type(e).__name__}); "
                f"falling back to local trace (no tool outputs).",
                file=sys.stderr,
            )

    return _parse_local_trace(res.get("detailed_trace", [])), "local"


def _build_probes(app_name, creds, results, args) -> List[Dict[str, Any]]:
    input_keys = [k.strip() for k in args.input_keys.split(",") if k.strip()]
    output_keys = [k.strip() for k in args.output_keys.split(",") if k.strip()]
    want_output = not args.no_tool_output
    probes: List[Dict[str, Any]] = []

    for res in results:
        name = res.get("name", "sim")
        if args.only_passing and not res.get("passed", False):
            print(f"  - skip {name}: sim did not pass (--only-passing)")
            continue

        turns, source = _reconstruct(app_name, creds, res, args)
        if not turns:
            print(f"  - skip {name}: no turns reconstructed")
            continue

        sparams = res.get("session_parameters", {}) or {}
        carried = (
            [e["expectation"] for e in res.get("expectation_details", [])]
            if args.carry_expectations
            else []
        )

        scenario_probes = []
        for i, turn in enumerate(turns):
            user_i = getattr(turn, "user", None)
            if not user_i:
                continue  # need a user input to probe this turn
            exps = _harvest_expectations(
                turn, input_keys, output_keys,
                want_output,
            )
            if args.include_text:
                text = _agent_text(turn)
                if text:
                    exps.append(
                        {"type": "fuzzy_match", "value": text[:200]}
                    )
            if len(exps) < args.min_assertions:
                continue

            probe: Dict[str, Any] = {
                "conversation": f"{name}__t{i}",
                "tags": ["from-sim", args.modality.lower(), name],
            }
            # Prefix context: turns before this user input.
            prefix = turns[:i]
            
            # Determine if we should generate session_id context:
            # We use session_id context IF:
            # 1. User requested "session" or "auto"
            # 2. AND we successfully got platform conversation (source == "platform")
            # 3. AND session_id is present in the results
            use_session_context = (
                args.context in ("session", "auto")
                and source == "platform"
                and res.get("session_id") is not None
            )

            if use_session_context:
                probe["historical_contexts"] = {"session_id": res["session_id"]}
                probe["turn_count"] = i  # include i prior turns
            else:
                utts: List[Dict[str, Any]] = []
                for t in prefix:
                    utts.extend(_as_utterance(t))
                if utts:
                    probe["historical_contexts"] = {"utterances": utts}
                # Fabricated prefix can't re-run prior tools -> inject state.
                if sparams:
                    probe["variables"] = sparams

            probe["user"] = str(user_i).strip()
            probe["expectations"] = exps
            scenario_probes.append(probe)

        # Carry scenario-level (LLM-judged) expectations onto the last probe.
        if carried and scenario_probes:
            scenario_probes[-1]["expectations"].extend(carried)

        if not scenario_probes:
            print(f"  - skip {name}: no assertion-bearing turns "
                  f"(context={source})")
        probes.extend(scenario_probes)

    return probes


def _write_yaml(probes, args) -> str:
    import yaml

    doc = {
        "config": {"modality": args.modality},
        "conversations": probes,
    }
    if args.use_tool_fakes:
        doc["config"]["use_tool_fakes"] = True

    header = (
        "# AUTO-GENERATED from simulation runs by scripts/sim_to_turn_evals.py.\n"
        "# Each probe replays a frozen prefix and asserts ONE turn deterministically.\n"
        "# REVIEW before trusting: generation bakes in observed behavior incl. bugs.\n"
        f"# Run:  cxas sxs --app-name-a <baseline> --app-name-b <candidate> \\\n"
        f"#               --eval-file {args.output}\n\n"
    )
    body = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=100)
    out = header + body
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(out)
    return out


def _review(probes) -> None:
    print("\n===== REVIEW: harvested probes (curate before trusting) =====")
    if not probes:
        print("  (none — likely no tool/transfer detail; see warnings above)")
    for p in probes:
        hc = p.get("historical_contexts", {})
        ctx = (
            f"session_id+turn_count={p.get('turn_count')}"
            if "session_id" in hc
            else f"utterances({len(hc.get('utterances', []))})"
        )
        print(f"\n  • {p['conversation']}  [{ctx}]")
        print(f"      user: {p['user'][:80]}")
        for e in p["expectations"]:
            if isinstance(e, str):
                print(f"      expect (LLM): {e[:80]}")
            else:
                print(f"      expect: {e['type']} = {e['value']}")


def _local_load_sim_test_cases(yaml_path: str) -> list[dict]:
    """Fallback parser when cxas_scrapi.utils.reporting is not available."""
    import yaml
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}
    if isinstance(data, list):
        return data

    common_params = data.get("common_session_parameters", {}) or {}
    common_expectations = data.get("common_expectations", []) or []
    cases = data.get("evals", [])
    if not isinstance(cases, list):
        return []

    merged_cases = []
    for c in cases:
        if isinstance(c, dict):
            case_copy = c.copy()
            # Merge session parameters
            case_params = case_copy.get("session_parameters", {}) or {}
            merged = common_params.copy()
            merged.update(case_params)
            case_copy["session_parameters"] = merged

            # Merge expectations
            case_expectations = case_copy.get("expectations", []) or []
            case_copy["expectations"] = common_expectations + case_expectations

            merged_cases.append(case_copy)
    return merged_cases


def _load_results(sim, args) -> List[Dict[str, Any]]:
    if args.sim_results:
        with open(args.sim_results, encoding="utf-8") as f:
            data = json.load(f)
        # Accept either a bare list or {"simulation": [...]} / {"results": [...]}
        if isinstance(data, dict):
            data = data.get("simulation") or data.get("results") or data.get(
                "sims", []
            )
        print(f"Loaded {len(data)} sim results from {args.sim_results}")
        return data

    # Capture fresh.
    if sim is None:
        raise ValueError("SimulationEvals client is not initialized.")

    try:
        from cxas_scrapi.utils.reporting import _load_sim_test_cases  # noqa: PLC0415
    except (ImportError, ModuleNotFoundError):
        _load_sim_test_cases = _local_load_sim_test_cases

    cases = _load_sim_test_cases(args.eval_file)
    print(
        f"Running {len(cases)} simulations (modality={args.capture_modality}, "
        f"runs={args.runs}) to capture trajectories..."
    )
    return sim.run_simulations(
        cases, runs=args.runs, modality=args.capture_modality
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app-name", required=True,
                   help="CXAS App resource (projects/.../apps/...).")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--sim-results",
                     help="Path to a saved sim results JSON to convert.")
    src.add_argument("--eval-file",
                     help="Simulation YAML to capture from (use with --run).")
    p.add_argument("--run", action="store_true",
                     help="Run sims from --eval-file before converting.")
    p.add_argument("--capture-modality", choices=["audio", "text"],
                   default="audio",
                   help="Modality for the capture run (default: audio).")
    p.add_argument("--runs", type=int, default=1,
                   help="Runs per scenario during capture (default: 1).")
    p.add_argument("--output", required=True, help="Output turn-eval YAML path.")

    p.add_argument("--modality", default="AUDIO",
                   help="Modality the GENERATED probes run in (default: AUDIO).")
    p.add_argument("--use-tool-fakes", action="store_true", default=True,
                   help="Set use_tool_fakes in the generated config (default).")
    p.add_argument("--no-tool-fakes", dest="use_tool_fakes",
                   action="store_false")
    p.add_argument("--context", choices=["auto", "session", "utterances"],
                   default="utterances",
                   help="Prefix replay strategy (default: utterances — replay using "
                        "inline utterances and variables; session replays the "
                        "REAL conversation via session_id+turn_count; auto uses "
                        "session if platform fetch succeeds, else utterances).")
    p.add_argument("--fetch-retries", type=int, default=5,
                   help="Attempts to fetch each conversation, to ride out "
                        "read-after-write consistency (default: 5).")
    p.add_argument("--fetch-delay", type=float, default=2.0,
                   help="Base seconds between fetch retries (linear backoff).")
    p.add_argument("--input-keys", default="*",
                   help="Comma-separated tool-arg keys to pin as tool_input "
                        "assertions (default: * to include all).")
    p.add_argument("--output-keys", default="status",
                   help="Comma-separated tool-response keys to pin as tool_output "
                        "assertions (empty -> existence-only). Session mode only.")
    p.add_argument("--no-tool-output", action="store_true",
                   help="Do not emit tool_output assertions.")
    p.add_argument("--carry-expectations", action="store_true", default=True,
                   help="Attach the sim's free-text expectations (LLM-judged) "
                        "to each scenario's last probe (default).")
    p.add_argument("--no-carry-expectations", dest="carry_expectations",
                   action="store_false")
    p.add_argument("--include-text", action="store_true",
                   help="Also add a fuzzy_match on the agent's text (brittle; "
                        "off by default).")
    p.add_argument("--min-assertions", type=int, default=1,
                   help="Skip turns with fewer than N deterministic assertions "
                        "(default: 1).")
    p.add_argument("--only-passing", action="store_true",
                   help="Only convert scenarios whose sim run passed.")
    p.add_argument("--write-results",
                   help="Save the raw captured sim results (trajectories) JSON to this path.")
    p.add_argument("--review", action="store_true",
                   help="Print harvested assertions for curation.")
    args = p.parse_args()

    if args.eval_file and not args.run:
        p.error("--eval-file requires --run (to capture trajectories).")

    sim = None
    if args.run:
        from cxas_scrapi.evals.simulation_evals import SimulationEvals  # noqa: PLC0415
        sim = SimulationEvals(app_name=args.app_name)

    results = _load_results(sim, args)
    if not results:
        print("No sim results to convert.", file=sys.stderr)
        sys.exit(1)

    if args.write_results:
        import json
        with open(args.write_results, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Saved raw sim results (trajectories) to {args.write_results}")

    print("Building turn-eval probes...")
    creds = getattr(sim, "creds", None) if sim else None
    probes = _build_probes(args.app_name, creds, results, args)
    _write_yaml(probes, args)
    print(f"\nWrote {len(probes)} probes to {args.output}")
    if args.review:
        _review(probes)


if __name__ == "__main__":
    main()
