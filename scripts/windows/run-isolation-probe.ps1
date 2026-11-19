# Riwaq GAP-036: run the tests that are normally skipped because they need the real local
# Docker workers / local model. Local only: the two switches that reach public websites
# (RIWAQ_TEST_LIVE_WEB_READ, RIWAQ_TEST_LIVE_REFINEMENT) are deliberately NOT enabled.
# Nothing is downloaded. Uses a throwaway temp folder on D. Results: results\live-workers\
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
$Build = $PSScriptRoot
$Src   = 'C:\Users\User\Documents\Codex\2026-11-12\chatgpt-claude-1-2-react-typescript'
$Repo  = 'D:\Riwaq'
$Out   = Join-Path $Build 'results\isolation-probe'
$Run   = Join-Path $Build ('run\iso-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $Out, $Run | Out-Null
$S = [ordered]@{ started = (Get-Date).ToString('s'); steps = [ordered]@{} }
function Note($k, $st, $d) { $S.steps[$k] = [ordered]@{ status = $st; detail = "$d"; at = (Get-Date).ToString('s') }
  Write-Host "[$st] ${k}: $d"; ($S | ConvertTo-Json -Depth 5) | Set-Content -Encoding UTF8 (Join-Path $Out 'summary.json') }
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'D:\RiwaqRuntime\riwaq-venv\Scripts\python.exe' }
if (-not (Test-Path $Py)) { Note 'python' 'FAILED' 'no interpreter'; exit 1 }
$branch = & git -C $Repo rev-parse --abbrev-ref HEAD 2>$null
$head = & git -C $Repo rev-parse --short HEAD 2>$null
Note 'repo' 'OK' "$branch @ $head"
$docker = $false
try { & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { $docker = $true } } catch {}
if ($docker) { Note 'docker' 'OK' 'engine answers' } else { Note 'docker' 'BLOCKED' 'Docker engine is not running; container tests will fail or skip' }
$cfgSrc = Join-Path $Src 'data\isolated-workers.json'
$cfg = Join-Path $Run 'isolated-workers.json'
if (Test-Path -LiteralPath $cfgSrc) { Copy-Item -LiteralPath $cfgSrc -Destination $cfg; $env:RIWAQ_TEST_ISOLATED_CONFIG = $cfg; Note 'config' 'OK' 'worker configuration copied (image names and limits only)' }
else { Note 'config' 'BLOCKED' 'no isolated-workers.json in the original data folder' }
$Tmp = Join-Path $Run 'tmp'; New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
$env:TEMP = $Tmp; $env:TMP = $Tmp
$files = @('tests\test_isolation_probe_live.py','tests\test_core_worker_inventory.py')
$args_ = @('-X','utf8','-m','pytest','-v','-rs','-p','no:cacheprovider','--basetemp',(Join-Path $Run 'pytest-tmp'),'--junitxml',(Join-Path $Out 'pytest-live.xml')) + $files
$p = Start-Process -FilePath $Py -ArgumentList $args_ -WorkingDirectory $Repo -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $Out 'pytest-live.log') -RedirectStandardError (Join-Path $Out 'pytest-live.err.log')
if (-not $p.WaitForExit(45 * 60 * 1000)) { try { $p.Kill() } catch {}; Note 'pytest-live' 'FAILED' 'timeout after 45 minutes' }
else { $tail = (Get-Content (Join-Path $Out 'pytest-live.log') -Tail 1); Note 'pytest-live' 'OK' "exit=$($p.ExitCode); $tail" }
& git -C $Repo status --short 2>$null | Set-Content -Encoding UTF8 (Join-Path $Out 'git-status-after.txt')
Note 'done' 'OK' 'finished'
