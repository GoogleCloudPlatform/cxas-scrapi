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
