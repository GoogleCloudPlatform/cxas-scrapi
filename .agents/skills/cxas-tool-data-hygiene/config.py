#!/usr/bin/env python3
"""Centralized Configuration File for the CXAS Tool Data Hygiene Skill (`cxas-tool-data-hygiene`).

================================================================================
SINGLE PLACE TO UPDATE VARIABLES BEFORE RUNNING THE SKILL
================================================================================
Team members and LLM agents ONLY need to update the variables in Section 1 below
before running the Tool Data Hygiene analysis. You do NOT need to modify any
other file in the skill folder.
"""

from typing import Optional

# ==============================================================================
# 1. TARGET AGENT & TOOL VARIABLES (UPDATE THESE BEFORE RUNNING)
# ==============================================================================

# The exact sub-agent role name as logged in BigQuery (`role` column).
# Examples: "BYOD", "VZ_Billing_Agent", "VZ_Account_Mgt", "VZ_Trouble_Shooter",
#           "Equipment_upgrade", "Activation", "VZ_Disconnect_Agent"
AGENT_ROLE: str = "Replacement_Device"

# The exact tool display name to analyze (`tool_call_display_name` / `$.toolResponse.displayName`).
# Examples: "get_addLine_createCart_byod", "addsim_byod", "Byod_checkOrder",
#           "submit_fee_waiver", "bill_cycle_change_init", "disconnect_webhook"
TOOL_NAME: str = "async_replacement_activation_device_wrapper"

# Start date filter for BigQuery production logs in 'YYYY-MM-DD' format.
# Typically set to the last 7 to 14 days of production traffic.
START_DATE: str = "2026-10-02"

# Maximum number of rows to fetch from BigQuery (SOP standard: 500).
ROW_LIMIT: int = 500

# Optional: If the sub-agent's folder name under `<LOCAL_AGENT_APP_DIR>/agents/`
# differs from `AGENT_ROLE` in BigQuery, specify it here. Leave as `None` to
# auto-resolve from `AGENT_ROLE`.
LOCAL_SUB_AGENT_FOLDER: Optional[str] = None

# Optional: Path to a pre-exported BigQuery JSON or CSV file if running offline
# without live BigQuery execution. Leave as `None` to query BigQuery directly.
OFFLINE_BQ_FILE: Optional[str] = None


# ==============================================================================
# 2. GOOGLE CLOUD, DYNAMIC BIGQUERY SCHEMA & WORKSPACE PATH CONFIGURATION
# ==============================================================================

# BigQuery table storing CXAS / CCAI Insights conversational agent & tool execution logs.
# Can be changed to ANY BigQuery project.dataset.table across any Google CXAS deployment.
BQ_PROJECT_DATASET_TABLE: str = (
    "conversation-analysis-433113.vz_vcg_wireless_chat_nga.ccai_insights_agent_tool"
)

# ------------------------------------------------------------------------------
# 2.1 DYNAMIC BIGQUERY SCHEMA & COLUMN MAPPING (SUPPORTS ANY CXAS BQ TABLE)
# ------------------------------------------------------------------------------
# Supported `BQ_SCHEMA_MODE` values:
#   - "ccai_chunks_json" : Standard CCAI Insights / CXAS table where toolCall and
#                          toolResponse are nested inside a JSON column (e.g., `chunks`).
#   - "flat_columns"     : Any flat BigQuery table where tool name and tool response
#                          payload are stored directly in separate columns.
#   - "auto"             : Automatically inspects the BigQuery table schema via `bq show`
#                          and maps columns dynamically (falls back to configured columns).
#   - "custom_sql"       : Uses `CUSTOM_BQ_SQL_QUERY` below for arbitrary complex schemas
#                          (e.g., UNNEST arrays or multi-table JOINs).
BQ_SCHEMA_MODE: str = "ccai_chunks_json"

