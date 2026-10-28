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

"""Unit tests for callback, tool, LLM, and session telemetry traces."""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
import pytest

from cxas_scrapi.evals.callback_chain import (
    BenchmarkComparisonReport,
    CallbackChainRunner,
    SessionMetricsSummary,
    SessionSimulator,
    compare_execution_metrics,
)
from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    LlmResponse,
    Part,
    UsageMetadata,
)


def test_duration_ms_across_all_trace_levels() -> None:
    """Verifies duration_ms is recorded (> 0) on step, stage, tool, LLM, and turn traces."""
    ba_code = "def before_agent_callback(ctx):\n    ctx.state['ba_ok'] = True\n"
    bm_code_1 = (
        "def before_model_callback(ctx, req):\n    ctx.state['bm_1'] = True\n"
    )
    bm_code_2 = (
        "def before_model_callback(ctx, req):\n    ctx.state['bm_2'] = True\n"
    )
    am_code = (
        "def after_model_callback(ctx, resp):\n    ctx.state['am_ok'] = True\n"
    )
    bt_code = (
        "def before_tool_callback(tool, args, ctx):\n"
        "    ctx.state['bt_ok'] = True\n"
    )
    at_code = (
        "def after_tool_callback(tool, args, ctx, resp):\n"
        "    ctx.state['at_ok'] = True\n"
    )
    aa_code = "def after_agent_callback(ctx):\n    ctx.state['aa_ok'] = True\n"

    for mode in ("per_callback", "combined"):
        runner = CallbackChainRunner.from_callbacks(
            {
                "before_agent": [("ba_01", ba_code)],
                "before_model": [("bm_01", bm_code_1), ("bm_02", bm_code_2)],
                "after_model": [("am_01", am_code)],
                "before_tool": [("bt_01", bt_code)],
                "after_tool": [("at_01", at_code)],
                "after_agent": [("aa_01", aa_code)],
            },
            mode=mode,
            new_session=False,
        )

        call_count = [0]

        def slow_llm(req: Any) -> LlmResponse:
            call_count[0] += 1  # noqa: B023
            time.sleep(0.002)
            if call_count[0] == 1:  # noqa: B023
                return LlmResponse.from_parts(
                    [
                        Part.from_function_call(
                            "lookup_order", {"order_id": "123"}
                        )
                    ]
                )
            return LlmResponse.from_parts(
                [Part.from_text("Order 123 is shipped.")]
            )

        def slow_tool(args: dict[str, Any]) -> dict[str, Any]:
            time.sleep(0.002)
            return {"status": "shipped", "order_id": args["order_id"]}

        resp, tt = runner.run_turn(
            CallbackContext(state={}),
            mock_llm=slow_llm,
            execute_tools=True,
            tool_impls={"lookup_order": slow_tool},
            include_agent_callbacks=True,
            max_llm_calls=2,
        )

        assert resp is not None
        assert tt.duration_ms > 0.0
        assert tt.before_agent_trace is not None
        assert tt.before_agent_trace.duration_ms > 0.0
        assert tt.before_agent_trace.steps[0].duration_ms > 0.0

        assert len(tt.llm_steps) == 2
        for ls in tt.llm_steps:
            assert ls.before_model_trace.duration_ms > 0.0
            for step in ls.before_model_trace.steps:
                assert step.duration_ms > 0.0
            assert ls.llm_duration_ms > 0.0
            assert ls.after_model_trace is not None
            assert ls.after_model_trace.duration_ms > 0.0
            assert ls.total_duration_ms == pytest.approx(
                ls.before_model_trace.duration_ms
                + ls.llm_duration_ms
                + ls.after_model_trace.duration_ms
            )

        assert len(tt.tool_steps) == 1
        ts = tt.tool_steps[0]
        assert ts.before_tool_trace.duration_ms > 0.0
        assert ts.tool_duration_ms > 0.0
        assert ts.after_tool_trace is not None
        assert ts.after_tool_trace.duration_ms > 0.0
        assert ts.total_duration_ms == pytest.approx(
            ts.before_tool_trace.duration_ms
            + ts.tool_duration_ms
            + ts.after_tool_trace.duration_ms
        )

        assert tt.after_agent_trace is not None
        assert tt.after_agent_trace.duration_ms > 0.0
        assert tt.llm_duration_ms == pytest.approx(
            sum(s.llm_duration_ms for s in tt.llm_steps)
        )
        assert tt.tool_duration_ms == pytest.approx(ts.tool_duration_ms)
        assert tt.callback_duration_ms > 0.0


