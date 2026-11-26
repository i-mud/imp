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
        if ((Test-Path $SshConfig) -and (Select-String -Path $SshConfig -Pattern '^\s*(Include|Match|Proxy|Host\s)' -Quiet)) {
            throw "Refusing synthetic SSH with routing directives in $SshConfig"
        }
    }
    $WebView = Join-Path $InstallDir ('acceptance-webview-' + [guid]::NewGuid())
    $Reservation = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $Reservation.Start()
    $DebugPort = $Reservation.LocalEndpoint.Port
    $Reservation.Stop()
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

function Start-FixtureDesktop([string]$Log) {
    Assert-RuntimePortsFree
    try {
        if ($CheckConnectionUi) {
            $env:WEBVIEW2_USER_DATA_FOLDER = $WebView
            $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--no-proxy-server --remote-debugging-address=127.0.0.1 --remote-debugging-port=$DebugPort"
        }
        $Process = Start-Process $Exe -PassThru -RedirectStandardError $Log
        # Redirection otherwise returns a Process without an open handle.
        $null = $Process.Handle
        return $Process
    } finally {
        if ($CheckConnectionUi) {
            Remove-Item Env:\WEBVIEW2_USER_DATA_FOLDER, Env:\WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
        }
    }
}

function Stop-FixtureDesktop($Process, [string]$Log) {
    if (!$Process.HasExited) {
        try { $Process.Kill() } catch { if (!$Process.HasExited) { Write-Error $_ -ErrorAction Continue } }
        if (!$Process.WaitForExit(10000)) { Write-Error 'Installed desktop did not stop' -ErrorAction Continue }
    }
    Remove-Item $Log -ErrorAction SilentlyContinue
}

function Invoke-ConnectionUi($Process, [string]$Phase) {
    $Deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        $Listeners = @(Get-NetTCPConnection -State Listen -LocalPort $DebugPort -ErrorAction SilentlyContinue)
        if ($Listeners) { break }
        if ($Process.HasExited) { throw 'Desktop exited while waiting for WebView2' }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $Deadline)
    if (!$Listeners) { throw 'Expected a loopback WebView2 debugger' }
    foreach ($Listener in $Listeners) {
        $Owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)"
        if ($Listener.LocalAddress -notin '127.0.0.1', '::1' -or
            $Owner.Name -ne 'msedgewebview2.exe' -or
            $Owner.CommandLine.IndexOf($WebView, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw 'Refusing to connect to a debugger outside the isolated Imp WebView'
        }
    }
    & $Node (Join-Path $PSScriptRoot 'test-installed-connection.mjs') $DebugPort $Phase $TunnelConfig
    if ($LASTEXITCODE) { throw "Installed connection UI phase failed: $Phase" }
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
        foreach ($Case in @(
                @{ Phase = 'deferred' },
                @{ Phase = 'local' },
                @{ Phase = 'managed'; Config = @{ mode = 'managed'; sshTarget = "ssh://imp-fixture@127.0.0.1:$Port" } },
                @{ Phase = 'external'; Config = @{ mode = 'external' } },
                @{ Phase = 'direct'; Config = @{ mode = 'direct'; remoteUrl = "wss://127.0.0.1:$Port/state"; pairingToken = ('A' * 43) } },
                @{ Phase = 'invalid' })) {
            if ($Case.Config) {
                [IO.File]::WriteAllText($TunnelConfig, ($Case.Config | ConvertTo-Json))
            } elseif ($Case.Phase -eq 'invalid') {
                [IO.File]::WriteAllText($TunnelConfig, $Invalid)
            }
            # Allow the previous desktop's owned node and isolated WebView to exit before relaunch.
            $Deadline = [DateTime]::UtcNow.AddSeconds(10)
            while (([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port |
                    Where-Object { $_ -in 8787, 8788, 8789, $DebugPort })) {
                if ([DateTime]::UtcNow -ge $Deadline) { throw 'Fixture listener survived desktop exit' }
                Start-Sleep -Milliseconds 100
            }
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
            } finally {
                Stop-FixtureDesktop $App $Log
            }
            if (!$App.HasExited) { throw 'Installed desktop is still running after cleanup' }
        }
    } finally {
        $Blocked.Dispose()
    }
}
