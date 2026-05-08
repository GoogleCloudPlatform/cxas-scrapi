# API Schemas: Apps

### App
Top-level container for agents.

- **name** (string): Identifier. Format: `projects/{project}/locations/{location}/apps/{app}`
- **displayName** (string): [required] Display name.
- **description** (string): Description.
- **rootAgent** (string): Root agent entry point. Format: `projects/.../agents/{agent}`
- **languageSettings** (-> LanguageSettings)
- **timeZoneSettings** (-> TimeZoneSettings)
- **loggingSettings** (-> LoggingSettings)
- **modelSettings** (-> ModelSettings): Default LLM settings. Agents can override.
- **toolExecutionMode** (enum: `PARALLEL` | `SEQUENTIAL`): Default: PARALLEL.
- **evaluationMetricsThresholds** (-> EvaluationMetricsThresholds): See `evaluations.md`.
- **variableDeclarations** (array[-> AppVariableDeclaration])
- **globalInstruction** (string): Shared instruction across all agents.
- **guardrails** (array[string]): Guardrail resource names.
- **evaluationPersonas** (array[-> EvaluationPersona]): Max 30. See `evaluations.md`.
- **evaluationSettings** (-> EvaluationSettings): See `evaluations.md`.

### AppVariableDeclaration
- **name** (string): [required] Must start with letter/underscore.
- **description** (string): [required]
- **schema** (-> Schema): [required]

### ModelSettings
- **model** (string): LLM model name. Inherits from parent if not set.
- **temperature** (number): Lower = predictable, higher = creative.

### LanguageSettings
- **defaultLanguageCode** (string)
- **supportedLanguageCodes** (array[string])
- **enableMultilingualSupport** (boolean)

### TimeZoneSettings
- **timeZone** (string): IANA format (e.g., `America/Los_Angeles`).
