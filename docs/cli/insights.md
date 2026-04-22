# cxas insights

`cxas insights` gives you programmatic access to QA scorecards in the CX Agent Studio Insights API — you can list, export, import, and copy scorecards without ever leaving your terminal.

## Usage

```
cxas insights <subcommand> [options]
```

## Subcommands

| Subcommand | Description |
|------------|-------------|
| [`list-scorecards`](#list-scorecards) | List all QA scorecards under a project and location. |
| [`export-scorecard-from-insights`](#export-scorecard-from-insights) | Export a scorecard and its questions to a local JSON or YAML file. |
| [`import-scorecard-to-insights`](#import-scorecard-to-insights) | Import a scorecard template file into Insights as a new revision. |
| [`copy-scorecard`](#copy-scorecard) | Copy a scorecard's questions into another scorecard. |

---

## list-scorecards

Lists all QA scorecards in the specified project and location.

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--parent PARENT` | Yes | — | Parent resource name in the format `projects/{project}/locations/{location}`. |

### Example

```bash
cxas insights list-scorecards \
  --parent "projects/my-gcp-project/locations/us-central1"
```

Output:

```
Scorecard: projects/my-gcp-project/locations/us-central1/qaScorecards/sc-001 (Customer Satisfaction)
Scorecard: projects/my-gcp-project/locations/us-central1/qaScorecards/sc-002 (Agent Quality)
```

---

## export-scorecard-from-insights

Exports a scorecard and all its questions from the Insights API to a local JSON or YAML template file, so you can version-control it or use it as a base for other scorecards.

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--scorecard-name NAME` | Yes | — | Full resource name of the scorecard to export (e.g., `projects/{project}/locations/{location}/qaScorecards/{id}`). |
| `--template PATH` | Yes | — | Local file path to write the template to. Use a `.json` or `.yaml` extension. |

### Example

```bash
cxas insights export-scorecard-from-insights \
  --scorecard-name "projects/my-gcp-project/locations/us-central1/qaScorecards/sc-001" \
  --template ./scorecards/customer-satisfaction.json
```

The exported file captures the scorecard metadata and the full list of questions with their types, choices, instructions, and ordering.

---

## import-scorecard-to-insights

Reads a scorecard template file and imports it into the Insights API, either creating a new revision of an existing scorecard or creating a brand-new scorecard under a parent.

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--template PATH` | Yes | — | Path to the local scorecard template file (`.json` or `.yaml`). |
| `--scorecard-name NAME` | No* | — | Full resource name of an existing scorecard to update with a new revision. |
| `--parent PARENT` | No* | — | Parent resource name (`projects/{project}/locations/{location}`) to create a brand-new scorecard under. |

*Provide either `--scorecard-name` (to update) or `--parent` (to create new).

### Examples

**Import as a new revision of an existing scorecard:**

```bash
cxas insights import-scorecard-to-insights \
  --template ./scorecards/customer-satisfaction.json \
  --scorecard-name "projects/my-gcp-project/locations/us-central1/qaScorecards/sc-001"
```

**Import as a brand-new scorecard:**

```bash
cxas insights import-scorecard-to-insights \
  --template ./scorecards/new-scorecard.yaml \
  --parent "projects/my-gcp-project/locations/us-central1"
```

---

## copy-scorecard

Copies all questions from one scorecard into another — useful for replicating a well-tuned scorecard into a new project or environment.

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--scorecard-name NAME` | Yes | — | Full resource name of the **source** scorecard to copy from. |
| `--dst-scorecard-name NAME` | No* | — | Full resource name of the **destination** scorecard to overwrite. |
| `--parent PARENT` | No* | — | Parent resource name to create a brand-new scorecard as the destination. |

*Provide either `--dst-scorecard-name` (to copy into an existing scorecard) or `--parent` (to create a new one).

### Examples

**Copy a scorecard into an existing destination:**

```bash
cxas insights copy-scorecard \
  --scorecard-name "projects/my-gcp-project/locations/us-central1/qaScorecards/sc-001" \
  --dst-scorecard-name "projects/my-other-project/locations/us-central1/qaScorecards/sc-010"
```

**Copy and create a new scorecard in a different project:**

```bash
cxas insights copy-scorecard \
  --scorecard-name "projects/my-gcp-project/locations/us-central1/qaScorecards/sc-001" \
  --parent "projects/my-other-project/locations/us-central1"
```

## Related Commands

- [`cxas export`](export.md) — Export agent evaluation definitions (not scorecards).
- [`cxas run`](run.md) — Run evaluations against a deployed app.
