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

"""Locates sensitive values in conversational text.

Conversation transcripts rarely contain values in their canonical form.
Speech-to-text and voice agents produce ``"4 1 1 1"``, ``"four one one
one"``, ``"double one"``, ``"niner"`` or values interrupted by fillers
(``"4111, uh, wait, 1111"``). ``digit_runs`` folds such sequences back into
digit strings so values can be found deterministically.
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

from cxas_scrapi.redaction.policy import MatchMode

if TYPE_CHECKING:
    from cxas_scrapi.redaction.policy import Category

_STRONG_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "niner": "9",
}

# Homophones only count as digits next to other digits.
_WEAK_WORDS = {
    "o": "0",
    "won": "1",
    "to": "2",
    "too": "2",
    "tree": "3",
    "for": "4",
    "fore": "4",
    "ate": "8",
}

_MULTIPLIERS = {"double": 2, "triple": 3}

# Tokens that may appear inside a spoken number without breaking it.
_FILLERS = {
    "uh",
    "um",
    "uhm",
    "umm",
    "er",
    "ah",
    "and",
    "then",
    "wait",
    "dash",
    "hyphen",
    "space",
    "sorry",
    "a",
    "moment",
    "is",
    "it's",
    "its",
}

_TOKEN_RE = re.compile(r"\d+|[A-Za-z]+(?:'[A-Za-z]+)?|[^\sA-Za-z\d]")

# Speech/SSML-style markup that may sit between digits.
_MARKUP_RE = re.compile(r"\{[^{}]{0,40}\}|<[^<>]{0,60}>")

# Punctuation that may separate digit groups ("4111-1111", "4111, 1111").
_SEPARATORS = {"-", ",", ".", "/", "(", ")"}

_MAX_GAP_TOKENS = 4


@dataclasses.dataclass(frozen=True)
class DigitRun:
    """A maximal digit sequence found in text."""

    digits: str
    start: int
    end: int


@dataclasses.dataclass(frozen=True)
class Match:
    """A located value."""

    start: int
    end: int
    text: str


def _classify(tokens: list[tuple[str, int, int]]) -> list[str | None]:
    """Maps tokens to digit strings; returns '' for bridgeable tokens."""
    out: list[str | None] = []
    for tok, _, _ in tokens:
        low = tok.lower()
        if tok.isdigit():
            out.append(tok)
        elif low in _STRONG_WORDS:
            out.append(_STRONG_WORDS[low])
        elif low in _MULTIPLIERS:
            out.append(f"*{_MULTIPLIERS[low]}")
        elif low in _WEAK_WORDS:
            out.append(f"?{_WEAK_WORDS[low]}")
        elif low in _FILLERS or tok in _SEPARATORS:
            out.append("")
        else:
            out.append(None)
    return out


def _resolve_weak(kinds: list[str | None]) -> list[str | None]:
    """Keeps homophones only when a real digit is adjacent (ignoring gaps)."""

    def neighbour_is_digit(i: int, step: int) -> bool:
        j = i + step
        while 0 <= j < len(kinds):
            k = kinds[j]
            if k == "":
                j += step
                continue
            return bool(k) and not k.startswith("?") and not k.startswith("*")
        return False

    resolved: list[str | None] = []
    for i, k in enumerate(kinds):
        if k and k.startswith("?"):
            if neighbour_is_digit(i, -1) or neighbour_is_digit(i, 1):
                resolved.append(k[1:])
            else:
                resolved.append(None)
        else:
            resolved.append(k)
    return resolved


def digit_runs(text: str) -> list[DigitRun]:
    """Returns digit sequences in ``text``, folding spoken/spaced forms."""
    if not text:
        return []
    # Blank out speech markup ("{pause}", "<break/>") without moving offsets.
    scan = _MARKUP_RE.sub(lambda m: " " * len(m.group(0)), text)
    tokens = [
        (m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(scan)
    ]
    kinds = _resolve_weak(_classify(tokens))

    runs: list[DigitRun] = []
    digits = ""
    start = end = -1
    pending_mult = 1
    gap = 0

    def flush() -> None:
        nonlocal digits, start, end
        if digits:
            runs.append(DigitRun(digits, start, end))
        digits = ""
        start = end = -1

    for (tok, t_start, t_end), kind in zip(tokens, kinds, strict=True):
        if kind is None:
            flush()
            pending_mult = 1
            gap = 0
            continue
        if kind == "":
            # Punctuation never breaks a run; filler words do after a few.
            if digits and tok not in _SEPARATORS:
                gap += 1
                if gap > _MAX_GAP_TOKENS:
                    flush()
                    gap = 0
            continue
        if kind.startswith("*"):
            pending_mult = int(kind[1:])
            if not digits:
                start = t_start
            continue
        if not digits and start < 0:
            start = t_start
        digits += (
            kind[0] * pending_mult + kind[1:] if pending_mult > 1 else kind
        )
        pending_mult = 1
        end = t_end
        gap = 0
    flush()
    return runs


def only_digits(value: str) -> str:
    """Canonical digit form of a value (spoken forms are folded)."""
    return "".join(r.digits for r in digit_runs(value))


def find_value(text: str, value: str, mode: MatchMode) -> list[Match]:
    """Finds ``value`` in ``text`` according to ``mode``."""
    if not text or not value:
        return []
    if mode == MatchMode.EXACT:
        # Case-insensitive, but never inside a longer word or number:
        # "123" must not match "H12345678".
        pattern = re.escape(value)
        if value[0].isalnum():
            pattern = r"(?<![A-Za-z0-9])" + pattern
        if value[-1].isalnum():
            pattern += r"(?![A-Za-z0-9])"
        return [
            Match(m.start(), m.end(), m.group(0))
            for m in re.finditer(pattern, text, flags=re.IGNORECASE)
        ]

    target = only_digits(value)
    if not target:
        return []
    out = []
    for run in digit_runs(text):
        # Short values must match a whole run so "988" does not match
        # inside "1988" or a longer account number.
        if run.digits == target or (len(target) >= 6 and target in run.digits):
            out.append(Match(run.start, run.end, text[run.start : run.end]))
    return out


def find_partial(
    text: str, value: str, allow_last: int = 0, min_len: int = 4
) -> list[Match]:
    """Finds visible fragments of a digit value.

    A fragment is a digit run that is a substring of the value, at least
    ``min_len`` digits long, and longer than ``allow_last`` (unless it is not
    the value's suffix, in which case any fragment counts).
    """
    target = only_digits(value)
    if len(target) < min_len:
        return []
    out = []
    for run in digit_runs(text):
        d = run.digits
        if len(d) < min_len or d == target or d not in target:
            continue
        if allow_last and target.endswith(d) and len(d) <= allow_last:
            continue
        out.append(Match(run.start, run.end, text[run.start : run.end]))
    return out


def find_category(text: str, category: Category) -> list[Match]:
    """Finds any of a category's values or patterns in ``text``."""
    found: list[Match] = []
    for value in category.values:
        found.extend(find_value(text, value, category.match))
    for pattern in category.patterns:
        for m in re.finditer(pattern, text):
            found.append(Match(m.start(), m.end(), m.group(0)))
    return found


def spoken_variants(value: str) -> list[str]:
    """Generates conversational renderings of a digit value.

    Useful for authoring eval utterances and documentation samples.
    """
    digits = only_digits(value)
    if not digits:
        return [value]
    names = {v: k for k, v in _STRONG_WORDS.items() if k not in {"oh", "niner"}}
    spaced = " ".join(digits)
    words = " ".join(names[d] for d in digits)
    grouped = " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))

    collapsed: list[str] = []
    i = 0
    while i < len(digits):
        j = i
        while j < len(digits) and digits[j] == digits[i]:
            j += 1
        count = j - i
        word = "niner" if digits[i] == "9" else names[digits[i]]
        while count >= 3:
            collapsed.append(f"triple {word}")
            count -= 3
        if count == 2:
            collapsed.append(f"double {word}")
        elif count == 1:
            collapsed.append(word)
        i = j
    fillers = (
        f"{digits[: len(digits) // 2]}, uh, wait, {digits[len(digits) // 2 :]}"
    )
    variants = [digits, spaced, grouped, words, " ".join(collapsed), fillers]
    seen: set[str] = set()
    return [v for v in variants if not (v in seen or seen.add(v))]
