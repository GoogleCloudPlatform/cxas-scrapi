# Domain Playbooks for AI Corpus Derivation (Cymbal Reference Verticals)

When building a Vector Steering intent catalog and training corpus for a specific industry—starting from either a PRD or an existing CXAS agent—use these vertical playbooks to derive **canonical intents**, **confusable sibling pairs**, **industry acronyms & layperson vernacular**, **entity-masking patterns**, and **tenant/compliance boundaries**.

*(All company examples below use **Cymbal**, the standard Google Cloud fictitious reference enterprise.)*

---

## 1. Healthcare — Cymbal Health (Payer & Pharmacy)

### Canonical Confusable Pairs
| Intent A | Confusable Sibling (Intent B) | Contrastive Boundary Rule |
| :--- | :--- | :--- |
| `prescription_refill` | `prior_authorization_status` | Refills cover routine renewals & mail-order shipping; Prior Auth covers insurer medical-necessity approvals/denials before a drug or procedure is covered. |
| `claim_status` | `billing_statement_dispute` | Claim Status checks processing/EOB timelines; Billing Dispute challenges an out-of-pocket invoice, duplicate copay, or balance bill. |
| `benefits_coverage_check` | `deductible_oop_balance` | Benefits Check asks *whether* a service is covered; Deductible/OOP asks *how much dollar accumulator* has been met this plan year. |
| `schedule_appointment` | `reschedule_cancel_appointment` | Separate new slot booking from modifying or cancelling an existing visit. |
| `find_in_network_provider` | `pcp_assignment_change` | Searching directory for an in-network clinician vs. officially updating the assigned Primary Care Provider on file. |

### Essential Healthcare Acronyms & Layperson Vernacular
Include both acronym phrasing and layperson symptom phrasing in `training_phrases.csv`:
- **`EOB`** <-> *"Explanation of Benefits"*, *"summary sheet showing what insurance paid"*
- **`PA` / `Prior Auth`** <-> *"pre-approval from insurance"*, *"doctor sent paperwork to approve my scan"*
- **`PCP`** <-> *"primary care doctor"*, *"main family physician on my card"*
- **`Rx` / `Script`** <-> *"prescription"*, *"maintenance medicine"*, *"90-day mail order"*
- **`HSA` / `FSA` / `OTC`** <-> *"health savings card"*, *"flex spending card"*, *"quarterly over-the-counter allowance"*
- **`OOP Max` / `Deductible` / `Coinsurance`** <-> *"out-of-pocket limit"*, *"how much left before insurance pays 100 percent"*

### High-Gravity Nouns to Mask (`entity_mask_patterns`)
- Specific medication chemical/brand nouns -> generalize to `medication` or `prescription` so drug nouns do not cause `prescription_refill`, `medication_copay`, and `prior_authorization_status` to collide.
- Specific imaging/specialties (`MRI`, `CT scan`, `cardiologist`) -> keep at most 1–2 varied examples or generalize to `procedure` / `specialist`.

### Tenant & Compliance Rules
- **Tenants**: `member` vs. `provider`; `medicare_advantage` vs. `commercial` vs. `medicaid`.
- **HIPAA & Clinical Safety**: Route clinical red-flag utterances (*chest pain, difficulty breathing*) via deterministic safety guardrails, configure Cloud Function ingress with `--ingress-settings=internal-and-gclb` or `internal-only`, and never place PHI in `training_phrases.csv`.

---

## 2. Banking & Cards — Cymbal Bank

### Confusable Pairs
- `billing_dispute` (*merchant double-charged me, unrecognized fee*) vs. `report_fraud` (*card stolen, account compromised, unauthorized identity theft*)
- `card_declined_troubleshoot` (*checkout rejected my card*) vs. `activate_new_card` (*just received replacement card in the mail*)
- `check_account_balance` (*checking/savings available funds*) vs. `check_credit_card_balance` (*statement balance, minimum payment, available credit*)
- `wire_transfer` (*domestic/international wire*) vs. `ach_bank_transfer` (*external routing/account transfer*)

### Banking Acronyms & Vernacular
- **`ACH` / `EFT`** <-> *"bank-to-bank transfer"*, *"direct deposit"*, *"routing and account number payment"*
- **`NSF` / `OD`** <-> *"non-sufficient funds"*, *"overdraft fee"*
- **`APR`** <-> *"interest rate on my card"*
- **`CD` / `HYSA` / `HELOC`** <-> *"certificate of deposit"*, *"high-yield savings"*, *"home equity line"*
- **`1099-INT` / `1098`** <-> *"tax form for interest"*, *"mortgage tax statement"*

