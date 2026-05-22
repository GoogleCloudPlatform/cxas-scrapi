---
name: cxas-drawio-ingestor
description: "Extracts conversational transcripts from .drawio XML files."
---

# Draw.io Ingestor Skill

Use this skill when you need to extract dialogue turns and conversation flows
from `.drawio` diagram files.

## Protocol

`.drawio` files are typically stored as XML. Dialogue turns are often embedded
within the file in encoded or plain text format inside specific XML elements.

1.  **Read File**: Open the `.drawio` file as a text file.
2.  **XML Parsing**: Look for `<mxCell>` elements or similar tags that contain
    `value` attributes.
3.  **Extraction**: Extract the text content from the `value` attributes. Look
    for patterns like "Agent: ..." and "User: ..." or "Provider: ...".
4.  **Cleanup**: Strip out HTML tags (like `<b>`, `<font>`, `&lt;br&gt;`) that
    are often used for formatting inside `.drawio` labels. Note that XML
    attribute values are often HTML-escaped (e.g., `&lt;b&gt;`). You should
    first unescape HTML entities before stripping tags to ensure clean text
    extraction.
5.  **Output**: Format the extracted dialogue turns into a structured list. If
    this skill is used during Phase 1 (Discovery) of the Robust Extraction
    Protocol, output the summarized intents. If used during Phase 4 (Execution),
    follow the `transcript_schema.yml` contract.

## Example

A `mxCell` might look like this: `xml <mxCell id="1"
value="&lt;b&gt;Agent:&lt;/b&gt;
&quot;Hello!&quot;&lt;br&gt;&lt;b&gt;User:&lt;/b&gt; &quot;Hi!&quot;" ...>`

You should extract: - Agent: Hello! - User: Hi!
