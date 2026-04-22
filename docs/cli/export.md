# cxas export

`cxas export` lets you download an evaluation definition from CX Agent Studio to a local YAML or JSON file, so you can version-control it, review it, or use it as a template for new evaluations.

## Usage

```
cxas export --app-name APP --evaluation-id EVAL
            [--format yaml|json] [--output PATH]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | Yes | — | Full resource name of the app that owns the evaluation (e.g., `projects/{project}/locations/{location}/apps/{app}`). |
| `--evaluation-id EVAL` | Yes | — | Full resource name of the evaluation to export (e.g., `projects/{project}/locations/{location}/apps/{app}/evaluations/{eval}`). |
| `--format yaml\|json` | No | `yaml` | Output format. Use `yaml` for human-readable files or `json` for machine consumption. |
| `--output PATH` | No | — | File path to write the exported evaluation to. If omitted, the content is printed to standard output. |

## Examples

**Export an evaluation to a YAML file:**

```bash
cxas export \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --evaluation-id projects/my-gcp-project/locations/us-central1/apps/abc123/evaluations/eval-001 \
  --output evaluations/billing-golden.yaml
```

**Export in JSON format and print to stdout (useful for piping):**

```bash
cxas export \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --evaluation-id projects/my-gcp-project/locations/us-central1/apps/abc123/evaluations/eval-001 \
  --format json
```

**Export and immediately inspect:**

```bash
cxas export \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --evaluation-id projects/my-gcp-project/locations/us-central1/apps/abc123/evaluations/eval-001 \
  --format yaml | less
```

## Related Commands

- [`cxas push-eval`](push-eval.md) — Upload evaluation definitions from a YAML file back to an app.
- [`cxas run`](run.md) — Run evaluations against an app.
- [`cxas apps`](apps.md) — List apps to find resource names.
