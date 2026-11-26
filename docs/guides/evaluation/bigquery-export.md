# Eval Results in BigQuery

`cxas evals report` can export every eval result to BigQuery, so you can follow pass rate, volume, duration and tags over time, find flaky cases and spot the commit that introduced a regression.

## Setup

1. Create a dataset once, in the location your data must stay in. The export never creates it implicitly, because a dataset's location can't change later:

   ```bash
   bq mk --dataset --location=EU my-project:cxas_evals
   ```

2. Grant the identity that runs the evals (your user or the CI service account):

   - `roles/bigquery.dataEditor` on the dataset (create the tables and views, write rows);
   - `roles/bigquery.jobUser` on the project (run the load and query jobs).

The export uses the same credentials as the rest of cxas-scrapi: `CXAS_OAUTH_TOKEN` if it's set, otherwise Application Default Credentials.

## Export results

Add `--bq-dataset` to the command you already run:

```bash
cxas evals report --app-name projects/P/locations/L/apps/A --app-dir . \
  --output-dir eval-reports --run --suite-name nightly \
  --bq-dataset my-project.cxas_evals
```

It also works on results already on disk (no `--run`), for example in a later CI step:

```bash
cxas evals report --output-dir eval-reports --simulation-dir evals/simulations \
  --suite-name nightly --bq-dataset my-project.cxas_evals
```

| Option | Description |
|---|---|
| `--bq-dataset project.dataset` | Dataset to write to. Tables and views are created if missing. |
| `--bq-create-dataset` | Also create the dataset if it doesn't exist. Requires `--bq-location`, and permission to create datasets in the project. |
| `--bq-location EU` | Location for `--bq-create-dataset`; otherwise checked against the existing dataset. |

Behaviour:

