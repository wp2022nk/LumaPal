$ErrorActionPreference = "Stop"

$env:PYTHONUTF8 = "1"
$env:FF_V2_EVENT_STREAMING = "true"
$env:CONTENT_BUILDER_REQUIRE_EXECUTE_CONFIRMATION = "false"

uv run langgraph dev --host 127.0.0.1 --port 2024
