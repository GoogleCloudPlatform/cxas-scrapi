# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Pre-indexing dataset diagnostic scanner for Vector Steering intent corpora."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import (  # noqa: E402
    DEFAULT_SAMPLE_CSV,
    IntentSpec,
    load_corpus_dataframe,
    load_intents_catalog,
    load_vector_steering_config,
    resolve_safe_path,
)

SMART_CHARS = ("\u2018", "\u2019", "\u201c", "\u201d")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
LONG_DIGIT_ID_RE = re.compile(r"\b(?:\d[ -]?){7,19}\b")


def normalize_phrase(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    cleaned = text.lower().strip()
    for ch in ('"', "'", *SMART_CHARS):
        cleaned = cleaned.replace(ch, "")
    cleaned = re.sub(r"[?.\!]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def analyze_corpus(
    csv_path: str,
    *,
    catalog: dict[str, IntentSpec] | None = None,
    entity_mask_patterns: list[str] | None = None,
    min_word_count: int = 2,
    min_phrases_per_intent: int = 5,
) -> dict[str, Any]:
    """Analyzes a training corpus and returns structured quality diagnostics."""
    resolved_csv = resolve_safe_path(csv_path)
    df = load_corpus_dataframe(resolved_csv)
    required_cols = {"intent", "phrase"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(
            f"Corpus {resolved_csv} is missing required columns: {sorted(missing_cols)}"
        )

    null_rows: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        intent_val = row.get("intent")
        phrase_val = row.get("phrase")
        if (
            pd.isna(intent_val)
            or pd.isna(phrase_val)
            or not str(intent_val).strip()
            or not str(phrase_val).strip()
        ):
            null_rows.append(
                {
                    "line": int(idx) + 2,
                    "intent": "" if pd.isna(intent_val) else str(intent_val),
                    "phrase": "" if pd.isna(phrase_val) else str(phrase_val),
                }
            )

    valid_df = df.dropna(subset=["intent", "phrase"]).copy()
    valid_df["intent"] = valid_df["intent"].astype(str).str.strip()
    valid_df["phrase"] = valid_df["phrase"].astype(str)
    valid_df = valid_df[
        (valid_df["intent"] != "") & (valid_df["phrase"].str.strip() != "")
    ].copy()
    valid_df["clean_phrase"] = valid_df["phrase"].apply(normalize_phrase)

    conflicts: list[dict[str, Any]] = []
    dup_phrases = valid_df[
        valid_df.duplicated(subset=["clean_phrase"], keep=False)
    ]
    for phrase_clean, group in dup_phrases.groupby("clean_phrase"):
        if not phrase_clean:
            continue
        unique_intents = sorted(group["intent"].unique().tolist())
        if len(unique_intents) > 1:
            conflicts.append(
                {
                    "phrase": str(group["phrase"].iloc[0]),
                    "clean_phrase": str(phrase_clean),
                    "intents": unique_intents,
                    "lines": [int(i) + 2 for i in group.index],
                }
            )

    exact_duplicates: list[dict[str, Any]] = []
    same_intent_dups = valid_df[
        valid_df.duplicated(subset=["intent", "clean_phrase"], keep=False)
    ]
    if not same_intent_dups.empty:
        grouped_dups = (
            same_intent_dups.groupby(["intent", "clean_phrase"])
            .size()
            .reset_index(name="count")
        )
        for _, r in grouped_dups[grouped_dups["count"] > 1].iterrows():
            exact_duplicates.append(
                {
                    "intent": str(r["intent"]),
                    "clean_phrase": str(r["clean_phrase"]),
                    "count": int(r["count"]),
                }
            )

    short_phrases: list[dict[str, Any]] = []
    for idx, row in valid_df.iterrows():
        words = [w for w in str(row["phrase"]).split() if w.strip()]
        if len(words) < min_word_count:
            short_phrases.append(
                {
                    "line": int(idx) + 2,
                    "intent": str(row["intent"]),
                    "phrase": str(row["phrase"]),
                    "word_count": len(words),
                }
            )

    unmasked_entities: list[dict[str, Any]] = []
    patterns = [p for p in (entity_mask_patterns or []) if p.strip()]
    if patterns:
        compiled = re.compile(
            "|".join(f"(?:{p})" for p in patterns), re.IGNORECASE
        )
        for idx, row in valid_df.iterrows():
            phrase_str = str(row["phrase"])
            matches = sorted(
                {m.group(0).lower() for m in compiled.finditer(phrase_str)}
            )
            if matches:
                unmasked_entities.append(
                    {
                        "line": int(idx) + 2,
                        "intent": str(row["intent"]),
                        "phrase": phrase_str,
                        "matched_terms": matches,
                    }
                )

    intent_counts = valid_df["intent"].value_counts().to_dict()
    low_sample_intents = [
        {"intent": intent, "count": int(cnt)}
        for intent, cnt in sorted(intent_counts.items())
        if int(cnt) < min_phrases_per_intent
    ]

    imbalance_ratio = 1.0
    imbalance_warning: str | None = None
    if len(intent_counts) >= 2:
        max_cnt = max(intent_counts.values())
        min_cnt = max(1, min(intent_counts.values()))
        imbalance_ratio = round(float(max_cnt) / float(min_cnt), 2)
        if imbalance_ratio > 5.0:
            imbalance_warning = (
                f"Max/min phrase ratio across intents is {imbalance_ratio}x "
                f"(max={max_cnt}, min={min_cnt}); keep within <=5x."
            )

    undocumented_intents: list[str] = []
    missing_from_csv: list[str] = []
    if catalog is not None:
        csv_intents = set(intent_counts.keys())
        catalog_intents = set(catalog.keys())
        undocumented_intents = sorted(csv_intents - catalog_intents)
        missing_from_csv = sorted(
            intent_id
            for intent_id, spec in catalog.items()
            if spec.vector_indexed and intent_id not in csv_intents
        )

    pii_leaks: list[dict[str, Any]] = []
    for idx, row in valid_df.iterrows():
        phrase_str = str(row["phrase"])
        leak_types: list[str] = []
        if SSN_RE.search(phrase_str):
            leak_types.append("SSN pattern")
        if EMAIL_RE.search(phrase_str):
            leak_types.append("Email address")
        if LONG_DIGIT_ID_RE.search(phrase_str):
            leak_types.append("7+ digit phone/card/member/order ID")
        if leak_types:
            pii_leaks.append(
                {
                    "line": int(idx) + 2,
                    "intent": str(row["intent"]),
                    "leak_types": leak_types,
                }
            )

    formatting_warnings: list[dict[str, Any]] = []
    for idx, row in valid_df.iterrows():
        phrase_str = str(row["phrase"])
        warns: list[str] = []
        if phrase_str != phrase_str.strip():
            warns.append("Leading/trailing whitespace")
        if any(ch in phrase_str for ch in SMART_CHARS):
            warns.append("Smart quotes detected")
        if '""' in phrase_str:
            warns.append("Double-double quotes")
        if CONTROL_RE.search(phrase_str):
            warns.append("ASCII control characters")
        if warns:
            formatting_warnings.append(
                {
                    "line": int(idx) + 2,
                    "intent": str(row["intent"]),
                    "phrase": phrase_str,
                    "warnings": warns,
                }
            )

    critical_count = (
        len(null_rows)
        + len(conflicts)
        + len(undocumented_intents)
        + len(missing_from_csv)
        + len(pii_leaks)
    )
    warning_count = (
        len(exact_duplicates)
        + len(short_phrases)
        + len(unmasked_entities)
        + len(low_sample_intents)
        + len(formatting_warnings)
        + (1 if imbalance_warning else 0)
    )

    return {
        "csv_file": os.path.basename(resolved_csv),
        "total_rows": len(df),
        "valid_rows": len(valid_df),
        "distinct_intents": len(intent_counts),
        "imbalance_ratio": imbalance_ratio,
        "imbalance_warning": imbalance_warning,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "null_rows": null_rows,
        "conflicts": conflicts,
        "pii_leaks": pii_leaks,
        "exact_duplicates": exact_duplicates,
        "short_phrases": short_phrases,
        "unmasked_entities": unmasked_entities,
        "low_sample_intents": low_sample_intents,
        "undocumented_intents": undocumented_intents,
        "missing_from_csv": missing_from_csv,
        "formatting_warnings": formatting_warnings,
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Vector Steering Corpus Diagnostic Report (`{report['csv_file']}`)",
        "",
        f"- **Total Rows**: {report['total_rows']}",
        f"- **Valid Phrases**: {report['valid_rows']}",
        f"- **Distinct Intents**: {report['distinct_intents']}",
        f"- **Critical Issues**: {report['critical_count']}",
        f"- **Warnings**: {report['warning_count']}",
        "",
        "## 1. Null or Empty Fields (Critical)",
    ]
    if report["null_rows"]:
        lines.extend(
            ["| CSV Line | Intent | Phrase |", "| :--- | :--- | :--- |"]
        )
        for r in report["null_rows"]:
            lines.append(f"| {r['line']} | `{r['intent']}` | `{r['phrase']}` |")
    else:
        lines.append("PASS: No null or empty rows found.")

    lines.extend(["", "## 2. Conflicting Phrase-to-Intent Mappings (Critical)"])
    if report["conflicts"]:
        lines.extend(
            [
                "| Phrase | Conflicting Intents | CSV Lines |",
                "| :--- | :--- | :--- |",
            ]
        )
        for c in report["conflicts"]:
            lines.append(
                f'| "{c["phrase"]}" | `{c["intents"]}` | {c["lines"]} |'
            )
    else:
        lines.append("PASS: Zero conflicting intent mappings.")

    lines.extend(["", "## 3. Intent Catalog Alignment (Critical)"])
    if report["undocumented_intents"] or report["missing_from_csv"]:
        if report["undocumented_intents"]:
            lines.append(
                "- **Intents in CSV missing from catalog**: "
                f"`{report['undocumented_intents']}`"
            )
        if report["missing_from_csv"]:
            lines.append(
                "- **Catalog intents with 0 training phrases**: "
                f"`{report['missing_from_csv']}`"
            )
    else:
        lines.append(
            "PASS: Training CSV and Intents Catalog are fully synchronized."
        )

    lines.extend(
        ["", "## 4. Unredacted PII / Live Identifier Leaks (Critical)"]
    )
    if report.get("pii_leaks"):
        lines.extend(
            [
                "| CSV Line | Intent | Detected Leak Type (Redacted) |",
                "| :--- | :--- | :--- |",
            ]
        )
        for leak in report["pii_leaks"]:
            lines.append(
                f"| {leak['line']} | `{leak['intent']}` | "
                f"{', '.join(leak['leak_types'])} |"
            )
    else:
        lines.append(
            "PASS: Zero PII or raw multi-digit identifier leaks detected."
        )

    lines.extend(["", "## 5. Exact Duplicates (Warning)"])
    if report["exact_duplicates"]:
        lines.extend(
            ["| Intent | Normalized Phrase | Count |", "| :--- | :--- | :--- |"]
        )
        for d in report["exact_duplicates"]:
            lines.append(
                f'| `{d["intent"]}` | "{d["clean_phrase"]}" | {d["count"]} |'
            )
    else:
        lines.append("PASS: No duplicate phrases found.")

    lines.extend(["", "## 6. Short / Ambiguous Utterances (Warning)"])
    if report["short_phrases"]:
        lines.extend(
            [
                "| CSV Line | Intent | Phrase | Words |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for s in report["short_phrases"]:
            lines.append(
                f'| {s["line"]} | `{s["intent"]}` | "{s["phrase"]}" | '
                f"{s['word_count']} |"
            )
    else:
        lines.append("PASS: All phrases meet minimum word count.")

    lines.extend(["", "## 7. Unmasked Domain Entities (Warning)"])
    if report["unmasked_entities"]:
        lines.extend(
            [
                "| CSV Line | Intent | Phrase | Matched Entities |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for u in report["unmasked_entities"]:
            lines.append(
                f'| {u["line"]} | `{u["intent"]}` | "{u["phrase"]}" | '
                f"{', '.join(u['matched_terms'])} |"
            )
    else:
        lines.append("PASS: No unmasked entity pattern leaks detected.")

    lines.extend(["", "## 8. Class Balance & Formatting Hygiene"])
    if report.get("imbalance_warning"):
        lines.append(
            f"- **Class Imbalance Warning**: {report['imbalance_warning']}"
        )
    if report["low_sample_intents"]:
        for item in report["low_sample_intents"]:
            lines.append(
                f"- Low phrase count for `{item['intent']}`: "
                f"**{item['count']}** phrases."
            )
    if report["formatting_warnings"]:
        for w in report["formatting_warnings"][:30]:
            lines.append(
                f"- Line {w['line']} (`{w['intent']}`): "
                f"{', '.join(w['warnings'])}"
            )
    if (
        not report.get("imbalance_warning")
        and not report["low_sample_intents"]
        and not report["formatting_warnings"]
    ):
        lines.append("PASS: Class balance and text normalization look clean.")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Vector Steering training dataset quality before embedding."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Optional path to vector_steering.yaml",
    )
    parser.add_argument(
        "--csv", type=str, default=None, help="Path to training phrases CSV"
    )
    parser.add_argument(
        "--catalog",
        type=str,
        default=None,
        help="Optional path to intents_catalog.yaml",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Optional path to output Markdown report",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        type=str,
        default=None,
        help="Optional path to output JSON report",
    )
    parser.add_argument(
        "--mask-pattern",
        action="append",
        default=[],
        help="Regex pattern for unmasked entities",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any critical issues are found",
    )
    args = parser.parse_args()

    cfg = load_vector_steering_config(args.config) if args.config else None
    csv_path = args.csv or (cfg.corpus_csv if cfg else DEFAULT_SAMPLE_CSV)
    catalog_path = args.catalog or (cfg.intents_catalog_path if cfg else None)
    catalog = (
        load_intents_catalog(catalog_path)
        if (catalog_path and os.path.exists(catalog_path))
        else None
    )

    mask_patterns = list(args.mask_pattern or [])
    if cfg and cfg.entity_mask_patterns:
        mask_patterns.extend(cfg.entity_mask_patterns)

    report = analyze_corpus(
        csv_path,
        catalog=catalog,
        entity_mask_patterns=mask_patterns,
        min_word_count=cfg.min_word_count if cfg else 2,
        min_phrases_per_intent=cfg.min_phrases_per_intent if cfg else 5,
    )

    md_text = render_markdown_report(report)
    if args.out:
        out_path = resolve_safe_path(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(md_text)
        print(f"Saved Markdown diagnostic report to: {out_path}")
    else:
        print(md_text)

    if args.json_out:
        json_path = resolve_safe_path(args.json_out)
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)

    if args.strict and report["critical_count"] > 0:
        print(
            f"FAILED strict validation: {report['critical_count']} critical issue(s).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
