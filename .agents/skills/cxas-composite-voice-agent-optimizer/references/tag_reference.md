# Gemini Composite v2 Audio Tag Reference

Evaluations showed that some inline audio tags perform better than others on
Gemini Composite v2. This page lists which tags to use, which to replace, and
what each does to delivery. `scripts/audit_agent.py` uses the same lists.

______________________________________________________________________

## 1. Syntax

- Use angle brackets (`<warm>`), not square brackets (`[warm]`). `<tag>` is read aloud far less often than `[tag]` and is followed more often.
- Put style, emotion, and paralinguistic tags at the **start** of the sentence they color. Stacking is fine there: `<calm><empathetic>`.
- Mid-sentence, use only pause tags, and always after punctuation: `"I checked your account, <short pause> everything is in order."`
- Tags are point markers. Never write a closing tag (`</prosody>`, `</warm>`). To return to normal pace, write `<prosody rate="100%">` before the words that resume it.
- Put `<prosody rate="N%">` first in a stack: `<prosody rate="70%"><empathetic>`.
- Tag keywords are always English, even when the spoken text is in another language: `<positive> ¡Excelente noticia! <short pause> Su reservación está confirmada.`

______________________________________________________________________

## 2. Tags by Reliability Tier

**Recommended**

| Category | Tags |
| :--- | :--- |
| Paralinguistic | `<sigh>`, `<sighs>`, `<chuckles>`, `<exhales>`, `<whispers>`, `<whispering>` |
| Speed & prosody | `<fast>`, `<faster>`, `<prosody rate="N%">` |
| Pauses | `<short pause>`, `<long pause>` |
| Emotion & tone | `<empathetic>`, `<warm>`, `<calm>`, `<clear>`, `<positive>`, `<determination>`, `<interest>`, `<enthusiasm>`, `<relief>` |

**Supported** — usually followed; keep at a sentence start:

`<sympathetic>`, `<tension>`, `<anxiety>`, `<awe>`, `<surprised>`, `<fearful>`, `<negative>`, `<disgusted>`

______________________________________________________________________

## 3. Tags to Replace

`audit_agent.py --remediate` replaces these automatically (rule `CV211` flags any left over).

| Tag | Use instead |
| :--- | :--- |
| `happy` | `<positive>` |
| `hope` | `<warm>` |
| `adoration` | `<warm>` |
| `curiosity` | `<interest>` |
| `reassuring` | `<empathetic>` |
| `admiration` | `<positive>` |
| `crying` | `<sympathetic>` |
| `laughs` | `<chuckles>` |
| `neutral` | Remove the tag; leave the line untagged |
| `professional` | `<clear>` |
| `cautious` | `<calm>` |
| `alarm` | `<calm>` |
| `nervousness` | `<calm>` |
| `annoyance` | `<determination>` |
| `aggression` | `<determination>` |
| `slow` | `<prosody rate="65%">` (`slow` is read aloud) |
| `slower` | `<prosody rate="65%">` (`slower` is read aloud) |

A caller emotion or register *named* "neutral" is fine; only the `<neutral>` tag goes. Reword prose that a deleted tag leaves awkward (*"open with then talk"*).

______________________________________________________________________

## 4. Unevaluated Tags

Tags such as `<seriousness>`, `<excited>`, `<celebratory>`, `<confusion>`, `<uhm>`, `<medium pause>`, `<deadpan>` were not part of the evaluation. The script converts them to angle brackets like any audio tag, and rule `CV212` reports them with the closest evaluated tag (e.g. `<seriousness>` → `<clear>`, `<uhm>` → `<short pause>`, `<celebratory>` → `<positive>`). Swap only what the user confirms.

______________________________________________________________________

## 5. Delivery Effect of Each Tag

Use this when a prompt needs a specific delivery change rather than a label.

| Tag | Effect on delivery |
| :--- | :--- |
| `<sigh>` | Audible inhale/exhale before speech |
| `<sighs>` | Longer, heavier sigh than `<sigh>` |
| `<chuckles>` | Crisp, warm chuckle; the preferred laugh |
| `<exhales>` | Audible breath release; can clip a leading "I" |
| `<whispers>` | Genuine unvoiced whisper, clearly quieter |
| `<whispering>` | The strongest drop in loudness of any tag |
| `<prosody rate="N%">` | Speed scales with `N`; below 100% slows, above speeds up |
| `<fast>` / `<faster>` | Slightly brisker cadence |
| `<short pause>` | Short silence inserted mid-utterance |
| `<long pause>` | Noticeably longer silence than `<short pause>` |

`<sigh>`, `<sighs>`, and `<exhales>` are fine in empathetic or sympathetic moments, but never in a turn that ends the session or escalates or transfers the call.

______________________________________________________________________

## 6. Tag Combinations

Stacking tags at the start of an utterance works well, including stacks of three or four, with a pause tag later in the sentence:

- `<prosody rate="70%"><empathetic>` … `<short pause>` …
- `<prosody rate="65%"><sigh><sympathetic>` (never when ending, escalating, or transferring the call)
- `<chuckles><warm><fast>`
- `<exhales><relief><calm>` … `<long pause>` …

______________________________________________________________________

## 7. Prosody Rates

- **Original instructions use prosody:** keep every rate exactly as written; only the brackets change (`[prosody rate=85%]` → `<prosody rate="85%">`). Don't round, standardize, add, or remove rates. Two rules with different rates for the same situation are a contradiction.
- **They don't:** ask the user whether to add prosody. Add rates only if they say yes, at the rates they choose. Rates are voice-dependent.
- **No prosody:** add no rates anywhere, except the `<prosody rate="65%">` that replaces `<slow>` / `<slower>`.

______________________________________________________________________

## 8. Known Side Effects

- **Hesitation fillers:** a pause tag sometimes vocalizes as "uh", "um", "hmm", or "Okay" instead of pure silence.
- **Initial pronoun clipping:** intense paralinguistic tags (`<exhales>`, `<sighs>`) at a sentence start can swallow a leading "I"/"We", or add an "Oh".
- **Leakage:** when a tag leaks, the model reads the keyword verbatim, e.g. `"...and relax, [warm] your refund..."` spoken as *"...and relax, warm, your refund..."*. This mostly happens with square brackets mid-sentence.
