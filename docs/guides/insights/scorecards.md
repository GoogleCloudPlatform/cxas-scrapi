---
title: Scorecards
description: Listing, exporting, importing, and copying CCAI Insights QA scorecards.
---

# Scorecards

QA Scorecards in CCAI Insights are structured rubrics for evaluating conversation quality. Each scorecard contains questions that an AI system answers (Met / Not Met) for each conversation it reviews. SCRAPI's `Scorecards` class and `cxas insights` CLI commands let you manage scorecards programmatically.

---

## The `Scorecards` class

```python
from cxas_scrapi.core.scorecards import Scorecards

sc = Scorecards(
    project_id="my-gcp-project",
    location="us-central1",
)
```

---

## Listing scorecards

### Python

```python
scorecards_list = sc.list_scorecards()

for scorecard in scorecards_list:
    print(f"Name: {scorecard['name']}")
    print(f"Display name: {scorecard.get('displayName', '(unnamed)')}")
    print()
```

### CLI

```bash
cxas insights list \
  --project_id my-gcp-project \
  --location us-central1
```

Output:

```
Scorecards in projects/my-gcp-project/locations/us-central1
============================================================
ID                    | Display Name              | Revisions
----------------------|---------------------------|----------
qa-scorecard-agent-v1 | Agent Quality Scorecard   | 3
qa-scorecard-billing  | Billing Agent Scorecard   | 1
```

---

## Getting a scorecard

### Python

```python
# By full resource name
scorecard = sc.get_scorecard(
    "projects/my-gcp-project/locations/us-central1/qaScorecards/qa-scorecard-agent-v1"
)

print(scorecard)
```

### Getting the latest revision

```python
revision = sc.get_latest_revision(
    "projects/my-gcp-project/locations/us-central1/qaScorecards/qa-scorecard-agent-v1"
)

print(revision["name"])
# projects/.../qaScorecards/qa-scorecard-agent-v1/revisions/3
```

---

## Creating a scorecard

### Python

```python
scorecard = sc.create_scorecard(
    scorecard_id="agent-quality-v1",
    scorecard={
        "displayName": "Agent Quality Scorecard",
        "description": "Evaluates the quality of customer support interactions",
    },
)

print(scorecard["name"])
```

### Adding questions to a scorecard

After creating the scorecard, create a revision and add questions:

```python
scorecard_name = scorecard["name"]

# Create a new revision
revision = sc.create_revision(scorecard_name)
revision_name = revision["name"]

# Add questions to the revision
questions = [
    {
        "body": "Did the agent correctly identify the customer's primary intent?",
        "answerChoices": [
            {"key": "yes", "score": 1.0, "body": "Yes"},
            {"key": "no", "score": 0.0, "body": "No"},
            {"key": "na", "score": None, "body": "N/A"},
        ],
        "tags": ["intent-classification"],
        "weight": 2.0,  # This question counts double
    },
    {
        "body": "Did the agent provide accurate information?",
        "answerChoices": [
            {"key": "yes", "score": 1.0, "body": "Yes"},
            {"key": "partial", "score": 0.5, "body": "Partially"},
            {"key": "no", "score": 0.0, "body": "No"},
        ],
        "tags": ["accuracy"],
        "weight": 3.0,
    },
    {
        "body": "Was the customer's issue resolved within the conversation?",
        "answerChoices": [
            {"key": "yes", "score": 1.0, "body": "Yes"},
            {"key": "no", "score": 0.0, "body": "No"},
            {"key": "escalated", "score": 0.5, "body": "Escalated appropriately"},
        ],
        "tags": ["resolution"],
        "weight": 3.0,
    },
]

for question in questions:
    sc.create_question(revision_name, question)

print(f"Created scorecard with {len(questions)} questions")
```

---

## Exporting a scorecard

Export a scorecard and all its questions to a JSON file. This is useful for backup, documentation, or sharing scorecards across projects.

### Python

```python
import json

# Get the scorecard and its latest revision
scorecard_name = "projects/my-gcp-project/locations/us-central1/qaScorecards/qa-scorecard-agent-v1"
scorecard = sc.get_scorecard(scorecard_name)
revision = sc.get_latest_revision(scorecard_name)
questions = sc.list_questions(revision["name"])

export_data = {
    "scorecard": scorecard,
    "revision": revision,
    "questions": questions,
}

with open("scorecard-export.json", "w") as f:
    json.dump(export_data, f, indent=2)

print("Exported to scorecard-export.json")
```

### CLI

```bash
cxas insights export \
  --scorecard "projects/my-gcp-project/locations/us-central1/qaScorecards/qa-scorecard-agent-v1" \
  --output scorecard-export.json
```

---

## Importing a scorecard

Import a previously exported scorecard, creating a new one with the same questions.

### Python

