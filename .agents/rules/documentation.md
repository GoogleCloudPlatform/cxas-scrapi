---
trigger: always_on
description: "Require documentation updates whenever a new feature is added or an existing feature is modified."
---

# Documentation Updates for Features and Enhancements

- **Mandatory Documentation Updates**: Whenever a new feature, CLI command, parameter, flag, or SDK capability is introduced—or an existing feature is modified or enhanced—the corresponding documentation MUST be updated as part of the same pull request or commit.
- **Surfaces to Review and Update**:
  - **CLI Reference (`docs/cli/`)**: When adding or altering CLI commands, arguments, or options, update or create the corresponding markdown documentation under `docs/cli/` with usage examples and flag descriptions.
  - **Guides & Patterns (`docs/guides/`, `docs/patterns/`, `docs/tutorials/`)**: When adding new workflows, evaluation capabilities, migration tools, or architectural patterns, update the relevant guides or add new walkthroughs.
  - **API Reference (`docs/api/`)**: When adding or changing public SDK interfaces, classes, or helper functions, ensure the API reference documentation is added or kept in sync.
  - **MkDocs Navigation (`mkdocs.yml`)**: Whenever new documentation pages are created under `docs/`, register them under `nav` in `mkdocs.yml` so they appear in site navigation.
  - **Workspace & Project Overviews (`AGENTS.md` & `README.md`)**: Keep `AGENTS.md` (specifically `## CLI Features` and `## Available Skills`) and `README.md` in sync with new functionality.
  - **In-Code Help & Docstrings**: Ensure all CLI options, arguments, and public functions provide clear, descriptive help text and Google-style docstrings.
- **Verification**: Run `mkdocs build --strict` when documentation files are added or modified to ensure there are no broken links, missing references, or syntax errors.
