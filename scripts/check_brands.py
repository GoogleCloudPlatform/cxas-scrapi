#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Pre-commit guard against third-party brand mentions.

The hook collects the *added* lines of the staged diff (for
``--code-files``) or the in-flight commit message (for
``--commit-msg``), sends them to Gemini with a tight prompt listing
the brands we ARE allowed to mention, and parses a structured-JSON
response identifying any third-party brand that slipped in.

Why diff-only: scanning entire files would re-flag every pre-existing
license header and English-sentence-starter docstring. The hook is
about catching what THIS commit introduces.

Failure mode: if Gemini can't be reached / authenticates fail / the
parsed response is invalid, the hook BLOCKS the commit with an
actionable error. This is intentional — silent passes when the
detector is down would defeat the purpose. Set ``BRAND_CHECK_SKIP=1``
in the environment to bypass deliberately (e.g. emergency hotfix
when GCP is down).

Two modes:
  ``--commit-msg <FILE>``    scan a commit-message file
  ``--code-files <FILE>...`` scan added lines in one or more files

Wired into ``.pre-commit-config.yaml`` as two ``repo: local`` hooks;
the ``commit-msg`` stage requires a one-time
``pre-commit install --hook-type commit-msg``.

Requirements
------------
- ``gcloud auth application-default login`` (or a service-account
  ``GOOGLE_APPLICATION_CREDENTIALS`` pointer)
- ``GOOGLE_CLOUD_PROJECT`` env var, OR ``gcloud config set project ...``
- ``pip install -e .[dev]`` so :class:`cxas_scrapi.utils.gemini.GeminiGenerate`
  is importable (the same wrapper every other Gemini caller in this
  repo uses — see ``MigrationService``, ``CXASOptimizer``, etc.)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

try:
    from cxas_scrapi.utils.gemini import GeminiGenerate
except ImportError:
    # Surfaced as a clear error inside _call_gemini so that argv parsing,
    # --help, and the BRAND_CHECK_SKIP=1 short-circuit still work even
    # when the package isn't installed. Top-level import keeps the hook
    # PEP 8 / ruff PLC0415 compliant.
    GeminiGenerate = None  # type: ignore[assignment,misc]

# Brands the project IS allowed to mention. Sent verbatim in the prompt
# so Gemini knows what NOT to flag. Lower-cased before comparison.
ALLOWED_BRANDS: tuple[str, ...] = (
    # --- Parent org + this project + our placeholder customer name ---
    "Google",
    "GECX",
    "CXAS",
    "DFCX",
    # --- Google products / services this repo legitimately references ---
    "Gemini",
    "Vertex AI",
    "Dialogflow",
    "Dialogflow CX",
    "BigQuery",
    "Cloud Storage",
    "GCS",
    "GCP",
    "Google Cloud",
    "Apigee",
    "Looker",
    "CCAI",
    "CES",
    "Conversational Agents",
    # --- Adjacent ecosystem this repo intentionally integrates with ---
    "Anthropic",
    "Claude",
    "GitHub",
)


# ---------------------------------------------------------------------------
# Diff collection
# ---------------------------------------------------------------------------


def _added_lines(filepath: str) -> list[tuple[int, str]]:
    """Return ``(new_lineno, content)`` for every added line in the
    staged diff for ``filepath``. Uses ``git diff --cached --unified=0``
    so context lines aren't included."""
    try:
        out = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--no-color",
                "--unified=0",
                "--",
                filepath,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if out.returncode != 0 or not out.stdout:
        return []

    added: list[tuple[int, str]] = []
    current_new = 0
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for raw in out.stdout.splitlines():
        if raw.startswith("@@"):
            m = hunk_re.match(raw)
            if m:
                current_new = int(m.group(1))
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            added.append((current_new, raw[1:]))
            current_new += 1
        elif raw.startswith(" "):
            current_new += 1
    return added


