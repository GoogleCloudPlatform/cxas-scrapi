# Terminology Study & Live CES A/B Benchmark: `domain / workflow` & 1-Level `workflow` vs. `playbook / task`

This reference documents why `cxas-self-steering-agent` uses **`select_domain` +
`load_workflow`** (`<domain_list>`, `<workflow_list>`, `<workflow name="...">`)
for 2-level agents and **`load_workflow`** (`--single-level`) for agents with
`<= 12` workflows, replacing the earlier `open_playbook` + `load_task` naming.

## 1. Historical Origin of `"Playbook"`

The original `open_playbook` / `load_task` terminology came from two sources:

1. **The Contact-Center "Shelf of Runbooks" Metaphor**: Describing
   `instruction.txt` as a shelf of playbooks, each containing several tasks,
   tracked by `current_playbook` and `current_task`.
2. **Dialogflow CX / Vertex AI `Playbook` Carryover**: When multi-agent CXAS
   applications (where each sub-agent or DFCX Playbook owned a business domain)
   were collapsed into a single root agent (`childAgents: []`), each former
   sub-agent/Playbook became an entry in `<playbook_list>`, leaving `<task>` to
   hold the step-by-step instructions.

## 2. Large-Scale Gemini Metaprompting Study (`160` Runs Across `4` Models)

We ran a **`160`-run metaprompting study** (`120` zero-anchoring blind runs +
`40` randomized-order rubric & critique runs) across **`gemini-4-argon`**,
**`gemini-4-carbon`**, **`gemini-4-hydrogen`**, and **`gemini-3.8-flash`** (`30`
blind + `10` rubric runs per model).

### 2.1 Stage 1: Zero-Anchoring Blind Elicitation (`120` Runs)

Across `120` unprimed runs counterbalanced across `5` neutral placeholder pairs
(`UNIT_A / UNIT_B`, `LAYER_1 / LAYER_2`, `TIER_OUTER / TIER_INNER`,
`BUCKET_X / BLOCK_Y`, `LEVEL_TOP / LEVEL_LEAF`), `6` enterprise domain example
sets, and `3` temperatures (`0.4`, `0.7`, `1.0` — `40` runs each), models saw
only the runtime mechanics with zero mention of any candidate nouns:

- **#1 Outer Noun (Coarse Grouping)**:
  - **`domain`**: **`109 / 120` (`90.8%`, 95% CI `[84.3%, 94.8%]`)** —
    `gemini-4-argon`: `25/30`, `gemini-4-carbon`: `30/30`,
    `gemini-4-hydrogen`: `24/30`, `gemini-3.8-flash`: `30/30`.
  - `category`: `11 / 120` (`9.2%`, 95% CI `[5.2%, 15.7%]`).
  - `playbook`: **`0 / 120` (`0.0%`, 95% CI `[0.0%, 3.1%]`)** at `#1`, and
    **`0 / 360`** Top-3 outer slots. Whenever `playbook` appeared anywhere in a
    model's Top-3 pairs (`79` times total), **`79 / 79` (`100.0%`)** placed
    `playbook` on the **INNER** instruction block, never the outer menu.
- **#1 Inner Noun (Executable Step-by-Step Guide)**:
  - **`workflow`**: **`113 / 120` (`94.2%`, 95% CI `[88.4%, 97.1%]`)** —
    `gemini-4-argon`: `30/30`, `gemini-4-carbon`: `30/30`,
    `gemini-4-hydrogen`: `29/30`, `gemini-3.8-flash`: `24/30`.
  - `task`: `3 / 120` (`2.5%`); `procedure`: `3 / 120` (`2.5%`); `flow`:
    `1 / 120` (`0.8%`).
- **#1 Unprimed Pair (`Top-1`, `Top-3`, and Borda Score out of `360`)**:

