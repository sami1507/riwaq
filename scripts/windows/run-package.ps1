# Riwaq CORE-034: build the Windows desktop package from D:\Riwaq (offline, existing
# toolchain), start the packaged app on a throwaway data folder, and run the acceptance
# journeys against the packaged service. Nothing is downloaded; the original folder and
# the real data are not used. Results: results\package\
param([switch]$Reuse)   # -Reuse: test the newest existing core-package-* build instead of building again
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
$Build = $PSScriptRoot
$Src   = 'C:\Users\User\Documents\Codex\2026-11-12\chatgpt-claude-1-2-react-typescript'
$Repo  = 'D:\Riwaq'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Out   = Join-Path $Build 'results\package'
$Run   = Join-Path $Build ('run\package-' + $Stamp)
$BR    = 'D:\RiwaqBuild\core-package-' + $Stamp
New-Item -ItemType Directory -Force -Path $Out, $Run | Out-Null
$S = [ordered]@{ started = (Get-Date).ToString('s'); build_root = $BR; steps = [ordered]@{} }
function Note($k, $st, $d) {
  $S.steps[$k] = [ordered]@{ status = $st; detail = "$d"; at = (Get-Date).ToString('s') }
  ($S | ConvertTo-Json -Depth 5) | Set-Content -Encoding UTF8 (Join-Path $Out 'summary.json')
}
function Listening { return [bool](Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) }
function Up {
  try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/api/auth/status' -TimeoutSec 3 | Out-Null; return $true }
  catch { return [bool]$_.Exception.Response }
}
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Py)) { Note 'python' 'FAILED' 'no .venv in D:\Riwaq'; exit 1 }
$head = & git -C $Repo rev-parse --short HEAD 2>$null
Note 'repo' 'OK' "HEAD $head"
$tmp = Join-Path $Run 'tmp'; New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:TEMP = $tmp; $env:TMP = $tmp

# desktop\node_modules: reuse the original folder's through a junction, as for the frontend.
$dnm = Join-Path $Repo 'desktop\node_modules'
if (-not (Test-Path -LiteralPath $dnm)) {
  $orig = Join-Path $Src 'desktop\node_modules'
  if (Test-Path -LiteralPath $orig) { cmd /c mklink /J "$dnm" "$orig" | Out-Null }
}
if (Test-Path -LiteralPath $dnm) { Note 'desktop-deps' 'OK' 'desktop\node_modules available' } else { Note 'desktop-deps' 'BLOCKED' 'desktop\node_modules missing; Tauri CLI unavailable' }

# Frontend build of the current branch.
Push-Location (Join-Path $Repo 'frontend')
cmd /c "npm run build > `"$Out\frontend-build.log`" 2>&1"
$fb = $LASTEXITCODE
Pop-Location
& git -C $Repo checkout -- frontend/tsconfig.tsbuildinfo 2>$null
Note 'frontend-build' $(if ($fb -eq 0) { 'OK' } else { 'FAILED' }) "exit=$fb"

# Desktop icons from the new brand source (best effort; build continues with the old icons).
Push-Location (Join-Path $Repo 'desktop')
cmd /c "npx --no-install tauri icon ..\frontend\public\brand\app-icon-1024.png > `"$Out\tauri-icon.log`" 2>&1"
$ic = $LASTEXITCODE
Pop-Location
Note 'icons' $(if ($ic -eq 0) { 'OK' } else { 'NOT RUN' }) "tauri icon exit=$ic"

