# Riwaq desktop launcher: starts the local service if needed, opens the browser, and
# puts a fresh one-use pairing code on the clipboard. The code is shown once and never
# written to disk.
param([switch]$Check, [switch]$App)   # -App: open the installed desktop application (its own window) instead of the browser
#   # -Check: start and verify only; no browser, no dialog, the code is discarded
$ErrorActionPreference = 'Continue'
$App_Root = $PSScriptRoot
$Repo = 'D:\Riwaq'
$Data = 'D:\RiwaqData'
$Url  = 'http://127.0.0.1:8765'
Add-Type -AssemblyName System.Windows.Forms
function Say($text, $icon) { if ($Check) { ([ordered]@{ at = (Get-Date).ToString('s'); error = $text } | ConvertTo-Json) | Set-Content -Encoding UTF8 (Join-Path $App_Root $(if ($App) { 'launch-check-app.json' } else { 'launch-check.json' })); return } [void][System.Windows.Forms.MessageBox]::Show($text, 'رواق', 'OK', $icon) }
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Py)) { $Py = 'D:\RiwaqRuntime\riwaq-venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Py)) { Say 'لم أجد بيئة Python الخاصة برواق.' 'Error'; exit 1 }
if (-not (Test-Path -LiteralPath $Data)) { Say 'مجلد البيانات D:\RiwaqData غير موجود. شغّل «تثبيت اختصار رواق» أولًا.' 'Error'; exit 1 }
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
$env:RIWAQ_DATA_DIR = $Data
$dist = Join-Path $Repo 'frontend\dist'
if (Test-Path (Join-Path $dist 'index.html')) { $env:RIWAQ_STATIC_DIR = $dist }
function Up { try { Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/auth/status" -TimeoutSec 3 | Out-Null; return $true } catch { return [bool]$_.Exception.Response } }
$appExe = $null
if ($App) {
  $pointer = Join-Path $App_Root 'app-path.txt'
  if (Test-Path -LiteralPath $pointer) { $appExe = Join-Path ((Get-Content -LiteralPath $pointer -TotalCount 1).Trim()) 'riwaq.exe' }
  if (-not $appExe -or -not (Test-Path -LiteralPath $appExe)) { Say 'التطبيق غير مثبت. شغّل «install-app» أولًا.' 'Error'; exit 1 }
  $running = Get-Process -Name 'riwaq' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $appExe }
  if (-not $running) {
    # The browser shortcut's service uses the same port and the same data; stop it first.
    Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Remove-Item Env:RIWAQ_STATIC_DIR -ErrorAction SilentlyContinue   # the application serves its own bundled interface
    Start-Process -FilePath $appExe -WorkingDirectory (Split-Path $appExe)
    $until = (Get-Date).AddSeconds(90)
    while (-not (Up) -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 700 }
    if (-not (Up)) { Say 'لم يبدأ تطبيق رواق خلال 90 ثانية.' 'Error'; exit 1 }
  }
} else {
if (-not (Up)) {
    # Local model runtime, if installed and not answering yet.
    $ollamaUp = $false
    try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null; $ollamaUp = $true } catch {}
    if (-not $ollamaUp) {
      $exe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
      if (-not $exe) { $exe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
      if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden }
    }
    $logs = Join-Path $App_Root 'logs'; New-Item -ItemType Directory -Force -Path $logs | Out-Null
    Start-Process -FilePath $Py -ArgumentList '-X','utf8','-m','backend.launcher' -WorkingDirectory $Repo -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $logs 'service.out.log') -RedirectStandardError (Join-Path $logs 'service.err.log')
    $until = (Get-Date).AddSeconds(90)
    while (-not (Up) -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 700 }
    if (-not (Up)) { Say "لم تبدأ خدمة رواق خلال 90 ثانية. التفاصيل في:`n$logs\service.err.log" 'Error'; exit 1 }
  }
}
Push-Location $Repo
$code = (& $Py -X utf8 -m backend.local_auth pair --show 2>$null | Select-Object -Last 1)
Pop-Location
if ($Check) {
  $ok = [bool]($code -and $code.Trim().Length -ge 20)
  ([ordered]@{ at = (Get-Date).ToString('s'); service_up = (Up); pairing_code_issued = $ok; static_ui = [bool]$env:RIWAQ_STATIC_DIR } | ConvertTo-Json) | Set-Content -Encoding UTF8 (Join-Path $App_Root $(if ($App) { 'launch-check-app.json' } else { 'launch-check.json' }))
  if ($App -and $appExe) { Get-Process -Name 'riwaq','riwaq-service' -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ((Split-Path $appExe) + '*') } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } }
  exit 0
}
if (-not $App) { Start-Process $Url }
if ($code -and $code.Trim().Length -ge 20) {
  Set-Clipboard -Value $code.Trim()
  Say "$(if ($App) { 'تطبيق رواق مفتوح.' } else { 'رواق يعمل وفُتح في المتصفح.' })`n`nإن طلبت الصفحة رمز الاقتران فهو منسوخ الآن: الصقه بـ Ctrl+V ثم ادخل.`nالرمز صالح لدقائق ولمرة واحدة." 'Information'
  # Do not leave the code on the clipboard longer than needed.
  Start-Sleep -Seconds 120
  try { if ((Get-Clipboard) -eq $code.Trim()) { Set-Clipboard -Value ' ' } } catch {}
} else {
  Say 'رواق يعمل وفُتح في المتصفح، لكن تعذر إصدار رمز اقتران. إن طلبته الصفحة أعد تشغيل الاختصار.' 'Warning'
}
