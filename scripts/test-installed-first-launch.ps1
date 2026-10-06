param(
    [Parameter(Mandatory)][string]$Installer,
    [int]$AliveSeconds = 15,
    [int]$ProvisionSeconds = 60
)

# Install the NSIS artifact into the default per-user layout (<LocalAppData>\Imp, the layout in which
# v0.3.1 crashed on first launch), start the installed desktop, and require it to stay alive for
# $AliveSeconds and to provision the Mudlet helper within $ProvisionSeconds. The installed app uses
# the real application identifier, so this runs only on a fresh profile (such as a CI runner): it
# refuses any existing installation, Imp settings, WebView data, or running Imp desktop rather than
# loading or changing them.
$ErrorActionPreference = 'Stop'
$Config = Get-Content (Join-Path $PSScriptRoot '..\apps\desktop\src-tauri\tauri.conf.json') -Raw |
    ConvertFrom-Json
$Version = $Config.version
$InstallDir = Join-Path $env:LOCALAPPDATA 'Imp'
$Exe = Join-Path $InstallDir 'imp-desktop.exe'
$Bundle = Join-Path $InstallDir 'mudlet-bundle'
$Root = Join-Path $InstallDir 'mudlet'
$Provisioned = Join-Path $Root "versions\$Version"
foreach ($Existing in @(
        $InstallDir,
        (Join-Path ([Environment]::GetFolderPath('ApplicationData')) $Config.identifier),
        (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $Config.identifier))) {
    if (Test-Path $Existing) { throw "Refusing to run against existing Imp state: $Existing" }
}
if (Get-Process imp-desktop -ErrorAction SilentlyContinue) { throw 'Refusing to run while Imp is running' }

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

$Setup = Start-Process (Resolve-Path $Installer).Path -ArgumentList '/S' -Wait -PassThru
if ($Setup.ExitCode) { throw "Installer exited with $($Setup.ExitCode)" }
# Separates packaging/layout regressions from runtime provisioning failures.
if (-not (Test-Helper $Bundle)) { throw "Installed Mudlet resource is incomplete: $Bundle" }

# Tauri builds the HUD window before the setup hook that provisions; its stderr says whether
# provisioning ran. Only the Mudlet helper lines are ever reported.
$StandardError = Join-Path $env:TEMP ('imp-first-launch-' + [guid]::NewGuid() + '.log')
$App = Start-Process $Exe -PassThru -RedirectStandardError $StandardError
# With redirection Start-Process returns a process found by id that holds no handle; open one now,
# or ExitCode is unavailable once the desktop exits.
$null = $App.Handle
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
    # Checked last so a crash during the final poll interval or the checks above still fails.
    if ($App.HasExited) { throw ('Installed desktop exited with 0x{0:X8}' -f $App.ExitCode) }
    Write-Output ("Installed desktop $Version provisioned the Mudlet helper and stayed alive for {0:N0} s." -f
        ([DateTime]::UtcNow - $Started).TotalSeconds)
} finally {
    # Cleanup failures must not mask an earlier failure, and must still fail an otherwise passing run.
    if (-not $App.HasExited) {
        Stop-Process -Id $App.Id -ErrorAction Continue
        if (-not $App.WaitForExit(10000)) { Write-Error 'Installed desktop did not stop' -ErrorAction Continue }
    }
    Remove-Item $StandardError -ErrorAction SilentlyContinue
}
if (-not $App.HasExited) { throw 'Installed desktop is still running after cleanup' }
