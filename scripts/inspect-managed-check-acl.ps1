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
using Microsoft.Win32.SafeHandles;

public static class ImpFixtureSecurity {
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetFileSecurity(string path, uint sections, [Out] byte[] buffer, uint size, out uint needed);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security,
        uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandleEx(SafeFileHandle file, int informationClass,
        [Out] byte[] information, uint size);
    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetKernelObjectSecurity(SafeFileHandle file, uint sections,
        [Out] byte[] buffer, uint size, out uint needed);
    [DllImport("advapi32.dll")]
    private static extern int SetSecurityInfo(SafeFileHandle file, int objectType, uint sections, IntPtr owner,
        IntPtr group, byte[] dacl, IntPtr sacl);

    [StructLayout(LayoutKind.Sequential)]
    private struct FileInformation {
        public uint Attributes;
        public uint CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }

    public static SafeFileHandle OpenOwned(string path, string expectedId, bool write) {
        const uint ReadControlAndAttributes = 0x20080, WriteDacl = 0x40000;
        const uint ShareReadAndWrite = 3, OpenExisting = 3, OpenReparsePoint = 0x200000;
        var file = CreateFile(path, ReadControlAndAttributes | (write ? WriteDacl : 0),
            ShareReadAndWrite, IntPtr.Zero, OpenExisting, OpenReparsePoint, IntPtr.Zero);
        try {
            FileInformation info;
            var id = new byte[24];
            const int FileIdInfo = 18;
            if (file.IsInvalid || !GetFileInformationByHandle(file, out info) ||
                !GetFileInformationByHandleEx(file, FileIdInfo, id, (uint)id.Length))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            const uint DirectoryOrReparsePoint = 0x410;
            if ((info.Attributes & DirectoryOrReparsePoint) != 0 || info.Links != 1 ||
                BitConverter.ToString(id).Replace("-", "").ToLowerInvariant() != expectedId)
                throw new InvalidOperationException("Fixture file identity did not match");
            return file;
        } catch {
            file.Dispose();
            throw;
        }
    }

    public static byte[] ReadOpened(SafeFileHandle file) {
        const uint OwnerAndDacl = 5;
        uint size;
        GetKernelObjectSecurity(file, OwnerAndDacl, null, 0, out size);
        if (size == 0 || size > 131072) throw new Win32Exception(Marshal.GetLastWin32Error());
        var descriptor = new byte[size];
        if (!GetKernelObjectSecurity(file, OwnerAndDacl, descriptor, size, out size))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return descriptor;
    }

    public static void WriteDacl(SafeFileHandle file, byte[] dacl) {
        const int FileObject = 1;
        const uint ProtectedDaclOnly = 0x80000004;
        int result = SetSecurityInfo(file, FileObject, ProtectedDaclOnly, IntPtr.Zero, IntPtr.Zero, dacl, IntPtr.Zero);
        if (result != 0) throw new Win32Exception(result);
    }

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

function Get-DescriptorFingerprint($bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Test-AuthorizedCorrection {
    $proof = $request.correction
    $rewrite = [bool]$proof.fingerprint
    if ($env:IMP_ACCEPTANCE_RUNNER -ne 'github-hosted' -or $proof.fileId -cnotmatch '^[0-9a-f]{48}$' -or
        ($rewrite -and ($proof.sid -or $proof.fingerprint -cnotmatch '^[0-9a-f]{64}$')) -or
        (!$rewrite -and $proof.sid -cnotmatch '^S-1(?:-[0-9]{1,20}){2,16}$')) {
        return @{ status = 'descriptor-not-applicable' }
    }

    # The sshd log supplies a SID, never a target path. Pin the original generated file for both checks and the write.
    try {
        $file = [ImpFixtureSecurity]::OpenOwned((Join-Path $root.FullName 'authorized_keys'), $proof.fileId, !$rewrite)
    } catch {
        return @{ status = 'file-identity-unavailable' }
    }
    try {
        $before = [ImpFixtureSecurity]::ReadOpened($file)
        if ($rewrite) {
            if ((Get-DescriptorFingerprint $before) -ceq $proof.fingerprint) { return @{ status = 'verified' } }
            return @{ status = 'correction-verification-failed' }
        }
        $raw = [Security.AccessControl.RawSecurityDescriptor]::new($before, 0)
        $dacl = $raw.DiscretionaryAcl
        $owner = $raw.Owner.Value
        if (!$login -or $login -ne $tokenUser -or $owner -notin @($login, $principals.administrators) -or
            !$dacl -or $dacl.Count -ne 4 -or !($raw.ControlFlags -band 0x1000) -or
            @(Get-PrincipalRoles $proof.sid).Count -ne 0) {
            return @{ status = 'descriptor-not-applicable' }
        }
        $remaining = @($login, $principals.system, $principals.administrators)
        $remove = -1
        for ($i = 0; $i -lt $dacl.Count; $i++) {
            $ace = $dacl[$i]
            if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.AceType -ne 0 -or
                $ace.AceFlags -ne 0 -or $ace.AccessMask -ne 0x001f01ff) {
                return @{ status = 'descriptor-not-applicable' }
            }
            $sid = $ace.SecurityIdentifier.Value
            if ($sid -eq $proof.sid -and $remove -eq -1) {
                $remove = $i
            } elseif ($sid -in $remaining) {
                $remaining = @($remaining | Where-Object { $_ -ne $sid })
            } else {
                return @{ status = 'descriptor-not-applicable' }
            }
        }
        if ($remove -lt 0 -or $remaining.Count) { return @{ status = 'descriptor-not-applicable' } }
        $dacl.RemoveAce($remove)
        $expected = New-Object byte[] $raw.BinaryLength
        $raw.GetBinaryForm($expected, 0)
        $daclBytes = New-Object byte[] $dacl.BinaryLength
        $dacl.GetBinaryForm($daclBytes, 0)
        try {
            [ImpFixtureSecurity]::WriteDacl($file, $daclBytes)
        } catch {
            return @{ status = 'correction-write-failed' }
        }
        $after = [ImpFixtureSecurity]::ReadOpened($file)
        $observed = [Security.AccessControl.RawSecurityDescriptor]::new($after, 0)
        $observedBytes = New-Object byte[] $observed.BinaryLength
        $observed.GetBinaryForm($observedBytes, 0)
        if ((Get-DescriptorFingerprint $expected) -cne (Get-DescriptorFingerprint $observedBytes)) {
            return @{ status = 'correction-verification-failed' }
        }
        return @{ status = 'corrected'; fingerprint = (Get-DescriptorFingerprint $after) }
    } catch {
        return @{ status = 'correction-verification-failed' }
    } finally {
        $file.Dispose()
    }
}

if ($request.correction) {
    Test-AuthorizedCorrection | ConvertTo-Json -Compress
    exit
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
