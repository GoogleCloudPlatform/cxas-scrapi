"""Utility for parsing Conversational core span telemetry into DataFrames."""

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


class LatencyParser:
    """A generic utility for parsing Conversational core span telemetry into
    DataFrames."""

    @staticmethod
    def fetch_conversation_traces(
        conv_ids: List[str], get_conversation_func: Callable
    ) -> Dict[str, Any]:
        """Fetches detailed conversation traces concurrently with rate
        limiting."""
        traces = {}
        # Rate Limiting: 600 requests per minute API quota = 10 req/s.
        # We will chunk into batches of 5 and sleep 1 second.
        chunk_size = 5
        conv_list = list(set(conv_ids))

        print(
            f"Fetching {len(conv_list)} conversation traces for detailed "
            f"latency metrics..."
        )

        for i in range(0, len(conv_list), chunk_size):
            chunk = conv_list[i : i + chunk_size]
            with ThreadPoolExecutor(max_workers=chunk_size) as executor:
                future_to_id = {
                    executor.submit(get_conversation_func, cid): cid
                    for cid in chunk
                }
                for future in as_completed(future_to_id):
                    cid = future_to_id[future]
                    try:
                        conv = future.result()
                        traces[cid] = (
                            type(conv).to_dict(conv)
                            if not isinstance(conv, dict)
                            else conv
                        )
                    except Exception as e:
                        logger.error(f"Failed to fetch conversation {cid}: {e}")
            if i + chunk_size < len(conv_list):
                time.sleep(1)  # Rate limit padding

        return traces

    @staticmethod
    def _parse_duration_ms(duration_str: str) -> float:
        """Helper to convert string durations like '1.450s' to floats in ms."""
        if not duration_str:
            return 0.0
        return float(duration_str.replace("s", "")) * 1000

    @staticmethod
    def _process_spans(
        spans: List[Dict],
        context_id: str,
        t_idx: int,
        tool_rows: List,
        callback_rows: List,
        guardrail_rows: List,
        llm_rows: List,
        context_key: str = "conversation_id",
    ) -> Dict[str, float]:
        """Recursively walks span trees and accumulates granular component
        attributes."""
        sums = {"LLM": 0.0, "Guardrail": 0.0, "Callback": 0.0}
        for s in spans:
            name = s.get("name")
            duration = LatencyParser._parse_duration_ms(s.get("duration", "0s"))
            attrs = s.get("attributes", {})

            if name in sums:
                sums[name] += duration

            if name == "Tool":
                tool_rows.append(
                    {
                        "tool_name": attrs.get("name", ""),
                        "agent": attrs.get("agent", ""),
                        context_key: context_id,
                        "turn_index": t_idx,
                        "duration_ms": duration,
                    }
                )
            elif name == "Callback":
                callback_rows.append(
                    {
                        "agent": attrs.get("agent", ""),
                        "stage": attrs.get("stage", ""),
                        "description": attrs.get("description", ""),
                        context_key: context_id,
                        "turn_index": t_idx,
                        "duration_ms": duration,
                    }
                )
            elif name == "Guardrail":
                guardrail_rows.append(
                    {
                        "agent": attrs.get("agent", ""),
                        "name": attrs.get("name", attrs.get("description", "")),
                        context_key: context_id,
                        "turn_index": t_idx,
                        "duration_ms": duration,
                    }
                )
            elif name == "LLM":
                llm_rows.append(
                    {
                        "agent": attrs.get("agent", ""),
                        "model": attrs.get("model", ""),
                        "input_tokens": attrs.get("input token count", 0),
                        "output_tokens": attrs.get("output token count", 0),
                        "time_to_first_token_ms": attrs.get(
                            "time to first chunk (ms)", 0
                        ),
                        "time_to_first_audio_ms": attrs.get(
                            "time to first audio (ms)", 0
                        ),
                        "audio_duration_ms": attrs.get(
                            "audio duration (ms)", 0
                        ),
                        context_key: context_id,
                        "turn_index": t_idx,
                        "duration_ms": duration,
                    }
                )

            if "child_spans" in s:
                child_sums = LatencyParser._process_spans(
                    s["child_spans"],
                    context_id,
                    t_idx,
                    tool_rows,
                    callback_rows,
                    guardrail_rows,
                    llm_rows,
                    context_key,
                )
                for k in sums:
                    sums[k] += child_sums[k]
        return sums

    @staticmethod
    def build_summary_df(
        df_d: pd.DataFrame, group_cols: List[str]
    ) -> pd.DataFrame:
        """Aggregates a detailed DataFrame into counts and latency
        percentiles."""
        if df_d.empty:
            return pd.DataFrame(
                columns=group_cols
                + ["count", "Average (ms)", "p50 (ms)", "p90 (ms)", "p99 (ms)"]
            )

        agg_df = (
            df_d.groupby(group_cols)
            .agg(
                count=("duration_ms", "count"),
                Average=("duration_ms", "mean"),
                p50=("duration_ms", lambda x: x.quantile(0.50)),
                p90=("duration_ms", lambda x: x.quantile(0.90)),
                p99=("duration_ms", lambda x: x.quantile(0.99)),
            )
            .reset_index()
        )

        for col in ["Average", "p50", "p90", "p99"]:
            agg_df[col] = agg_df[col].fillna(0).astype(int)

        agg_df.rename(
            columns={
                "Average": "Average (ms)",
                "p50": "p50 (ms)",
                "p90": "p90 (ms)",
                "p99": "p99 (ms)",
            },
            inplace=True,
        )

        agg_df = agg_df.sort_values(by="count", ascending=False).reset_index(
            drop=True
        )
        return agg_df

    @staticmethod
    def _build_llm_summary_df(
        df_d: pd.DataFrame, group_cols: List[str]
    ) -> pd.DataFrame:
        """Aggregates a detailed LLM DataFrame into counts, percentiles, and
        average tokens."""
        if df_d.empty:
            return pd.DataFrame(
                columns=group_cols
                + [
                    "count",
                    "Average Input Tokens",
                    "Average (ms)",
                    "p50 (ms)",
                    "p90 (ms)",
                    "p99 (ms)",
                ]
            )

        agg_df = (
            df_d.groupby(group_cols)
            .agg(
                count=("duration_ms", "count"),
                Average_Input_Tokens=("input_tokens", "mean"),
                Average=("duration_ms", "mean"),
                p50=("duration_ms", lambda x: x.quantile(0.50)),
                p90=("duration_ms", lambda x: x.quantile(0.90)),
                p99=("duration_ms", lambda x: x.quantile(0.99)),
            )
            .reset_index()
        )

        for col in ["Average_Input_Tokens", "Average", "p50", "p90", "p99"]:
            agg_df[col] = agg_df[col].fillna(0).astype(int)

        agg_df.rename(
            columns={
                "Average_Input_Tokens": "Average Input Tokens",
                "Average": "Average (ms)",
                "p50": "p50 (ms)",
                "p90": "p90 (ms)",
                "p99": "p99 (ms)",
            },
            inplace=True,
        )

        agg_df = agg_df.sort_values(by="count", ascending=False).reset_index(
            drop=True
        )
        return agg_df

    @staticmethod
    def extract_trace_metrics(
        traces: Dict[str, Any], context_type: str = "conversation"
    ) -> Dict[str, pd.DataFrame]:
        """
        Extracts execution traces from Conversation history objects.

        Args:
            traces: A dictionary mapping `{conversation_id: Conversation
                object dict}`.
            context_type: Whether the `context_id` should represent the
                native `conversation_id` or an `eval_result_id`. Currently
                defaults to mapping conversation_ids natively, as eval routing
                expects EvalUtils to coordinate the span tree walk directly for
                synchronized sequence generation.

        Returns:
            Dictionary mapped to generic 6 Pandas DataFrames.
        """
        tool_details_rows = []
        callback_details_rows = []
        guardrail_details_rows = []
        llm_details_rows = []

        for cid, conv in traces.items():
            conv_dict = (
                type(conv).to_dict(conv) if not isinstance(conv, dict) else conv
            )
            conv_turns = conv_dict.get("turns", [])
            for turn_idx, t in enumerate(conv_turns):
                root = t.get("root_span", {})
                if root:
                    # In a generic conversation context, we use the
                    # conversation_id and absolute turn index as the link
                    _ = LatencyParser._process_spans(
                        [root],
                        cid,
                        turn_idx + 1,
                        tool_details_rows,
                        callback_details_rows,
                        guardrail_details_rows,
                        llm_details_rows,
                    )

        tool_details = pd.DataFrame(tool_details_rows)
        callback_details = pd.DataFrame(callback_details_rows)
        guardrail_details = pd.DataFrame(guardrail_details_rows)
        llm_details = pd.DataFrame(llm_details_rows)

        tool_summary = LatencyParser.build_summary_df(
            tool_details, ["tool_name"]
        )
        callback_summary = LatencyParser.build_summary_df(
            callback_details, ["agent", "stage", "description"]
        )
        guardrail_summary = LatencyParser.build_summary_df(
            guardrail_details, ["agent", "name"]
        )
        llm_summary = LatencyParser._build_llm_summary_df(
            llm_details, ["agent", "model"]
        )

        return {
            "tool_summary": tool_summary,
            "tool_details": tool_details,
            "callback_summary": callback_summary,
            "callback_details": callback_details,
            "guardrail_summary": guardrail_summary,
            "guardrail_details": guardrail_details,
            "llm_summary": llm_summary,
            "llm_details": llm_details,
        }
