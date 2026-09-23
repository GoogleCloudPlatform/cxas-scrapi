# PayPal Root_agent Ground Truth Fixture

## Depot Locations & Provenance
* **CL 982641003**: Authored by Alex Tumanov (`alextumanov@google.com`), approved by Dahlia Shehata (`dahliashehata@google.com`).
  * `//depot/google3/cloud/ai/fde/customers/paypal/cxas/dev/agents/Root_agent/Root_agent.json`
  * `//depot/google3/cloud/ai/fde/customers/paypal/cxas/dev/agents/Root_agent/definition.yaml`
* **CL 983851552**: Authored by Dahlia Shehata (`dahliashehata@google.com`), approved by Alex Tumanov (`alextumanov@google.com`).
  * Synchronized `Root_agent.json` with `definition.yaml`.
* **CitC Snapshot**: `/google/src/cloud/alextumanov/173372/google3/cloud/ai/fde/customers/paypal/cxas/dev/agents/Root_agent/`

## Access Control Note
* Path `//depot/google3/cloud/ai/fde/customers/paypal/` is ACL-restricted under a customer silo.
* Piper `OWNERS_METADATA` specifies:
  * `access_type: LSC_ACCESS`
  * `default_ganpati_group: "ces-deployment-team"`
* If access is denied (`p4 protect` returns `READ_OR_WRITE_REQUEST: DENIED`), join the `ces-deployment-team` Ganpati group via AccessNow (go/accessnow) or request membership to pull the exact files into this directory.