def test_token_usage_extraction_and_preservation_across_after_model_override() -> (
    None
):
    """Verifies token usage on LlmStepTrace and TurnTrace even when after_model overrides."""
    am_override = (
        "def after_model_callback(ctx, resp):\n"
        "    if resp.content and resp.content.parts and resp.content.parts[0].text == 'draft':\n"
        "        return LlmResponse.from_parts([Part.from_text('rewritten by after_model')])\n"
        "    return None\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"after_model": [("am_override", am_override)]},
        new_session=False,
    )

    step_responses = [
        LlmResponse.from_parts(
            [Part.from_function_call("fetch_data", {"q": "test"})],
            usage_metadata=UsageMetadata(
                prompt_token_count=120,
                candidates_token_count=30,
                total_token_count=150,
            ),
            model_version="gemini-2.5-flash",
        ),
        LlmResponse.from_parts(
            [Part.from_text("draft")],
            usage_metadata={
                "promptTokenCount": 200,
                "candidatesTokenCount": 45,
            },
            model_version="gemini-2.5-flash",
        ),
    ]

    final_resp, tt = runner.run_turn(
        CallbackContext(state={}),
        mock_llm=step_responses,
        execute_tools=True,
        tool_impls={"fetch_data": {"items": [1, 2, 3]}},
        max_llm_calls=2,
    )

    assert final_resp is not None
    assert final_resp.content.parts[0].text == "rewritten by after_model"
    assert len(tt.llm_steps) == 2

    # Step 0
    assert tt.llm_steps[0].prompt_tokens == 120
    assert tt.llm_steps[0].candidates_tokens == 30
    assert tt.llm_steps[0].total_tokens == 150
    assert tt.llm_steps[0].model_name == "gemini-2.5-flash"

    # Step 1 (overridden by after_model, but raw token usage is preserved!)
    assert tt.llm_steps[1].prompt_tokens == 200
    assert tt.llm_steps[1].candidates_tokens == 45
    assert tt.llm_steps[1].total_tokens == 245
    assert tt.llm_steps[1].model_name == "gemini-2.5-flash"

    # Turn-level aggregation
    assert tt.prompt_tokens == 320
    assert tt.candidates_tokens == 75
    assert tt.total_tokens == 395


def test_thoughts_and_cached_tokens_aggregation_in_summary_and_report() -> None:
    """Verifies thoughts_tokens and cached_tokens aggregate across turns and reports."""
    runner = CallbackChainRunner.from_callbacks(
        {},
        agent_config={"displayName": "reasoning_agent"},
        new_session=False,
    )
    sim = SessionSimulator(runner)
    sim.turn(
        "Turn 1",
        llm=LlmResponse.from_parts(
            [Part.from_text("A1")],
            usage_metadata=UsageMetadata(
                prompt_token_count=100,
                candidates_token_count=30,
                thoughts_token_count=20,
                cached_content_token_count=10,
            ),
        ),
    )
    sim.turn(
        "Turn 2",
        llm=LlmResponse.from_parts(
            [Part.from_text("A2")],
            usage_metadata={
                "promptTokenCount": 120,
                "candidatesTokenCount": 40,
                "thoughtsTokenCount": 25,
                "cachedContentTokenCount": 15,
            },
        ),
    )

    summary = sim.get_metrics_summary(label="reasoning_run")
    assert summary.prompt_tokens == 220
    assert summary.candidates_tokens == 70
    assert summary.thoughts_tokens == 45
    assert summary.cached_tokens == 25
    assert summary.total_tokens == 335

    report = compare_execution_metrics(
        {"reasoning_run": sim.turns}, baseline="reasoning_run"
    )
    as_dict = report.to_dict()
    assert as_dict["summaries"]["reasoning_run"]["thoughts_tokens"] == 45
    assert as_dict["summaries"]["reasoning_run"]["cached_tokens"] == 25
    df = report.to_dataframe()
    assert int(df.loc[0, "thoughts_tokens"]) == 45
    assert int(df.loc[0, "cached_tokens"]) == 25


