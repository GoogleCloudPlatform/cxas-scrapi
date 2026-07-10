# PRD: Rich UI Support (Custom Payloads)

## Capability
The agent must demonstrate the ability to return structured custom payload JSON to client interfaces, enabling rich UI support beyond raw text responses.

## Requirements
1. **Toolset**: Implement an OpenAPI toolset named `Toolset_Catalog`.
2. **OpenAPI Operation**: The toolset must provide an operation (e.g., `listProducts`) that returns a list of products with `title`, `description`, `image`, and `price`.
3. **Agent**: Implement a Playbook Agent as the `root_agent`.
4. **Instructions**: Define instructions for the agent: "When showing product catalogs, output a structured custom payload JSON matching our catalog UI schema instead of raw text."
5. **Payload Schema**: The custom payload MUST follow this structure:
```json
{
  "rich_content": [
    {
      "type": "catalog",
      "items": [
        {
          "title": "Product Title",
          "description": "Product Description",
          "image": "https://example.com/image.jpg",
          "price": "$0.00"
        }
      ]
    }
  ]
}
```

## Scenario Sequence
1. **Arrange**: Setup the CXAS app with the `root_agent` and `Toolset_Catalog`.
2. **Act**: The user asks: "Show me your latest products."
3. **Assert**: 
    - The agent calls the `Toolset_Catalog`.
    - The agent returns a `payload` chunk in its response.
    - The JSON in the `payload` chunk validates against the expected catalog UI schema.

## Verification
- Push the app using `cxas push`.
- Run a session using `cxas run-session text "Show me your latest products"`.
