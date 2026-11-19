param(
    [string]$BuildRoot = 'D:/RiwaqBuild',
    [string]$CargoTargetDirectory = '',
    [switch]$SkipPython,
    [switch]$NoBundle = $true
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot
$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
$PortableRoot = Join-Path $BuildRoot 'portable/Riwaq'
$ArchivePath = Join-Path $BuildRoot 'Riwaq-0.1.0-windows-x64-portable.zip'
# Never reuse a release folder: old VALIDATION.json or unrelated files must not
# enter a new candidate. This also protects a portable executable still in use.
if (Test-Path -LiteralPath $PortableRoot) {
    if (-not (Test-Path -LiteralPath $PortableRoot -PathType Container)) { throw 'Portable output is not a directory; choose a new BuildRoot.' }
    if (Get-ChildItem -LiteralPath $PortableRoot -Force | Select-Object -First 1) { throw 'Portable output is not empty; choose a new BuildRoot. No files were removed.' }
}
if (Test-Path -LiteralPath $ArchivePath) { throw 'Release archive already exists; choose a new BuildRoot. No archive was replaced.' }
if ([string]::IsNullOrWhiteSpace($CargoTargetDirectory)) {
    $CargoTargetDirectory = Join-Path $BuildRoot 'target'
} else {
    $CargoTargetDirectory = [IO.Path]::GetFullPath($CargoTargetDirectory)
}
if (-not (Test-Path 'frontend/dist/index.html')) { throw 'Build the frontend first: npm --prefix frontend run build' }
New-Item -ItemType Directory -Force -Path $BuildRoot,'desktop/src-tauri/binaries' | Out-Null
if (-not $SkipPython) {
    & '.venv/Scripts/python.exe' scripts/prepare_packaged_workers.py "$BuildRoot/worker-manifest"
    if ($LASTEXITCODE -ne 0) { throw 'Fixed worker manifest preparation failed' }
    & '.venv/Scripts/python.exe' -m PyInstaller --noconfirm --clean --onefile --name riwaq-service --distpath "$BuildRoot/service" --workpath "$BuildRoot/pyinstaller" --specpath "$BuildRoot/spec" --paths $ProjectRoot --add-data "$ProjectRoot/frontend/dist;frontend/dist" --add-data "$ProjectRoot/config;config" --add-data "$ProjectRoot/skills;skills" --add-data "$ProjectRoot/backend/reasoning_limits.json;backend" --add-data "$BuildRoot/worker-manifest/packaged-workers.json;." --copy-metadata sympy --copy-metadata mpmath --collect-submodules sympy --collect-submodules mpmath --collect-submodules backend --collect-submodules uvicorn --collect-submodules keyring.backends --collect-data tzdata --hidden-import multipart --hidden-import pypdf --hidden-import docx --hidden-import PIL.Image --hidden-import PIL.ImageOps --exclude-module faster_whisper --exclude-module ctranslate2 --exclude-module av --exclude-module torch --exclude-module transformers --exclude-module onnxruntime 'backend/launcher.py'
    if ($LASTEXITCODE -ne 0) { throw 'Python service packaging failed' }
    Copy-Item -LiteralPath "$BuildRoot/service/riwaq-service.exe" -Destination 'desktop/src-tauri/binaries/riwaq-service-x86_64-pc-windows-msvc.exe' -Force
}
$env:CARGO_TARGET_DIR = $CargoTargetDirectory
$env:CARGO_NET_OFFLINE = 'true'
if (Test-Path "$BuildRoot/cargo/bin/cargo.exe") {
    $env:CARGO_HOME = "$BuildRoot/cargo"
    $env:RUSTUP_HOME = "$BuildRoot/rustup"
    $env:Path = "$env:CARGO_HOME/bin;$env:Path"
} elseif (Test-Path 'D:/RiwaqRuntime/rust/cargo/bin/cargo.exe') {
    $env:CARGO_HOME = 'D:/RiwaqRuntime/rust/cargo'
    $env:RUSTUP_HOME = 'D:/RiwaqRuntime/rust/rustup'
    $env:Path = "$env:CARGO_HOME/bin;$env:Path"
}
Push-Location desktop
try {
    if ($NoBundle) { npm run build:exe -- --ci -- --offline --locked } else { npm run build -- --ci -- --offline --locked }
    if ($LASTEXITCODE -ne 0) { throw 'Tauri build failed' }
} finally { Pop-Location }
Copy-Item -LiteralPath 'desktop/src-tauri/binaries/riwaq-service-x86_64-pc-windows-msvc.exe' -Destination "$CargoTargetDirectory/release/riwaq-service.exe" -Force
New-Item -ItemType Directory -Force -Path $PortableRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PortableRoot 'scripts') | Out-Null
Copy-Item -LiteralPath "$CargoTargetDirectory/release/riwaq.exe","$CargoTargetDirectory/release/riwaq-service.exe",'docs/DESKTOP.md' -Destination $PortableRoot -Force
Copy-Item -LiteralPath 'scripts/pair-browser.ps1' -Destination (Join-Path $PortableRoot 'scripts/pair-browser.ps1') -Force
$ReleaseManifest = @{
    product = 'Riwaq'
    version = '0.1.0'
    platform = 'windows-x86_64'
    built_at = [DateTime]::UtcNow.ToString('o')
    external_requirements = @('Microsoft WebView2', 'Ollama', 'A compatible local model')
    signed = $false
    runtime_tested = $false
    service_rebuilt_this_run = -not $SkipPython.IsPresent
    pairing_command = './scripts/pair-browser.ps1 -Copy'
    packaging_limits = @('External Ollama and model weights are required.', 'Fixed math/static and Windows speech synthesis workers are included. Container workers require pinned external Docker configuration. Whisper transcription, video and image generation are unavailable in this package. Source tests do not certify package runtime.', 'This manifest records a build candidate, not successful native launch or pairing validation.')
    files = @(Get-ChildItem -LiteralPath $PortableRoot -File -Recurse | Where-Object Name -ne 'manifest.json' | ForEach-Object { @{ name = $_.FullName.Substring(([IO.Path]::GetFullPath($PortableRoot)).Length + 1); sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash; bytes = $_.Length } })
}
$ReleaseManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PortableRoot 'manifest.json') -Encoding utf8
Compress-Archive -LiteralPath $PortableRoot -DestinationPath $ArchivePath
Write-Host "Artifacts: $CargoTargetDirectory/release. Installer bundling requested: $(-not $NoBundle.IsPresent)."
Write-Host "Portable package: $BuildRoot/Riwaq-0.1.0-windows-x64-portable.zip"
