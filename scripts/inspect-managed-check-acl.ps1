# Invoked only by the hosted Managed fixture after an sshd permission rejection.
$ErrorActionPreference = 'Stop'
function Stop-Inspection($status) {
    @{ status = $status } | ConvertTo-Json -Compress
    exit
}

try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
} catch {
    Stop-Inspection 'invalid-request'
}
$names = @{
    'fixture-directory' = '.'
    'server-config' = 'sshd.config'
    'host-key' = 'host_key'
    'authorized-keys' = 'authorized_keys'
    'client-key' = 'client_key'
}
if (!$request.paths -or !$request.root -or $request.limit -lt 1 -or $request.limit -gt 12) {
    Stop-Inspection 'invalid-request'
}
try {
    $root = Get-Item -LiteralPath $request.root -Force
} catch {
    Stop-Inspection 'fixture-root-unavailable'
}
if (!$root.PSIsContainer -or ($root.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    Stop-Inspection 'fixture-root-redirected'
}

# Read the native self-relative descriptor without normalizing the DACL through FileSecurity.
try {
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
} catch {
    Stop-Inspection 'descriptor-api-unavailable'
}

function Resolve-AccountSid($account) {
    try {
        ([Security.Principal.NTAccount]::new($account)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        $null
    }
}

try {
    $token = [Security.Principal.WindowsIdentity]::GetCurrent()
} catch {
    Stop-Inspection 'current-token-unavailable'
}
try {
    $tokenUser = $token.User.Value
    $tokenGroups = @($token.Groups | ForEach-Object { $_.Value })
} catch {
    Stop-Inspection 'current-token-unavailable'
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
$files = @{}
foreach ($path in $request.paths.PSObject.Properties) {
    $role = $path.Name
    if (!$names.ContainsKey($role)) { Stop-Inspection 'invalid-request' }
    $file = $files[$role] = @{ status = 'file-unavailable'; descriptor = $null }
    try {
        $item = Get-Item -LiteralPath (Join-Path $root.FullName $names[$role]) -Force
        if (($role -eq 'fixture-directory') -ne $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $file.status = 'not-ordinary-file'
            continue
        }
        $file.status = 'native-read-failed'
        $bytes = [ImpFixtureSecurity]::Read($item.FullName)
        $file.status = 'descriptor-decode-failed'
        $raw = [Security.AccessControl.RawSecurityDescriptor]::new($bytes, 0)
        $file.status = 'descriptor-enumeration-failed'
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
        $descriptor = @{
            owner = $null
            control = [int]$raw.ControlFlags
            aceCount = $null
            reportedSidMatches = $null
            aces = @(($matching + $other) | Select-Object -First $request.limit | Sort-Object { $_.index })
        }
        # Empty PowerShell 5.1 subexpressions serialize as objects, not JSON null.
        if ($null -ne $dacl) { $descriptor.aceCount = $dacl.Count }
        if ($sid) { $descriptor.reportedSidMatches = $matched }
        if ($raw.Owner) { $descriptor.owner = @(Get-PrincipalRoles $raw.Owner.Value) }
        $file.descriptor = $descriptor
        $file.status = 'read'
    } catch {
        if ($file.status -eq 'file-unavailable' -and $_.Exception -is [Management.Automation.ItemNotFoundException]) {
            $file.status = 'missing'
        } elseif ($file.status -eq 'native-read-failed' -and
                  $_.Exception.InnerException -is [ComponentModel.Win32Exception] -and
                  $_.Exception.InnerException.NativeErrorCode -eq 5) {
            $file.status = 'access-denied'
        }
    }
}
@{ status = 'completed'; identities = $identities; files = $files } | ConvertTo-Json -Compress -Depth 8
