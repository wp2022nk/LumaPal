$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

Push-Location $ProjectRoot
try {
    uv run python -m content_builder.server.print_pairing_token
}
finally {
    Pop-Location
}
