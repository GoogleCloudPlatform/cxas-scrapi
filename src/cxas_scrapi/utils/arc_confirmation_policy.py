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

"""Decides how a programmatic Arc client answers confirmation cards.

The CXAS Console renders every `AssistantConfirmationRequest` from the CXAS
AI assistant as a card. A human can click Confirm/Decline, pick options on a
question (`ask_user`) card, skip the questions, or ignore the card and type in
the composer, which the server treats as a reply to the paused tool call. A
`ConfirmationPolicy` reproduces those choices for scripts, the `cxas arc` CLI
and test harnesses, so that programmatic traffic exercises the same server
paths as the Console.

Answer text for question cards uses the Console's wire format for submitted
option choices.
"""

from __future__ import annotations

import abc
import dataclasses
import enum
import fnmatch
import logging
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Mirrors the Console's option card; keep in sync so programmatic answers are
# byte-identical to what the Console sends.
UNANSWERED_MARKER = "(skipped)"
SKIP_ROUND_ANSWER = "Skip these questions for now and continue."
SKIP_ALL_ANSWER = (
    "Skip all remaining questions and draft the plan now using your "
    "best-judgment defaults."
)

# What users commonly type on approval cards instead of clicking.
DEFAULT_TYPED_CONFIRM_TEXT = "Yes"
DEFAULT_TYPED_DENY_TEXT = "No"
DEFAULT_TYPED_REPLY_PROBABILITY = 0.2


class CardLike(Protocol):
    """Structural type of a pending confirmation card.

    Satisfied by the Arc client's `ConfirmationRequestRecord` and by any
    object exposing the same read-only attributes.
    """

    @property
    def confirmation_id(self) -> str: ...

    @property
    def context(self) -> str: ...

    @property
    def tool(self) -> str: ...

    @property
    def questions(self) -> Sequence[Any]: ...


class ConfirmationActionType(str, enum.Enum):
    """A UI gesture a Console user can make on a pending confirmation card."""

    # Click the positive button
    # (`AssistantConfirmationResponse.confirmed=true`).
    CLICK_CONFIRM = "CLICK_CONFIRM"
    # Click the negative button (`confirmed=false`).
    CLICK_DENY = "CLICK_DENY"
    # Submit a question card (`AssistantConfirmationResponse.answer_text`).
    CLICK_ANSWER = "CLICK_ANSWER"
    # Ignore the card and type in the composer: a plain `message` with no
    # `confirmation_response`, which the server routes to the paused call.
    TYPE_TEXT = "TYPE_TEXT"


@dataclasses.dataclass(frozen=True)
class ConfirmationAction:
    """One answer to one pending card."""

    action: ConfirmationActionType
    confirmation_id: str
    text: str = ""
    tool: str = ""
    reason: str = ""

    @property
    def is_typed(self) -> bool:
        """Whether the answer is typed in the composer rather than clicked."""
        return self.action == ConfirmationActionType.TYPE_TEXT

    @property
    def confirmed(self) -> bool | None:
        """The `confirmed` value to send, or None for answers and typed text."""
        if self.action == ConfirmationActionType.CLICK_CONFIRM:
            return True
        if self.action == ConfirmationActionType.CLICK_DENY:
            return False
        return None


QuestionAnswerer = Callable[[CardLike], str | None]


def is_question_card(card: CardLike) -> bool:
    """Returns whether the card asks questions (`ask_user`) vs. a gate."""
    return bool(card.questions) or fnmatch.fnmatchcase(
        (card.tool or "").lower(), "*ask_user*"
    )


def format_question_answer(
    questions: Sequence[Any], answers: Sequence[str | None]
) -> str:
    """Formats per-question answers exactly like the Console.

    Used for question cards (`answer_text`) and for the option panel, which
    sends the same text as a plain message.

    Args:
        questions: The card's or panel's `OptionQuestionRecord`s.
        answers: One entry per question; None or empty marks it skipped.

    Returns:
        The text the Console would send.
    """
    parts = []
    for idx, question in enumerate(questions):
        answer = answers[idx] if idx < len(answers) else None
        q_text = getattr(question, "question", "") or ""
        parts.append(
            f"> {q_text}\n{answer.strip() if answer else UNANSWERED_MARKER}"
        )
    return "\n\n".join(parts)


