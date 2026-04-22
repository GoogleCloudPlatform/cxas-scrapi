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

"""
Callback Tests — before_model_callback (Root Agent)

Tests the trigger pattern implementation for deterministic escalation.

KEY PATTERNS DEMONSTRATED:
    1. Mock injection: python_code.tools = MagicMock() — injects a mock for the
       'tools' global that GECX provides at runtime. Without this, the callback
       crashes on import because 'tools' doesn't exist in the test environment.
    2. CallbackContext from cxas_scrapi — provides a test-friendly version of
       the callback context with mutable state.
    3. Each test covers a specific path through the callback:
       - No-op path (no trigger set)
       - Escalation trigger path
       - API failure flag path
       - Unknown trigger value path
       - Text always included in response
       - Trigger cleared after handling

RUNNING:
    pytest evals/callback_tests/tests/ -v

    Or via SCRAPI:
    from cxas_scrapi.evals.callback_evals import CallbackEvals
    cb = CallbackEvals()
    results = cb.test_all_callbacks_in_app_dir(app_dir="evals/callback_tests")
"""

import sys
import os
from unittest.mock import MagicMock

# -------------------------------------------------------------------------
# MOCK INJECTION: Must happen BEFORE importing python_code.
# The GECX runtime provides a 'tools' global that doesn't exist in test.
# We inject a MagicMock so the module loads without errors.
# -------------------------------------------------------------------------
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "..", "agents", "root_agent",
    "before_model_callbacks", "before_model",
))

import python_code  # noqa: E402
python_code.tools = MagicMock()

from python_code import before_model_callback  # noqa: E402
from cxas_scrapi.utils.callback_libs import CallbackContext, Content, Part  # noqa: E402


def _session_start_content():
    """Create user_content simulating a session start event."""
    return Content(role="user", parts=[Part(text="<event>session start</event>")])


def _normal_user_content(text="My phone is not working"):
    """Create user_content simulating a normal user message."""
    return Content(role="user", parts=[Part(text=text)])


class TestGreeting:
    """Tests for deterministic greeting on session start."""

    def test_session_start_returns_greeting(self):
        """Session start event returns the fixed greeting."""
        ctx = CallbackContext(state={})
        ctx.user_content = _session_start_content()
        result = before_model_callback(ctx, llm_request=MagicMock())
        assert result is not None
        has_text = any(hasattr(p, "text") and p.text for p in result.content.parts)
        assert has_text

    def test_greeting_text_content(self):
        """Greeting contains the expected opening text."""
        ctx = CallbackContext(state={})
        ctx.user_content = _session_start_content()
        result = before_model_callback(ctx, llm_request=MagicMock())
        text = [p.text for p in result.content.parts if hasattr(p, "text") and p.text][0]
        assert "virtual assistant" in text.lower()

    def test_normal_message_skips_greeting(self):
        """A normal user message does not trigger the greeting."""
        ctx = CallbackContext(state={
            "_action_trigger": "",
            "api_failed": "false",
        })
        ctx.user_content = _normal_user_content()
        result = before_model_callback(ctx, llm_request=MagicMock())
        assert result is None


class TestNoOpPath:
    """Tests for when the callback should do nothing (return None)."""

    def test_no_trigger_returns_none(self):
        """No-op: No trigger set, no API failure, normal user message — LLM handles normally."""
        ctx = CallbackContext(state={
            "_action_trigger": "",
            "api_failed": "false",
        })
        ctx.user_content = _normal_user_content()
        result = before_model_callback(ctx, llm_request=MagicMock())
        assert result is None

    def test_missing_trigger_key_returns_none(self):
        """No-op: _action_trigger key doesn't exist in state."""
        ctx = CallbackContext(state={})
        ctx.user_content = _normal_user_content()
        result = before_model_callback(ctx, llm_request=MagicMock())
        assert result is None

    def test_unknown_trigger_returns_none(self):
        """No-op: Trigger value not in ESCALATION_MAP — unknown trigger."""
        ctx = CallbackContext(state={
            "_action_trigger": "unknown_trigger_value",
        })
        ctx.user_content = _normal_user_content()
        result = before_model_callback(ctx, llm_request=MagicMock())
        assert result is None
        # Trigger should still be cleared even if unknown
        assert ctx.state["_action_trigger"] == ""


