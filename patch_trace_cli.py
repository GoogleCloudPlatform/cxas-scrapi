import re

with open("src/cxas_scrapi/cli/trace_cli.py", "r") as f:
    text = f.read()

# Replace trace_estimate_quota function
pattern_func = re.compile(r"def trace_estimate_quota\(args: argparse\.Namespace\) -> None:.*?# ----------------------------- argparse wiring ------------------------------", re.DOTALL)

new_func = """def trace_estimate_quota(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        stats = traces.estimate_quota(
            peak_text_cpm=args.peak_text_cpm,
            peak_audio_cpm=args.peak_audio_cpm,
            time_filter=args.time_filter,
            source_filter=args.source,
            limit=args.limit,
        )
    except Exception as e:
        print(f"Estimation failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        import json
        print(json.dumps(stats, indent=2, default=str))
        return

    # default: formatted text
    print(f"# Quota Estimation Based on Last {args.time_filter}")
    print("\\n## Traffic Assumptions")
    print(f"- Peak Text Conversations / Minute: {stats.get('traffic_assumptions', {}).get('peak_text_cpm')}")
    print(f"- Peak Audio Conversations / Minute: {stats.get('traffic_assumptions', {}).get('peak_audio_cpm')}")
    print(f"- Total Traces Sampled: {stats.get('traffic_assumptions', {}).get('sample_size')} (Audio: {stats.get('traffic_assumptions', {}).get('audio_samples')}, Text: {stats.get('traffic_assumptions', {}).get('text_samples')})")

    if "error" in stats:
        print(f"\\nError: {stats['error']}")
        return

    avgs = stats.get('averages_per_conversation', {})
    qs = stats.get('estimated_quotas_per_minute', {})

    if args.peak_text_cpm > 0 and "text" in avgs:
        print("\\n### TEXT Traffic")
        print("#### Averages per Conversation")
        print(f"- Tokens Input:  {avgs['text'].get('tokens_input', 0):.2f}")
        print(f"- Tokens Output: {avgs['text'].get('tokens_output', 0):.2f}")
        print(f"- Tokens Total:  {avgs['text'].get('tokens_total', 0):.2f}")
        print(f"- Tool Calls:    {avgs['text'].get('tool_calls', 0):.2f}")
        print(f"- Turns:         {avgs['text'].get('turns', 0):.2f}")
        
        print("\\n#### Estimated Quotas per Minute")
        tq = qs.get("text", {})
        print(f"- Chat Token Quota: {tq.get('chat_token_quota', 0):.2f}")
        print(f"- ExecuteTool Quota: {tq.get('execute_tool_quota', 0):.2f}")
        print(f"- StreamingAnalyzeContent Quota: {tq.get('streaming_analyze_content_quota', 0):.2f} (≈ turns per minute)")

    if args.peak_audio_cpm > 0 and "audio" in avgs:
        print("\\n### AUDIO Traffic")
        print("#### Averages per Conversation")
        print(f"- Tokens Input:  {avgs['audio'].get('tokens_input', 0):.2f}")
        print(f"- Tokens Output: {avgs['audio'].get('tokens_output', 0):.2f}")
        print(f"- Tokens Total:  {avgs['audio'].get('tokens_total', 0):.2f}")
        print(f"- Tool Calls:    {avgs['audio'].get('tool_calls', 0):.2f}")
        print(f"- Turns:         {avgs['audio'].get('turns', 0):.2f}")
        print(f"- Duration (s):  {avgs['audio'].get('duration_seconds', 0):.2f}")
        
        print("\\n#### Estimated Quotas per Minute")
        aq = qs.get("audio", {})
        print(f"- Chat Token Quota: {aq.get('chat_token_quota', 0):.2f}")
        print(f"- ExecuteTool Quota: {aq.get('execute_tool_quota', 0):.2f}")
        print(f"- StreamingAnalyzeContent Quota: {aq.get('streaming_analyze_content_quota', 0):.2f} (≈ turns per minute)")
        print(f"- Audio Seconds per Minute: {aq.get('audio_seconds_per_minute', 0):.2f}")
        print(f"- Concurrent BidiRunSession: {aq.get('concurrent_bidi_sessions', 0):.2f} (≈ active connections)")


# ----------------------------- argparse wiring ------------------------------"""

text = pattern_func.sub(new_func, text)

# Replace argparse setup
pattern_args = re.compile(r"    p_eq.add_argument\(\"--peak-conversations-per-minute\".*?\)", re.DOTALL)
new_args = """    p_eq.add_argument("--peak-text-cpm", type=int, default=0, help="Estimated peak text conversations per minute.")
    p_eq.add_argument("--peak-audio-cpm", type=int, default=0, help="Estimated peak audio conversations per minute.")"""

text = pattern_args.sub(new_args, text)

with open("src/cxas_scrapi/cli/trace_cli.py", "w") as f:
    f.write(text)

print("PATCH CLI SUCCESS")
