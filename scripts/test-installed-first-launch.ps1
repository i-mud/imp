param(
    [Parameter(Mandatory)][string]$Installer,
    [int]$AliveSeconds = 15,
    [int]$ProvisionSeconds = 60,
    [switch]$CheckConnectionUi
)

# Install the NSIS artifact into the default per-user layout (<LocalAppData>\Imp, the layout in which
# v0.3.1 crashed on first launch), start the installed desktop, and require it to stay alive for
# $AliveSeconds and to provision the Mudlet helper within $ProvisionSeconds. The installed app uses
# the real application identifier, so this runs only on a fresh profile (such as a CI runner): it
# refuses any existing installation, Imp settings, WebView data, or running Imp desktop rather than
# loading or changing them. The optional UI step additionally requires Node 24 and is for a fresh
# interactive Windows CI runner; it enables a temporary loopback-only WebView debugger.
$ErrorActionPreference = 'Stop'
$Config = Get-Content (Join-Path $PSScriptRoot '..\apps\desktop\src-tauri\tauri.conf.json') -Raw |
    ConvertFrom-Json
$Version = $Config.version
$InstallDir = Join-Path $env:LOCALAPPDATA 'Imp'
$Exe = Join-Path $InstallDir 'imp-desktop.exe'
$Bundle = Join-Path $InstallDir 'mudlet-bundle'
$Root = Join-Path $InstallDir 'mudlet'
$Provisioned = Join-Path $Root "versions\$Version"
$AppConfig = Join-Path ([Environment]::GetFolderPath('ApplicationData')) $Config.identifier
$TunnelConfig = Join-Path $AppConfig 'tunnel.json'
foreach ($Existing in @(
        $InstallDir,
        $AppConfig,
        (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $Config.identifier))) {
    if (Test-Path $Existing) { throw "Refusing to run against existing Imp state: $Existing" }
}
if (Get-Process imp-desktop -ErrorAction SilentlyContinue) { throw 'Refusing to run while Imp is running' }

function Assert-RuntimePortsFree {
    $Ports = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port
    foreach ($Port in 8787, 8788, 8789) {
        if ($Ports -contains $Port) { throw "Refusing to run with Imp runtime port $Port occupied" }
    }
}

