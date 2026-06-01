$ErrorActionPreference = "Stop"

if (-not $env:ANDROID_HOME) {
    throw "Set ANDROID_HOME before preparing the Android SDK."
}

$sdkRoot = [System.IO.Path]::GetFullPath($env:ANDROID_HOME)
$toolsRoot = [System.IO.Path]::GetFullPath((Join-Path $sdkRoot "cmdline-tools\latest"))
if (-not $toolsRoot.StartsWith($sdkRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected Android SDK target: $toolsRoot"
}

$env:JAVA_HOME = $null
$sdkManager = Join-Path $toolsRoot "bin\sdkmanager.bat"
if (-not (Test-Path $sdkManager)) {
    $downloadRoot = Join-Path $env:TEMP "content-builder-android-tools-14742923"
    $archive = Join-Path $downloadRoot "commandlinetools-win-14742923_latest.zip"
    $expanded = Join-Path $downloadRoot "expanded"
    $source = Join-Path $expanded "cmdline-tools\*"

    New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
    if (-not (Test-Path $archive)) {
        Write-Host "Downloading Android command-line tools from dl.google.com..."
        Invoke-WebRequest `
            -Uri "https://dl.google.com/android/repository/commandlinetools-win-14742923_latest.zip" `
            -OutFile $archive
    }
    if (-not (Test-Path (Join-Path $expanded "cmdline-tools\bin\sdkmanager.bat"))) {
        Expand-Archive -LiteralPath $archive -DestinationPath $expanded -Force
    }
    New-Item -ItemType Directory -Force -Path $toolsRoot | Out-Null
    Copy-Item -Path $source -Destination $toolsRoot -Recurse -Force
}

if (-not (Test-Path $sdkManager)) {
    throw "sdkmanager.bat is still unavailable after installing command-line tools."
}

Write-Host "Review and accept the Android SDK licenses:"
& $sdkManager --licenses
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $sdkManager "platform-tools" "platforms;android-35" "build-tools;34.0.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
