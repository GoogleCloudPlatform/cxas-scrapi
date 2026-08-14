The goal of this project is to evaluate agent building bots.

Rules:

*   engineering excellency: unit test coverage, clean interfaces, the state is
    cleanly and explicitly represented, modular code, OOP, fail fast.

Every Git commit or Pull Request (PR) description should contain a summary of the evaluation results. Local visual HTML reports are compiled under the `skill_eval/reports/` directory. Before providing any link to the user, you MUST always verify the
existence and content of that file (e.g. check index.html). No linter warnings
are allowed.

## Smoke Test (Fake Head & Fast Model)

To quickly verify the benchmarking infrastructure without running heavy agent
heads, use the **Fake Head** combined with a lightweight Gemini model.

```bash
python3 run_benchmark.py \
  --run_fake=True --scenario_path=scenarios/clone_app.yaml
```