def _commit_message_lines(msg_file: str) -> list[tuple[int, str]]:
    """Read the commit-message file, skip ``#``-prefixed template lines,
    return ``(line_no, content)``."""
    try:
        with open(msg_file, encoding="utf-8") as f:
            raw_lines = f.readlines()
    except OSError as exc:
        print(f"Could not read commit message: {exc}", file=sys.stderr)
        return []
    return [
        (idx, line.rstrip("\n"))
        for idx, line in enumerate(raw_lines, 1)
        if not line.lstrip().startswith("#") and line.strip()
    ]


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------


def _resolve_project_id() -> str | None:
    """Look for the GCP project ID in the env, then in
    ``gcloud config get-value project``."""
    for var in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT"):
        v = os.environ.get(var)
        if v:
            return v
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        v = out.stdout.strip()
        if out.returncode == 0 and v and v != "(unset)":
            return v
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


_GEMINI_SYSTEM = (
    "You are a brand-name auditor for a Google Cloud open-source "
    "Python project. Your sole job: scan the provided text for any "
    "third-party company, brand, or commercial product name that is "
    "NOT in the allowlist. Return STRICT JSON only."
)


def _build_user_prompt(
    scoped_label: str,
    numbered_lines: list[tuple[int, str]],
) -> str:
    allowed_csv = ", ".join(ALLOWED_BRANDS)
    body = "\n".join(
        f"{lineno}: {content}" for lineno, content in numbered_lines
    )
    return f"""Scan the following {scoped_label} for third-party brand /
company / product names.

Allowed names (never flag these, case-insensitive; also allow any
phrase starting with one of these as a prefix, e.g. "Cymbal Telco"
matches the "Cymbal" entry):
{allowed_csv}

What TO flag:
- Any company / brand / product name that is NOT in the allowed list
  above, regardless of capitalization or surrounding context
- Such names embedded as identifiers (functions, variables, file paths,
  zip filenames, URLs, modules) — even when lowercased or
  underscore-joined

What NOT to flag:
- Any name in the allowed list above
- Generic English words, comments, or docstring prose
- Code identifiers that are clearly project-internal (anything from
  the project's own modules / classes / functions)
- Ruff / pylint suppression codes (short alphanumeric tokens like
  `PLC0415`, `BLE001`)
- License-header boilerplate words

{scoped_label.title()} (one per line, format `<line_no>: <content>`):
{body}

Return STRICT JSON of the form:
{{"findings": [{{"line_number": <int>, "brand": "<name>",
"snippet": "<the relevant text>"}}]}}

If no third-party brand mentions are present, return
{{"findings": []}}."""


