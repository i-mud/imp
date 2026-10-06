param(
    [Parameter(Mandatory)][string]$Installer,
    [int]$AliveSeconds = 15
)

# Install the NSIS artifact into the default per-user layout (<LocalAppData>\Imp, the layout in which
# v0.3.1 crashed on first launch), start the installed desktop, and require it to stay alive and
# provision the Mudlet helper. The installed app uses the real application identifier, so this runs
# only on a fresh profile (such as a CI runner): it refuses any existing installation, Imp settings,
# WebView data, or running Imp desktop rather than loading or changing them.
$ErrorActionPreference = 'Stop'
$Config = Get-Content (Join-Path $PSScriptRoot '..\apps\desktop\src-tauri\tauri.conf.json') -Raw |
    ConvertFrom-Json
$Version = $Config.version
$InstallDir = Join-Path $env:LOCALAPPDATA 'Imp'
$Exe = Join-Path $InstallDir 'imp-desktop.exe'
$Root = Join-Path $InstallDir 'mudlet'
$Provisioned = Join-Path $Root "versions\$Version"
foreach ($Existing in @(
        $InstallDir,
        (Join-Path ([Environment]::GetFolderPath('ApplicationData')) $Config.identifier),
        (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $Config.identifier))) {
    if (Test-Path $Existing) { throw "Refusing to run against existing Imp state: $Existing" }
}
if (Get-Process imp-desktop -ErrorAction SilentlyContinue) { throw 'Refusing to run while Imp is running' }

$Setup = Start-Process (Resolve-Path $Installer).Path -ArgumentList '/S' -Wait -PassThru
if ($Setup.ExitCode) { throw "Installer exited with $($Setup.ExitCode)" }

$App = Start-Process $Exe -PassThru
try {
    $Deadline = [DateTime]::UtcNow.AddSeconds($AliveSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($App.HasExited) { throw ('Installed desktop exited with 0x{0:X8}' -f $App.ExitCode) }
        Start-Sleep -Milliseconds 500
    }
    $Current = Join-Path $Root 'current.txt'
    if (-not (Test-Path $Current) -or (Get-Content $Current -Raw).Trim() -ne "versions/$Version") {
        throw 'Mudlet helper was not provisioned for this version'
    }
    foreach ($Expected in @(
            (Join-Path $Provisioned 'imp-mudlet-helper.exe'),
            (Join-Path $Provisioned 'Imp.mpackage'),
            (Join-Path $Root 'Imp.mpackage'))) {
        if (-not (Test-Path $Expected -PathType Leaf)) { throw "Provisioned file is missing: $Expected" }
    }
    if (-not (Test-Path (Join-Path $Provisioned 'imp-mudlet-runtime') -PathType Container)) {
        throw 'Provisioned Mudlet runtime is missing'
    }
    if (Get-ChildItem $Root -Force -Directory -Filter '.staging-*') { throw 'Provisioning left staging output' }
    # Checked last so a crash during the final poll interval or the checks above still fails.
    if ($App.HasExited) { throw ('Installed desktop exited with 0x{0:X8}' -f $App.ExitCode) }
    Write-Output "Installed desktop $Version stayed alive for $AliveSeconds s and provisioned the Mudlet helper."
} finally {
    # Cleanup failures must not mask an earlier failure, and must still fail an otherwise passing run.
    if (-not $App.HasExited) {
        Stop-Process -Id $App.Id -ErrorAction Continue
        if (-not $App.WaitForExit(10000)) { Write-Error 'Installed desktop did not stop' -ErrorAction Continue }
    }
}
if (-not $App.HasExited) { throw 'Installed desktop is still running after cleanup' }