```python
import json

with open("scorecard-export.json") as f:
    export_data = json.load(f)

# Create a new scorecard
new_scorecard = sc.create_scorecard(
    scorecard_id="agent-quality-v2",
    scorecard={
        "displayName": export_data["scorecard"].get("displayName", "") + " (imported)",
        "description": export_data["scorecard"].get("description", ""),
    },
)

# Create a revision
revision = sc.create_revision(new_scorecard["name"])

# Recreate all questions
for question in export_data["questions"]:
    # Strip server-generated fields before creating
    clean_question = {k: v for k, v in question.items()
                      if k not in ("name", "createTime", "updateTime")}
    sc.create_question(revision["name"], clean_question)

print(f"Imported scorecard: {new_scorecard['name']}")
```

### CLI

```bash
cxas insights import \
  --file scorecard-export.json \
  --scorecard_id agent-quality-v2 \
  --project_id my-gcp-project \
  --location us-central1
```

---

## Copying a scorecard

Copying lets you duplicate a scorecard to a different project or region — useful when you maintain the same quality standards across multiple environments.

### Python

```python
# Copy from one project to another
source_scorecard = "projects/source-project/locations/us-central1/qaScorecards/qa-scorecard-agent-v1"
target_sc = Scorecards(
    project_id="target-project",
    location="us-central1",
)

# Export from source
source = Scorecards(project_id="source-project", location="us-central1")
scorecard_data = source.get_scorecard(source_scorecard)
revision = source.get_latest_revision(source_scorecard)
questions = source.list_questions(revision["name"])

# Import to target
new_scorecard = target_sc.create_scorecard(
    scorecard_id="agent-quality-v1",
    scorecard={
        "displayName": scorecard_data.get("displayName", ""),
        "description": scorecard_data.get("description", ""),
    },
)

new_revision = target_sc.create_revision(new_scorecard["name"])

for question in questions:
    clean = {k: v for k, v in question.items()
             if k not in ("name", "createTime", "updateTime")}
    target_sc.create_question(new_revision["name"], clean)

print(f"Copied to: {new_scorecard['name']}")
```

### CLI

```bash
cxas insights copy \
  --source "projects/source-project/locations/us-central1/qaScorecards/qa-scorecard-agent-v1" \
  --target_project target-project \
  --target_location us-central1 \
  --scorecard_id agent-quality-v1
```

---

## Updating scorecard questions

To update an existing question (e.g., change its wording or scoring):

```python
revision_name = "projects/.../qaScorecards/qa-scorecard-agent-v1/revisions/3"
questions = sc.list_questions(revision_name)

# Find the question you want to update
question = next(q for q in questions if "primary intent" in q["body"])

# Update it
updated = sc.patch_question(
    name=question["name"],
    question={
        **question,
        "body": "Did the agent correctly identify and acknowledge the customer's primary intent?",
    },
    update_mask="body",
)

print("Question updated")
```

---

## Full workflow example

Here's a complete workflow for setting up a scorecard for a new agent team:

```python
from cxas_scrapi.core.scorecards import Scorecards
import json

sc = Scorecards(project_id="my-project", location="us-central1")

# Step 1: Create the scorecard
scorecard = sc.create_scorecard(
    scorecard_id="support-agent-quality-v1",
    scorecard={
        "displayName": "Support Agent Quality Scorecard",
        "description": "Evaluates quality of support interactions for the My Support Agent",
    },
)

# Step 2: Create a revision to add questions to
revision = sc.create_revision(scorecard["name"])

# Step 3: Add questions
quality_criteria = [
    ("Intent understanding", "Did the agent correctly identify what the customer needed?", 2.0),
    ("Information accuracy", "Did the agent provide accurate and complete information?", 3.0),
    ("Tool usage", "Did the agent use the appropriate tools to answer the question?", 2.0),
    ("Resolution", "Was the customer's issue resolved by the end of the conversation?", 3.0),
    ("Tone and empathy", "Did the agent maintain an appropriate, empathetic tone?", 1.0),
]

for tag, body, weight in quality_criteria:
    sc.create_question(
        revision["name"],
        {
            "body": body,
            "tags": [tag.lower().replace(" ", "_")],
            "weight": weight,
            "answerChoices": [
                {"key": "yes", "score": 1.0, "body": "Yes"},
                {"key": "partial", "score": 0.5, "body": "Partially"},
                {"key": "no", "score": 0.0, "body": "No"},
            ],
        },
    )

print(f"Scorecard ready: {scorecard['name']}")
print(f"Revision: {revision['name']}")

# Step 4: Export for sharing with team
export_data = {
    "scorecard": scorecard,
    "revision": revision,
    "questions": sc.list_questions(revision["name"]),
}
with open("scorecard-support-agent-v1.json", "w") as f:
    json.dump(export_data, f, indent=2)

print("Exported to scorecard-support-agent-v1.json")
```