def _call_gemini(prompt: str) -> dict:
    """Send the prompt to Gemini in JSON-mode and return the parsed
    response. Routes through :class:`GeminiGenerate` (the same wrapper
    every other Gemini caller in this repo uses) so credential
    handling, thread-local client construction, and concurrency
    semantics stay consistent. We override the wrapper's defaults to
    a cheap, deterministic configuration (``gemini-2.5-flash`` at
    ``temperature=0.0``) suitable for a per-commit guard.

    Raises :class:`RuntimeError` on any failure (no auth, no project,
    network down, empty / non-JSON response). The wrapper swallows
    exceptions and returns ``None``; we translate that back into an
    explicit raise to honor the hook's block-on-failure contract."""
    project_id = _resolve_project_id()
    if not project_id:
        raise RuntimeError(
            "GCP project not resolvable. Set GOOGLE_CLOUD_PROJECT or "
            "run `gcloud config set project <id>`."
        )

    if GeminiGenerate is None:
        raise RuntimeError(
            "cxas_scrapi not importable — run `pip install -e .[dev]` "
            "from the repo root."
        )

    try:
        client = GeminiGenerate(
            project_id=project_id,
            location="global",
            model_name="gemini-2.5-flash",
        )
        text = client.generate(
            prompt=prompt,
            system_prompt=_GEMINI_SYSTEM,
            response_mime_type="application/json",
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Gemini call failed: {exc}") from exc

    if not text:
        # GeminiGenerate returns None on any exception inside the call.
        raise RuntimeError(
            "Gemini returned no response (check auth, quota, and "
            "network connectivity)."
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned non-JSON: {text[:200]!r}") from exc


# ---------------------------------------------------------------------------
# Mode dispatchers
# ---------------------------------------------------------------------------


def _format_findings(findings: list[dict], source_label: str) -> list[str]:
    out: list[str] = []
    for f in findings:
        line_no = f.get("line_number", "?")
        brand = f.get("brand", "?")
        snippet = f.get("snippet", "").strip()
        snippet_str = f" — {snippet}" if snippet else ""
        out.append(f"  {source_label} line {line_no}: '{brand}'{snippet_str}")
    return out


def check_commit_message(msg_file: str) -> int:
    """Returns 0 (clean), 1 (findings or Gemini failure)."""
    lines = _commit_message_lines(msg_file)
    if not lines:
        return 0
    prompt = _build_user_prompt("commit message", lines)
    try:
        parsed = _call_gemini(prompt)
    except RuntimeError as exc:
        print(f"❌ Brand check FAILED — commit blocked: {exc}")
        print(
            "    To bypass deliberately (e.g. emergency hotfix when "
            "GCP is unreachable), set BRAND_CHECK_SKIP=1 in the env."
        )
        return 1

    findings = parsed.get("findings") or []
    if findings:
        print("❌ COMMIT BLOCKED — third-party brand in commit message:")
        for line in _format_findings(findings, "msg"):
            print(line)
        print(
            "\nReplace the brand with 'Cymbal' (the project's "
            "fictitious-brand placeholder) or remove the reference."
        )
        return 1
    return 0


def check_code_files(filepaths: list[str]) -> int:
    """Returns 0 (clean), 1 (findings or Gemini failure)."""
    numbered: list[tuple[str, int, str]] = []
    for fp in filepaths:
        for lineno, content in _added_lines(fp):
            numbered.append((fp, lineno, content))

    if not numbered:
        return 0  # no added lines → nothing to scan

    # Render with file-qualified line numbers so multiple files round-trip
    # in one Gemini call.
    flat = [
        (idx + 1, f"[{fp}:{lineno}] {content}")
        for idx, (fp, lineno, content) in enumerate(numbered)
    ]
    prompt = _build_user_prompt("added diff lines (across files)", flat)

    try:
        parsed = _call_gemini(prompt)
    except RuntimeError as exc:
        print(f"❌ Brand check FAILED — commit blocked: {exc}")
        print(
            "    To bypass deliberately (e.g. emergency hotfix when "
            "GCP is unreachable), set BRAND_CHECK_SKIP=1 in the env."
        )
        return 1

    findings = parsed.get("findings") or []
    if findings:
        print("❌ COMMIT BLOCKED — third-party brand mentions detected:")
        # Map back from flattened line numbers to (file, real_lineno).
        index = {idx: (fp, ln) for idx, (fp, ln, _c) in enumerate(numbered, 1)}
        for f in findings:
            raw_ln = f.get("line_number")
            brand = f.get("brand", "?")
            snippet = (f.get("snippet") or "").strip()
            snippet_str = f" — {snippet}" if snippet else ""
            if isinstance(raw_ln, int) and raw_ln in index:
                fp, real_ln = index[raw_ln]
                print(f"  {fp}:{real_ln}: '{brand}'{snippet_str}")
            else:
                print(f"  (unknown location): '{brand}'{snippet_str}")
        print(
            "\nReplace the brand with 'Cymbal' (the project's "
            "fictitious-brand placeholder) or rephrase the comment / "
            "identifier / string."
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if os.environ.get("BRAND_CHECK_SKIP"):
        print("⚠ Brand check skipped (BRAND_CHECK_SKIP set).", file=sys.stderr)
        return 0

    if len(argv) < 2:
        print(
            "Usage:\n"
            "  check_brands.py --commit-msg <FILE>\n"
            "  check_brands.py --code-files <FILE> [<FILE> ...]",
            file=sys.stderr,
        )
        return 2

    mode = argv[1]
    targets = argv[2:]

    if mode == "--commit-msg":
        if not targets:
            print("--commit-msg requires a file path", file=sys.stderr)
            return 2
        return check_commit_message(targets[0])

    if mode == "--code-files":
        if not targets:
            return 0
        return check_code_files(targets)

    print(f"Unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
