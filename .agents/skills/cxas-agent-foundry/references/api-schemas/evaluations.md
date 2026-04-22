# API Schemas: Evaluations

## Structure

### Evaluation
- **name** (string): Format: `projects/.../evaluations/{evaluation}`
- **displayName** (string): [required] Unique within app.
- **description** (string)
- **tags** (array[string])
- **golden** (-> EvaluationGolden): Golden config.
- **scenario** (-> EvaluationScenario): Scenario config.
- **evaluationStatus** (enum: `PASS` | `FAIL`): Output only.
- **aggregatedMetrics** (-> AggregatedMetrics): Output only.
- **lastCompletedResult** (-> EvaluationResult): Output only.
- **invalid** (boolean): Output only. True if eval references deleted tool/agent.

### EvaluationConfig
- **evaluationChannel** (enum: `TEXT` | `AUDIO`)
- **toolCallBehaviour** (enum: `REAL` | `FAKE`)

### EvaluationSettings
App-level settings.

- **scenarioConversationInitiator** (enum: `USER` | `AGENT`)
- **goldenRunMethod** (enum: `STABLE` | `NAIVE`): Default golden method.
- **goldenEvaluationToolCallBehaviour** (enum: `REAL` | `FAKE`)
- **scenarioEvaluationToolCallBehaviour** (enum: `REAL` | `FAKE`)

## Goldens

### EvaluationGolden
- **turns** (array[-> EvaluationGoldenTurn]): [required]
- **evaluationExpectations** (array[string]): Expectation resource names.

### EvaluationGoldenTurn
- **steps** (array[-> EvaluationStep]): [required]

### EvaluationStep
- **userInput** (-> SessionInput)
- **agentTransfer** (-> AgentTransfer)
- **expectation** (-> EvaluationGoldenExpectation)

### EvaluationGoldenExpectation
- **toolCall** (-> ToolCall): Expected tool call.
- **toolResponse** (-> ToolResponse): Expected tool response.
- **agentResponse** (-> Message): Expected agent response.
- **agentTransfer** (-> AgentTransfer): Expected transfer.
- **updatedVariables** (object): Expected variable updates.
- **mockToolResponse** (-> ToolResponse): Response to mock. Unspecified params hallucinated.
- **note** (string): Label for reporting (e.g., "Check_Payment_Tool_Called").

## Scenarios

### EvaluationScenario
- **task** (string): [required] Task description.
- **userFacts** (array[-> EvaluationScenarioUserFact]): Facts known to sim user.
- **maxTurns** (integer): Max turns. Default: until task complete.
- **rubrics** (array[string]): [required] Rubrics to score against.
- **scenarioExpectations** (array[-> EvaluationScenarioExpectation]): [required]
- **variableOverrides** (object): Session parameter overrides.
- **userGoalBehavior** (enum: `USER_GOAL_SATISFIED` | `USER_GOAL_REJECTED` | `USER_GOAL_IGNORED`)
- **evaluationExpectations** (array[string]): Expectation resource names.

### EvaluationScenarioExpectation
- **toolExpectation** (-> EvaluationScenarioExpectationToolExpectation)
- **agentResponse** (-> Message)

### EvaluationScenarioExpectationToolExpectation
- **expectedToolCall** (-> ToolCall): [required]
- **mockToolResponse** (-> ToolResponse): [required]

### EvaluationScenarioUserFact
- **name** (string): [required]
- **value** (string): [required]

## LLM-Judged Expectations

### EvaluationExpectation
- **name** (string): Format: `projects/.../evaluationExpectations/{evaluation_expectation}`
- **displayName** (string): [required] Unique within app.
- **llmCriteria** (-> EvaluationExpectationLlmCriteria)
- **tags** (array[string])

### EvaluationExpectationLlmCriteria
- **prompt** (string): [required] Instructions for the LLM judge.

## Personas

### EvaluationPersona
- **name** (string): [required]
- **displayName** (string): [required] Unique within app.
- **personality** (string): [required] Behavioral instructions.
- **speechConfig** (-> EvaluationPersonaSpeechConfig)

### EvaluationPersonaSpeechConfig
- **speakingRate** (number): 1.0 = normal, 0.8 = slow, 1.5 = fast.
- **environment** (enum: `CALL_CENTER` | `TRAFFIC` | `KIDS_NOISE` | `CAFE`)
- **voiceId** (string): e.g., `en-US-Wavenet-D`.

## Scoring Thresholds

### EvaluationMetricsThresholds
- **goldenEvaluationMetricsThresholds** (-> GoldenEvaluationMetricsThresholds)
- **goldenHallucinationMetricBehavior** (enum: `DISABLED` | `ENABLED`)
- **scenarioHallucinationMetricBehavior** (enum: `DISABLED` | `ENABLED`)

### GoldenEvaluationMetricsThresholds
- **turnLevelMetricsThresholds** (-> TurnLevelMetricsThresholds)
- **expectationLevelMetricsThresholds** (-> ExpectationLevelMetricsThresholds)
- **toolMatchingSettings** (-> ToolMatchingSettings)

### TurnLevelMetricsThresholds
- **semanticSimilaritySuccessThreshold** (integer): 0-4. Default: >= 3.
- **overallToolInvocationCorrectnessThreshold** (number): 0-1. Default: 1.0.
- **semanticSimilarityChannel** (enum: `TEXT` | `AUDIO`)

