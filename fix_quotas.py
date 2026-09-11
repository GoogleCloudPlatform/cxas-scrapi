with open("src/cxas_scrapi/core/traces.py", "r") as f:
    text = f.read()

# We need to define _compute_quotas right before it is used.
import re

pattern = re.compile(r"        ret_avgs = \{\}\n        ret_quotas = \{\}", re.DOTALL)
new_defs = """        def _compute_quotas(avgs, peak_cpm):
            q = {
                "chat_token_quota": avgs.get("tokens_total", 0) * peak_cpm,
                "execute_tool_quota": avgs.get("tool_calls", 0) * peak_cpm,
                "streaming_analyze_content_quota": avgs.get("turns", 0) * peak_cpm,
            }
            if "duration_seconds" in avgs:
                q["audio_seconds_per_minute"] = avgs["duration_seconds"] * peak_cpm
                q["concurrent_bidi_sessions"] = (peak_cpm / 60.0) * avgs["duration_seconds"]
            return q

        ret_avgs = {}
        ret_quotas = {}"""

text = pattern.sub(new_defs, text)

# We also still have the old hardcoded return block floating below our new return block!!!
# Let's purge everything after our return { traffic_assumptions... } up to the end of the method.

pattern2 = re.compile(r"                    \"estimated_quotas_per_minute\": ret_quotas\n        \}\n        \n        if peak_audio_cpm > 0 and audio_avgs:.*?        return \{\n.*?        \}", re.DOTALL)
text = pattern2.sub("                    \"estimated_quotas_per_minute\": ret_quotas\n        }", text)

with open("src/cxas_scrapi/core/traces.py", "w") as f:
    f.write(text)