Pair `(Outer / Inner)` | Top-1 (`N=120`) | `4-argon` | `4-carbon` | `4-hydrogen` | `3.8-flash` | In Top-3 (`N=120`) | Borda Score
--- | --- | --- | --- | --- | --- | --- | ---
**`domain / workflow`** | **`103 / 120` (`85.8%`)** | `25/30` | `30/30` | `24/30` | `24/30` | **`104 / 120` (`86.7%`)** | **`311`**
`category / workflow` | `10 / 120` (`8.3%`) | `5/30` | `0/30` | `5/30` | `0/30` | `14 / 120` (`11.7%`) | `38`
`domain / task` | `3 / 120` (`2.5%`) | `0/30` | `0/30` | `0/30` | `3/30` | `4 / 120` (`3.3%`) | `10`
`domain / procedure` | `2 / 120` (`1.7%`) | `0/30` | `0/30` | `0/30` | `2/30` | `10 / 120` (`8.3%`) | `22`
`domain / flow` | `1 / 120` (`0.8%`) | `0/30` | `0/30` | `0/30` | `1/30` | `1 / 120` (`0.8%`) | `3`
`category / procedure` | `1 / 120` (`0.8%`) | `0/30` | `0/30` | `1/30` | `0/30` | `82 / 120` (`68.3%`) | `164`
**`playbook / task`** | **`0 / 120` (`0.0%`)** | `0/30` | `0/30` | `0/30` | `0/30` | **`0 / 120` (`0.0%`)** | **`0`**

- **Natural Tool Verbs & Sub-Tags (`N=120`)**:
  - Outer verb: **`select`** (`111 / 120` = `92.5%`), `switch` (`6 / 120` =
    `5.0%`), `set` (`3 / 120` = `2.5%`).
  - Inner verb: **`load`** (`116 / 120` = `96.7%`), `start` (`2 / 120` =
    `1.7%`), `activate` (`2 / 120` = `1.7%`).
  - Spontaneous `<current_step>` XML sub-tag convergence: **`119 / 120`
    (`99.2%`, 95% CI `[95.4%, 99.9%]`)**.
- **Spontaneously Flagged Terms to Avoid (`N=120`)**:
  - `agent`: `88 / 120` (`73.3%`, CI `[64.8%, 80.4%]`) — induces fake
    multi-agent delegation/transfer announcements.
  - `intent`: `80 / 120` (`66.7%`, CI `[57.8%, 74.5%]`) — conflates loading a
    stateful guide with single-turn NLU intent classification.
  - `step`: `41 / 120` (`34.2%`), `department`: `32 / 120` (`26.7%`), `task`:
    `25 / 120` (`20.8%`), `action`: `23 / 120` (`19.2%`), `skill`: `22 / 120`
    (`18.3%`).

### 2.2 Stage 2: Randomized-Order Rubric Leaderboard & Critique (`40` Runs)

Across `40` evaluation runs (`10` per model) where all `12` candidate pairs were
presented in shuffled order:

- **Critique of `playbook / task`**: **`0 / 40` (`0.0%`)** rated it `NATURAL`
  (`AMBIGUOUS = 28 / 40` = `70.0%`; `INVERTED = 12 / 40` = `30.0%`). Models
  noted that in SRE runbooks, automation playbooks, contact-center SOPs, and
  Dialogflow CX Playbooks, a *playbook* or *workflow* is the executable
  step-by-step operational guide while a *task* is an atomic step, so
  `playbook -> task -> <current_step>` creates semantic collision and makes
  `open_playbook` look like the guide is already open.

Rank | Pair `(Outer / Inner)` | Mean Overall (`1-10`, `n=40`) | Hierarchy | Prior Fit | Anti-Skip | Voice Safety | `4-argon` | `4-carbon` | `4-hydrogen` | `3.8-flash`
--- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---
`1` | **`category / workflow`** | **`9.20 +/- 0.20`** | `9.43` | `9.31` | `9.48` | `8.55` | `9.35` | `9.21` | `9.05` | `9.16`
`2` | `topic / workflow` | `9.14 +/- 0.27` | `9.13` | `9.19` | `9.24` | `9.00` | `9.28` | `9.17` | `8.80` | `9.32`
`3` | `category / procedure` | `9.14 +/- 0.16` | `9.46` | `9.09` | `9.54` | `8.47` | `9.19` | `9.17` | `9.11` | `9.09`
`4` | `topic / procedure` | `9.07 +/- 0.24` | `9.10` | `8.94` | `9.28` | `8.96` | `9.15` | `9.11` | `8.87` | `9.14`
`5` | **`domain / workflow`** | **`8.87 +/- 0.29`** | `9.17` | `9.19` | `9.22` | `7.88` | `9.15` | `8.95` | `8.71` | `8.67`
`6` | `domain / procedure` | `8.84 +/- 0.28` | `9.15` | `8.98` | `9.29` | `7.91` | `9.03` | `8.96` | `8.77` | `8.58`
`7` | `category / task` | `8.56 +/- 0.38` | `8.43` | `8.24` | `8.89` | `8.68` | `8.81` | `8.78` | `8.13` | `8.53`
`8` | `topic / task` | `8.55 +/- 0.39` | `8.19` | `8.22` | `8.64` | `9.13` | `8.79` | `8.75` | `8.05` | `8.60`
`9` | `domain / task` | `8.27 +/- 0.41` | `8.23` | `8.18` | `8.65` | `8.00` | `8.56` | `8.52` | `7.86` | `8.15`
`10` | `skill / procedure` | `6.41 +/- 0.54` | `6.38` | `6.65` | `5.50` | `7.09` | `6.78` | `6.45` | `5.87` | `6.53`
`11` | `workflow / task` | `5.45 +/- 0.85` | `4.86` | `5.42` | `4.00` | `7.50` | `5.39` | `5.06` | `4.87` | `6.46`
`12` | **`playbook / task`** | **`5.40 +/- 0.74`** | `5.07` | `5.36` | `4.15` | `7.01` | `5.46` | `5.57` | `4.84` | `5.72`

