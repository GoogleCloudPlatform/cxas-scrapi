# Corpus Engineering, Domain Derivation & 7-Axis Phrase Synthesis

High-precision vector routing depends on sharp decision boundaries and authentic caller language in embedding space. Follow this methodology whenever authoring or expanding `intents_catalog.yaml` and `training_phrases.csv`.

For industry-specific intents, confusable pairs, acronyms, and entity masks across Healthcare, Banking, Telecom, Retail, Insurance, and Travel, also read [domain_playbooks.md](domain_playbooks.md).

---

## 1. Dual-Source Signal Extraction

### Source A: Brownfield Extraction (Existing CXAS Agent, Traces, or Evals)
1. **Topology & Instruction Mining**: Run `scripts/bootstrap_from_scrapi.py` or inspect `app.json`, `agents/*/*.json` (`transferRules`, `description`), and `agents/*/instruction.txt` to map every specialist workflow. Split coarse catch-all sub-agents (such as `billing_and_fraud`) into distinct `action_object` intents (`billing_dispute` vs. `report_fraud`).
2. **Eval & Trace Mining**: Extract real user turns from `evaluations/`, CCAI Insights loss clusters, or `uv run cxas trace search`.
3. **Mandatory PII/PHI Scrubbing**: Replace all real names, phone numbers, emails, SSNs, member/order/card numbers, and street addresses before adding any live utterance to `training_phrases.csv`.

### Source B: Greenfield Domain Derivation (PRD Only)
1. **Build a Confusable-Pair Matrix**: List every pair of intents sharing `>60%` domain nouns (`claim_status` vs. `billing_dispute`, `cancel_order` vs. `modify_order`). Write explicit *"Includes X. Excludes Y (use intent_z)."* contrastive rules into `intents_catalog.yaml`.
2. **Compile a Domain Acronym & Vernacular Lexicon**: Map industry acronyms (`EOB`, `PA`, `PCP`, `ACH`, `NSF`, `eSIM`, `BOPIS`, `FNOL`, `PNR`) alongside plain-English symptom statements callers actually speak.
3. **Identify High-Gravity Nouns (`entity_mask_patterns`)**: List specific catalog nouns (medication names, handset models, merchant names, retail SKUs) that could pull different actions into the same vector cluster.

---

## 2. Sharp Intent Boundaries (`intents_catalog.yaml`)

1. **Action + Object Naming (`snake_case`)**:
   - Good: `check_credit_balance`, `check_gift_card_balance`, `prior_authorization_status`, `report_damaged_item`
   - Poor: `card`, `pharmacy`, `order_help`, `general_issue`
2. **Contrastive `intent_description` Authoring**:
   Vector Steering injects `intent_description` into every `top_matches` candidate returned to Gemini. State both inclusion and exclusion criteria:
   ```yaml
   billing_dispute:
     description: "Questioning an unexpected statement charge, duplicate billing entry, copay discrepancy, or fee calculation. Excludes lost/stolen cards or unauthorized identity theft (use report_fraud)."
   ```

---

## 3. The 7-Axis Utterance Synthesis Matrix (`training_phrases.csv`)

Distribute each intent's phrases (`25–60` phrases for production; `8–12` for prototypes) across all 7 linguistic axes:

| Axis | Share | Purpose | Example (`prior_authorization_status` in Cymbal Health) |
| :--- | :--- | :--- | :--- |
| **1. Direct Imperatives** | ~20% | Clean canonical commands | *"Check the status of my prior authorization request"* |
| **2. Indirect / Polite Questions** | ~15% | Natural question framing | *"Has my insurance approved the pre-authorization my doctor submitted?"* |
| **3. Layperson Symptom Phrasing** | ~20% | Callers unfamiliar with internal labels | *"The pharmacy says they are waiting on insurance approval before filling my medicine"* |
| **4. Domain Acronyms & Jargon** | ~15% | Bridges acronym and full-term space | *"Did my PA go through yet"* / *"Check status on pending prior auth"* |
| **5. Voice Telephony / ASR Realism** | ~15% | Spoken cadence and run-ons | *"yeah hi im calling to see if the pre approval for my procedure was approved"* |
| **6. Chat Shorthand & Brevity** | ~10% | Terse web/SMS inputs | *"prior auth approval status"* / *"check pending pre-auth"* |
| **7. Hard-Negative Boundary Phrases** | ~5% | Sharpens border against closest sibling | *"Check if insurance approved coverage for my prescription"* (contrasts with routine refill) |

### Anti-Patterns to Avoid
- **Single-Word Utterances**: Never index 1-word phrases (`"balance"`, `"claim"`). They become universal hub vectors that attract unrelated queries. Keep phrases `>= 2` words (ideal: `4–16` words).
- **Severe Class Imbalance**: Keep phrase counts within a `2x–3x` band across intents (`<= 5x` max/min ratio).
- **Unmasked PII / Live Numbers**: Never leave real dates, dollar amounts, order/member IDs, emails, or phone numbers in training phrases.

---

## 4. Domain Entity Masking

In domains with large product, medication, device, or merchant catalogs, rare nouns carry heavy semantic weight in dense embeddings:
- **Problem**: *"Refill my specialty injector"* and *"Did prior auth get approved for my specialty injector"* cluster around a specific drug brand noun if unmasked, narrowing the cosine margin between `prescription_refill` and `prior_authorization_status`.
- **Mitigation**:
  1. Generalize specific catalog nouns in `training_phrases.csv` to neutral domain terms (`"medication"`, `"item"`, `"device"`, `"merchant"`) or `[ENTITY]`.
  2. Add regex patterns to `dataset.entity_mask_patterns` in `vector_steering.yaml` so `analyze_dataset.py --strict` blocks leaks automatically.
  3. Add a query-normalization note in the steering agent's `instruction.txt` to generalize specific catalog nouns when calling `intent_classifier` while keeping raw entities in session state for downstream fulfillment.