def test_short_circuit_before_model_records_zero_llm_duration_and_tokens() -> (
    None
):
    """Verifies before_model short-circuit records 0 llm_duration_ms and 0 tokens."""
    bm_short = (
        "def before_model_callback(ctx, req):\n"
        "    return LlmResponse.from_parts([Part.from_text('short-circuited')])\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_model": [("bm_short", bm_short)]},
        new_session=False,
    )
    resp, tt = runner.run_turn(CallbackContext(state={}))
    assert resp is not None
    assert resp.content.parts[0].text == "short-circuited"
    assert len(tt.llm_steps) == 1
    assert tt.llm_steps[0].short_circuited is True
    assert tt.llm_steps[0].llm_duration_ms == 0.0
    assert tt.llm_steps[0].total_tokens == 0
    assert tt.total_tokens == 0
    assert tt.callback_duration_ms > 0.0


def test_before_tool_short_circuit_records_zero_tool_duration() -> None:
    """Verifies before_tool short-circuit skips tool callable and records 0 duration."""
    bt_short = (
        "def before_tool_callback(tool, args, ctx):\n"
        "    return {'cached': True}\n"
    )
    runner = CallbackChainRunner.from_callbacks(
        {"before_tool": [("bt_short", bt_short)]},
        new_session=False,
    )
    tool_ran = [False]

    def never_called(args: dict[str, Any]) -> dict[str, Any]:
        tool_ran[0] = True
        return {"cached": False}

    resp, tt = runner.run_turn(
        CallbackContext(state={}),
        llm=[
            LlmResponse.from_parts(
                [Part.from_function_call("my_tool", {"x": 1})]
            ),
            LlmResponse.from_parts([Part.from_text("Done")]),
        ],
        execute_tools=True,
        tool_impls={"my_tool": never_called},
    )
    assert resp is not None
    assert tool_ran[0] is False
    assert len(tt.tool_steps) == 1
    assert tt.tool_steps[0].short_circuited is True
    assert tt.tool_steps[0].tool_duration_ms == 0.0
    assert tt.tool_steps[0].final_tool_response == {"cached": True}


def test_session_metrics_summary_and_benchmark_comparison_report() -> None:
    """Verifies SessionMetricsSummary.from_turns and compare_execution_metrics."""
    bm_code = (
        "def before_model_callback(ctx, req):\n"
        "    ctx.state['counter'] = ctx.state.get('counter', 0) + 1\n"
    )
    runner_pc = CallbackChainRunner.from_callbacks(
        {"before_model": [("bm_1", bm_code), ("bm_2", bm_code)]},
        mode="per_callback",
        agent_config={"displayName": "support_agent"},
        new_session=False,
    )
    runner_cb = CallbackChainRunner.from_callbacks(
        {"before_model": [("bm_1", bm_code), ("bm_2", bm_code)]},
        mode="combined",
        agent_config={"displayName": "support_agent"},
        new_session=False,
    )

    sim_pc = SessionSimulator(runner_pc)
    sim_cb = SessionSimulator(runner_cb)

    for i in range(2):
        resp_pc = LlmResponse.from_parts(
            [Part.from_text(f"reply {i}")],
            usage_metadata=UsageMetadata(
                prompt_token_count=100 + i * 10,
                candidates_token_count=20 + i * 5,
                total_token_count=120 + i * 15,
            ),
        )
        resp_cb = LlmResponse.from_parts(
            [Part.from_text(f"reply {i}")],
            usage_metadata=UsageMetadata(
                prompt_token_count=150 + i * 10,
                candidates_token_count=30 + i * 5,
                total_token_count=180 + i * 15,
            ),
        )
        sim_pc.turn(f"user msg {i}", mock_llm=resp_pc)
        sim_cb.turn(f"user msg {i}", mock_llm=resp_cb)

    summary_pc = sim_pc.get_metrics_summary(label="per_callback")
    assert isinstance(summary_pc, SessionMetricsSummary)
    assert summary_pc.label == "per_callback"
    assert summary_pc.turn_count == 2
    assert summary_pc.llm_call_count == 2
    assert summary_pc.prompt_tokens == 210
    assert summary_pc.candidates_tokens == 45
    assert summary_pc.total_tokens == 255
    assert summary_pc.total_duration_ms > 0.0
    assert summary_pc.callback_duration_ms > 0.0

    report = compare_execution_metrics(
        {
            "per_callback": sim_pc.turns,
            "combined": sim_cb.turns,
        },
        baseline="per_callback",
    )
    assert isinstance(report, BenchmarkComparisonReport)
    assert report.baseline_label == "per_callback"
    assert set(report.summaries.keys()) == {"per_callback", "combined"}

    as_dict = report.to_dict()
    assert as_dict["baseline_label"] == "per_callback"
    assert as_dict["summaries"]["combined"]["total_tokens"] == 375
    assert (
        as_dict["deltas_vs_baseline"]["combined"]["total_tokens_delta"] == 120
    )

    df = report.to_dataframe()
    assert isinstance(df, pd.DataFrame)
    assert list(df["label"]) == ["per_callback", "combined"]
    assert "total_tokens" in df.columns
    assert "duration_ratio_vs_baseline" in df.columns

    md = report.to_markdown()
    assert "| Label | Turns | LLM Calls |" in md
    assert "per_callback" in md
    assert "combined" in md