Assert-RuntimePortsFree
if ($CheckConnectionUi) {
    $Node = (Get-Command node.exe).Source
    if ($env:WEBVIEW2_USER_DATA_FOLDER -or $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS) {
        throw 'Refusing to run with pre-existing WebView2 overrides'
    }
    # The Managed fixture is a literal loopback target; stock config files are fine, indirection is not.
    foreach ($SshConfig in @(
            (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh\config'),
            (Join-Path $env:ProgramData 'ssh\ssh_config'))) {
        if ((Test-Path $SshConfig) -and (Select-String -Path $SshConfig -Pattern '^\s*[^#\s]' -Quiet)) {
            throw "Refusing synthetic SSH with non-comment directives in $SshConfig"
        }
    }
    # Registry policy outranks the environment, so refuse any existing WebView2 policy values.
    $PolicyKeys = 'HKLM:\SOFTWARE\Policies\Microsoft\Edge\WebView2\UserDataFolder',
        'HKLM:\SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments'
    foreach ($Key in $PolicyKeys + ($PolicyKeys -replace '^HKLM:', 'HKCU:')) {
        if ((Test-Path $Key) -and (Get-Item $Key).GetValueNames()) { throw "Refusing to run with existing WebView2 policy: $Key" }
    }
    $Elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    $PolicyActive = $false
    $CreatedPolicyKeys = @()
    Add-Type -Namespace Imp -Name Token -MemberDefinition @'
[DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr p, uint access, out IntPtr token);
[DllImport("advapi32.dll", SetLastError = true)] static extern bool GetTokenInformation(IntPtr token, int cls, IntPtr buffer, int length, out int needed);
[DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
public static int Rid(IntPtr process) {
    IntPtr token; int needed;
    if (!OpenProcessToken(process, 8, out token)) return -1;
    IntPtr buffer = Marshal.AllocHGlobal(256);
    try {
        if (!GetTokenInformation(token, 25, buffer, 256, out needed)) return -1;
        IntPtr sid = Marshal.ReadIntPtr(buffer);
        int count = Marshal.ReadByte(sid, 1);
        return Marshal.ReadInt32(sid, 8 + 4 * (count - 1));
    } finally { Marshal.FreeHGlobal(buffer); CloseHandle(token); }
}
public static int ElevationType(IntPtr process) {
    IntPtr token; int needed;
    if (!OpenProcessToken(process, 8, out token)) return -1;
    IntPtr buffer = Marshal.AllocHGlobal(8);
    try {
        if (!GetTokenInformation(token, 18, buffer, 8, out needed)) return -1;
        return Marshal.ReadInt32(buffer);
    } finally { Marshal.FreeHGlobal(buffer); CloseHandle(token); }
}
'@
    $WebView = Join-Path $InstallDir ('acceptance-webview-' + [guid]::NewGuid())
    $Reservation = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $Reservation.Start()
    $DebugPort = $Reservation.LocalEndpoint.Port
    $Reservation.Stop()
    $WebViewArgs = "--no-proxy-server --remote-debugging-address=127.0.0.1 --remote-debugging-port=$DebugPort"
}

function Test-Helper([string]$Directory) {
    (Test-Path (Join-Path $Directory 'imp-mudlet-helper.exe') -PathType Leaf) -and
    (Test-Path (Join-Path $Directory 'Imp.mpackage') -PathType Leaf) -and
    (Test-Path (Join-Path $Directory 'imp-mudlet-runtime') -PathType Container)
}

function Get-Staging { @(Get-ChildItem $Root -Force -Directory -Filter '.staging-*' -ErrorAction SilentlyContinue) }

function Test-Provisioned {
    $Current = Join-Path $Root 'current.txt'
    (Test-Path $Current -PathType Leaf) -and (Get-Content $Current -Raw).Trim() -eq "versions/$Version" -and
    (Test-Helper $Provisioned) -and (Test-Path (Join-Path $Root 'Imp.mpackage') -PathType Leaf) -and
    -not (Get-Staging)
}

function Get-Integrity($Process) {
    '0x{0:X}' -f [Imp.Token]::Rid($Process.Handle)
}

function Get-WebViewProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" | Where-Object { $_.CommandLine })
}

function Get-OwnedWebViews($Process) {
    $Table = @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'")
    $Seen = @{}
    $Roots = @([uint32]$Process.Id)
    while ($Roots) {
        $Next = @($Table | Where-Object { $_.ParentProcessId -in $Roots -and !$Seen.ContainsKey($_.ProcessId) })
        $Roots = @(foreach ($Item in $Next) { $Item.ProcessId })
        foreach ($Item in $Next) { $Seen[$Item.ProcessId] = $true }
    }
    foreach ($Item in $Table | Where-Object { $Seen.ContainsKey($_.ProcessId) }) {
        try {
            $Child = [Diagnostics.Process]::GetProcessById([int]$Item.ProcessId)
            $null = $Child.Handle
            if ($Child.StartTime -ge $Process.StartTime -and
                [Math]::Abs(($Child.StartTime.ToUniversalTime() - $Item.CreationDate.ToUniversalTime()).TotalMilliseconds) -lt 1) {
                $Child
            } else { $Child.Dispose() }
        } catch { }
    }
}

# Diagnostics are Write-Host only (the caller's success stream carries the Process) and are limited to
# launcher-provided flag booleans, integrity/session/elevation facts, versions and child command-line
# matches; never the environment, process memory, credentials or account names.
function Write-LaunchEvidence([string]$Label, $Process, [bool]$LauncherEnv) {
    $All = Get-WebViewProcesses
    $Child = $All | Where-Object { $_.ParentProcessId -eq $Process.Id -and $_.CommandLine -notlike '*--type=*' } |
        Select-Object -First 1
    $Rid = -1; $Runtime = '<none>'; $Udf = $false; $Debug = $false; $Type = -1
    if ($Child) {
        try {
            $Rid = [Imp.Token]::Rid([Diagnostics.Process]::GetProcessById($Child.ProcessId).Handle)
            $Runtime = (Get-Item $Child.ExecutablePath).VersionInfo.FileVersion
        } catch { }
        $Udf = $Child.CommandLine.IndexOf($WebView, [StringComparison]::OrdinalIgnoreCase) -ge 0
        $Debug = $Child.CommandLine.Contains("--remote-debugging-port=$DebugPort")
    }
    $Own = [Diagnostics.Process]::GetCurrentProcess()
    $Type = [Imp.Token]::ElevationType($Own.Handle)
    $Lua = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableLUA -ErrorAction SilentlyContinue).EnableLUA
    $Listen = [bool](Get-NetTCPConnection -State Listen -LocalPort $DebugPort -ErrorAction SilentlyContinue)
    $Owned = @(Get-OwnedWebViews $Process)
    $OwnedCount = $Owned.Count
    foreach ($Handle in $Owned) { $Handle.Dispose() }
    $RunnerIL = '0x{0:X}' -f [Imp.Token]::Rid($Own.Handle)
    $DesktopIL = '0x{0:X}' -f [Imp.Token]::Rid($Process.Handle)
    $BrowserIL = '0x{0:X}' -f $Rid
    Write-Host ("evidence[$Label]: elevated=$Elevated enableLUA=$Lua elevationType=$Type runnerSession=$($Own.SessionId) " +
        "desktopSession=$($Process.SessionId) runnerIL=$RunnerIL desktopIL=$DesktopIL browserChildOfApp=$([bool]$Child) " +
        "browserIL=$BrowserIL runtime=$Runtime launcherEnv=$LauncherEnv policy=$PolicyActive udfMatch=$Udf debugArgMatch=$Debug " +
        "ownedWebViews=$OwnedCount competingWebViews=$($All.Count - $OwnedCount) listener=$Listen")
    [pscustomobject]@{ HostHigh = [Imp.Token]::Rid($Process.Handle) -ge 0x3000 }
}

