# Connect Riwaq to a rented GPU server through an SSH tunnel, so the remote model appears at 127.0.0.1:11434.
# The server's model port is never opened to the internet. Authentication is by SSH key only.
#
#   First time:   .\riwaq-remote-tunnel.ps1 -ServerHost 203.0.113.7 -ServerPort 17445 -Setup
#   Afterwards:   .\riwaq-remote-tunnel.ps1 -ServerHost 203.0.113.7 -ServerPort 17445
#
# Take ServerHost and ServerPort from the provider's "SSH over exposed TCP" command. Keep this window open while you use Riwaq.
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9.\-:]{3,253}$')][string]$ServerHost,
  [ValidateRange(1, 65535)][int]$ServerPort = 22,
  [ValidatePattern('^[a-z_][a-z0-9_-]{0,31}$')][string]$User = 'root',
  [string]$KeyPath = (Join-Path $env:USERPROFILE '.ssh\id_ed25519'),
  [ValidatePattern('^[A-Za-z0-9._:/\-]{1,100}$')][string]$Model = 'qwen3:32b',
  [switch]$Setup,
  [switch]$StopLocalOllama
)
$ErrorActionPreference = 'Stop'
$ssh = (Get-Command ssh.exe -ErrorAction SilentlyContinue).Source
if (-not $ssh) { Write-Host 'OpenSSH client is not installed. Add it from Windows Settings > Optional features.'; exit 2 }
if (-not (Test-Path -LiteralPath $KeyPath)) { Write-Host "SSH key not found: $KeyPath. Create one with: ssh-keygen -t ed25519  and add the .pub file to the provider account."; exit 2 }
$known = Join-Path $env:USERPROFILE '.ssh\riwaq_known_hosts'
# accept-new records the server key on first contact and refuses the connection if it ever changes afterwards.
$common = @('-i', $KeyPath, '-p', "$ServerPort", '-o', 'IdentitiesOnly=yes', '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
  '-o', 'StrictHostKeyChecking=accept-new', '-o', "UserKnownHostsFile=$known", '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
  '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=4', '-o', 'ConnectTimeout=20')
$target = "$User@$ServerHost"

if ($Setup) {
  $script = Join-Path $PSScriptRoot '..\remote\riwaq-server-setup.sh'
  if (-not (Test-Path -LiteralPath $script)) { $script = Join-Path $PSScriptRoot 'riwaq-server-setup.sh' }
  if (-not (Test-Path -LiteralPath $script)) { Write-Host 'riwaq-server-setup.sh was not found next to this script.'; exit 2 }
  Write-Host "Preparing the server and pulling $Model (this can take a long time) ..."
  Get-Content -LiteralPath $script -Raw | & $ssh @common $target "tr -d '\r' > /tmp/riwaq-server-setup.sh && bash /tmp/riwaq-server-setup.sh '$Model'"
  if ($LASTEXITCODE -ne 0) { Write-Host 'Server setup failed. Nothing was connected.'; exit 1 }
}

function Listening { [bool](Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue) }
if (Listening) {
  if (-not $StopLocalOllama) { Write-Host 'Port 11434 is in use, probably by the local Ollama. Quit Ollama from the tray, or run again with -StopLocalOllama.'; exit 3 }
  Get-Process -Name 'ollama', 'ollama app' -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Seconds 2
  if (Listening) { Write-Host 'Port 11434 is still in use by another program. Nothing was connected.'; exit 3 }
}

Write-Host "Tunnel open: Riwaq on this PC -> 127.0.0.1:11434 -> $ServerHost (encrypted). In Riwaq choose the model $Model."
Write-Host 'Close this window to disconnect. Stop the server in the provider console when you finish.'
# The tunnel listens on this PC's loopback only, and fails instead of running without the forward.
& $ssh @common '-N' '-o' 'ExitOnForwardFailure=yes' '-L' '127.0.0.1:11434:127.0.0.1:11434' $target
Write-Host "Tunnel closed (exit $LASTEXITCODE). Riwaq no longer reaches the remote model."