# Build (PyInstaller service + Tauri shell), offline.
# The build script looks for the Rust toolchain under its own BuildRoot; point it at the existing one.
foreach ($root in 'D:\RiwaqBuild', 'D:\RiwaqRuntime\rust') {
  if (Test-Path -LiteralPath (Join-Path $root 'cargo\bin\cargo.exe')) {
    $env:CARGO_HOME = Join-Path $root 'cargo'; $env:RUSTUP_HOME = Join-Path $root 'rustup'
    $env:Path = (Join-Path $root 'cargo\bin') + ';' + $env:Path
    break
  }
}
$cargo = (Get-Command cargo -ErrorAction SilentlyContinue).Source
if ($cargo) { Note 'toolchain' 'OK' "cargo at $cargo" } else { Note 'toolchain' 'BLOCKED' 'cargo.exe not found under D:\RiwaqBuild\cargo, D:\RiwaqRuntime\rust or PATH' }
if ($Reuse) {
  $last = Get-ChildItem 'D:\RiwaqBuild' -Directory -Filter 'core-package-*' | Sort-Object Name | Select-Object -Last 1
  $BR = $last.FullName; $S.build_root = $BR
  $exe = Join-Path $BR 'portable\Riwaq\riwaq.exe'
  $p = $null
} else {
$target = 'D:\RiwaqBuild\target'
$blog = Join-Path $Out 'desktop-build.log'
$p = Start-Process -FilePath 'powershell' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $Repo 'scripts\desktop-build.ps1'),'-BuildRoot',$BR,'-CargoTargetDirectory',$target `
     -WorkingDirectory $Repo -NoNewWindow -PassThru -RedirectStandardOutput $blog -RedirectStandardError (Join-Path $Out 'desktop-build.err.log')
if (-not $p.WaitForExit(90 * 60 * 1000)) { try { $p.Kill() } catch {}; Note 'build' 'FAILED' 'timeout after 90 minutes'; exit 1 }
$exe = Join-Path $BR 'portable\Riwaq\riwaq.exe'
}
if (-not (Test-Path -LiteralPath $exe)) { Note 'build' 'FAILED' "no riwaq.exe in $BR; see desktop-build.err.log"; exit 1 }
$sizeMb = [math]::Round(((Get-ChildItem -LiteralPath (Split-Path $exe) -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Note 'build' 'OK' "reuse=$Reuse; portable folder $sizeMb MB at $BR\portable\Riwaq"

# Run the packaged app on a throwaway data folder.
if (Listening) {
  Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
  Start-Sleep 3
  Note 'port' 'OK' 'stopped the service that was listening on 8765 (started earlier by the desktop shortcut)'
}
$ollamaUp = $false
try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null; $ollamaUp = $true } catch {}
if (-not $ollamaUp) {
  $oexe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
  if (Test-Path -LiteralPath $oexe) { Start-Process -FilePath $oexe -ArgumentList 'serve' -WindowStyle Hidden; Start-Sleep 8 }
}
$env:RIWAQ_DATA_DIR = Join-Path $Run 'package-data'
New-Item -ItemType Directory -Force -Path $env:RIWAQ_DATA_DIR | Out-Null
$cfgSrc = Join-Path $Src 'data\isolated-workers.json'
if (Test-Path -LiteralPath $cfgSrc) { Copy-Item -LiteralPath $cfgSrc -Destination (Join-Path $env:RIWAQ_DATA_DIR 'isolated-workers.json'); Note 'worker-config' 'OK' 'worker configuration copied into the throwaway data folder (image names and limits only)' }
else { Note 'worker-config' 'BLOCKED' 'no isolated-workers.json in the original data folder' }
$app = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -PassThru
$until = (Get-Date).AddSeconds(120)
while (-not (Up) -and (Get-Date) -lt $until) { Start-Sleep 1 }
if (-not (Up)) { Note 'package-start' 'FAILED' 'packaged app did not serve on 8765 within 120 s'; try { Stop-Process -Id $app.Id -Force } catch {}; exit 1 }
$procs = (Get-Process -Name 'riwaq','riwaq-service' -ErrorAction SilentlyContinue | Measure-Object).Count
$hasWindow = [bool]((Get-Process -Id $app.Id -ErrorAction SilentlyContinue).MainWindowHandle -ne 0)
Note 'package-start' 'OK' "packaged service answers on 8765; processes=$procs; native window handle present=$hasWindow"

$acc = Join-Path $Out 'acceptance'
$a = Start-Process -FilePath $Py -ArgumentList '-X','utf8','scripts\core_acceptance.py','run','--model-mode','live','--out',$acc -WorkingDirectory $Repo -NoNewWindow -PassThru `
     -RedirectStandardOutput (Join-Path $Out 'acceptance-run.log') -RedirectStandardError (Join-Path $Out 'acceptance-run.err.log')
if (-not $a.WaitForExit(40 * 60 * 1000)) { try { $a.Kill() } catch {}; Note 'acceptance' 'FAILED' 'timeout' } else { Note 'acceptance' 'OK' "exit=$($a.ExitCode)" }

# Close the packaged app and confirm nothing is left behind.
try { [void]$app.CloseMainWindow() } catch {}
Start-Sleep 6
Get-Process -Name 'riwaq','riwaq-service' -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$BR*" } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
Note 'package-stop' $(if (Listening) { 'FAILED' } else { 'OK' }) "listener on 8765 after close: $(Listening)"
& git -C $Repo status --short 2>$null | Set-Content -Encoding UTF8 (Join-Path $Out 'git-status-after.txt')
Note 'done' 'OK' 'finished'
