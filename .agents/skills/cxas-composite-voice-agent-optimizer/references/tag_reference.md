# Gemini Composite v2 Audio Tag Reference

Evaluations and runtime verification on Gemini Composite v2 show which inline
audio tags perform reliably, which legacy tags should be replaced, and how each
tag shapes delivery. `scripts/audit_agent.py` uses the same lists.

______________________________________________________________________

## 1. Syntax

- Use angle brackets (`<warm>`, `<breath>`), not square brackets (`[warm]`). `<tag>` is read aloud far less often than `[tag]` and is followed more often.
- Put style, emotion, and paralinguistic tags at the **start** of the sentence they color. Stacking is fine there: `<calm><empathetic>`.
- Open a subsequent sentence in the same turn with a new tone tag when shifting delivery mid-response: `"<calm><empathetic> I'm so sorry your flight was canceled. <positive> The good news is that I've already rebooked you on the next flight out."`
- Mid-sentence, use only pause tags (`<short pause>`, `<long pause>`), and always after punctuation: `"I checked your account, <short pause> everything is in order."`
- Tags are point markers. Never write a closing tag (`</prosody>`, `</warm>`). To return to normal pace, write `<prosody rate="100%">` before the words that resume it (`audit_agent.py --remediate` rewrites `</prosody>` before continuing speech automatically).
- Put `<prosody rate="N%">` first in a stack: `<prosody rate="70%"><empathetic>` (`--remediate` reorders reversed stacks).
- Tag keywords are **always English**, even when the spoken text is in another language (`CV210` flags and `--remediate` converts translated tags such as `<pausa corta>` or `<短暂停顿>`): `<positive> ¡Excelente noticia! <short pause> Su reservación está confirmada.`

______________________________________________________________________

## 2. Tags by Reliability Tier

**Recommended (Customer-Care Core)**

| Category | Tags |
| :--- | :--- |
| Paralinguistic | `<breath>`, `<sigh>`, `<sighs>`, `<laugh>`, `<chuckle>`, `<chuckles>`, `<exhales>`, `<gasp>`, `<throat-clearing>`, `<whispers>`, `<whispering>` |
| Speed & prosody | `<fast>`, `<faster>`, `<prosody rate="N%">` |
| Pauses | `<short pause>`, `<long pause>` |
| Emotion & tone | `<empathetic>`, `<warm>`, `<calm>`, `<clear>`, `<positive>`, `<determination>`, `<interest>`, `<enthusiasm>`, `<relief>` |

**Supported** — recognized by the Composite v2 speech model; keep strictly at a sentence start:

- **Tone:** `<sympathetic>`, `<tension>`, `<anxiety>`, `<awe>`, `<surprised>`, `<fearful>`, `<negative>`, `<disgusted>`
- **Additional vocal bursts** *(avoid instructing customer-service agents to emit harsh or casual bursts like `<cackle>`, `<snort>`, `<tsk>`, or `<yawn>`)*: `<heavy breath>`, `<pant>`, `<giggle>`, `<snicker>`, `<cackle>`, `<cough>`, `<sneeze>`, `<snort>`, `<tsk>`, `<argh>`, `<sob>`, `<cry>`, `<whimper>`, `<groan>`, `<yawn>`

______________________________________________________________________

## 3. Tags to Replace

`audit_agent.py --remediate` replaces these automatically (rule `CV211` flags any left over, and `CV210` converts translated non-English tags to their English equivalents).

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
| `medium pause` | `<short pause>` (standardize mid-sentence pauses) |
| `clears throat` | `<throat-clearing>` (canonical spelling) |
| `exhale` | `<exhales>` (canonical spelling) |
| `whisper` | `<whispers>` (canonical spelling) |

A caller emotion or register *named* "neutral" is fine; only the `<neutral>` tag goes. Reword prose that a deleted tag leaves awkward (*"open with then talk"*).

______________________________________________________________________

## 4. Unevaluated & Misspelled Tags

