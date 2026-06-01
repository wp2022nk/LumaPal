$ErrorActionPreference = "Stop"

$versionLine = (cmd.exe /d /c "java -version 2>&1" | Select-Object -First 1) -join ""
if ($versionLine -notmatch '"(?<major>\d+)') {
    throw "Unable to read the Java version from PATH."
}
if ([int]$Matches.major -lt 17) {
    throw "Android build requires JDK 17 or newer on PATH. Found: $versionLine"
}

# Some Windows machines retain a stale JAVA_HOME while PATH already resolves a
# newer JDK. Gradle's wrapper prefers JAVA_HOME, so clear it for this process.
$env:JAVA_HOME = $null

npm run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

npx cap sync android
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location (Join-Path $PSScriptRoot "..\android")
try {
    .\gradlew.bat assembleDebug
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}

Write-Host "Debug APK: android\app\build\outputs\apk\debug\app-debug.apk"
