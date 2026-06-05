$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

$env:PYTHONUTF8 = "1"
$env:CONTENT_BUILDER_REQUIRE_EXECUTE_CONFIRMATION = "false"

Write-Host "Starting trusted-LAN development server on http://0.0.0.0:2024"
Write-Host "Pair the phone with this token:"
Push-Location $ProjectRoot
try {
    uv run python -m content_builder.server.print_pairing_token
    $env:CONTENT_BUILDER_PAIRING_TOKEN_PRINTED = "1"
    Write-Host "Starting FastAPI LAN gateway and loopback LangGraph Agent Server..."
    uv run python -m content_builder.server
}
finally {
    Pop-Location
}
