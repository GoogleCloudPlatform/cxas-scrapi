# Scorecard Template Format & Schema Reference

This guide details the YAML and JSON template structures supported by SCRAPI for Contact Center Insights QA Scorecards.

---

## YAML Template Format

```yaml
qaScorecard:
  displayName: "Customer Experience & Quality Assurance"
  description: "Standard quality assurance evaluation rubric for contact center support."

qaQuestions:
  - questionBody: "Did the agent greet the customer professionally?"
    abbreviation: "greeting"
    answerChoices:
      - key: "yes"
        body: "Agent provided standard warm greeting and introduced themselves."
        score: 1.0
      - key: "no"
        body: "Agent did not greet or greeting was unprofessional."
        score: 0.0
      - key: "na"
        body: "Not applicable."
    answerInstructions: |
      Check if the agent stated company name and their name within the first 2 agent turns.

  - questionBody: "Did the agent correctly resolve or escalate the customer inquiry?"
    abbreviation: "resolution"
    answerChoices:
      - key: "resolved"
        body: "Issue completely resolved."
        score: 1.0
      - key: "escalated"
        body: "Properly escalated to tier 2 or supervisor."
        score: 0.8
      - key: "unresolved"
        body: "Issue remained unresolved without valid reason."
        score: 0.0
    answerInstructions: |
      Review entire conversation outcome. Verify customer confirmed satisfaction or agreement before closing.
```

---

## JSON / JSON5 Template Format

```json
{
  "qaScorecard": {
    "displayName": "Agent Compliance Scorecard",
    "description": "Evaluates regulatory and disclosure compliance."
  },
  "qaQuestions": [
    {
      "questionBody": "Did the agent deliver the mandatory recording disclosure?",
      "abbreviation": "recording_disclosure",
      "answerChoices": [
        {"key": "yes", "body": "Disclosure provided", "score": 1.0},
        {"key": "no", "body": "Disclosure omitted", "score": 0.0}
      ],
      "answerInstructions": "Verify the agent stated 'This call may be recorded for quality purposes' at the start."
    }
  ]
}
```
