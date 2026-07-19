#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre/post migration CLI performance benchmarking script."""

import json
import os
import statistics
import subprocess
import sys
import time
from typing import Any

COMMANDS_TO_BENCHMARK = [
    ("cxas --help", ["uv", "run", "cxas", "--help"]),
    ("cxas help llm-lint", ["uv", "run", "cxas", "help", "llm-lint"]),
    ("cxas lint --list-rules", ["uv", "run", "cxas", "lint", "--list-rules"]),
    ("cxas trace --help", ["uv", "run", "cxas", "trace", "--help"]),
    ("cxas trace search --help", ["uv", "run", "cxas", "trace", "search", "--help"]),
    ("cxas evals --help", ["uv", "run", "cxas", "evals", "--help"]),
    ("cxas evals report --help", ["uv", "run", "cxas", "evals", "report", "--help"]),
    ("cxas apps --help", ["uv", "run", "cxas", "apps", "--help"]),
    ("cxas apps list --help", ["uv", "run", "cxas", "apps", "list", "--help"]),
    ("cxas deployments --help", ["uv", "run", "cxas", "deployments", "--help"]),
    ("cxas run-session --help", ["uv", "run", "cxas", "run-session", "--help"]),
    ("cxas migrate dfcx --help", ["uv", "run", "cxas", "migrate", "dfcx", "--help"]),
]

def run_benchmark(iterations: int = 5) -> dict[str, dict[str, float]]:
    """Runs each command N times and records timing statistics in milliseconds."""
    results: dict[str, dict[str, float]] = {}
    print(f"Running CLI performance benchmarks ({iterations} iterations per command)...")
    for label, cmd in COMMANDS_TO_BENCHMARK:
        print(f"  Benchmarking: {label}", end="", flush=True)
        durations = []
        for _ in range(iterations):
            start = time.perf_counter()
            res = subprocess.run(cmd, capture_output=True, text=True)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if res.returncode != 0:
                print(f" [ERROR: {res.returncode}]")
                print(res.stderr)
                break
            durations.append(elapsed_ms)
            print(".", end="", flush=True)
        if durations:
            stats = {
                "mean_ms": round(statistics.mean(durations), 2),
                "median_ms": round(statistics.median(durations), 2),
                "min_ms": round(min(durations), 2),
                "max_ms": round(max(durations), 2),
                "p95_ms": round(sorted(durations)[int(len(durations) * 0.95)], 2) if len(durations) > 1 else round(durations[0], 2),
            }
            results[label] = stats
            print(f" -> mean: {stats['mean_ms']}ms | min: {stats['min_ms']}ms | max: {stats['max_ms']}ms")
    return results

def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    results = run_benchmark(iterations=5)
    
    out_file = "cli_performance_baselines.json"
    data: dict[str, Any] = {}
    if os.path.exists(out_file):
        try:
            with open(out_file, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[mode] = results
    with open(out_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved '{mode}' baseline results to {out_file}")

if __name__ == "__main__":
    main()