### ExpectationLevelMetricsThresholds
- **toolInvocationParameterCorrectnessThreshold** (number): 0-1. Default: 1.0.

### ToolMatchingSettings
- **extraToolCallBehavior** (enum: `FAIL` | `ALLOW`): Default: FAIL.

## Results

### EvaluationResult
- **name** (string): Format: `projects/.../evaluations/{evaluation}/results/{result}`
- **evaluationStatus** (enum: `PASS` | `FAIL`): Output only.
- **executionState** (enum: `RUNNING` | `COMPLETED` | `ERROR`): Output only.
- **goldenResult** (-> GoldenResult)
- **scenarioResult** (-> ScenarioResult)
- **evaluationMetricsThresholds** (-> EvaluationMetricsThresholds): Thresholds used.
- **config** (-> EvaluationConfig)
- **goldenRunMethod** (enum: `STABLE` | `NAIVE`)
- **persona** (-> EvaluationPersona)
- **errorInfo** (-> EvaluationErrorInfo)

### GoldenResult (EvaluationResultGoldenResult)
- **turnReplayResults** (array[-> TurnReplayResult])
- **evaluationExpectationResults** (array[-> EvaluationExpectationResult])

### TurnReplayResult (EvaluationResultGoldenResultTurnReplayResult)
- **expectationOutcome** (array[-> GoldenExpectationOutcome])
- **hallucinationResult** (-> HallucinationResult)
- **semanticSimilarityResult** (-> SemanticSimilarityResult)
- **overallToolInvocationResult** (-> OverallToolInvocationResult)
- **toolOrderedInvocationScore** (number)

### SemanticSimilarityResult
- **score** (integer): 0-4. 4=Fully Consistent, 3=Mostly, 2=Partial, 1=Largely Inconsistent, 0=Contradictory.
- **label** (string)
- **explanation** (string)
- **outcome** (enum: `PASS` | `FAIL`)

### HallucinationResult
- **score** (integer): -1, 0, or 1. 1=Justified, 0=Not Justified, -1=No Claim.
- **label** (string)
- **explanation** (string)

### OverallToolInvocationResult
- **toolInvocationScore** (number): Percent of expected tools invoked.
- **outcome** (enum: `PASS` | `FAIL`)

### GoldenExpectationOutcome
- **expectation** (-> EvaluationGoldenExpectation)
- **outcome** (enum: `PASS` | `FAIL`)
- **observedToolCall** (-> ToolCall)
- **observedToolResponse** (-> ToolResponse)
- **observedAgentResponse** (-> Message)
- **toolInvocationResult** (-> ToolInvocationResult)

### ToolInvocationResult
- **parameterCorrectnessScore** (number): Percent of expected params present.
- **outcome** (enum: `PASS` | `FAIL`)
- **explanation** (string)

### ScenarioResult (EvaluationResultScenarioResult)
- **conversation** (string)
- **task** (string)
- **expectationOutcomes** (array[-> ScenarioExpectationOutcome])
- **rubricOutcomes** (array[-> RubricOutcome])
- **taskCompleted** (boolean): Composite of all checks.
- **allExpectationsSatisfied** (boolean)
- **userGoalSatisfactionResult** (-> UserGoalSatisfactionResult)

### UserGoalSatisfactionResult
- **score** (integer): -1, 0, or 1. 1=Satisfied, 0=Not Satisfied, -1=Unspecified.

### RubricOutcome
- **rubric** (string)
- **score** (number)
- **scoreExplanation** (string)

### EvaluationExpectationResult
- **evaluationExpectation** (string): Resource name.
- **prompt** (string)
- **outcome** (enum: `PASS` | `FAIL`)
- **explanation** (string)

### EvaluationErrorInfo
- **errorType** (enum: `RUNTIME_FAILURE` | `CONVERSATION_RETRIEVAL_FAILURE` | `METRIC_CALCULATION_FAILURE` | `EVALUATION_UPDATE_FAILURE` | `QUOTA_EXHAUSTED` | `USER_SIMULATION_FAILURE`)
- **errorMessage** (string)
- **sessionId** (string)

## Runs

### EvaluationRun
- **name** (string): Format: `projects/.../evaluationRuns/{evaluationRun}`
- **evaluationType** (enum: `GOLDEN` | `SCENARIO` | `MIXED`)
- **state** (enum: `RUNNING` | `COMPLETED` | `ERROR`)
- **progress** (-> EvaluationRunProgress)
- **config** (-> EvaluationConfig)
- **runCount** (integer)
- **goldenRunMethod** (enum: `STABLE` | `NAIVE`)

### EvaluationRunProgress
- **totalCount** (integer)
- **completedCount** (integer)
- **passedCount** (integer)
- **failedCount** (integer)
- **errorCount** (integer)

### RunEvaluationRequest
- **app** (string): [required] Format: `projects/.../apps/{app}`
- **evaluations** (array[string]): Evaluations to run.
- **evaluationDataset** (string): Dataset to run.
- **config** (-> EvaluationConfig)
- **runCount** (integer): Default: 1 per golden, 5 per scenario.
- **goldenRunMethod** (enum: `STABLE` | `NAIVE`): Default: STABLE.
