# cxas migrate

Use `cxas migrate` to migrate resources from legacy systems to CX Agent Studio.

## Subcommands

### `dfcx`

Launch the interactive migration dashboard to migrate a Dialogflow CX agent to CXAS.

#### Usage

```bash
cxas migrate dfcx [--default-agent-name NAME]
```

#### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--default-agent-name NAME` | No | `migrated-agent` | Default name for the target agent if not specified interactively. |

#### Description

This command launches an interactive CLI dashboard that guides you through the process of migrating a Dialogflow CX agent to CXAS. It performs:
- Environment and authentication checks.
- Loading source agent from a local ZIP file or by fetching via Agent ID.
- Interactive resource selection (Playbooks and Flows).
- Automated dependency analysis and topology visualization.
- Transformation and generation of CXAS-optimized instructions, tools and other resources.
- Deployment of migrated resources and agents to your CXAS project.

#### Examples

**Launch the dashboard:**

```bash
cxas migrate dfcx
```

**Launch with a custom default target agent name:**

```bash
cxas migrate dfcx --default-agent-name my-migrated-agent
```

## Related Commands

- [`cxas pull`](pull.md) — Download a CXAS app after migration to inspect or edit it.
- [`cxas push`](push.md) — Upload changes back to CXAS.
