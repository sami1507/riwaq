param(
    [string]$ServicePath,
    [string]$DataDirectory,
    [string]$BrowserProfileDirectory
)
$ErrorActionPreference = 'Stop'
$BaseUrl = 'http://127.0.0.1:8765'
if (-not $ServicePath) { $ServicePath = Join-Path $PSScriptRoot 'riwaq-service.exe' }
$AppRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Riwaq'
if (-not $DataDirectory) { $DataDirectory = Join-Path $AppRoot 'data' }
if (-not $BrowserProfileDirectory) { $BrowserProfileDirectory = Join-Path $AppRoot 'EdgeProfile' }
$LogDirectory = Join-Path $AppRoot 'logs'
$StartedService = $null
$BrowserProcess = $null
$PreviousDataDirectory = [Environment]::GetEnvironmentVariable('RIWAQ_DATA_DIR','Process')
$PreviousPackagedWorker = [Environment]::GetEnvironmentVariable('RIWAQ_PACKAGED_MEDIA_WORKER','Process')

function Test-RiwaqService {
    try {
        $State = Invoke-RestMethod -Uri "$BaseUrl/api/auth/status" -Headers @{'X-Riwaq-Client'='local'} -TimeoutSec 2
        return ($State.paired -is [bool] -and $State.authenticated -is [bool])
    } catch { return $false }
}

function Test-OccupiedPort {
    $Probe = [Net.Sockets.TcpClient]::new()
    try {
        $Connect = $Probe.ConnectAsync('127.0.0.1', 8765)
        if (-not $Connect.Wait(1000)) { throw 'Could not establish whether local port 8765 is free; launch refused.' }
        return $Probe.Connected
    } catch {
        $Cause = $_.Exception.GetBaseException()
        if ($Cause -is [Net.Sockets.SocketException] -and $Cause.SocketErrorCode -eq [Net.Sockets.SocketError]::ConnectionRefused) { return $false }
        throw 'Could not safely inspect local port 8765; launch refused.'
    } finally { $Probe.Dispose() }
}

function Stop-OwnedService {
    if ($null -eq $StartedService) { return }
    $StartedService.Refresh()
    if (-not $StartedService.HasExited) {
        # Onefile PyInstaller has a worker under the bootloader. Restrict
        # cleanup to the exact process tree this launcher created.
        $Taskkill = Join-Path ([Environment]::GetFolderPath('System')) 'taskkill.exe'
        $Killer = Start-Process -FilePath $Taskkill -ArgumentList @('/PID', $StartedService.Id.ToString(), '/T', '/F') -WindowStyle Hidden -PassThru -Wait
        if ($Killer.ExitCode -ne 0) { Write-Warning 'The owned service did not stop cleanly; inspect the local Riwaq service process.' }
    }
}

$EdgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft/Edge/Application/msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft/Edge/Application/msedge.exe')
)
$EdgePath = $EdgeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $EdgePath) { throw 'Microsoft Edge was not found. Run the local service and open http://127.0.0.1:8765 in your browser, or install Edge normally.' }

try {
    if (Test-OccupiedPort) {
        throw 'Local port 8765 already has a listener. This launcher refuses to trust or open an existing service. Close the separately started service explicitly, or use its already-paired browser directly.'
    }
        if (-not (Test-Path -LiteralPath $ServicePath -PathType Leaf)) { throw "The bundled service is missing: $ServicePath" }
        New-Item -ItemType Directory -Force -Path $DataDirectory,$LogDirectory | Out-Null
        $env:RIWAQ_DATA_DIR = [IO.Path]::GetFullPath($DataDirectory)
        # Packaged transcription is intentionally not advertised as validated.
        Remove-Item Env:RIWAQ_PACKAGED_MEDIA_WORKER -ErrorAction SilentlyContinue
        $StartedService = Start-Process -FilePath $ServicePath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LogDirectory 'service.out.log') -RedirectStandardError (Join-Path $LogDirectory 'service.err.log')
        $Deadline = [DateTime]::UtcNow.AddSeconds(35)
        while ($true) {
            $StartedService.Refresh()
            if ($StartedService.HasExited) { throw "The local service exited. See $LogDirectory/service.err.log. If Application Control blocked it, stop; do not bypass the policy." }
            if (Test-RiwaqService) { break }
            if ([DateTime]::UtcNow -ge $Deadline) { throw "The service did not start within 35 seconds. Check port 8765 and $LogDirectory/service.err.log." }
            Start-Sleep -Milliseconds 250
        }
    New-Item -ItemType Directory -Force -Path $BrowserProfileDirectory | Out-Null
    $ProfileArgument = '--user-data-dir="' + [IO.Path]::GetFullPath($BrowserProfileDirectory) + '"'
    # A separate Riwaq profile preserves the user's normal Edge tabs/profile.
    # This is an explicitly launched browser app window, not a native shell.
    $BrowserProcess = Start-Process -FilePath $EdgePath -ArgumentList @("--app=$BaseUrl",$ProfileArgument,'--no-first-run','--no-default-browser-check','--disable-background-mode','--disable-background-networking','--disable-sync','--disable-extensions') -PassThru
    $BrowserProcess.WaitForExit()
} catch {
    Write-Error -ErrorAction Continue $_
    throw
} finally {
    Stop-OwnedService
    [Environment]::SetEnvironmentVariable('RIWAQ_DATA_DIR',$PreviousDataDirectory,'Process')
    [Environment]::SetEnvironmentVariable('RIWAQ_PACKAGED_MEDIA_WORKER',$PreviousPackagedWorker,'Process')
}