class TestEscalationTrigger:
    """Tests for the standard escalation trigger path."""

    def _escalation_ctx(self, **overrides):
        ctx = CallbackContext(state={
            "_action_trigger": "escalate",
            "_escalation_reason": "Customer wants human",
            "_escalation_topic": "billing",
            **overrides,
        })
        ctx.user_content = _normal_user_content()
        return ctx

    def test_escalation_returns_response(self):
        """Trigger: _action_trigger = 'escalate' — returns escalation response."""
        result = before_model_callback(self._escalation_ctx(), llm_request=MagicMock())
        assert result is not None

    def test_escalation_clears_trigger(self):
        """Trigger is cleared after handling to prevent re-firing."""
        ctx = self._escalation_ctx()
        before_model_callback(ctx, llm_request=MagicMock())
        assert ctx.state["_action_trigger"] == ""

    def test_escalation_includes_text(self):
        """Response always includes text so customer hears something."""
        result = before_model_callback(self._escalation_ctx(), llm_request=MagicMock())

        # Check that at least one part has text
        has_text = any(
            p.text_or_transcript() for p in result.content.parts
            if hasattr(p, "text_or_transcript")
        )
        assert has_text, "Escalation response must include text"

    def test_escalation_resolves_state_references(self):
        """Payload args starting with '_' are resolved from state."""
        result = before_model_callback(self._escalation_ctx(
            _escalation_reason="Billing dispute",
            _escalation_topic="billing",
        ), llm_request=MagicMock())

        # Find the payload_update_tool call and verify args
        for part in result.content.parts:
            fc = getattr(part, "function_call", None)
            if fc and fc.name == "payload_update_tool":
                assert fc.args["escalation_reason"] == "Billing dispute"
                assert fc.args["main_topic"] == "billing"
                return
        assert False, "payload_update_tool not found in response"


class TestApiFailurePath:
    """Tests for the API failure escalation path."""

    def _api_failure_ctx(self):
        ctx = CallbackContext(state={"api_failed": "true"})
        ctx.user_content = _normal_user_content()
        return ctx

    def test_api_failure_triggers_escalation(self):
        """api_failed = 'true' triggers escalation even without _action_trigger."""
        result = before_model_callback(self._api_failure_ctx(), llm_request=MagicMock())
        assert result is not None

    def test_api_failure_clears_flag(self):
        """api_failed flag is cleared to prevent re-firing on next model call."""
        ctx = self._api_failure_ctx()
        before_model_callback(ctx, llm_request=MagicMock())
        assert ctx.state["api_failed"] == "false"

    def test_api_failure_includes_apology_text(self):
        """API failure response includes empathetic text about the issue."""
        result = before_model_callback(self._api_failure_ctx(), llm_request=MagicMock())

        text_parts = [
            p.text_or_transcript() for p in result.content.parts
            if hasattr(p, "text_or_transcript") and p.text_or_transcript()
        ]
        assert len(text_parts) > 0, "API failure response must include text"
        assert "technical issue" in text_parts[0].lower() or "transfer" in text_parts[0].lower()


class TestSilenceHandling:
    """Tests for the silence/no-input detection and response path.

    WHY TEST THIS:
        Silence handling is deterministic callback logic that the LLM would
        otherwise handle inconsistently (hallucinating responses, asking random
        questions). These tests verify the counter-based escalation pattern:
        1st silence -> repeat, 2nd -> repeat with different prefix, 3rd -> end session.
    """

    def _silence_content(self):
        """Create user_content simulating a silence signal from the platform."""
        return Content(
            role="user",
            parts=[Part(text="<context>no user activity detected for 10 seconds.</context>")],
        )

    def _silence_ctx(self, counter="0"):
        """Create a context with silence signal and configurable counter."""
        llm_request = MagicMock()
        # contents needs at least 2 entries for is_user_inactive to return True
        llm_request.contents = [
            Content(role="model", parts=[Part(text="How can I help you?")]),
            self._silence_content(),
        ]
        ctx = CallbackContext(state={
            "no_input_counter": counter,
            "_action_trigger": "",
            "api_failed": "false",
        })
        ctx.user_content = self._silence_content()
        return ctx, llm_request

    def test_first_silence_returns_response(self):
        """First silence returns a repeat message, not None."""
        ctx, llm_request = self._silence_ctx("0")
        result = before_model_callback(ctx, llm_request)
        assert result is not None

    def test_first_silence_increments_counter(self):
        """Counter goes from 0 to 1 after first silence."""
        ctx, llm_request = self._silence_ctx("0")
        before_model_callback(ctx, llm_request)
        assert ctx.state["no_input_counter"] == "1"

    def test_first_silence_includes_sorry_prefix(self):
        """First silence response starts with 'Sorry, I didn't hear anything'."""
        ctx, llm_request = self._silence_ctx("0")
        result = before_model_callback(ctx, llm_request)
        text = [p.text for p in result.content.parts if hasattr(p, "text") and p.text][0]
        assert "sorry" in text.lower()

    def test_third_silence_ends_session(self):
        """Third consecutive silence ends the session gracefully."""
        ctx, llm_request = self._silence_ctx("2")
        result = before_model_callback(ctx, llm_request)
        assert result is not None
        # Should include end_session function call
        has_end = any(
            hasattr(p, "function_call") and p.function_call
            and p.function_call.name == "end_session"
            for p in result.content.parts
        )
        assert has_end, "Third silence must trigger end_session"

    def test_normal_message_resets_counter(self):
        """A normal user message (not silence) resets the counter to 0."""
        ctx = CallbackContext(state={
            "no_input_counter": "2",
            "_action_trigger": "",
            "api_failed": "false",
        })
        ctx.user_content = _normal_user_content()
        llm_request = MagicMock()
        llm_request.contents = [
            Content(role="user", parts=[Part(text="My phone is broken")]),
        ]
        before_model_callback(ctx, llm_request)
        assert ctx.state["no_input_counter"] == "0"