def first_option_answer(card: CardLike) -> str:
    """Picks the first option of every question, like a click-through user."""
    if not card.questions:
        return SKIP_ROUND_ANSWER
    answers: list[str | None] = []
    for question in card.questions:
        options = list(getattr(question, "options", None) or [])
        if options:
            opt = options[0]
            answers.append(
                getattr(opt, "submit_text", "") or getattr(opt, "title", "")
            )
        else:
            answers.append(None)
    return format_question_answer(card.questions, answers)


class ConfirmationPolicy(abc.ABC):
    """Maps pending cards to the UI actions a user would take."""

    name: str = "abstract"

    @property
    def answers_cards(self) -> bool:
        """Whether the client should loop and answer cards with this policy."""
        return True

    @abc.abstractmethod
    def decide(self, cards: Sequence[CardLike]) -> list[ConfirmationAction]:
        """Returns at most one action per card, in the order to send them."""

    def describe(self) -> str:
        """Returns a short description for logs and recorded actions."""
        return self.name


class ManualPolicy(ConfirmationPolicy):
    """Surfaces cards without answering them (the caller decides)."""

    name = "manual"

    @property
    def answers_cards(self) -> bool:
        """See base class."""
        return False

    def decide(self, cards: Sequence[CardLike]) -> list[ConfirmationAction]:
        """See base class."""
        del cards
        return []


class ClickPolicy(ConfirmationPolicy):
    """Clicks like an attentive user: confirm gates, answer question cards.

    Question cards are answered by, in order: an explicit `answers` entry
    keyed by confirmation id or tool, the `question_answerer` callback (e.g. a
    test harness's deterministic responder), then the first option of every
    question.
    """

    name = "click"

    def __init__(
        self,
        answers: Mapping[str, str] | None = None,
        question_answerer: QuestionAnswerer | None = None,
        deny_tools: Sequence[str] = (),
    ):
        """Initializes ClickPolicy with explicit answers and deny globs."""
        self._answers = dict(answers or {})
        self._question_answerer = question_answerer
        self._deny_tools = tuple(deny_tools)

    def _answer_text(self, card: CardLike) -> tuple[str, str]:
        """Returns (answer_text, reason) for a question card."""
        for key in (card.confirmation_id, card.tool):
            if key and key in self._answers:
                return self._answers[key], f"answers[{key!r}]"
        if self._question_answerer is not None:
            try:
                answer = self._question_answerer(card)
            except Exception:
                logger.exception(
                    "question_answerer failed; using first options."
                )
                answer = None
            if answer:
                return answer, "question_answerer"
        return first_option_answer(card), "first_option"

    def _click(self, card: CardLike) -> ConfirmationAction:
        """Returns the click a user would make on the card."""
        if is_question_card(card):
            text, reason = self._answer_text(card)
            return ConfirmationAction(
                action=ConfirmationActionType.CLICK_ANSWER,
                confirmation_id=card.confirmation_id,
                text=text,
                tool=card.tool,
                reason=reason,
            )
        if any(
            fnmatch.fnmatchcase(card.tool or "", pat)
            for pat in self._deny_tools
        ):
            return ConfirmationAction(
                action=ConfirmationActionType.CLICK_DENY,
                confirmation_id=card.confirmation_id,
                tool=card.tool,
                reason="deny_tools",
            )
        return ConfirmationAction(
            action=ConfirmationActionType.CLICK_CONFIRM,
            confirmation_id=card.confirmation_id,
            tool=card.tool,
            reason="gate",
        )

    def decide(self, cards: Sequence[CardLike]) -> list[ConfirmationAction]:
        """See base class."""
        return [self._click(c) for c in cards if c.confirmation_id]


def _typed_variant(
    action: ConfirmationAction,
    typed_confirm_text: str,
    typed_deny_text: str,
    reason: str,
) -> ConfirmationAction:
    """Turns a click into the equivalent typed composer reply."""
    if action.action == ConfirmationActionType.CLICK_DENY:
        text = typed_deny_text
    elif action.action == ConfirmationActionType.CLICK_ANSWER:
        text = action.text
    else:
        text = typed_confirm_text
    return dataclasses.replace(
        action,
        action=ConfirmationActionType.TYPE_TEXT,
        text=text,
        reason=reason,
    )