### High-Gravity Nouns to Mask
- Specific merchant or vendor names -> generalize to `"merchant"` or `"vendor"` so *"Freeze card after vendor fraud"* does not collide with *"Dispute a vendor overcharge"*.

---

## 3. Telecom & ISP — Cymbal Telco

### Confusable Pairs
- `internet_outage_troubleshoot` (*no connection at all, red optical alarm light*) vs. `slow_speed_throttling` (*buffering, hit high-speed data cap*)
- `port_number_in` (*bring phone number from another carrier*) vs. `add_new_line` (*activate a brand-new line*)
- `upgrade_existing_device` (*trade in phone for newer model*) vs. `device_unlock_request` (*carrier-unlock paid-off handset for international travel*)

### Telecom Acronyms & Vernacular
- **`eSIM` / `SIM` / `IMEI`** <-> *"digital SIM activation"*, *"move cellular service to replacement phone"*
- **`PAC` / `Transfer PIN`** <-> *"number transfer authorization code"*
- **`ONT` / `Gateway` / `Modem`** <-> *"fiber box on the wall"*, *"wifi router blinking red"*

### High-Gravity Nouns to Mask
- Specific handset model names -> generalize to `"phone"` or `"device"`.

---

## 4. Retail & E-Commerce — Cymbal Retail

### Confusable Pairs
- `order_status` (*in-transit carrier tracking ETA*) vs. `report_missing_item` (*marked delivered but not at door, or item missing from package*)
- `cancel_order` (*void entire unshipped purchase*) vs. `modify_order` (*remove 1 line item, change quantity, update shipping address*)
- `start_return` (*request return label / exchange*) vs. `return_policy` (*return window rules*) vs. `return_refund_status` (*mailed return back, checking refund payout*)
- `check_credit_balance` vs. `check_gift_card_balance` vs. `loyalty_rewards_balance`

### Retail Acronyms & Vernacular
- **`BOPIS` / `Curbside`** <-> *"buy online pick up in store"*, *"store pickup locker"*
- **`RMA` / `QR Return Code`** <-> *"return authorization"*, *"printerless return barcode"*
- **`Price Adjustment`** <-> *"item went on sale right after purchase"*

### High-Gravity Nouns to Mask
- Specific catalog merchandise nouns (`mattress`, `sofa`, `dress`, `laptop`, `sneakers`) -> generalize to `"item"` or `"order"`.

---

## 5. Insurance — Cymbal Insurance

### Confusable Pairs
- `file_new_claim` (*report a new collision or property loss*) vs. `check_claim_status` (*follow up on existing claim adjuster or payout*)
- `roadside_assistance` (*need tow truck, flat tire, dead battery right now*) vs. `file_new_claim` (*post-incident damage filing*)
- `proof_of_insurance_id` (*get COI / ID card*) vs. `modify_policy_coverage` (*add driver, add vehicle, change deductible*)

### Insurance Acronyms & Vernacular
- **`FNOL`** <-> *"report a new accident"*, *"first notice of loss"*
- **`COI` / `Dec Page` / `SR-22`** <-> *"certificate of insurance"*, *"declarations page"*, *"state filing proof"*
- **`VIN` / `Subrogation`** <-> *"vehicle identification number"*, *"recovering deductible from other insurer"*

---

## 6. Travel & Airlines — Cymbal Air

### Confusable Pairs
- `change_booking` (*move flight to another date*) vs. `cancel_booking` (*cancel trip for refund or travel credit*)
- `flight_status_gate` (*flight delay or gate check*) vs. `missed_connection_rebook` (*delayed inbound leg caused missed connection*)
- `baggage_allowance_policy` (*checked bag fee rules*) vs. `delayed_damaged_baggage` (*checked bag missing at carousel*)

### Travel Acronyms & Vernacular
- **`PNR` / `Record Locator`** <-> *"6-character confirmation code"*
- **`Travel Credit` vs. `Loyalty Miles`** <-> *"unused ticket voucher"* vs. *"reward miles balance"*
- **`IRROPS` / `Same-Day Standby`** <-> *"weather cancellation rebooking"*, *"standby for earlier flight today"*