- **Why `domain / workflow` & `category / workflow` Both Beat
  `playbook / task`**:
  - **`domain / workflow`** dominated unprimed generative priors (**`85.8%`
    Top-1**, `86.7%` Top-3, Borda `311` vs. `0` for `playbook / task`), meaning
    Gemini spontaneously writes `select_domain` + `load_workflow` even with
    zero prompt hints. Its slightly lower rubric `Voice Safety` score (`7.88`)
    comes from engineering jargon caution about `"domain"` in rare unguarded
    replies, whereas live CES testing with `<constraints>` showed **`0` spoken
    jargon leaks** across all `15/15` Benchmark App A, `5/5` Benchmark App B,
    and `5/5` Benchmark App C sessions (including explicit baiting).
  - **`category / workflow`** ranked **`#1` (`9.20 +/- 0.20`)** on the explicit
    rubric leaderboard (`AntiSkip 9.48` vs. `4.15` for `playbook / task`, and
    picked best 2-level pair in `18/40` = `45.0%` vs. `domain / workflow`'s
    `+3.47` overall margin over `playbook / task`'s `5.40`).
  - Between the two top performers, `cxas-self-steering-agent` standardizes on
    **`select_domain` / `load_workflow`** because it is simultaneously Gemini's
    overwhelming **#1 unprimed generative prior (`85.8%`)** and high-scoring on
    explicit critique (`8.87/10`, `Hier 9.17`, `Prior 9.19`, `AntiSkip 9.22`),
    beating `playbook / task` (`5.40/10`, `0%` unprimed, `0%` rated `NATURAL`)
    on every axis.
- **1-Level Direct Mode Consensus (`N=40`)**:
  - Median threshold to omit the outer layer: **`12` workflows** (mean = `13.3`
    workflows).
  - Preferred 1-level router tool: **`load_workflow` (`29 / 40` = `72.5%`)** vs.
    `load_procedure` (`11 / 40` = `27.5%`).

## 3. Live Multi-Vertical CES A/B Benchmark (`9` Sandbox Apps Across `3` Verticals)

We deployed and benchmarked `9` sandbox CES applications on
`gemini-composite-v1` across three enterprise verticals (**Benchmark App A**:
`Mobility Subscription`, 5 workflows / 2 domains; **Benchmark App B**:
`Telecom Support`, 4 workflows / 2 domains; **Benchmark App C**:
`Financial Card Services`, 4 workflows / 2 domains) and five multi-turn
scenarios per app (`S1` primary lookup/FAQ, `S2` multi-step transaction with
verbatim deterministic `next_agent_actions` disclosure + goodbye, `S3` Turn-1
informational policy question, `S4` mid-call cross-domain pivot + caller jargon
bait `"what guide or playbook are you pulling up?"`, and `S5` live specialist
escalation):

Benchmark App / Variant | Vertical Scope | Routing Topology | Grounded Accuracy | ToolEvals | Spoken Jargon Leaks | Turn-1 Mean Latency | Per-Turn Mean Latency | Router Hops (5 Scenarios) | Total Tool Calls
--- | --- | --- | --- | --- | --- | --- | --- | --- | ---
**Benchmark App A — Baseline** | `Mobility Subscription` (5 workflows / 2 domains) | 2-level `open_playbook` + `load_task` | `5/5` (`15/15`) | `10/10` | `0` | `2417.6 ms` | `1852.8 ms` | `12.0` | `26.0`
**Benchmark App A — Treatment A** | `Mobility Subscription` (5 workflows / 2 domains) | 2-level `select_domain` + `load_workflow` | `5/5` (`15/15`) | `10/10` | `0` | `2431.5 ms` | `1935.1 ms` | `13.0` | `27.0`
**Benchmark App A — Treatment B** | `Mobility Subscription` (5 workflows / 2 domains) | 1-level `load_workflow` (`--single-level`) | `5/5` (`15/15`) | `9/9` | `0` | **`1908.1 ms` (`-21.1%`)** | **`1692.8 ms` (`-8.6%`)** | **`7.0` (`-41.7%`)** | **`21.0` (`-19.2%`)**
**Benchmark App B — Baseline** | `Telecom Support` (4 workflows / 2 domains) | 2-level `open_playbook` + `load_task` | `5/5` | `9/9` | `0` | `2153.7 ms` | `1622.9 ms` | `14` | `34`
**Benchmark App B — Treatment A** | `Telecom Support` (4 workflows / 2 domains) | 2-level `select_domain` + `load_workflow` | `5/5` | `9/9` | `0` | `2161.4 ms` | `1665.8 ms` | `14` | `34`
**Benchmark App B — Treatment B** | `Telecom Support` (4 workflows / 2 domains) | 1-level `load_workflow` (`--single-level`) | `5/5` | `8/8` | `0` | **`1617.0 ms` (`-24.9%`)** | **`1387.9 ms` (`-14.5%`)** | **`8` (`-42.9%`)** | **`28` (`-17.6%`)**
**Benchmark App C — Baseline** | `Financial Card Services` (4 workflows / 2 domains) | 2-level `open_playbook` + `load_task` | `5/5` | `11/11` | `0` | `2348.1 ms` | `1815.2 ms` | `11` | `28`
**Benchmark App C — Treatment A** | `Financial Card Services` (4 workflows / 2 domains) | 2-level `select_domain` + `load_workflow` | `5/5` | `11/11` | `0` | `2331.6 ms` | **`1749.5 ms` (`-3.6%`)** | `11` | `28`
**Benchmark App C — Treatment B** | `Financial Card Services` (4 workflows / 2 domains) | 1-level `load_workflow` (`--single-level`) | `5/5` | `11/11` | `0` | **`1573.4 ms` (`-33.0%`)** | **`1452.2 ms` (`-20.0%`)** | **`5` (`-54.5%`)** | **`22` (`-21.4%`)**

### Key Takeaways from the Live CES A/B Benchmark

1. **100% Grounded Accuracy & Zero Jargon Leaks Across All New Variants**: Both
   2-level `select_domain` + `load_workflow` (`Treatment A`) and 1-level
   `load_workflow` (`Treatment B`) achieved `100%` (`5/5`) scenario accuracy,
   `100%` `ToolEvals` pass rate, and `0` spoken internal jargon leaks across
   all three enterprise verticals, including under adversarial caller jargon
   baiting (`S4`).
2. **1-Level `load_workflow` (`--single-level`) Cuts Turn-1 Latency by
   `21%-33%` (`510-775 ms`) and Router Hops by `42%-55%`**:
   - **Benchmark App A** (`Mobility Subscription`, 5 workflows / 2 domains):
     Turn-1 latency dropped by **`-509.5 ms` (`-21.1%`)** (`-627.5 ms` /
     `-22.3%` on routed turns `S1-S4`), router hops dropped by **`-41.7%`**
     (`12 -> 7`), and average session tokens dropped by **`-22.4%`**
     (`18,280 -> 14,180`).
   - **Benchmark App B** (`Telecom Support`, 4 workflows / 2 domains): Turn-1
     latency dropped by **`-536.7 ms` (`-24.9%`)** (`-685.0 ms` / `-27.4%` on
     routed turns `S1-S4`), per-turn median latency dropped by **`-28.0%`**
     (`1686.8 ms -> 1215.1 ms`), and router hops dropped by **`-42.9%`**
     (`14 -> 8`).
   - **Benchmark App C** (`Financial Card Services`, 4 workflows / 2 domains):
     Turn-1 latency dropped by **`-774.7 ms` (`-33.0%`)**, per-turn mean
     latency dropped by **`-363.0 ms` (`-20.0%`)**, and router hops dropped by
     **`-54.5%`** (`11 -> 5`).
3. **Architectural Guidance**:
   - Use **1-level `load_workflow` (`--single-level`)** whenever an agent has
     `<= 12` total workflows so callers get `~500-775 ms` faster Turn-1
     responses and `~20%` lower token usage with full dynamic tool hiding.
   - Use **2-level `select_domain` + `load_workflow`** (default) when an agent
     spans `15+` workflows across multiple distinct business domains,
     eliminating the semantic inversion of `open_playbook` / `load_task`.