- **Every export adds one run.** Each call gets its own `run_key` (also written to `EVAL_STAMP-<suite>.txt`), so a retried CI job adds a run next to the first attempt instead of overwriting it. Export a set of result files once: a second export of the same files adds a second run. [Example queries](#example-queries) shows how to delete a run.
- **Errors are not failures.** A result that couldn't be evaluated is stored as `ERROR` and excluded from the pass rate. For simulations and goldens, that's a failed result that carries an error message (timeout, quota, crash). For tool and callback tests, a failed assertion also has a message, so only a result with the status `ERROR` counts as an error.
- **The gate is never affected.** If the export fails, a warning is printed (shown as an annotation in GitHub Actions) and the report's result and exit code are unchanged.
- **No half-written runs.** The tables and views are prepared first, then the results are loaded before the run row. The views only read runs that have both.
- **The conversation stays out.** Transcripts, traces and audio are not exported. `session_id` lets you open the conversation where it's stored.
- **Run context** comes from the CI environment (`git_sha`, `git_ref`, `ci_run_id`, `ci_job_id`, `ci_attempt`). GitHub Actions and GitLab CI are recognised; `ci_job_id` is filled on GitLab CI and `ci_attempt` on GitHub Actions. Outside CI, `git_sha`, `git_ref` and `ci_run_id` are `local`.
- **Schema changes are additive.** Later versions only add nullable columns, so older and newer clients can share the tables.

## Finding the export's BigQuery activity

The BigQuery calls of the export use the user agent `cxas-scrapi/<version>:eval-bq-export`, and its load jobs have the label `cxas-scrapi: eval-bq-export`. Filter on the label in the BigQuery jobs list or the billing export to see what the feature runs and costs in your project.

## Tables and views

The tables use these terms:

- a **run** is one export of the evals, with its own `run_key`;
- a **case** is one simulation, golden, tool test or callback test (`test_id`);
- an **attempt** is one repetition of a case within a run;
- a **result** is the outcome of one attempt. The views count results as `executions`.

`eval_runs` has one row per run: `run_key`, `started_at`, `finished_at`, `eval_duration_s`, `app_name`, `suite_name`, `modality`, `runs` (the attempts asked for per case, from `--runs`), `min_run_passes`, `min_pass_rate`, `pass_rate`, `total_cases`, `passed_cases`, `gate_passed`, git and CI context, `cxas_scrapi_version` and `report_url`.

How the run times are set:

| Column | With `--run` | From existing result files |
|---|---|---|
| `eval_duration_s` | Measured wall-clock time of the evals | The `wall_clock_s` recorded in the simulation results file, or empty if there is none |
| `started_at` | When the command started | The timestamp in the name of the simulation results file (`..._YYYYMMDD_HHMMSS`, written by `--timestamped` when the run starts). Without one: `finished_at` − `eval_duration_s` |
| `finished_at` | `started_at` + `eval_duration_s` | `started_at` + `eval_duration_s`. Without a timestamp in the file name: the time the result files were last modified |

- Result files written by `cxas evals report --run` don't record the run time. To record it, pass `--bq-dataset` to the command that runs the evals. If the duration is unknown, `started_at` and `finished_at` are the same.
- The file-name timestamp is in the local time of the machine that ran the evals, and the export reads it in its own local time. Export on the same machine or runner image as the evals, or with the same time zone, to get the right time.

`pass_rate`, `total_cases` and `passed_cases` are the gate's numbers: a case passes when enough of its attempts pass, and a case that errored counts as failed. They can differ from a pass rate computed over the rows of `eval_results`, where every attempt counts and errors are left out. `gate_passed` is empty when no `--min-pass-rate` was given. Pass rates in the tables and views are fractions between 0 and 1.

`eval_results` has one row per result. One exception: goldens loaded from a platform run with `--golden-run` and `--min-run-passes` are already reduced to one result per case.

| Column | Description |
|---|---|
| `run_key`, `started_at` | Join key to `eval_runs` (suite, commit, CI context) and partition column |
| `category` | `simulation`, `golden`, `tool` or `callback` |
| `name`, `test_id`, `attempt` | Case name; a unique ID (callbacks: `agent/callback_type/test`, tools: `tool/test`); repeat index |
| `tags` | For simulations, the `tags` of the case definition in `--simulation-dir` (default `evals/simulations/`). Empty for the other categories, and when the definitions aren't found |
| `status` | `PASS`, `FAIL` or `ERROR` |
| `duration_s` | Duration of the result in seconds (tool tests: latency) |
| `turns` | Number of conversation turns |
| `goals_met`, `goals_total` | Simulation goals completed, out of the goals defined |
| `expectations_met`, `expectations_total` | Expectations met, out of the expectations checked |
| `session_id` | Conversation session, to open the transcript |
| `failed_expectations`, `error` | Expectations not met; error message |
| `details` | A few more fields of the result, as JSON: its raw status, the tool (with its latency) or the agent and callback type tested, the status and justification of each goal and expectation, and the naturalness score. A justification can quote the conversation |

Both tables are partitioned by day on `started_at`. Two views come with them:

- `v_eval_daily`: per day, suite and category: runs, executions, PASS/FAIL/ERROR counts, pass rate, goal completion rate, expectation-met rate, average turns, p50/p90 and total duration;
- `v_eval_daily_by_tag`: per day, suite, category and tag: runs, executions, PASS/FAIL/ERROR counts, pass rate and p50 duration. A result with several tags counts once per tag.

The export keeps the definition of these two views in line with the installed version, so don't edit them: create your own views next to them.

## Example queries

Daily pass rate per suite and category, all attempts:

```sql
SELECT day, suite_name, category, runs, executions, pass_rate, p50_duration_s
FROM `my-project.cxas_evals.v_eval_daily`
ORDER BY day DESC, suite_name, category;
```

Daily pass rate per tag:

```sql
SELECT day, suite_name, tag, executions, pass_rate
FROM `my-project.cxas_evals.v_eval_daily_by_tag`
WHERE category = 'simulation'
ORDER BY day DESC, suite_name, pass_rate;
```

Pass rate on the first attempt of each CI run only (not biased by retries):

```sql
WITH first_runs AS (
  SELECT run_key, started_at, suite_name
  FROM `my-project.cxas_evals.eval_runs`
  WHERE ci_run_id != 'local'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY ci_run_id, suite_name ORDER BY started_at) = 1
)
SELECT DATE(r.started_at) AS day, r.suite_name,
  SAFE_DIVIDE(COUNTIF(x.status = 'PASS'),
              COUNTIF(x.status IN ('PASS', 'FAIL'))) AS pass_rate
FROM first_runs AS r
JOIN `my-project.cxas_evals.eval_results` AS x USING (run_key)
GROUP BY day, r.suite_name
ORDER BY day DESC;
```

Flaky cases: both PASS and FAIL on the same commit in the last 14 days:

```sql
SELECT x.category, x.test_id, r.git_sha,
  COUNTIF(x.status = 'PASS') AS passes, COUNTIF(x.status = 'FAIL') AS fails
FROM `my-project.cxas_evals.eval_results` AS x
JOIN `my-project.cxas_evals.eval_runs` AS r USING (run_key)
WHERE x.started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
  AND r.git_sha != 'local'
GROUP BY x.category, x.test_id, r.git_sha
HAVING passes > 0 AND fails > 0
ORDER BY fails DESC;
```

Remove a run, for example one that was exported twice (take the `run_key` from the export's output or from `eval_runs`):

```sql
DELETE FROM `my-project.cxas_evals.eval_results` WHERE run_key = '<run_key>';
DELETE FROM `my-project.cxas_evals.eval_runs` WHERE run_key = '<run_key>';
```