function Wait-DebugListener($Process, [int]$Seconds) {
    $Deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $Listeners = @(Get-NetTCPConnection -State Listen -LocalPort $DebugPort -ErrorAction SilentlyContinue)
        if ($Listeners) { return $Listeners }
        if ($Process.HasExited) { throw 'Desktop exited while waiting for WebView2' }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $Deadline)
}

# Fallback for an elevated host app, per Microsoft's guidance to use the appropriate override flags:
# https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/security#for-an-elevated-host-app-use-appropriate-override-flags
# Executable-scoped HKLM policies (never wildcard) apply only on a disposable GitHub-hosted runner;
# an extra user, token or scheduled task would change the profile, session and integrity the
# production binary is meant to be tested in.
function Enable-WebViewPolicy {
    $script:PolicyActive = $true
    foreach ($Key in $PolicyKeys) {
        for ($Missing = $Key; !(Test-Path $Missing); $Missing = Split-Path $Missing) {
            $script:CreatedPolicyKeys += $Missing
        }
        if (!(Test-Path $Key)) { $null = New-Item $Key -Force }
    }
    Set-ItemProperty $PolicyKeys[0] -Name 'imp-desktop.exe' -Value $WebView
    Set-ItemProperty $PolicyKeys[1] -Name 'imp-desktop.exe' -Value $WebViewArgs
}

function Restore-WebViewPolicy {
    if (!$PolicyActive) { return }
    foreach ($Key in $PolicyKeys) {
        if (Test-Path $Key) {
            if ($null -ne (Get-Item $Key).GetValue('imp-desktop.exe')) { Remove-ItemProperty $Key -Name 'imp-desktop.exe' -ErrorAction Stop }
        }
        if ((Test-Path $Key) -and $null -ne (Get-Item $Key).GetValue('imp-desktop.exe')) { throw "Policy value was not removed: $Key" }
    }
    foreach ($Key in ($CreatedPolicyKeys | Sort-Object Length -Descending)) {
        if (!(Test-Path $Key)) { continue }
        $RegistryKey = Get-Item $Key
        if ($RegistryKey.ValueCount -or $RegistryKey.SubKeyCount) { throw "Refusing to remove a nonempty policy key: $Key" }
        Remove-Item $Key -ErrorAction Stop
    }
    $script:PolicyActive = $false
}