Tags such as `<seriousness>`, `<excited>`, `<celebratory>`, `<confusion>`, `<uhm>`, `<pause>`, `<deadpan>` were not part of the customer-care evaluation. The script converts them to angle brackets like any audio tag, and rule `CV212` reports them with the closest evaluated tag (e.g. `<seriousness>` → `<clear>`, `<uhm>` → `<short pause>`, `<celebratory>` → `<positive>`), as well as close-match bracket typos (`[clam]` → `<calm>`). Swap only what the user confirms.

______________________________________________________________________

## 5. Delivery Effect of Each Tag

Use this when a prompt needs a specific delivery change rather than a label.

| Tag | Effect on delivery |
| :--- | :--- |
| `<breath>` | Natural audible inhale before speech; clean sentence-start breath |
| `<sigh>` | Audible empathetic inhale/exhale before speech |
| `<sighs>` | Longer, heavier sigh than `<sigh>` |
| `<laugh>` / `<chuckle>` / `<chuckles>` | Warm laugh or chuckle; `<chuckles>` is the preferred support laugh |
| `<exhales>` | Audible breath release; can clip a leading `"I"` |
| `<gasp>` / `<throat-clearing>` | Brief intake of breath or subtle throat-clear |
| `<whispers>` | Genuine unvoiced whisper, clearly quieter |
| `<whispering>` | The strongest drop in loudness of any tag |
| `<prosody rate="N%">` | Speed scales with `N`; below 100% slows, above speeds up |
| `<fast>` / `<faster>` | Slightly brisker cadence |
| `<short pause>` | Short silence inserted at a comma or full-stop boundary |
| `<long pause>` | Noticeably longer silence than `<short pause>` |

`<sigh>`, `<sighs>`, and `<exhales>` are fine in empathetic or sympathetic moments, but **never** in a turn that ends the session or escalates or transfers the call (`CV215`); open those turns with `<calm><empathetic>` instead.

______________________________________________________________________

## 6. Tag Combinations

Stacking tags at the start of an utterance works well, including stacks of three or four, with a pause tag later in the sentence or a tone transition on the next sentence:

- `<prosody rate="70%"><empathetic>` … `<short pause>` …
- `<calm><empathetic>` … `<positive>` … (multi-sentence empathy-to-resolution transition)
- `<prosody rate="65%"><sigh><sympathetic>` (never when ending, escalating, or transferring the call)
- `<chuckles><warm><fast>`
- `<exhales><relief><calm>` … `<long pause>` …

______________________________________________________________________

## 7. Prosody Rates

- **Original instructions use prosody:** keep every rate exactly as written; only the brackets change (`[prosody rate=85%]` → `<prosody rate="85%">`). Don't round, standardize, add, or remove rates. Two rules with different rates for the same situation are a contradiction.
- **They don't:** ask the user whether to add prosody. Add rates only if they say yes, at the rates they choose. Rates are voice-dependent.
- **No prosody:** add no rates anywhere, except the `<prosody rate="65%">` that replaces `<slow>` / `<slower>`.

______________________________________________________________________

## 8. Known Side Effects & Anti-Collision Rules

- **Hesitation fillers:** a pause tag sometimes vocalizes as `"uh"`, `"um"`, `"hmm"`, or `"Okay"` instead of pure silence.
- **Double-pause stutter (`CV215`):** never stack an ellipsis right before a pause tag (`... <short pause>`); use either `...` for a light hesitation or `", <short pause>"` / `". <short pause>"`, never both together.
- **Initial pronoun clipping & ALL-CAPS bark (`CV215`):** intense paralinguistic tags (`<exhales>`, `<sighs>`) at a sentence start can swallow a leading `"I"`/`"We"`, add an `"Oh"`, or bark if followed immediately by an ALL-CAPS word. Keep the first word after `<exhales>` or `<sigh>` normal case and place ALL-CAPS stress later in the clause (`"<exhales> Solid green on the status light means you're ONLINE."`).
- **Leakage:** when a tag leaks, the model reads the keyword verbatim, e.g. `"...and relax, [warm] your refund..."` or `"<pausa corta>"`. This happens with square brackets, translated non-English tags, or mid-sentence style tags.
