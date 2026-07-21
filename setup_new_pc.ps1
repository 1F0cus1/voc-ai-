param(
    [string]$ArchivePath = "",
    [string]$ExpectedSha256 = ""
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeRoot = Join-Path $repoRoot ".runtime"
$runtimePython = Join-Path $runtimeRoot "python"
$pythonExe = Join-Path $runtimePython "python.exe"
$tempRoot = Join-Path $runtimeRoot "setup-temp"
$downloadZip = Join-Path $tempRoot "runtime.zip"
$extractRoot = Join-Path $tempRoot "extract"
$repository = "1F0cus1/voc-ai-"
$version = (Get-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Raw).Trim()
$assetName = "voc_tagger_windows_x64_v$version.zip"

function Test-Runtime {
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        return $false
    }
    & $pythonExe -c "import pymysql, tkinter" *> $null
    return $LASTEXITCODE -eq 0
}

function Assert-InRuntimeRoot([string]$Path) {
    $runtimeFull = [IO.Path]::GetFullPath($runtimeRoot).TrimEnd('\') + '\'
    $pathFull = [IO.Path]::GetFullPath($Path)
    if (-not $pathFull.StartsWith($runtimeFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside .runtime: $pathFull"
    }
}

if (Test-Runtime) {
    Write-Host "Portable Python is already ready: $pythonExe"
    exit 0
}

if (Test-Path -LiteralPath $runtimePython) {
    throw "An incomplete runtime exists at $runtimePython. Rename or remove that folder, then run setup again."
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
Assert-InRuntimeRoot $tempRoot
if (Test-Path -LiteralPath $tempRoot) {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $tempRoot | Out-Null

try {
    if ($ArchivePath) {
        $resolvedArchive = (Resolve-Path -LiteralPath $ArchivePath).Path
        Copy-Item -LiteralPath $resolvedArchive -Destination $downloadZip
        Write-Host "Using local release archive: $resolvedArchive"
    } else {
        $releaseBaseUrl = "https://github.com/$repository/releases/download/v$version"
        Write-Host "Downloading $assetName..."
        Invoke-WebRequest `
            -Uri "$releaseBaseUrl/$assetName" `
            -OutFile $downloadZip `
            -Headers @{ "User-Agent" = "voc-ai-setup" }

        $checksumPath = Join-Path $tempRoot "runtime.sha256"
        Invoke-WebRequest `
            -Uri "$releaseBaseUrl/$assetName.sha256" `
            -OutFile $checksumPath `
            -Headers @{ "User-Agent" = "voc-ai-setup" }
        $ExpectedSha256 = ((Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\s+')[0]
    }

    if ($ExpectedSha256) {
        $actualSha256 = (Get-FileHash -LiteralPath $downloadZip -Algorithm SHA256).Hash
        if ($actualSha256 -ne $ExpectedSha256.ToUpperInvariant()) {
            throw "Runtime checksum mismatch. Expected $ExpectedSha256, got $actualSha256."
        }
        Write-Host "SHA256 verification passed."
    }

    Write-Host "Extracting the portable runtime..."
    Expand-Archive -LiteralPath $downloadZip -DestinationPath $extractRoot -Force
    $foundPython = Get-ChildItem -LiteralPath $extractRoot -Filter python.exe -Recurse -File |
        Where-Object { $_.Directory.Name -eq "python" } |
        Select-Object -First 1
    if (-not $foundPython) {
        throw "python.exe was not found in the release archive."
    }

    $sourcePython = $foundPython.Directory.FullName
    Move-Item -LiteralPath $sourcePython -Destination $runtimePython
    if (-not (Test-Runtime)) {
        throw "Portable Python verification failed after extraction."
    }
    Write-Host "Setup completed: $pythonExe"
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Assert-InRuntimeRoot $tempRoot
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
