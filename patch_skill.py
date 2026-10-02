import re

with open(".agents/skills/cxas-agent-foundry/SKILL.md", "r") as f:
    text = f.read()

conflict = re.compile(r"<<<<<<< HEAD.*?=======\n(.*?)\n>>>>>>>.*?\n", re.DOTALL)

def replacer(match):
    # Keep both the HEAD part (interactive report) and the feature part (estimate quota)
    # But for estimate-quota, we must use our updated flags that I had patched earlier locally
    return """# Generate dynamic interactive HTML dashboard with Gemini LLM failure clustering and parameter filters
python .agents/skills/cxas-agent-foundry/scripts/generate_interactive_report.py --input <path_to_sim_results.json> --output <path_to_report.html>

# Estimate token and tool call production quotas based on sampled trace telemetry
cxas trace estimate-quota --app-name projects/<project_id>/locations/<location>/apps/<app_id> --peak-text-cpm 100 --peak-audio-cpm 50 --time-filter 7d\n"""

text = conflict.sub(replacer, text)

with open(".agents/skills/cxas-agent-foundry/SKILL.md", "w") as f:
    f.write(text)
