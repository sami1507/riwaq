param([string]$OllamaPath='D:/RiwaqRuntime/ollama/ollama.exe',[string]$ModelPath='D:/RiwaqRuntime/models')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8='1'
$env:OLLAMA_NO_CLOUD='1'
$env:OLLAMA_HOST='127.0.0.1:11434'
$env:RIWAQ_EMBED_MODEL='embeddinggemma-2:270m-q8_0-text'
if (Test-Path -LiteralPath $ModelPath) { $env:OLLAMA_MODELS=$ModelPath }
if (Test-Path -LiteralPath 'D:/RiwaqRuntime/whisper-small/model.bin') { $env:RIWAQ_WHISPER_MODEL='D:/RiwaqRuntime/whisper-small' }
try { Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null }
catch {
  if (-not (Test-Path -LiteralPath $OllamaPath)) { $OllamaPath=(Get-Command ollama -ErrorAction Stop).Source }
  Start-Process -FilePath $OllamaPath -ArgumentList 'serve' -WindowStyle Hidden
}
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Build frontend first: npm --prefix frontend ci; npm --prefix frontend run build' }
Write-Host 'Riwaq: http://127.0.0.1:8765  |  Ctrl+C stops the API service'
& '.venv/Scripts/python.exe' -m backend.launcher
