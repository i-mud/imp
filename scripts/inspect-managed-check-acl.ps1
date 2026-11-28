# Invoked only by the hosted Managed fixture after an sshd permission rejection.
$ErrorActionPreference = 'Stop'
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$names = @{
    'fixture-directory' = '.'
    'server-config' = 'sshd.config'
    'host-key' = 'host_key'
    'authorized-keys' = 'authorized_keys'
    'client-key' = 'client_key'
}
$root = Get-Item -LiteralPath $request.root -Force
if (!$root.PSIsContainer -or ($root.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Expected an ordinary fixture directory'
}

# Read the native self-relative descriptor without normalizing the DACL through FileSecurity.
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class ImpFixtureSecurity {
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetFileSecurity(string path, uint sections, [Out] byte[] buffer, uint size, out uint needed);

    public static byte[] Read(string path) {
        const uint OwnerAndDacl = 5;
        uint size;
        GetFileSecurity(path, OwnerAndDacl, null, 0, out size);
        if (size == 0 || size > 131072) throw new Win32Exception(Marshal.GetLastWin32Error());
        var descriptor = new byte[size];
        if (!GetFileSecurity(path, OwnerAndDacl, descriptor, size, out size))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return descriptor;
    }
}
'@

function Resolve-AccountSid($account) {
    try {
        ([Security.Principal.NTAccount]::new($account)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        $null
    }
}

$token = [Security.Principal.WindowsIdentity]::GetCurrent()
try {
    $tokenUser = $token.User.Value
    $tokenGroups = @($token.Groups | ForEach-Object { $_.Value })
} finally {
    $token.Dispose()
}
$login = Resolve-AccountSid $request.login
$daemon = $null
if ($request.serverPid -match '^[0-9]{1,10}$') {
    try {
        $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($request.serverPid) AND Name = 'sshd.exe'"
        if ($process) {
            $result = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid
            if ($result.ReturnValue -eq 0) { $daemon = $result.Sid }
        }
    } catch {
        # The fixture may already have exited or the process identity may not be readable.
    }
}
$principals = [ordered]@{
    'token-user' = $tokenUser
    'login-user' = $login
    'sshd-listener-user' = $daemon
    'token-group' = $tokenGroups
    'system' = 'S-1-5-18'
    'administrators' = 'S-1-5-32-544'
    'trusted-installer' = Resolve-AccountSid 'NT SERVICE\TrustedInstaller'
    'builtin-users' = 'S-1-5-32-545'
    'authenticated-users' = 'S-1-5-11'
    'everyone' = 'S-1-1-0'
}

function Get-PrincipalRoles($sid) {
    foreach ($item in $principals.GetEnumerator()) {
        if ($sid -and $item.Value -contains $sid) { $item.Key }
    }
}

$identities = @{}
foreach ($role in 'token-user', 'login-user', 'sshd-listener-user') {
    $identities[$role] = $null
    if ($principals[$role]) { $identities[$role] = @(Get-PrincipalRoles $principals[$role]) }
}
$descriptors = @{}
foreach ($path in $request.paths.PSObject.Properties) {
    $role = $path.Name
    if (!$names.ContainsKey($role)) { throw 'Unexpected fixture role' }
    $descriptors[$role] = $null
    try {
        $item = Get-Item -LiteralPath (Join-Path $root.FullName $names[$role]) -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        $bytes = [ImpFixtureSecurity]::Read($item.FullName)
        $raw = [Security.AccessControl.RawSecurityDescriptor]::new($bytes, 0)
        $dacl = $raw.DiscretionaryAcl
        $sid = $path.Value
        $matching = @()
        $other = @()
        $matched = 0
        for ($i = 0; $dacl -and $i -lt $dacl.Count; $i++) {
            $ace = $dacl[$i]
            $known = $ace -is [Security.AccessControl.KnownAce]
            $reported = [bool]($sid -and $known -and $ace.SecurityIdentifier.Value -eq $sid)
            if ($reported) { $matched++ }
            if (($reported -and $matching.Count -ge $request.limit) -or
                (!$reported -and $other.Count -ge $request.limit)) { continue }
            $entry = @{
                index = $i
                type = [int]$ace.AceType
                flags = [int]$ace.AceFlags
                mask = $null
                principal = $null
                matchesReportedSid = $reported
            }
            if ($known) {
                $entry.mask = '{0:x8}' -f $ace.AccessMask
                $entry.principal = @(Get-PrincipalRoles $ace.SecurityIdentifier.Value)
            }
            if ($reported) { $matching += $entry } else { $other += $entry }
        }
        $descriptors[$role] = @{
            owner = $null
            control = [int]$raw.ControlFlags
            aceCount = $(if ($null -ne $dacl) { $dacl.Count })
            reportedSidMatches = $(if ($sid) { $matched })
            aces = @(($matching + $other) | Select-Object -First $request.limit | Sort-Object { $_.index })
        }
        if ($raw.Owner) { $descriptors[$role].owner = @(Get-PrincipalRoles $raw.Owner.Value) }
    } catch {
        # Never emit the provider's exception, which can include paths and identities.
    }
}
@{ identities = $identities; descriptors = $descriptors } | ConvertTo-Json -Compress -Depth 7