class MixedPolicy(ClickPolicy):
    """Like `click`, but types the answer instead with probability p.

    In production most users type a reply instead of clicking the card, which
    exercises a different server path than a click. The draw is seeded and
    logged so a failing run can be replayed.
    """

    name = "mixed"

    def __init__(
        self,
        typed_reply_probability: float = DEFAULT_TYPED_REPLY_PROBABILITY,
        seed: int | None = None,
        typed_confirm_text: str = DEFAULT_TYPED_CONFIRM_TEXT,
        typed_deny_text: str = DEFAULT_TYPED_DENY_TEXT,
        **click_kwargs: Any,
    ):
        """Initializes MixedPolicy with a seeded typed-reply probability."""
        super().__init__(**click_kwargs)
        if not 0.0 <= typed_reply_probability <= 1.0:
            raise ValueError(
                "typed_reply_probability must be in [0, 1], got"
                f" {typed_reply_probability}"
            )
        self.typed_reply_probability = typed_reply_probability
        self.seed = seed if seed is not None else time.time_ns() % (2**31)
        self._rng = random.Random(self.seed)
        self._typed_confirm_text = typed_confirm_text
        self._typed_deny_text = typed_deny_text
        logger.info(
            "MixedPolicy typed_reply_probability=%s seed=%s",
            self.typed_reply_probability,
            self.seed,
        )

    def decide(self, cards: Sequence[CardLike]) -> list[ConfirmationAction]:
        """See base class."""
        actions = []
        for click in super().decide(cards):
            action = click
            draw = self._rng.random()
            if draw < self.typed_reply_probability:
                action = _typed_variant(
                    click,
                    self._typed_confirm_text,
                    self._typed_deny_text,
                    reason=f"mixed draw={draw:.4f} seed={self.seed}",
                )
            actions.append(action)
        return actions

    def describe(self) -> str:
        """Returns the probability and seed, so a run can be replayed."""
        return f"mixed(p={self.typed_reply_probability}, seed={self.seed})"


_SCRIPT_ACTIONS = {
    "confirm": ConfirmationActionType.CLICK_CONFIRM,
    "deny": ConfirmationActionType.CLICK_DENY,
    "answer": ConfirmationActionType.CLICK_ANSWER,
    "type": ConfirmationActionType.TYPE_TEXT,
}


@dataclasses.dataclass
class ScriptRule:
    """One rule of a scripted policy; the first matching rule wins.

    Attributes:
        action: confirm | deny | answer | type | manual | skip_round |
            skip_all.
        tool: fnmatch pattern on the card's tool (empty matches any tool).
        context_regex: Optional regex searched in the card context (ignore
            case).
        kind: any | gate | question.
        occurrence: Match only the Nth card (1-based) this rule's filters
            match.
        text: Answer or typed text; defaults depend on the action.
        max_uses: Stop matching after this many uses (0 = unlimited).
    """

    action: str
    tool: str = ""
    context_regex: str = ""
    kind: str = "any"
    occurrence: int = 0
    text: str = ""
    max_uses: int = 0
    _seen: int = dataclasses.field(default=0, repr=False)
    _used: int = dataclasses.field(default=0, repr=False)

    def __post_init__(self) -> None:
        valid = set(_SCRIPT_ACTIONS) | {"manual", "skip_round", "skip_all"}
        if self.action not in valid:
            raise ValueError(
                f"Unknown scripted action {self.action!r}; expected one of"
                f" {sorted(valid)}"
            )
        if self.kind not in ("any", "gate", "question"):
            raise ValueError(f"Unknown card kind {self.kind!r}")

    def filters_match(self, card: CardLike) -> bool:
        """Whether the card passes the tool, context and kind filters."""
        if self.tool and not fnmatch.fnmatchcase(card.tool or "", self.tool):
            return False
        if self.context_regex and not re.search(
            self.context_regex, card.context or "", re.IGNORECASE
        ):
            return False
        if self.kind == "gate" and is_question_card(card):
            return False
        return not (self.kind == "question" and not is_question_card(card))


