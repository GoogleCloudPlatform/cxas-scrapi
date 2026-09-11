with open("src/cxas_scrapi/core/traces.py", "r") as f:
    text = f.read()

import re

# We will modify how stats_by_channel is constructed.
# Let's dynamically add the channel to stats_by_channel!

old_code = """        stats_by_channel = {
            "TEXT": {"traffic_assumptions": {}, "averages_per_conversation": {}, "sum": {}, "sample_size": 0},
            "AUDIO": {"traffic_assumptions": {}, "averages_per_conversation": {}, "sum": {}, "sample_size": 0},
        }

        for fut in futures:
            item = futures[fut]
            if "error" in item:
                continue

            ch = item["channel"] or "TEXT"
            if ch not in stats_by_channel:
                ch = "TEXT"

            b = stats_by_channel[ch]"""

new_code = """        stats_by_channel = {}
        for fut in futures:
            item = futures[fut]
            if "error" in item:
                continue
                
            ch = item["channel"] or "UNKNOWN"
            if ch not in stats_by_channel:
                stats_by_channel[ch] = {"traffic_assumptions": {}, "averages_per_conversation": {}, "sum": {}, "sample_size": 0}

            b = stats_by_channel[ch]"""

if old_code in text:
    text = text.replace(old_code, new_code)
else:
    print("Could not find old_code")

# Fix what gets returned
old_return = """        text_avgs = _compute_averages(stats_by_channel["TEXT"])
        audio_avgs = _compute_averages(stats_by_channel["AUDIO"])

        text_q = _compute_quotas(text_avgs, peak_text_cpm)
        audio_q = _compute_quotas(audio_avgs, peak_audio_cpm)

        return {
            "traffic_assumptions": {
                "peak_text_cpm": peak_text_cpm,
                "peak_audio_cpm": peak_audio_cpm,
                "sample_size": len([f for f in futures if "error" not in futures[f]]),
                "audio_samples": stats_by_channel["AUDIO"]["sample_size"],
                "text_samples": stats_by_channel["TEXT"]["sample_size"]
            },
            "averages_per_conversation": {
                "text": text_avgs,
                "audio": audio_avgs,
            },
            "estimated_quotas_per_minute": {
                "text": text_q,
                "audio": audio_q,
            }
        }"""

new_return = """        ret_avgs = {}
        ret_quotas = {}
        
        # Determine the peak cpm dynamically per channel based on inputs (fallback to text cpm for unknown)
        for ch, stats in stats_by_channel.items():
            avgs = _compute_averages(stats)
            ret_avgs[ch.lower()] = avgs
            
            cpm = peak_text_cpm
            if ch == "AUDIO":
                cpm = peak_audio_cpm
            
            ret_quotas[ch.lower()] = _compute_quotas(avgs, cpm)
            
        return {
            "traffic_assumptions": {
                "peak_text_cpm": peak_text_cpm,
                "peak_audio_cpm": peak_audio_cpm,
                "sample_size": len([f for f in futures if "error" not in futures[f]]),
                "samples_per_channel": {ch: stats["sample_size"] for ch, stats in stats_by_channel.items()}
            },
            "averages_per_conversation": ret_avgs,
            "estimated_quotas_per_minute": ret_quotas
        }"""

if old_return in text:
    text = text.replace(old_return, new_return)
else:
    # Try regex fallback for return if we missed something due to indentation
    pattern = re.compile(r"        text_avgs = _compute_averages\(stats_by_channel\[\"TEXT\"\]\).*?        }", re.DOTALL)
    text = pattern.sub(new_return, text)


with open("src/cxas_scrapi/core/traces.py", "w") as f:
    f.write(text)

