
# Albertsons FDE Onboarding & CCAI/Dialogflow CX Directives

## 1. Dynamic Onboarding Greeting & intent checks
- Greet the caller dynamically using the store's banner payload parameters (e.g. Safeway vs Albertsons).
- Suspend or bypass querying for phone number or triggering full authentication flows until the caller explicitly states their request intent (e.g. refill or status check).

## 2. Verbal Refill Confirmations (initiate_refills)
- Ensure a mandatory verbal confirmation of drug names and patient first name before submitting the refill:
  `"Just to confirm, you want to refill [Drug Name] for [Patient First Name], is that correct?"`
- Support verbal combo confirmations for multiple drugs cleanly.
- State exactly upon successful submission: `"Okay, I've sent that refill request to the pharmacy team. We will contact you once it's ready."`

## 3. Patient Privacy & Verification Scope bounds (DOB vs RXWA)
- **Rx Number Scope (Single Drug):** Authenticating via a 7-digit Rx Number ONLY unlocks access to read or refill that specific prescription. Never allow an Rx Number authentication to unlock full profile lookup.
- **DOB Scope (Full Profile):** Checking other medications or full list requires a combined match of Date of Birth + Phone Number. Prompt: `"Please provide the date of birth for the patient."`

## 4. CCAS deflection payloads & SIP Transfers
- Primary UJet deflection Lab extension routing: `sip:6697011265@50.175.187.220:5060`
- Prescribers On-Premextension routing (Immediate Transfer): `sip:6697011225@50.175.187.220:5060`
- Always establish redundant failover routing to lab IP `50.175.187.221` if the primary laboratory IP is down.

## 5. Staged SCRAPI Repository Integration
- The host's `scrapi` Git repository is **automatically bind-mounted** inside your container sandbox at the absolute path:
  `/workspace/scrapi/`
- You can access, read, and execute all local Scrapi scripts, customer app files, and evaluations directly under `/workspace/scrapi/`!
