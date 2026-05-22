# API Schemas: Tools

### Tool
- **name** (string): Identifier. Format: `projects/.../tools/{tool}`
- **displayName** (string): Output only. Derived from tool type's name.
- **executionType** (enum: `SYNCHRONOUS` | `ASYNCHRONOUS`)
- **pythonFunction** (-> PythonFunction)
- **clientFunction** (-> ClientFunction)
- **systemTool** (-> SystemTool)
- **toolFakeConfig** (-> ToolFakeConfig): Fake mode config.

### PythonFunction
- **name** (string): Function name. Must match function in pythonCode. Case sensitive.
- **pythonCode** (string): [required] Python code file path (e.g., "tools/<name>/python_function/python_code.py").
- **description** (string): Output only. Parsed from docstring.

### ClientFunction
Client-side function -- agent invokes, client executes and returns result.

- **name** (string): [required]
- **description** (string)
- **parameters** (-> Schema): Parameter schema.
- **response** (-> Schema): Response schema.

### SystemTool
Pre-defined: `end_session`, `customize_response`, `transfer_to_agent`.

- **name** (string): [required]
- **description** (string): Output only.

### Toolset
Represents a collection of tools, currently supporting OpenAPI, MCP, or Connector toolsets.

- **name** (string): Identifier. Format: `projects/.../locations/.../apps/.../toolsets/{toolset_id}`
- **displayName** (string): [required] User-friendly name. Must be snake_case.
- **description** (string): Description of the toolset.
- **openApiToolset** (-> OpenApiToolset): Configuration for OpenAPI toolset.

### OpenApiToolset
- **openApiSchema** (string): [required] Local path to the OpenAPI schema file (relative to app root, e.g., `"toolsets/<name>/open_api_toolset/open_api_schema.yaml"`).
- **apiAuthentication** (-> ApiAuthentication): Optional authentication configuration.

### ApiAuthentication
Oneof authentication configuration:
- **apiKeyConfig** (-> ApiKeyConfig)
- **oauthConfig** (-> OAuthConfig)
- **serviceAccountAuthConfig** (-> ServiceAccountAuthConfig)
- **serviceAgentIdTokenAuthConfig** (-> ServiceAgentIdTokenAuthConfig)

### ApiKeyConfig
- **keyName** (string): [required] Name of the header or query parameter (e.g., `"Authorization"`).
- **requestLocation** (enum: `HEADER` | `QUERY_STRING`): [required] Where to inject the key.
- **apiKeySecretVersion** (string): [required] Secret Manager secret version resource name (e.g., `"projects/.../secrets/.../versions/..."`).

### OAuthConfig
- **oauthGrantType** (enum: `CLIENT_CREDENTIAL`): [required] OAuth grant type.
- **clientId** (string): [required] Client ID.
- **clientSecretVersion** (string): [required] Secret Manager secret version resource name for client secret.
- **tokenEndpoint** (string): [required] Token endpoint URL.
- **scopes** (list of strings): OAuth scopes.

### ServiceAccountAuthConfig
- **serviceAccount** (string): [required] Service account email for impersonation.

### ServiceAgentIdTokenAuthConfig
- (empty object)

### ToolCall
- **tool** (string): Tool resource name.
- **id** (string): Unique ID for matching with ToolResponse.
- **args** (object): Input parameters as JSON.

### ToolResponse
- **tool** (string): Tool resource name.
- **id** (string): Matching ID.
- **response** (object): [required] Use `"output"` key for response, `"error"` for errors.

### CodeBlock
Python code for tool fake mode.

- **pythonCode** (string): [required] `def fake_tool_call(tool, input, callback_context) -> Optional[dict]`. Return `None` to use real tool.