def test_empty_turns_metrics_summary_and_comparison_report() -> None:
    """Verifies SessionMetricsSummary and compare_execution_metrics handle empty turn lists."""
    empty_summary = SessionMetricsSummary.from_turns([], label="empty_run")
    assert empty_summary.turn_count == 0
    assert empty_summary.total_tokens == 0
    assert empty_summary.avg_turn_duration_ms == 0.0
    assert empty_summary.avg_llm_call_duration_ms == 0.0

    report = compare_execution_metrics(
        {"empty_run": [], "other_empty": []}, baseline="empty_run"
    )
    assert report.speedup_vs_baseline(0) is None
    assert report.token_savings_ratio(0) is None
    md_empty = report.to_markdown_table()
    assert "inf" not in md_empty
    assert "| other_empty | 0 | 0 | 0 | - | - | 0.0 | - | 0 | - |" in md_empty

    as_dict = report.to_dict()
    assert (
        as_dict["deltas_vs_baseline"]["other_empty"][
            "duration_ratio_vs_baseline"
        ]
        is None
    )
    assert (
        as_dict["deltas_vs_baseline"]["other_empty"]["token_ratio_vs_baseline"]
        is None
    )
    df_empty = report.to_dataframe()
    assert pd.isna(df_empty.loc[0, "duration_ratio_vs_baseline"])
    assert pd.isna(df_empty.loc[1, "duration_ratio_vs_baseline"])
    assert pd.isna(df_empty.loc[1, "token_ratio_vs_baseline"])

    # When candidates=[], speedup_vs_baseline(0) and token_savings_ratio(0) raise IndexError
    single_report = compare_execution_metrics(
        {"only_baseline": []}, baseline="only_baseline"
    )
    with pytest.raises(IndexError):
        single_report.speedup_vs_baseline(0)
    with pytest.raises(IndexError):
        single_report.token_savings_ratio(0)

    # 0-token baseline and 0-duration candidate return None and render '-'
    base_zero_tok = SessionMetricsSummary(
        label="mock_base", total_duration_ms=50.0, total_tokens=0
    )
    cand_zero_dur = SessionMetricsSummary(
        label="zero_dur_cand", total_duration_ms=0.0, total_tokens=100
    )
    edge_report = compare_execution_metrics(base_zero_tok, cand_zero_dur)
    assert edge_report.speedup_vs_baseline(0) is None
    assert edge_report.token_savings_ratio(0) is None
    edge_md = edge_report.to_markdown_table()
    assert "inf" not in edge_md
    assert (
        "| zero_dur_cand | 0 | 0 | 0 | - | - | 0.0 | - | 100 | - |" in edge_md
    )


def test_multi_turn_models_used_and_routed_agents_in_summary() -> None:
    """Verifies models_used and routed_agents deduplication in SessionMetricsSummary."""
    runner = CallbackChainRunner.from_callbacks(
        {},
        agent_config={"displayName": "agent_alpha"},
        new_session=False,
    )
    sim = SessionSimulator(runner)
    sim.turn(
        "Turn 1",
        llm=LlmResponse.from_parts(
            [Part.from_text("R1")], model_version="gemini-2.5-flash"
        ),
    )
    sim.turn(
        "Turn 2",
        llm=LlmResponse.from_parts(
            [Part.from_text("R2")], model_version="gemini-2.5-pro"
        ),
    )
    summary = sim.get_metrics_summary(label="multi_model")
    assert summary.models_used == ["gemini-2.5-flash", "gemini-2.5-pro"]
    assert summary.routed_agents == ["agent_alpha"]
