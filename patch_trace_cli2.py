import re

with open("src/cxas_scrapi/cli/trace_cli.py", "r") as f:
    text = f.read()

# Replace the specific print calls with a dynamic loop over channels
pattern = re.compile(r"    if args\.peak_text_cpm > 0 and \"text\" in avgs:.*?\(≈ active connections\)\"\)", re.DOTALL)

new_logic = """    for ch, a in avgs.items():
        # Fallback to 0 if a custom channel doesn't have a specific peak cpm flag
        cpm = args.peak_text_cpm
        if ch.upper() == "AUDIO":
            cpm = args.peak_audio_cpm
        
        # Don't print stats for a channel if its target CPM is 0 and it isn't text
        if cpm == 0 and ch.upper() != "TEXT":
            continue

        print(f"\\n### {ch.upper()} Traffic")
        print("#### Averages per Conversation")
        print(f"- Tokens Input:  {a.get('tokens_input', 0):.2f}")
        print(f"- Tokens Output: {a.get('tokens_output', 0):.2f}")
        print(f"- Tokens Total:  {a.get('tokens_total', 0):.2f}")
        print(f"- Tool Calls:    {a.get('tool_calls', 0):.2f}")
        print(f"- Turns:         {a.get('turns', 0):.2f}")
        if 'duration_seconds' in a:
            print(f"- Duration (s):  {a.get('duration_seconds', 0):.2f}")
        
        print("\\n#### Estimated Quotas per Minute")
        q = qs.get(ch, {})
        print(f"- Chat Token Quota: {q.get('chat_token_quota', 0):.2f}")
        print(f"- ExecuteTool Quota: {q.get('execute_tool_quota', 0):.2f}")
        print(f"- StreamingAnalyzeContent Quota: {q.get('streaming_analyze_content_quota', 0):.2f} (≈ turns per minute)")
        if 'audio_seconds_per_minute' in q:
            print(f"- Audio Seconds per Minute: {q.get('audio_seconds_per_minute', 0):.2f}")
            print(f"- Concurrent BidiRunSession: {q.get('concurrent_bidi_sessions', 0):.2f} (≈ active connections)")"""

text = pattern.sub(new_logic, text)

with open("src/cxas_scrapi/cli/trace_cli.py", "w") as f:
    f.write(text)

