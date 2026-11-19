# One-time setup: copy the Riwaq data folder to D:\RiwaqData (the original on C is only
# read), then create the desktop shortcut. Safe to run again: an existing D:\RiwaqData is
# never overwritten.
$ErrorActionPreference = 'Continue'
$App  = $PSScriptRoot
$Src  = 'C:\Users\User\Documents\Codex\2026-11-12\chatgpt-claude-1-2-react-typescript\data'
$Data = 'D:\RiwaqData'
$R = [ordered]@{ at = (Get-Date).ToString('s') }
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { $R.service = 'a Riwaq service is running on 8765 during the copy' } else { $R.service = 'not running' }
if (Test-Path -LiteralPath $Data) { $R.data = 'kept existing D:\RiwaqData (nothing copied)' }
elseif (-not (Test-Path -LiteralPath $Src)) { New-Item -ItemType Directory -Force -Path $Data | Out-Null; $R.data = 'original data folder not found; created an empty D:\RiwaqData' }
else {
  $log = '/LOG:' + (Join-Path $App 'data-copy.log')
  & robocopy $Src $Data /E /COPY:DAT /R:1 /W:1 /XF *.lock /NFL /NDL /NP $log | Out-Null
  $R.robocopy_exit = $LASTEXITCODE
  $R.data = if ($LASTEXITCODE -lt 8) { 'copied once from the original (original untouched)' } else { 'copy reported failures; see data-copy.log' }
  $R.size_mb = [math]::Round(((Get-ChildItem -LiteralPath $Data -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1MB, 1)
}
$desktop = [Environment]::GetFolderPath('Desktop')
$arabic = ([string][char]0x0631 + [char]0x0648 + [char]0x0627 + [char]0x0642 + '.lnk')
$lnk = Join-Path $desktop $arabic
$tmpLnk = Join-Path $desktop 'Riwaq.lnk'   # the shortcut API cannot save a non-ANSI name; rename afterwards
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($tmpLnk)
$s.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
$s.Arguments = '"' + (Join-Path $App 'Riwaq.vbs') + '"'
$s.WorkingDirectory = $App
$s.IconLocation = (Join-Path $App 'riwaq.ico') + ',0'
$s.Description = 'Riwaq'
$s.Save()
if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk -Force }   # replace this script's own earlier shortcut
if (Test-Path -LiteralPath $tmpLnk) { Rename-Item -LiteralPath $tmpLnk -NewName $arabic }
$R.shortcut = if (Test-Path -LiteralPath $lnk) { "created on the desktop: $desktop" } else { 'FAILED to create' }
$R.dist = [bool](Test-Path 'D:\Riwaq\frontend\dist\index.html')
$R.branch = (& git -C 'D:\Riwaq' rev-parse --abbrev-ref HEAD 2>$null)
($R | ConvertTo-Json) | Set-Content -Encoding UTF8 (Join-Path $App 'install-result.json')
