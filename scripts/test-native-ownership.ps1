param(
    [string]$Executable = "$PSScriptRoot\..\apps\desktop\src-tauri\target\release\imp-desktop.exe",
    [string]$Identifier = 'dev.imud.imp.ownership-acceptance',
    [switch]$InjectFailureAfterSpawn
)

# Build the real desktop with this disposable identifier; never alter operator settings.
$ErrorActionPreference = 'Stop'
$Executable = (Resolve-Path $Executable).Path
$Sidecar = Join-Path (Split-Path $Executable) 'imp-node.exe'
$Config = Join-Path ([Environment]::GetFolderPath('ApplicationData')) $Identifier
if (Test-Path $Config) { throw "Acceptance config already exists: $Config" }
foreach ($Port in 8787, 8788, 8789) {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "Port $Port is occupied; its owner will not be terminated"
    }
}
$Temporary = Join-Path $env:TEMP ('imp-ownership-' + [guid]::NewGuid())
$TemporaryCreated = $false
$ConfigCreated = $false
$SshListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$Reserve = $null
$Desktop = $null
$External = $null
$DesktopProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$SpawnedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$ExternalProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$Runs = [Collections.Generic.List[object]]::new()
$TrackedSpawned = @{}
$CleanupFailures = [Collections.Generic.List[string]]::new()
$Failure = $null
$InjectionReached = $false

function Wait-Until([scriptblock]$Condition, [string]$Description) {
    $Deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (!(& $Condition)) {
        if ([DateTime]::UtcNow -ge $Deadline) { throw "Timed out: $Description" }
        Start-Sleep -Milliseconds 50
    }
}

function Healthy([int]$Port) {
    try { $null = Invoke-RestMethod "http://127.0.0.1:$Port/healthz" -TimeoutSec 1; return $true }
    catch { return $false }
}

function Read-Log([string]$Path) {
    $Reader = [IO.StreamReader]::new([IO.FileStream]::new(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite))
    try { return $Reader.ReadToEnd() } finally { $Reader.Dispose() }
}

function Track-Spawn([int]$Id, [int]$ParentId, [switch]$Required) {
    $Process = $null
    try {
        $Process = Get-Process -Id $Id -ErrorAction Stop
        $null = $Process.Handle
        $Identity = "$($Process.Id):$($Process.StartTime.ToUniversalTime().Ticks)"
        if ($script:TrackedSpawned.ContainsKey($Identity)) {
            $Process.Dispose()
            return $script:TrackedSpawned[$Identity]
        }
        $Lineage = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$Id" -ErrorAction Stop
        if (!$Lineage -or $Lineage.ParentProcessId -ne $ParentId -or
            [Math]::Abs(($Process.StartTime.ToUniversalTime() - $Lineage.CreationDate.ToUniversalTime()).TotalSeconds) -gt 1) {
            throw "Unexpected spawned process identity for pid=$Id"
        }
        $script:TrackedSpawned[$Identity] = $Process
        $script:SpawnedProcesses.Add($Process)
        return $Process
    } catch {
        $UnverifiedAlive = $false
        if ($Process) {
            try { $UnverifiedAlive = !$Process.HasExited } catch {}
            $Process.Dispose()
        }
        if ($UnverifiedAlive) {
            $script:CleanupFailures.Add("Could not safely register logged pid=$Id; it was not signaled")
        }
        if ($Required) { throw }
    }
}

function Track-LoggedSpawns([string]$Log, [int]$ParentId) {
    try { $Text = Read-Log $Log } catch { return }
    foreach ($Match in [regex]::Matches($Text, '(?m)^imp: owned (?:node|gateway|SSH) pid=(\d+)\r?\n')) {
        $null = Track-Spawn ([int]$Match.Groups[1].Value) $ParentId
    }
}

