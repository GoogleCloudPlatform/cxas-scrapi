#!/usr/bin/env python3
"""
Markdown to HTML Report Converter
This script converts a Markdown report into a stunning, premium HTML document.
It leverages pure Python (`markdown`) with zero native OS C dependencies.

Dependencies:
    pip install markdown

Usage:
    python3 md_to_html.py -i input.md -o output.html
"""

import os
import sys
import argparse
from markdown import markdown

# Premium CSS styling for a state-of-the-art, visual-first HTML report.
PREMIUM_CSS = """
:root {
    --primary: #1e3a8a;
    --primary-light: #eff6ff;
    --slate-50: #f8fafc;
    --slate-100: #f1f5f9;
    --slate-200: #e2e8f0;
    --slate-300: #cbd5e1;
    --slate-700: #334155;
    --slate-800: #1e293b;
    --slate-900: #0f172a;
    
    --critical-bg: #fee2e2;
    --critical-text: #991b1b;
    --critical-border: #fca5a5;
    
    --high-bg: #ffedd5;
    --high-text: #9a3412;
    --high-border: #fdba74;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 16px;
    line-height: 1.6;
    color: var(--slate-800);
    background-color: var(--slate-50);
    margin: 0;
    padding: 40px 20px;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    background-color: #ffffff;
    padding: 40px;
    border-radius: 12px;
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
    border: 1px solid var(--slate-200);
}

h1 {
    font-size: 2.2rem;
    color: var(--primary);
    margin-top: 0;
    margin-bottom: 20px;
    border-bottom: 3px solid var(--primary);
    padding-bottom: 10px;
    font-weight: 800;
}

h2 {
    font-size: 1.5rem;
    color: var(--slate-900);
    margin-top: 40px;
    margin-bottom: 15px;
    border-bottom: 1px solid var(--slate-200);
    padding-bottom: 8px;
    font-weight: 700;
}

h3 {
    font-size: 1.2rem;
    color: var(--primary);
    margin-top: 25px;
    margin-bottom: 10px;
    font-weight: 600;
}

p {
    margin-top: 0;
    margin-bottom: 15px;
}

/* Styled Metadata block */
ul {
    list-style-type: none;
    padding-left: 0;
}

/* Standard markdown lists */
.container > ul {
    padding-left: 20px;
    list-style-type: disc;
}

.container > ul li {
    margin-bottom: 8px;
}

/* Sleek premium tables */
table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    margin: 30px 0;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--slate-200);
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.02);
}

th {
    background-color: var(--primary);
    color: #ffffff;
    font-weight: 600;
    font-size: 0.95rem;
    text-align: left;
    padding: 14px 16px;
    border: none;
}

td {
    padding: 14px 16px;
    font-size: 0.9rem;
    border-bottom: 1px solid var(--slate-200);
    background-color: #ffffff;
    vertical-align: top;
}

tr:last-child td {
    border-bottom: none;
}

tr:nth-child(even) td {
    background-color: var(--slate-50);
}

tr:hover td {
    background-color: var(--primary-light);
}

/* Rich, colorful badges */
.badge {
    display: inline-block;
    font-size: 0.75rem;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 9999px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.badge-critical {
    background-color: var(--critical-bg);
    color: var(--critical-text);
    border: 1px solid var(--critical-border);
}

.badge-very-high, .badge-high {
    background-color: var(--high-bg);
    color: var(--high-text);
    border: 1px solid var(--high-border);
}

/* Code blocks */
code {
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    background-color: var(--slate-100);
    color: var(--slate-900);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.85rem;
}

pre {
    background-color: var(--slate-900);
    color: var(--slate-100);
    border-radius: 8px;
    padding: 16px;
    overflow-x: auto;
    margin-bottom: 20px;
}

pre code {
    background-color: transparent;
    color: inherit;
    padding: 0;
    font-size: 0.85rem;
}

a {
    color: #2563eb;
    text-decoration: none;
    font-weight: 500;
}

a:hover {
    text-decoration: underline;
    color: var(--primary);
}

/* Metadata Box */
.metadata-box {
    background-color: var(--slate-100);
    border-left: 4px solid var(--primary);
    padding: 15px 20px;
    border-radius: 0 8px 8px 0;
    margin-bottom: 30px;
}

.metadata-box p {
    margin: 4px 0;
    font-size: 0.95rem;
}
"""

def convert_md_to_html(input_file, output_file):
    """Converts Markdown report to styled HTML."""
    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' does not exist.", file=sys.stderr)
        return False

    with open(input_file, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # Convert markdown to HTML with extensions
    html_body = markdown(md_content, extensions=['extra', 'tables', 'fenced_code'])

    # Post-process to inject pretty badges and semantic divs
    html_body = html_body.replace("<td>Critical</td>", "<td><span class='badge badge-critical'>Critical</span></td>")
    html_body = html_body.replace("<td>Very High</td>", "<td><span class='badge badge-very-high'>Very High</span></td>")
    html_body = html_body.replace("<td>High</td>", "<td><span class='badge badge-high'>High</span></td>")

    # Build final HTML document
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GECX Prompt Review Report</title>
    <style>
        {PREMIUM_CSS}
    </style>
</head>
<body>
    <div class="container">
        {html_body}
    </div>
</body>
</html>
"""

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"Success: Converted '{input_file}' to '{output_file}' successfully!")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Markdown reports to highly styled HTML.")
    parser.add_argument("-i", "--input", required=True, help="Path to the input markdown file.")
    parser.add_argument("-o", "--output", required=True, help="Path to save the output HTML.")

    args = parser.parse_args()
    success = convert_md_to_html(args.input, args.output)
    sys.exit(0 if success else 1)
