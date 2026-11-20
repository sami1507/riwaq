# Riwaq: build the standard Windows installer (NSIS, per-user) with Tauri, then verify it by a
# silent install into a test folder on D, a launch on throwaway data, and a silent uninstall.
# Approved by the owner on 19 Nov 2026: the Tauri bundler downloads its NSIS toolchain from
# its official release host on first use. That download cache is redirected to D.
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
$Build = $PSScriptRoot
$Repo  = 'D:\Riwaq'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Out   = Join-Path $Build 'results\installer'
$Run   = Join-Path $Build ('run\installer-' + $Stamp)
$BR    = 'D:\RiwaqBuild\core-installer-' + $Stamp
$Cache = 'D:\RiwaqBuild\tauri-cache'
New-Item -ItemType Directory -Force -Path $Out, $Run, $Cache | Out-Null
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
$head = & git -C $Repo rev-parse --short HEAD 2>$null
Note 'repo' 'OK' "HEAD $head"
$tmp = Join-Path $Run 'tmp'; New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:TEMP = $tmp; $env:TMP = $tmp
foreach ($root in 'D:\RiwaqBuild', 'D:\RiwaqRuntime\rust') {
  if (Test-Path -LiteralPath (Join-Path $root 'cargo\bin\cargo.exe')) {
    $env:CARGO_HOME = Join-Path $root 'cargo'; $env:RUSTUP_HOME = Join-Path $root 'rustup'
    $env:Path = (Join-Path $root 'cargo\bin') + ';' + $env:Path
    break
  }
}
# The bundler keeps its downloaded tools under the local application-data folder; point that
# at D for the build process only.
$realLocal = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $Cache
$target = 'D:\RiwaqBuild\target'
$script = Join-Path $Repo 'scripts\desktop-build.ps1'
$command = "& '$script' -BuildRoot '$BR' -CargoTargetDirectory '$target' -NoBundle:`$false"
$p = Start-Process -FilePath 'powershell' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',$command `
     -WorkingDirectory $Repo -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $Out 'installer-build.log') -RedirectStandardError (Join-Path $Out 'installer-build.err.log')
if (-not $p.WaitForExit(90 * 60 * 1000)) { try { $p.Kill() } catch {}; $env:LOCALAPPDATA = $realLocal; Note 'build' 'FAILED' 'timeout after 90 minutes'; exit 1 }
$env:LOCALAPPDATA = $realLocal
& git -C $Repo checkout -- frontend/tsconfig.tsbuildinfo 2>$null
$setup = Get-ChildItem -LiteralPath (Join-Path $target 'release\bundle\nsis') -Filter '*-setup.exe' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $setup -or $setup.LastWriteTime -lt (Get-Date).AddMinutes(-100)) { Note 'build' 'FAILED' "exit=$($p.ExitCode); no fresh installer under $target\release\bundle\nsis; see installer-build.err.log"; exit 1 }
$dest = Join-Path $BR 'installer'; New-Item -ItemType Directory -Force -Path $dest | Out-Null
$final = Join-Path $dest $setup.Name
Copy-Item -LiteralPath $setup.FullName -Destination $final
$sha = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower()
$sig = (Get-AuthenticodeSignature -LiteralPath $final).Status
Note 'build' 'OK' "installer $final; $([math]::Round($setup.Length / 1MB, 1)) MB; sha256 $sha; signature status: $sig"
$tools = Get-ChildItem -LiteralPath $Cache -Recurse -File -ErrorAction SilentlyContinue
Note 'downloaded-tools' 'OK' "$($tools.Count) files, $([math]::Round((($tools | Measure-Object Length -Sum).Sum) / 1MB, 1)) MB under $Cache"
($tools | Select-Object -First 400 | ForEach-Object { $_.FullName.Substring($Cache.Length) + "`t" + $_.Length }) | Set-Content -Encoding UTF8 (Join-Path $Out 'downloaded-tools.txt')

# Verify: silent per-user install into a test folder on D.
$testDir = 'D:\RiwaqApp\installer-test-' + $Stamp
$i = Start-Process -FilePath $final -ArgumentList '/S', "/D=$testDir" -PassThru
if (-not $i.WaitForExit(5 * 60 * 1000)) { try { $i.Kill() } catch {}; Note 'install' 'FAILED' 'installer did not finish in 5 minutes'; exit 1 }
$exe = Join-Path $testDir 'riwaq.exe'
$unins = Join-Path $testDir 'uninstall.exe'
if (-not (Test-Path -LiteralPath $exe)) { Note 'install' 'FAILED' "exit=$($i.ExitCode); riwaq.exe not found in $testDir"; exit 1 }
Note 'install' 'OK' "exit=$($i.ExitCode); installed to $testDir; uninstaller present=$([bool](Test-Path -LiteralPath $unins))"

if (Listening) { Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Start-Sleep 3; Note 'port' 'OK' 'stopped the service that was listening on 8765' }
$env:RIWAQ_DATA_DIR = Join-Path $Run 'installer-data'
New-Item -ItemType Directory -Force -Path $env:RIWAQ_DATA_DIR | Out-Null
$app = Start-Process -FilePath $exe -WorkingDirectory $testDir -PassThru
$until = (Get-Date).AddSeconds(120)
while (-not (Up) -and (Get-Date) -lt $until) { Start-Sleep 1 }
$up = Up
# The installed application must pair its own window with no command line: the fresh data folder starts unpaired.
$paired = $false; $pairUntil = (Get-Date).AddSeconds(90)
while ($up -and -not $paired -and (Get-Date) -lt $pairUntil) {
  try { $paired = ((Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/api/auth/status' -Headers @{ 'X-Riwaq-Client' = 'local' } -TimeoutSec 3).Content -match '"paired"\s*:\s*true') } catch {}
  if (-not $paired) { Start-Sleep 1 }
}
Note 'installed-app-self-pairing' $(if ($paired) { 'OK' } else { 'FAILED' }) "paired without a command line: $paired"
$window = [bool]((Get-Process -Id $app.Id -ErrorAction SilentlyContinue).MainWindowHandle -ne 0)
Note 'installed-app-start' $(if ($up) { 'OK' } else { 'FAILED' }) "service answers=$up; native window handle present=$window"
try { [void]$app.CloseMainWindow() } catch {}
Start-Sleep 6
Get-Process -Name 'riwaq','riwaq-service' -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$testDir*" } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
Remove-Item Env:RIWAQ_DATA_DIR -ErrorAction SilentlyContinue

# Silent uninstall of the test installation (application data is kept by default).
if (Test-Path -LiteralPath $unins) {
  $u = Start-Process -FilePath $unins -ArgumentList '/S' -PassThru
  [void]$u.WaitForExit(3 * 60 * 1000)
  Start-Sleep 8
  Note 'uninstall' $(if (Test-Path -LiteralPath $exe) { 'FAILED' } else { 'OK' }) "riwaq.exe still present after uninstall: $([bool](Test-Path -LiteralPath $exe)); listener on 8765: $(Listening)"
} else { Note 'uninstall' 'NOT RUN' 'no uninstaller found' }
Note 'done' 'OK' 'finished'
