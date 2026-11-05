with open("src/cxas_scrapi/core/traces.py", "r") as f:
    text = f.read()

# Replace the hardcoded TEXT/AUDIO initialization and forced defaulting logic
old_logic = """        stats_by_channel = {
            "TEXT": _init_stats(),
            "AUDIO": _init_stats()
        }

        for item in fetched_items:
            n = item["normalized"]
            ch = item["channel"] or "TEXT"
            if ch not in stats_by_channel:
                ch = "TEXT" # Default unknown to text"""

new_logic = """        stats_by_channel = {}

        for item in fetched_items:
            n = item["normalized"]
            ch = item["channel"] or "UNKNOWN"
            if ch not in stats_by_channel:
                stats_by_channel[ch] = _init_stats()"""

text = text.replace(old_logic, new_logic)

# Re-read and output
with open("src/cxas_scrapi/core/traces.py", "w") as f:
    f.write(text)