# Column Name Mapping (Change these if your BigQuery table uses different column names)
BQ_COL_CONVERSATION_ID: str = "conversationName"      # e.g., "conversation_id", "session_id"
BQ_COL_TIMESTAMP: str = "startTimestamp"              # e.g., "event_timestamp", "created_at", "timestamp"
BQ_COL_AGENT_ROLE: Optional[str] = "role"             # e.g., "agent_role", "sub_agent", or None if no role column exists
BQ_COL_TOOL_NAME: str = "tool_call_display_name"      # e.g., "tool_name", "function_name", "tool_display_name"
BQ_COL_PAYLOAD: str = "chunks"                        # In "ccai_chunks_json" mode: JSON column name (e.g., "chunks").
                                                      # In "flat_columns" mode: column containing the tool response JSON/string.

# JSON Path Mapping (Used when `BQ_SCHEMA_MODE` is "ccai_chunks_json" or "auto" with nested JSON)
BQ_JSON_PATH_TOOL_CALL: str = "$.toolCall"
BQ_JSON_PATH_TOOL_RESPONSE: str = "$.toolResponse"
BQ_JSON_PATH_TOOL_DISPLAY_NAME: str = "$.toolResponse.displayName"
BQ_JSON_PATH_TOOL_RESPONSE_DATA: str = "$.toolResponse.response"

# Optional Custom SQL Query Template (Used when `BQ_SCHEMA_MODE = "custom_sql"` or when non-None).
# Supports placeholders: `{table}`, `{agent_role}`, `{tool_name}`, `{start_date}`, `{row_limit}`.
# Your SELECT statement should alias the tool response payload column as `distinct_tool_response`.
CUSTOM_BQ_SQL_QUERY: Optional[str] = None

# ------------------------------------------------------------------------------
# 2.2 LOCAL WORKSPACE & OUTPUT PATHS
# ------------------------------------------------------------------------------

# Local workspace path to the exported GECX / CXAS Agent Application
LOCAL_AGENT_APP_DIR: str = "VZAmitLocal/app/Dev_-_Profile_Chat_Agent"

# Directory where generated Tool Data Hygiene reports, CSVs, and JSON audits are saved
OUTPUT_DIR: str = "InvestigationData/ToolDataHygiene"

# Reference Team Artifacts for HITL Final Documentation
TRACKER_XLSX_PATH: str = "ToolDataHygiene/VZ_Chat\xa0 Hillclimbing Tracker.xlsx"
DETAIL_DOCX_PATH: str = "ToolDataHygiene/VZ_ToolData_DetailAnalysis&Recommendation.docx"
SOP_DOCX_PATH: str = "ToolDataHygiene/Tool_Data_Hygiene_SOP_and_Process_Guide.docx"


# ==============================================================================
# 3. TOOL DATA HYGIENE THRESHOLDS & EXEMPT CATEGORIES (SOP STANDARDS)
# ==============================================================================

# Thresholds used in Step 3 to flag token bloat and deep nesting
MAX_RECOMMENDED_CHARS: int = 1500          # ~375-400 tokens; responses above this are scrutinized
MAX_RECOMMENDED_TOKENS: int = 400          # Standard heuristic (chars / 4)
MAX_RECOMMENDED_NESTING_DEPTH: int = 4     # SOP target: <= 4-5 levels of JSON nesting
COMPACT_CLEAN_CHAR_LIMIT: int = 220        # Flat responses under this size (e.g., {"result": "Pass"}) are marked Clean
HIGH_EMPTY_FIELD_RATIO_THRESHOLD: float = 0.25  # Flag when >25% of keys are "", null, [], or {}

# Standard Exempt Tool Categories from SOP Step 0 (Fast-Track Closure)
EXEMPT_COT_TOOLS = {
    "setCurrentActivities",
}

EXEMPT_WEB_SEARCH_DATASTORE_TOOLS = {
    "customer_website_default_datastore_google",
    "troubleshoot_datastore",
    "VZ_internal_datastore",
    "check_destination_coverage",
    "chatbot_doc_datastore",
    "one_search_datastore",
    "conversational_intelligence_ds",
}

EXEMPT_UI_WIDGET_AND_TEMPLATE_TOOLS = {
    "calendar",
    "showTemplate",
    "show_2fa_template",
    "managePinTemplate",
}

EXEMPT_STATIC_PROMPT_AND_TELEMETRY_TOOLS = {
    "rule_book",
    "set_transfer_context",
    "record_user_feedback",
    "set_user_utterance",
}
