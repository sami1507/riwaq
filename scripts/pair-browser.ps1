param(
    [switch]$Copy,
    [switch]$Show,
    [string]$DataDirectory,
    [string]$PythonPath,
    [string]$ServicePath
)
$ErrorActionPreference = 'Stop'
if ($Copy.IsPresent -eq $Show.IsPresent) {
    throw 'Choose exactly one explicit action: -Copy or -Show. No pairing secret is displayed or copied automatically.'
}
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $ProjectRoot '.venv/Scripts/python.exe' }
if (-not $ServicePath) {
    $ServicePath = Join-Path $PSScriptRoot 'riwaq-service.exe'
    # Native portable packages keep this helper in scripts/ beside the source
    # layout shown by PairingGate, and the service one directory above it.
    if (-not (Test-Path -LiteralPath $ServicePath -PathType Leaf)) {
        $ServicePath = Join-Path $ProjectRoot 'riwaq-service.exe'
    }
}
$PreviousDataDirectory = [Environment]::GetEnvironmentVariable('RIWAQ_DATA_DIR','Process')
$PairCode = $null
try {
    if ($DataDirectory) { $env:RIWAQ_DATA_DIR = [IO.Path]::GetFullPath($DataDirectory) }
    if (Test-Path -LiteralPath $PythonPath -PathType Leaf) {
        Push-Location -LiteralPath $ProjectRoot
        try { $PairCode = & $PythonPath -X utf8 -B -m backend.local_auth pair --show }
        finally { Pop-Location }
    } elseif (Test-Path -LiteralPath $ServicePath -PathType Leaf) {
        if (-not $DataDirectory -and -not $env:RIWAQ_DATA_DIR) {
            # Native launcher retains the frozen service default Riwaq/. The
            # Edge launcher explicitly selects Riwaq/data. Match the package
            # layout; custom service directories still require -DataDirectory.
            $DefaultRelativeData = if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'riwaq.exe') -PathType Leaf) { 'Riwaq' } else { 'Riwaq/data' }
            $env:RIWAQ_DATA_DIR = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $DefaultRelativeData
        }
        # Frozen launcher must implement this explicit CLI dispatch. It must
        # never start the API service in response to these arguments.
        $PairCode = & $ServicePath --pair-browser --show
    } else {
        throw 'No supported local pairing CLI was found. Run this script from the source installation with its venv, or a package with explicit pairing CLI support.'
    }
    if ($LASTEXITCODE -ne 0 -or $PairCode -is [Array] -or $PairCode -notmatch '^[A-Za-z0-9_-]{43}$') {
        throw 'The local pairing CLI did not produce a valid code. No output was copied or displayed.'
    }
    if ($Copy) {
        Set-Clipboard -Value $PairCode
        Write-Host 'A new one-use Riwaq pairing code was copied by your explicit request. Paste it into the local Riwaq pairing screen within 5 minutes. Previous unused pairing codes are invalid.'
    } else {
        Write-Warning 'This explicitly requested code will appear in terminal scrollback. It expires after 5 minutes and can be used once.'
        Write-Output $PairCode
    }
} finally {
    $PairCode = $null
    [Environment]::SetEnvironmentVariable('RIWAQ_DATA_DIR',$PreviousDataDirectory,'Process')
}