# Bounded wait for every fixture artifact: ports, the desktop, and WebView processes in the isolated profile.
function Wait-FixtureGone {
    $Deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $Ports = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port
        $Left = @(Get-WebViewProcesses |
            Where-Object { $_.CommandLine.IndexOf($WebView, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
        if (!($Ports | Where-Object { $_ -in 8787, 8788, 8789, $DebugPort }) -and !$Left -and
            !(Get-Process imp-desktop -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Fixture listener, desktop or isolated WebView survived exit'
}

function Start-Desktop([string]$Log, [bool]$UseEnvironment) {
    Assert-RuntimePortsFree
    try {
        if ($UseEnvironment) {
            $env:WEBVIEW2_USER_DATA_FOLDER = $WebView
            $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $WebViewArgs
        }
        $Process = Start-Process $Exe -PassThru -RedirectStandardError $Log
        # Redirection otherwise returns a Process without an open handle.
        $null = $Process.Handle
        return $Process
    } finally {
        if ($UseEnvironment) {
            Remove-Item Env:\WEBVIEW2_USER_DATA_FOLDER, Env:\WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
        }
    }
}

function Start-FixtureDesktop([string]$Log) {
    if (!$CheckConnectionUi) { return Start-Desktop $Log $false }
    # Same-context env-only launch first; the policy is a hosted-CI-only fallback.
    $Process = Start-Desktop $Log (!$PolicyActive)
    if (Wait-DebugListener $Process 15) {
        if (!$PolicyActive -and !$script:EvidenceShown) {
            $script:EvidenceShown = $true
            $null = Write-LaunchEvidence 'env-only' $Process $true
        }
        return $Process
    }
    $Label = if ($PolicyActive) { 'policy' } else { 'env-only' }
    $Evidence = Write-LaunchEvidence $Label $Process (!$PolicyActive)
    $Fallback = !$PolicyActive -and $Elevated -and $env:IMP_ACCEPTANCE_RUNNER -eq 'github-hosted' -and $Evidence.HostHigh
    Stop-FixtureDesktop $Process $Log
    if (!$Fallback) { throw 'Expected a loopback WebView2 debugger; no supported fallback applies here' }
    if (!$Process.HasExited) { throw 'Installed desktop is still running after cleanup' }
    Wait-FixtureGone
    Enable-WebViewPolicy
    $Process = Start-Desktop $Log $false
    $Listening = Wait-DebugListener $Process 15
    $null = Write-LaunchEvidence 'policy' $Process $false
    if (!$Listening) {
        Stop-FixtureDesktop $Process $Log
        throw 'Expected a loopback WebView2 debugger after the executable-scoped policy launch'
    }
    return $Process
}

function Stop-FixtureDesktop($Process, [string]$Log) {
    # Descendant handles are captured while the parent links still exist, then waited on and, if
    # needed, stopped through those exact handles rather than by a possibly stale PID.
    $Owned = if ($CheckConnectionUi) { @(Get-OwnedWebViews $Process) } else { @() }
    if (!$Process.HasExited) {
        try { $Process.Kill() } catch { if (!$Process.HasExited) { Write-Error $_ -ErrorAction Continue } }
        if (!$Process.WaitForExit(10000)) { Write-Error 'Installed desktop did not stop' -ErrorAction Continue }
    }
    $Deadline = [DateTime]::UtcNow.AddSeconds(10)
    foreach ($Child in $Owned) {
        $Remaining = [Math]::Max(0, ($Deadline - [DateTime]::UtcNow).TotalMilliseconds)
        if (!$Child.WaitForExit([int]$Remaining)) {
            Write-Error 'An owned WebView2 process outlived the desktop; stopping it' -ErrorAction Continue
            try { $Child.Kill() } catch { if (!$Child.HasExited) { Write-Error $_ -ErrorAction Continue } }
        }
        $Child.Dispose()
    }
    Remove-Item $Log -ErrorAction SilentlyContinue
}

function Invoke-ConnectionUi($Process, [string]$Phase) {
    $Listeners = @(Wait-DebugListener $Process 15)
    if (!$Listeners) { throw 'Expected a loopback WebView2 debugger' }
    foreach ($Listener in $Listeners) {
        $Owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)"
        if ($Listener.LocalAddress -notin '127.0.0.1', '::1' -or
            $Owner.Name -ne 'msedgewebview2.exe' -or
            $Owner.CommandLine.IndexOf($WebView, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
            $Owner.ParentProcessId -ne $Process.Id -or
            !$Owner.CommandLine.Contains("--remote-debugging-port=$DebugPort")) {
            throw 'Refusing to connect to a debugger outside the isolated Imp WebView'
        }
    }
    & $Node (Join-Path $PSScriptRoot 'test-installed-connection.mjs') $DebugPort $Phase $TunnelConfig
    if ($LASTEXITCODE) { throw [IO.InvalidDataException]::new("Installed connection UI phase failed: $Phase") }
}

function Assert-NoManagedRuntime([string]$Log) {
    if (Select-String -Path $Log -Pattern '^imp: owned (gateway|SSH) pid=' -Quiet) {
        throw 'Installed desktop started unexpected SSH or remote gateway processes'
    }
}

function Assert-OwnedNode([string]$Log) {
    $Deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $Owned = Select-String -Path $Log -Pattern '^imp: owned node pid=(\d+)' | Select-Object -Last 1
        $Listeners = Get-NetTCPConnection -State Listen -LocalPort 8787 -ErrorAction SilentlyContinue
        if ($Owned -and $Listeners.OwningProcess -contains [int]$Owned.Matches[0].Groups[1].Value) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Installed desktop did not own the local node listener'
}

$Completed = $false
try {
$Setup = Start-Process (Resolve-Path $Installer).Path -ArgumentList '/S' -Wait -PassThru
if ($Setup.ExitCode) { throw "Installer exited with $($Setup.ExitCode)" }
# Separates packaging/layout regressions from runtime provisioning failures.
if (-not (Test-Helper $Bundle)) { throw "Installed Mudlet resource is incomplete: $Bundle" }

# Tauri builds the HUD window before the setup hook that provisions; its stderr says whether
# provisioning ran. Only the Mudlet helper lines are ever reported.
$StandardError = Join-Path $env:TEMP ('imp-first-launch-' + [guid]::NewGuid() + '.log')
$App = Start-FixtureDesktop $StandardError
try {
    $Started = [DateTime]::UtcNow
    $Ready = $false
    while ($true) {
        if ($App.HasExited) { throw ('Installed desktop exited with 0x{0:X8}' -f $App.ExitCode) }
        $Elapsed = ([DateTime]::UtcNow - $Started).TotalSeconds
        if (-not $Ready) { $Ready = Test-Provisioned }
        if ($Ready -and $Elapsed -ge $AliveSeconds) { break }
        if (-not $Ready -and $Elapsed -ge $ProvisionSeconds) {
            $Log = Get-Content $StandardError -ErrorAction SilentlyContinue | Where-Object { $_ -like 'imp: Mudlet helper*' }
            throw (@(
                    "Mudlet helper was not provisioned within $ProvisionSeconds s"
                    "installed bundle complete=$(Test-Helper $Bundle)"
                    "writable root exists=$(Test-Path $Root)"
                    "current.txt exists=$(Test-Path (Join-Path $Root 'current.txt'))"
                    "version directory exists=$(Test-Path $Provisioned)"
                    "staging directories=$((Get-Staging).Count)"
                    "desktop running=$(-not $App.HasExited)"
                    "provisioning log=$(if ($Log) { $Log -join ' | ' } else { '<none>' })"
                ) -join '; ')
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-Provisioned)) { throw 'Provisioned Mudlet helper did not remain complete' }
    Assert-NoManagedRuntime $StandardError
    Assert-OwnedNode $StandardError
    if (Test-Path $TunnelConfig) { throw 'First launch unexpectedly wrote connection settings' }
    if ($CheckConnectionUi) { Invoke-ConnectionUi $App 'first' }
    if (Test-Path $TunnelConfig) { throw 'Deferring first-use setup wrote connection settings' }
    # Checked last so a crash during the final poll interval or the checks above still fails.
    if ($App.HasExited) { throw ('Installed desktop exited with 0x{0:X8}' -f $App.ExitCode) }
    Write-Output ("Installed desktop $Version provisioned the Mudlet helper and stayed alive for {0:N0} s." -f
        ([DateTime]::UtcNow - $Started).TotalSeconds)
} finally {
    Stop-FixtureDesktop $App $StandardError
}
if (-not $App.HasExited) { throw 'Installed desktop is still running after cleanup' }

if ($CheckConnectionUi) {
    # Reserve, but never listen on, the fixture endpoint. Neither WSS nor SSH gets an external target.
    $Blocked = [Net.Sockets.Socket]::new([Net.Sockets.AddressFamily]::InterNetwork,
        [Net.Sockets.SocketType]::Stream, [Net.Sockets.ProtocolType]::Tcp)
    $Blocked.ExclusiveAddressUse = $true
    try {
        $Blocked.Bind([Net.IPEndPoint]::new([Net.IPAddress]::Loopback, 0))
        $Port = $Blocked.LocalEndPoint.Port
        $Invalid = '{"mode":'
        $FailedPhases = @()
        foreach ($Case in @(
                @{ Phase = 'deferred' },
                @{ Phase = 'local' },
                @{ Phase = 'managed'; Config = @{ mode = 'managed'; sshTarget = "ssh://imp-fixture@127.0.0.1:$Port" } },
                @{ Phase = 'external'; Config = @{ mode = 'external' } },
                @{ Phase = 'direct'; Config = @{ mode = 'direct'; remoteUrl = "wss://127.0.0.1:$Port/state"; pairingToken = ('A' * 43) } },
                @{ Phase = 'legacy'; Config = @{} },
                @{ Phase = 'invalid' })) {
            if ($null -ne $Case.Config) {
                [IO.File]::WriteAllText($TunnelConfig, ($Case.Config | ConvertTo-Json))
            } elseif ($Case.Phase -eq 'invalid') {
                [IO.File]::WriteAllText($TunnelConfig, $Invalid)
            }
            # Allow the previous desktop's owned node and isolated WebView to exit before relaunch.
            Wait-FixtureGone
            $Log = Join-Path $env:TEMP ('imp-installed-' + [guid]::NewGuid() + '.log')
            $App = Start-FixtureDesktop $Log
            try {
                Invoke-ConnectionUi $App $Case.Phase
                Assert-OwnedNode $Log
                if ($Case.Phase -ne 'managed') { Assert-NoManagedRuntime $Log }
                if ($Case.Phase -eq 'managed' -and
                    !(Select-String -Path $Log -Pattern '^imp: owned SSH pid=' -Quiet)) {
                    throw 'Saved Managed route did not start its owned SSH process'
                }
                switch ($Case.Phase) {
                    'deferred' {
                        if ((Get-Content $TunnelConfig -Raw | ConvertFrom-Json).mode -ne 'local') {
                            throw 'Installed UI did not save the selected local route'
                        }
                    }
                    'direct' {
                        $Saved = Get-Content $TunnelConfig -Raw | ConvertFrom-Json
                        if ($Saved.mode -ne 'local' -or $Saved.pairingToken -or $Saved.remoteUrl) {
                            throw 'Confirmed mode switch did not remove the synthetic Direct credentials'
                        }
                    }
                    'invalid' {
                        if ([IO.File]::ReadAllText($TunnelConfig) -cne $Invalid) {
                            throw 'Invalid settings were overwritten'
                        }
                    }
                }
            } catch [IO.InvalidDataException] {
                # Only the Local case depends on the previous (deferred) save. Security and cleanup
                # failures remain fatal; independent seeded cases can report their own UI assertions.
                if ($Case.Phase -eq 'deferred') { throw }
                $FailedPhases += $Case.Phase
            } finally {
                Stop-FixtureDesktop $App $Log
            }
            if (!$App.HasExited) { throw 'Installed desktop is still running after cleanup' }
        }
        if ($FailedPhases) { throw "Installed connection UI assertions failed: $($FailedPhases -join ', ')" }
    } finally {
        $Blocked.Dispose()
    }
}
$Completed = $true
} finally {
    $CleanupErrors = @()
    if ($CheckConnectionUi) {
        try { Restore-WebViewPolicy } catch { $CleanupErrors += "WebView2 policy cleanup failed: $($_.Exception.Message)" }
        try { Wait-FixtureGone } catch { $CleanupErrors += $_.Exception.Message }
    }
    foreach ($Message in $CleanupErrors) { Write-Error $Message -ErrorAction Continue }
    # Throwing here would replace the original failure, so only surface cleanup errors after success.
    if ($CleanupErrors -and $Completed) { throw 'Acceptance cleanup failed' }
    if ($CheckConnectionUi -and !$CleanupErrors) { Write-Host 'Cleanup verified: no fixture listener or desktop remains; temporary WebView policy removed.' }
}