class ScriptedPolicy(ConfirmationPolicy):
    """Answers cards from an ordered rule list, falling back to `click`.

    Script schema (dict or YAML):

        default: click            # click | manual
        typed_reply_probability: 0.0
        seed: 0
        typed_confirm_text: "Yes"
        rules:
          - {tool: "deploy*", action: type, text: "yes"}
          - {kind: question, action: skip_all}
    """

    name = "scripted"

    def __init__(
        self,
        rules: Sequence[ScriptRule] = (),
        default: str = "click",
        typed_reply_probability: float = 0.0,
        seed: int | None = None,
        typed_confirm_text: str = DEFAULT_TYPED_CONFIRM_TEXT,
        typed_deny_text: str = DEFAULT_TYPED_DENY_TEXT,
        question_answerer: QuestionAnswerer | None = None,
        answers: Mapping[str, str] | None = None,
        deny_tools: Sequence[str] = (),
    ):
        """Initializes ScriptedPolicy with ordered rules and a fallback."""
        if default not in ("click", "manual"):
            raise ValueError(f"Unknown scripted default {default!r}")
        self._rules = [dataclasses.replace(r, _seen=0, _used=0) for r in rules]
        self._default = default
        self._typed_confirm_text = typed_confirm_text
        self._fallback = MixedPolicy(
            typed_reply_probability=typed_reply_probability,
            seed=seed if seed is not None else 0,
            typed_confirm_text=typed_confirm_text,
            typed_deny_text=typed_deny_text,
            question_answerer=question_answerer,
            answers=answers,
            deny_tools=deny_tools,
        )
        self._click = ClickPolicy(
            question_answerer=question_answerer,
            answers=answers,
            deny_tools=deny_tools,
        )

    def reset(self) -> None:
        """Resets per-rule occurrence and usage counters for a new session."""
        for rule in self._rules:
            rule._seen = 0
            rule._used = 0

    @classmethod
    def from_dict(
        cls,
        script: Mapping[str, Any],
        question_answerer: QuestionAnswerer | None = None,
    ) -> ScriptedPolicy:
        """Builds a policy from a parsed script (see the class docstring)."""
        known = {
            "rules",
            "default",
            "typed_reply_probability",
            "seed",
            "typed_confirm_text",
            "typed_deny_text",
            "answers",
            "deny_tools",
        }
        unknown = set(script) - known
        if unknown:
            raise ValueError(
                f"Unknown confirmation script keys: {sorted(unknown)}"
            )
        rules = [
            r if isinstance(r, ScriptRule) else ScriptRule(**dict(r))
            for r in script.get("rules") or []
        ]
        return cls(
            rules=rules,
            default=str(script.get("default", "click")),
            typed_reply_probability=float(
                script.get("typed_reply_probability", 0.0)
            ),
            seed=script.get("seed"),
            typed_confirm_text=str(
                script.get("typed_confirm_text", DEFAULT_TYPED_CONFIRM_TEXT)
            ),
            typed_deny_text=str(
                script.get("typed_deny_text", DEFAULT_TYPED_DENY_TEXT)
            ),
            question_answerer=question_answerer,
            answers=script.get("answers"),
            deny_tools=tuple(script.get("deny_tools") or ()),
        )

    @classmethod
    def from_yaml(
        cls,
        yaml_text: str,
        question_answerer: QuestionAnswerer | None = None,
    ) -> ScriptedPolicy:
        """Builds a policy from YAML (optionally under `confirmation_script:`).

        The `yaml` package is imported lazily so the module stays importable
        without it.
        """
        import yaml  # noqa: PLC0415 - optional dependency, loaded lazily.

        data = yaml.safe_load(yaml_text) or {}
        if "confirmation_script" in data:
            data = data["confirmation_script"] or {}
        return cls.from_dict(data, question_answerer=question_answerer)

    def _from_rule(
        self, rule: ScriptRule, card: CardLike
    ) -> ConfirmationAction | None:
        """Turns a matched rule into an action (None for `manual`)."""
        reason = f"rule action={rule.action} tool={rule.tool or '*'}"
        if rule.action == "manual":
            return None
        if rule.action in ("skip_round", "skip_all"):
            return ConfirmationAction(
                action=ConfirmationActionType.CLICK_ANSWER,
                confirmation_id=card.confirmation_id,
                text=SKIP_ROUND_ANSWER
                if rule.action == "skip_round"
                else SKIP_ALL_ANSWER,
                tool=card.tool,
                reason=reason,
            )
        action_type = _SCRIPT_ACTIONS[rule.action]
        if action_type == ConfirmationActionType.CLICK_ANSWER:
            text = rule.text or self._click.decide([card])[0].text
        elif action_type == ConfirmationActionType.TYPE_TEXT:
            if rule.text:
                text = rule.text
            elif is_question_card(card):
                text = self._click.decide([card])[0].text
            else:
                text = self._typed_confirm_text
        else:
            text = ""
        return ConfirmationAction(
            action=action_type,
            confirmation_id=card.confirmation_id,
            text=text,
            tool=card.tool,
            reason=reason,
        )

    def decide(self, cards: Sequence[CardLike]) -> list[ConfirmationAction]:
        """See base class."""
        actions = []
        for card in cards:
            if not card.confirmation_id:
                continue
            matched = False
            for rule in self._rules:
                if rule.max_uses and rule._used >= rule.max_uses:
                    continue
                if not rule.filters_match(card):
                    continue
                rule._seen += 1
                if rule.occurrence and rule._seen != rule.occurrence:
                    continue
                rule._used += 1
                matched = True
                action = self._from_rule(rule, card)
                if action is not None:
                    actions.append(action)
                break
            if not matched and self._default == "click":
                actions.extend(self._fallback.decide([card]))
        return actions

    def describe(self) -> str:
        """Returns the rule count, default and fallback policy."""
        return (
            f"scripted(rules={len(self._rules)}, default={self._default},"
            f" fallback={self._fallback.describe()})"
        )


