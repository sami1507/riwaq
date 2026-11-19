param(
    [string]$BuildRoot = 'D:/RiwaqBuild',
    [switch]$SkipFrontendBuild
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot
if (-not $SkipFrontendBuild) {
    npm --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed; no package was produced.' }
}
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'The compiled frontend is missing.' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Install the documented Python environment first.' }
$ServiceOutput = Join-Path $BuildRoot 'web-service'
$PortableRoot = Join-Path $BuildRoot 'web-desktop/Riwaq'
New-Item -ItemType Directory -Force -Path $BuildRoot,$ServiceOutput,$PortableRoot | Out-Null
$PyInstallerArgs = @(
    '-m','PyInstaller','--noconfirm','--clean','--onefile',
    '--name','riwaq-service',
    '--distpath',$ServiceOutput,
    '--workpath',(Join-Path $BuildRoot 'pyinstaller-web-service'),
    '--specpath',(Join-Path $BuildRoot 'web-service-spec'),
    '--paths',$ProjectRoot,
    '--add-data',"$ProjectRoot/frontend/dist;frontend/dist",
    '--add-data',"$ProjectRoot/config;config",
    '--add-data',"$ProjectRoot/skills;skills",
    '--add-data',"$ProjectRoot/backend/reasoning_limits.json;backend",
    '--collect-submodules','backend',
    '--collect-submodules','uvicorn',
    '--collect-submodules','keyring.backends',
    '--collect-data','tzdata',
    '--hidden-import','multipart',
    '--hidden-import','pypdf',
    '--hidden-import','docx',
    '--hidden-import','PIL.Image',
    '--hidden-import','PIL.ImageOps',
    '--exclude-module','faster_whisper',
    '--exclude-module','ctranslate2',
    '--exclude-module','av',
    '--exclude-module','torch',
    '--exclude-module','transformers',
    '--exclude-module','onnxruntime'
)
if (Test-Path -LiteralPath 'reports/media-validation.json') {
    $PyInstallerArgs += @('--add-data',"$ProjectRoot/reports/media-validation.json;reports")
}
$PyInstallerArgs += 'backend/launcher.py'
& '.venv/Scripts/python.exe' @PyInstallerArgs
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller packaging failed. No runnable package is claimed.' }
Copy-Item -LiteralPath (Join-Path $ServiceOutput 'riwaq-service.exe') -Destination $PortableRoot -Force
Copy-Item -LiteralPath 'scripts/launch-web-desktop.ps1','docs/DESKTOP.md','README.md' -Destination $PortableRoot -Force
$Manifest = @{
    product = 'Riwaq'
    version = '0.1.0'
    platform = 'windows-x86_64'
    delivery = 'local web service with an Edge app window; not a native Tauri build'
    built_at = [DateTime]::UtcNow.ToString('o')
    runtime_tested = $false
    signed = $false
    requirements = @('Microsoft Edge', 'Ollama running on 127.0.0.1:11434', 'Installed compatible local model weights')
    included = @('Python runtime', 'FastAPI service', 'Compiled React UI', 'Pillow image validation')
    excluded = @('Ollama', 'Model weights', 'Native Tauri shell', 'Document/media workers and their external runtimes')
    document_processing = 'Unavailable in this frozen package: packaged_worker_unavailable. Bundled Python parser libraries do not provide a configured isolated document worker.'
    note = 'Consult the final validation report for actual launch, model, persistence and browser-window test results. Successful packaging is not a runtime test.'
    files = @(Get-ChildItem -LiteralPath $PortableRoot -File | Where-Object Name -ne 'manifest.json' | ForEach-Object {
        @{name=$_.Name; bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    })
}
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PortableRoot 'manifest.json') -Encoding utf8
$ArchivePath = Join-Path $BuildRoot 'Riwaq-0.1.0-windows-web-desktop.zip'
Compress-Archive -LiteralPath $PortableRoot -DestinationPath $ArchivePath -Force
Write-Host "Packaged service: $ServiceOutput/riwaq-service.exe"
Write-Host "Portable web-desktop candidate: $ArchivePath"
Write-Host 'Runtime validation is still required. Stop if Windows Application Control blocks the executable; do not change policy or rename it to evade the block.'
