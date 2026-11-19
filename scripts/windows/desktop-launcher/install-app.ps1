# Installs the built Riwaq desktop application for this user: copies the newest portable
# build to D:\RiwaqApp, records its location for the launcher, and creates a second
# desktop shortcut. Nothing is downloaded; no registry or system setting is changed.
# Unsigned. To remove: delete D:\RiwaqApp and the desktop shortcut.
$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
$R = [ordered]@{ at = (Get-Date).ToString('s') }
$pkg = Get-ChildItem 'D:\RiwaqBuild' -Directory -Filter 'core-package-*' | Sort-Object Name | Select-Object -Last 1
$src = if ($pkg) { Join-Path $pkg.FullName 'portable\Riwaq' } else { $null }
if (-not $src -or -not (Test-Path -LiteralPath (Join-Path $src 'riwaq.exe'))) { $R.result = 'no built package found under D:\RiwaqBuild\core-package-*'; ($R | ConvertTo-Json) | Set-Content -Encoding UTF8 (Join-Path $Root 'install-app-result.json'); exit 1 }
$dest = Join-Path 'D:\RiwaqApp' ('Riwaq-' + $pkg.Name.Substring(13))
if (-not (Test-Path -LiteralPath (Join-Path $dest 'riwaq.exe'))) {
  $log = '/LOG:' + (Join-Path $Root 'app-copy.log')
  & robocopy $src $dest /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NP $log | Out-Null
  $R.robocopy_exit = $LASTEXITCODE
}
$R.source = $src; $R.installed_to = $dest
$R.exe_present = [bool](Test-Path -LiteralPath (Join-Path $dest 'riwaq.exe'))
$R.size_mb = [math]::Round(((Get-ChildItem -LiteralPath $dest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1MB, 1)
$dest | Set-Content -Encoding ASCII (Join-Path $Root 'app-path.txt')
$desktop = [Environment]::GetFolderPath('Desktop')
$arabic = ([string][char]0x0631 + [char]0x0648 + [char]0x0627 + [char]0x0642 + ' ' + [char]0x2014 + ' ' + [char]0x062A + [char]0x0637 + [char]0x0628 + [char]0x064A + [char]0x0642 + '.lnk')
$tmpLnk = Join-Path $desktop 'RiwaqApp.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($tmpLnk)
$s.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
$s.Arguments = '"' + (Join-Path $Root 'RiwaqApp.vbs') + '"'
$s.WorkingDirectory = $Root
$s.IconLocation = (Join-Path $dest 'riwaq.exe') + ',0'
$s.Description = 'Riwaq desktop application'
$s.Save()
$final = Join-Path $desktop $arabic
if (Test-Path -LiteralPath $final) { Remove-Item -LiteralPath $final -Force }
if (Test-Path -LiteralPath $tmpLnk) { Rename-Item -LiteralPath $tmpLnk -NewName $arabic }
$R.shortcut = if (Test-Path -LiteralPath $final) { 'created on the desktop' } else { 'FAILED to create' }
($R | ConvertTo-Json) | Set-Content -Encoding UTF8 (Join-Path $Root 'install-app-result.json')