POLICY_NAMES = ("manual", "click", "mixed", "scripted")


def make_policy(
    name: str | None,
    typed_reply_probability: float | None = None,
    seed: int | None = None,
    script: Mapping[str, Any] | None = None,
    question_answerer: QuestionAnswerer | None = None,
    answers: Mapping[str, str] | None = None,
    deny_tools: Sequence[str] = (),
) -> ConfirmationPolicy:
    """Builds a policy from its CLI/config name.

    Args:
        name: Policy name ("manual", "click", "mixed", or "scripted"). None or
            empty defaults to "manual".
        typed_reply_probability: Probability of typing instead of clicking for
            "mixed" or "scripted" policies. None uses
            DEFAULT_TYPED_REPLY_PROBABILITY for "mixed".
        seed: Optional RNG seed for "mixed" or "scripted" policies; None picks
            a time-based seed for "mixed" or 0 for "scripted".
        script: Script configuration mapping for "scripted" policy; None uses
            an empty script.
        question_answerer: Optional callback returning an answer for question
            cards.
        answers: Optional explicit answer overrides keyed by confirmation ID
            or tool name.
        deny_tools: Optional fnmatch globs of gated tools to automatically
            decline under "click", "mixed", or "scripted" fallback.

    Returns:
        The configured ConfirmationPolicy instance.

    Raises:
        ValueError: If `name` is not a recognized policy name or the
            script/policy parameters are invalid.
    """
    key = (name or "manual").strip().lower()
    if key == "manual":
        return ManualPolicy()
    if key == "click":
        return ClickPolicy(
            question_answerer=question_answerer,
            answers=answers,
            deny_tools=deny_tools,
        )
    if key == "mixed":
        return MixedPolicy(
            typed_reply_probability=(
                DEFAULT_TYPED_REPLY_PROBABILITY
                if typed_reply_probability is None
                else typed_reply_probability
            ),
            seed=seed,
            question_answerer=question_answerer,
            answers=answers,
            deny_tools=deny_tools,
        )
    if key == "scripted":
        merged = dict(script or {})
        if (
            typed_reply_probability is not None
            and "typed_reply_probability" not in merged
        ):
            merged["typed_reply_probability"] = typed_reply_probability
        if seed is not None and "seed" not in merged:
            merged["seed"] = seed
        if answers is not None and "answers" not in merged:
            merged["answers"] = answers
        if deny_tools and "deny_tools" not in merged:
            merged["deny_tools"] = list(deny_tools)
        return ScriptedPolicy.from_dict(
            merged, question_answerer=question_answerer
        )
    raise ValueError(
        f"Unknown confirmation policy {name!r}; expected one of {POLICY_NAMES}"
    )
