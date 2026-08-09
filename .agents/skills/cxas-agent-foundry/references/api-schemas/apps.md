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
- **audioProcessingConfig** (-> AudioProcessingConfig): Voice agents only.
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
- **supportedLanguageCodes** (array[string]): Additional locales. Does not repeat the default.
- **enableMultilingualSupport** (boolean)

### AudioProcessingConfig
Only consulted on a composite model app, which cascades into a separate synthesis step. A native audio model speaks directly and never reaches it.

- **synthesizeSpeechConfigs** (map[string -> SynthesizeSpeechConfig]): Keyed by language code, matched case-insensitively. The only fallback is to the root language, so an `es` entry serves `es-US` but an `en-US` entry does not. A locale with neither its own key nor its root gets nothing. Lint rule A007 checks the coverage.

### SynthesizeSpeechConfig
- **voice** (string): Voice name, e.g. `en-US-Chirp3-HD-Zephyr`. Service picks one from the language code if unset.
- **instruction** (string): Style prompt. Audio profile and director's note steering persona, pacing, intonation and accent. Applies "when using a generative model", so it needs `model` set below. Chirp3-HD is not generative and ignores it.
- **speakingRate** (number): `1.0` is the default. Above speeds up, below slows down.
- **model** (string): Synthesis model. One supported value, `gemini-3.1-flash-tts-preview`. Chirp3-HD is used when empty. Setting it forces bare Gemini voice names (`Zephyr`, not `en-US-Chirp3-HD-Zephyr`).
- **voiceSampleGcsUri** (string)
- **consentAudioGcsUri** (string)

### TimeZoneSettings
- **timeZone** (string): IANA format (e.g., `America/Los_Angeles`).