function Stop-ExactProcess([System.Diagnostics.Process]$Process) {
    try {
        if (!$Process.HasExited) {
            $Process.Kill()
            if (!$Process.WaitForExit(15000)) {
                $script:CleanupFailures.Add("Process handle $($Process.Id) did not exit")
            }
        }
    } catch {
        $StillAlive = $true
        try { $StillAlive = !$Process.HasExited } catch {}
        if ($StillAlive) {
            $script:CleanupFailures.Add("Process handle cleanup failed: $($_.Exception.Message)")
        }
    }
}

try {
    New-Item -ItemType Directory $Temporary | Out-Null
    $TemporaryCreated = $true
    New-Item -ItemType Directory $Config | Out-Null
    $ConfigCreated = $true

    $SshListener.Start()
    $SshPort = $SshListener.LocalEndpoint.Port
    $Reserve = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $Reserve.Start()
    $ExternalPort = $Reserve.LocalEndpoint.Port
    $Reserve.Stop()
    $External = Start-Process $Sidecar -ArgumentList @('--port', $ExternalPort) -PassThru `
        -RedirectStandardError (Join-Path $Temporary 'external.log')
    $ExternalProcesses.Add($External)
    $null = $External.Handle
    Wait-Until { Healthy $ExternalPort } 'external relay ready'
    [IO.File]::WriteAllText((Join-Path $Config 'remote-access.json'),
        (@{ wssEnabled = $true; gatewayPort = 8788; pairingTokenSha256 = ('0' * 64) } | ConvertTo-Json))

    foreach ($Case in @(@{Mode='local';Hard=$true}, @{Mode='managed';Hard=$true}, @{Mode='local';Hard=$false})) {
        $Connection = @{mode=$Case.Mode}
        if ($Case.Mode -eq 'managed') {
            # A local banner-stalling TCP peer keeps actual OpenSSH alive, without keys/VPS/MUD access.
            $Connection.sshTarget = "ssh://imp-ownership@127.0.0.1:$SshPort"
        }
        [IO.File]::WriteAllText((Join-Path $Config 'tunnel.json'), ($Connection | ConvertTo-Json))
        $Log = Join-Path $Temporary ($Case.Mode + '-' + $Case.Hard + '.log')
        $Desktop = Start-Process $Executable -PassThru -RedirectStandardError $Log
        $DesktopProcesses.Add($Desktop)
        $null = $Desktop.Handle
        $Runs.Add([pscustomobject]@{Log=$Log; ParentId=$Desktop.Id})
        Wait-Until {
            if ($Desktop.HasExited) { throw "Desktop exited during startup; see $Log" }
            (Healthy 8787) -and (Healthy 8788) -and
                (($Case.Mode -ne 'managed') -or ((Read-Log $Log) -match '(?m)^imp: owned SSH pid=\d+\r?\n'))
        } 'desktop-owned runtime ready'
        Track-LoggedSpawns $Log $Desktop.Id
        $Text = Read-Log $Log
        $Roles = if ($Case.Mode -eq 'managed') { @('node', 'gateway', 'SSH') } else { @('node', 'gateway') }
        $Owned = @()
        foreach ($Role in $Roles) {
            $Match = [regex]::Match($Text, "(?m)^imp: owned $Role pid=(\d+)\r?\n")
            if (!$Match.Success) { throw "Missing spawn PID for $Role" }
            $Owned += Track-Spawn ([int]$Match.Groups[1].Value) $Desktop.Id -Required
        }
        if ($Case.Mode -eq 'managed' -or !$Case.Hard) {
            for ($Index = 0; $Index -lt $Roles.Count; $Index++) {
                $Role = $Roles[$Index]
                $Old = $Owned[$Index]
                $Old.Kill()
                $Old.WaitForExit()
                $Replacement = $null
                Wait-Until {
                    Track-LoggedSpawns $Log $Desktop.Id
                    $Matches = [regex]::Matches((Read-Log $Log), "(?m)^imp: owned $Role pid=(\d+)\r?\n")
                    if ($Matches.Count -eq 0) { return $false }
                    $Replacement = [int]$Matches[$Matches.Count - 1].Groups[1].Value
                    ($Replacement -ne $Old.Id) -and (Healthy 8787) -and (Healthy 8788)
                } "$Role supervisor restart"
                Track-LoggedSpawns $Log $Desktop.Id
                $Matches = [regex]::Matches((Read-Log $Log), "(?m)^imp: owned $Role pid=(\d+)\r?\n")
                $Replacement = [int]$Matches[$Matches.Count - 1].Groups[1].Value
                $Owned[$Index] = Track-Spawn $Replacement $Desktop.Id -Required
                Write-Output "$Role restart: $($Old.Id) -> $Replacement"
            }
        }
        if ($InjectFailureAfterSpawn -and $Case.Mode -eq 'managed') {
            $InjectionReached = $true
            throw 'Injected failure after managed replacements were discovered'
        }
        $Started = [DateTime]::UtcNow
        if ($Case.Hard) {
            $Desktop.Kill() # TerminateProcess: no Rust Drop or Tauri Exit callbacks.
        } elseif (!$Desktop.CloseMainWindow()) {
            throw 'Could not request the real window-close path'
        }
        Wait-Until { $Desktop.HasExited -and (@($Owned | Where-Object { !$_.HasExited }).Count -eq 0) } 'owned processes exit'
        if ((Healthy 8787) -or (Healthy 8788)) { throw 'Owned listener survived desktop exit' }
        if ($External.HasExited -or !(Healthy $ExternalPort)) { throw 'External relay was terminated' }
        [pscustomobject]@{
            mode=$Case.Mode; abrupt=$Case.Hard; desktop=$Desktop.Id;
            owned=@($Owned | ForEach-Object Id); external=$External.Id;
            exitMilliseconds=([DateTime]::UtcNow-$Started).TotalMilliseconds;
            portsReleased=$true; externalAlive=$true
        } | ConvertTo-Json -Compress
        $Desktop = $null
    }
    Write-Output 'Native ownership acceptance passed: hard death, real SSH spawn, normal close, relaunch, external safety.'
} catch {
    $Failure = $_
} finally {
    foreach ($Run in $Runs) {
        Track-LoggedSpawns $Run.Log $Run.ParentId
    }
    foreach ($Process in $DesktopProcesses) { Stop-ExactProcess $Process }
    foreach ($Run in $Runs) {
        Track-LoggedSpawns $Run.Log $Run.ParentId
    }
    foreach ($Process in $SpawnedProcesses) { Stop-ExactProcess $Process }
    foreach ($Process in $ExternalProcesses) { Stop-ExactProcess $Process }
    foreach ($Process in @($DesktopProcesses) + @($SpawnedProcesses) + @($ExternalProcesses)) {
        try {
            if (!$Process.HasExited) { $CleanupFailures.Add("Process handle $($Process.Id) remained alive") }
        } catch {
            $CleanupFailures.Add("Process handle verification failed: $($_.Exception.Message)")
        }
        try { $Process.Dispose() } catch { $CleanupFailures.Add("Process handle disposal failed: $($_.Exception.Message)") }
    }
    try { if ($Reserve -and $Reserve.Server.IsBound) { $Reserve.Stop() } } catch {
        $CleanupFailures.Add("Reserved listener cleanup failed: $($_.Exception.Message)")
    }
    try { if ($SshListener.Server.IsBound) { $SshListener.Stop() } } catch {
        $CleanupFailures.Add("SSH listener cleanup failed: $($_.Exception.Message)")
    }
    if ($ConfigCreated) {
        try { Remove-Item $Config -Recurse -Force } catch { $CleanupFailures.Add("Config cleanup failed: $($_.Exception.Message)") }
    }
    if ($TemporaryCreated) {
        try { Remove-Item $Temporary -Recurse -Force } catch { $CleanupFailures.Add("Temporary cleanup failed: $($_.Exception.Message)") }
    }
    if ($InjectionReached -and !$CleanupFailures.Count) {
        Write-Output 'Injected failure cleanup passed: all exact fixture handles exited.'
    }
}

if ($Failure) {
    if ($CleanupFailures.Count) { Write-Warning ($CleanupFailures -join '; ') }
    throw $Failure
}
if ($CleanupFailures.Count) { throw ($CleanupFailures -join '; ') }
