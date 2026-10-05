use std::env;
use std::ffi::{OsStr, OsString};
use std::fs::{File, OpenOptions};
use std::io;
use std::mem::{size_of, size_of_val, zeroed};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::io::{AsRawHandle, FromRawHandle, IntoRawHandle, OwnedHandle, RawHandle};
use std::os::windows::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::ExitStatus;
use std::ptr::{null, null_mut};

use super::RuntimeStdout;

use windows_sys::Win32::Foundation::{
    SetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT, WAIT_FAILED, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::Storage::FileSystem::{GetFileAttributesW, INVALID_FILE_ATTRIBUTES};
use windows_sys::Win32::System::JobObjects::{
    CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::SystemInformation::{GetSystemDirectoryW, GetWindowsDirectoryW};
use windows_sys::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess,
    InitializeProcThreadAttributeList, TerminateProcess, UpdateProcThreadAttribute,
    WaitForSingleObject, CREATE_NO_WINDOW, EXTENDED_STARTUPINFO_PRESENT, INFINITE,
    LPPROC_THREAD_ATTRIBUTE_LIST, PROCESS_INFORMATION, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
    PROC_THREAD_ATTRIBUTE_JOB_LIST, STARTF_USESTDHANDLES, STARTUPINFOEXW,
};

/// Owns the private job that contains every runtime child spawned by this desktop.
pub struct RuntimeOwner {
    job: OwnedHandle,
}

impl RuntimeOwner {
    pub fn new() -> io::Result<Self> {
        // SAFETY: A null security descriptor requests the default security attributes; a null
        // name creates an unnamed, non-inheritable job object.
        let raw_job = unsafe { CreateJobObjectW(null(), null()) };
        if raw_job.is_null() {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: CreateJobObjectW returned a fresh handle, now owned by this value.
        let job = unsafe { OwnedHandle::from_raw_handle(raw_job as RawHandle) };

        // SAFETY: The all-zero extended-limit structure is a valid initialization.
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: `limits` is initialized, correctly sized, and remains live for this call.
        if unsafe {
            SetInformationJobObject(
                job.as_raw_handle() as HANDLE,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }

        Ok(Self { job })
    }

    pub fn spawn<'a>(
        &self,
        program: &'a OsStr,
        args: impl IntoIterator<Item = &'a OsStr>,
        stdout: RuntimeStdout,
    ) -> io::Result<OwnedChild> {
        spawn_in_job(self.job.as_raw_handle() as HANDLE, program, args, stdout)
    }
}

/// A process handle plus the parent ends of its stdout/stderr pipes.
pub struct OwnedChild {
    process: OwnedHandle,
    id: u32,
    pub stdout: Option<File>,
    pub stderr: Option<File>,
}

impl OwnedChild {
    pub fn id(&self) -> u32 {
        self.id
    }

    pub fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        // SAFETY: The process handle remains owned and valid for the duration of this call.
        match unsafe { WaitForSingleObject(self.process.as_raw_handle() as HANDLE, 0) } {
            WAIT_OBJECT_0 => exit_status(self.process.as_raw_handle() as HANDLE).map(Some),
            WAIT_TIMEOUT => Ok(None),
            WAIT_FAILED => Err(io::Error::last_os_error()),
            _ => Err(io::Error::other(
                "WaitForSingleObject returned an unexpected result for a process handle",
            )),
        }
    }

    pub fn kill(&mut self) -> io::Result<()> {
        // TerminateProcess can race normal exit; an already-signaled process handle is success.
        // SAFETY: The process handle remains owned and valid for the duration of this call.
        if unsafe { TerminateProcess(self.process.as_raw_handle() as HANDLE, 1) } == 0 {
            let error = io::Error::last_os_error();
            // SAFETY: The process handle remains owned and valid for the duration of this call.
            if unsafe { WaitForSingleObject(self.process.as_raw_handle() as HANDLE, 0) }
                == WAIT_OBJECT_0
            {
                return Ok(());
            }
            return Err(error);
        }
        Ok(())
    }

    pub fn wait(&mut self) -> io::Result<ExitStatus> {
        // SAFETY: The process handle remains owned and valid for the duration of this call.
        match unsafe { WaitForSingleObject(self.process.as_raw_handle() as HANDLE, INFINITE) } {
            WAIT_OBJECT_0 => exit_status(self.process.as_raw_handle() as HANDLE),
            WAIT_FAILED => Err(io::Error::last_os_error()),
            _ => Err(io::Error::other(
                "WaitForSingleObject returned an unexpected result for a process handle",
            )),
        }
    }
}

fn exit_status(process: HANDLE) -> io::Result<ExitStatus> {
    let mut code = 0;
    // SAFETY: `process` is the owned process handle and `code` is a writable output value.
    if unsafe { GetExitCodeProcess(process, &mut code) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(ExitStatus::from_raw(code))
}

struct ProcThreadAttributes {
    list: LPPROC_THREAD_ATTRIBUTE_LIST,
    _storage: Vec<usize>,
}

impl ProcThreadAttributes {
    fn new() -> io::Result<Self> {
        let mut byte_count = 0;
        // SAFETY: A null list requests the required storage size; no list is initialized here.
        unsafe { InitializeProcThreadAttributeList(null_mut(), 2, 0, &mut byte_count) };
        if byte_count == 0 {
            return Err(io::Error::last_os_error());
        }

        let words = byte_count.div_ceil(size_of::<usize>());
        let mut storage = Vec::new();
        storage.try_reserve_exact(words).map_err(io::Error::other)?;
        storage.resize(words, 0);
        let list = storage.as_mut_ptr().cast();
        // SAFETY: `storage` is suitably aligned and large enough for the reported size, and stays
        // alive until after DeleteProcThreadAttributeList runs.
        if unsafe { InitializeProcThreadAttributeList(list, 2, 0, &mut byte_count) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Self {
            list,
            _storage: storage,
        })
    }
}

impl Drop for ProcThreadAttributes {
    fn drop(&mut self) {
        // SAFETY: The list was successfully initialized and its backing allocation is still live.
        unsafe { DeleteProcThreadAttributeList(self.list) };
    }
}

fn create_pipe() -> io::Result<(OwnedHandle, OwnedHandle)> {
    let security = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: null_mut(),
        bInheritHandle: 1,
    };
    let mut read = null_mut();
    let mut write = null_mut();
    // SAFETY: Both output pointers and the inheritable security attributes are valid for this call.
    if unsafe { CreatePipe(&mut read, &mut write, &security, 0) } == 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: Successful CreatePipe returned two fresh handles, now owned by these values.
    Ok(unsafe {
        (
            OwnedHandle::from_raw_handle(read as RawHandle),
            OwnedHandle::from_raw_handle(write as RawHandle),
        )
    })
}

fn make_parent_handle_noninheritable(handle: &OwnedHandle) -> io::Result<()> {
    // SAFETY: `handle` is an open pipe handle owned by the caller.
    if unsafe { SetHandleInformation(handle.as_raw_handle() as HANDLE, HANDLE_FLAG_INHERIT, 0) }
        == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn null_handle(read: bool) -> io::Result<OwnedHandle> {
    let handle: OwnedHandle = OpenOptions::new()
        .read(read)
        .write(!read)
        .open("NUL")?
        .into();
    // SAFETY: The handle is owned and HANDLE_LIST restricts inheritance to child stdio.
    if unsafe {
        SetHandleInformation(
            handle.as_raw_handle() as HANDLE,
            HANDLE_FLAG_INHERIT,
            HANDLE_FLAG_INHERIT,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(handle)
}

fn resolve_executable(program: &OsStr) -> io::Result<Vec<u16>> {
    let encoded = program.as_encoded_bytes();
    let trailing_separator = if encoded.starts_with(br"\\?\") {
        encoded.ends_with(b"\\")
    } else {
        encoded.ends_with(b"\\") || encoded.ends_with(b"/")
    };
    if encoded.is_empty() || trailing_separator {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "program path has no file name",
        ));
    }

    let has_exe_suffix = encoded
        .get(encoded.len().saturating_sub(4)..)
        .is_some_and(|suffix| suffix.eq_ignore_ascii_case(b".exe"));
    let has_path_separator = encoded.contains(&b'\\') || encoded.contains(&b'/');
    if has_path_separator {
        if has_exe_suffix {
            return wide_path(Path::new(program));
        }

        let mut suffixed = program.to_os_string();
        suffixed.push(".exe");
        if let Some(path) = program_exists(Path::new(&suffixed)) {
            return Ok(path);
        }
        return wide_path(Path::new(program));
    }

    if encoded.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "program path cannot contain NUL",
        ));
    }
    let has_extension = encoded.contains(&b'.');
    if let Some(paths) = search_executable_paths(program, has_extension) {
        return Ok(paths);
    }
    Err(io::Error::new(io::ErrorKind::NotFound, "program not found"))
}

fn search_executable_paths(program: &OsStr, has_extension: bool) -> Option<Vec<u16>> {
    let search = |mut directory: PathBuf| {
        directory.push(program);
        if !has_extension {
            directory.set_extension("exe");
        }
        program_exists(&directory)
    };

    if let Ok(mut application) = env::current_exe() {
        application.pop();
        if let Some(path) = search(application) {
            return Some(path);
        }
    }
    for get_directory in [
        GetSystemDirectoryW as unsafe extern "system" fn(*mut u16, u32) -> u32,
        GetWindowsDirectoryW,
    ] {
        if let Some(directory) = windows_directory(get_directory) {
            if let Some(path) = search(directory) {
                return Some(path);
            }
        }
    }
    if let Some(parent_paths) = env::var_os("PATH") {
        for path in env::split_paths(&parent_paths).filter(|path| !path.as_os_str().is_empty()) {
            if let Some(path) = search(path) {
                return Some(path);
            }
        }
    }
    None
}

fn windows_directory(
    get_directory: unsafe extern "system" fn(*mut u16, u32) -> u32,
) -> Option<PathBuf> {
    let mut buffer = vec![0; 260];
    loop {
        // SAFETY: The buffer is writable and its length is passed to the system API.
        let length = unsafe { get_directory(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
        if length == 0 {
            return None;
        }
        if length < buffer.len() {
            buffer.truncate(length);
            return Some(PathBuf::from(OsString::from_wide(&buffer)));
        }
        buffer.resize(length + 1, 0);
    }
}

fn program_exists(path: &Path) -> Option<Vec<u16>> {
    let wide = wide_path(path).ok()?;
    // SAFETY: `wide` is a NUL-terminated UTF-16 path.
    (unsafe { GetFileAttributesW(wide.as_ptr()) } != INVALID_FILE_ATTRIBUTES).then_some(wide)
}

fn wide_path(path: &Path) -> io::Result<Vec<u16>> {
    let mut wide: Vec<_> = path.as_os_str().encode_wide().collect();
    if wide.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "program path cannot contain NUL",
        ));
    }
    wide.push(0);
    // Match std's to_user_path/get_long_path: normalize relative paths, preserve literal
    // verbatim names, and add a verbatim prefix only when Win32's legacy limit requires it.
    const SEP: u16 = b'\\' as u16;
    const ALT_SEP: u16 = b'/' as u16;
    const QUERY: u16 = b'?' as u16;
    const COLON: u16 = b':' as u16;
    const DOT: u16 = b'.' as u16;
    const U: u16 = b'U' as u16;
    const N: u16 = b'N' as u16;
    const C: u16 = b'C' as u16;

    if wide.len() > 260 {
        return Ok(wide);
    }
    let plain_start = match wide.as_slice() {
        [SEP, SEP, QUERY, SEP, _, COLON, SEP, ..] => Some(4),
        [SEP, SEP, QUERY, SEP, U, N, C, SEP, ..] => {
            wide[6] = SEP;
            Some(6)
        }
        _ => None,
    };
    if let Some(start) = plain_start {
        let plain = OsString::from_wide(&wide[start..wide.len() - 1]);
        if std::path::absolute(Path::new(&plain))?.as_os_str() == plain {
            wide.drain(..start);
        } else if start == 6 {
            wide[6] = C;
        }
        return Ok(wide);
    }
    if wide.starts_with(&[SEP, SEP, QUERY, SEP])
        || wide.starts_with(&[SEP, QUERY, QUERY, SEP])
        || (wide.len() < 248
            && matches!(
                wide.as_slice(),
                [drive, COLON, 0] | [drive, COLON, SEP | ALT_SEP, ..]
                    if *drive != SEP && *drive != ALT_SEP
            ))
        || (wide.len() < 248 && matches!(wide.as_slice(), [SEP | ALT_SEP, SEP | ALT_SEP, ..]))
    {
        return Ok(wide);
    }
    let absolute = std::path::absolute(path)?;
    wide.clear();
    wide.extend(absolute.as_os_str().encode_wide());
    if wide.len() + 1 >= 248 {
        match wide.as_slice() {
            [_, COLON, SEP, ..] => {
                wide.splice(..0, [SEP, SEP, QUERY, SEP]);
            }
            [SEP, SEP, DOT, SEP, ..] => wide[2] = QUERY,
            [SEP, SEP, QUERY, SEP, ..] | [SEP, QUERY, QUERY, SEP, ..] => {}
            [SEP, SEP, ..] => {
                wide.splice(..2, [SEP, SEP, QUERY, SEP, U, N, C, SEP]);
            }
            _ => {}
        }
    }
    wide.push(0);
    Ok(wide)
}

fn spawn_in_job<'a>(
    job: HANDLE,
    program: &'a OsStr,
    args: impl IntoIterator<Item = &'a OsStr>,
    stdout: RuntimeStdout,
) -> io::Result<OwnedChild> {
    let application_name = resolve_executable(program)?;
    let mut command_line = Vec::new();
    append_quoted_arg(&mut command_line, program)?;
    for arg in args {
        command_line.push(b' ' as u16);
        append_quoted_arg(&mut command_line, arg)?;
    }
    command_line.push(0);

    let stdin_read = null_handle(true)?;
    let (stdout_read, stdout_write) = match stdout {
        RuntimeStdout::Piped => {
            let (read, write) = create_pipe()?;
            make_parent_handle_noninheritable(&read)?;
            (Some(read), write)
        }
        RuntimeStdout::Null => (None, null_handle(false)?),
    };
    let (stderr_read, stderr_write) = create_pipe()?;
    make_parent_handle_noninheritable(&stderr_read)?;

    let attributes = ProcThreadAttributes::new()?;
    let jobs = [job];
    let inherited_handles = [
        stdin_read.as_raw_handle() as HANDLE,
        stdout_write.as_raw_handle() as HANDLE,
        stderr_write.as_raw_handle() as HANDLE,
    ];
    // SAFETY: Both arrays remain live through CreateProcessW; the job and stdio handles are valid.
    if unsafe {
        UpdateProcThreadAttribute(
            attributes.list,
            0,
            PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
            jobs.as_ptr().cast(),
            size_of_val(&jobs),
            null_mut(),
            null(),
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: The exact inheritable child stdio handles remain live through CreateProcessW.
    if unsafe {
        UpdateProcThreadAttribute(
            attributes.list,
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
            inherited_handles.as_ptr().cast(),
            size_of_val(&inherited_handles),
            null_mut(),
            null(),
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }

    let mut startup = STARTUPINFOEXW::default();
    startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = stdin_read.as_raw_handle() as HANDLE;
    startup.StartupInfo.hStdOutput = stdout_write.as_raw_handle() as HANDLE;
    startup.StartupInfo.hStdError = stderr_write.as_raw_handle() as HANDLE;
    startup.lpAttributeList = attributes.list;

    let mut process_info = PROCESS_INFORMATION::default();
    // SAFETY: The application name and mutable command line are NUL-terminated; startup and
    // attribute storage are valid, and HANDLE_LIST restricts inheritance to the three listed
    // stdio handles. JOB_LIST assigns the process before it can execute.
    if unsafe {
        CreateProcessW(
            application_name.as_ptr(),
            command_line.as_mut_ptr(),
            null(),
            null(),
            1,
            CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT,
            null(),
            null(),
            &startup.StartupInfo,
            &mut process_info,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }

    // SAFETY: Successful CreateProcessW returns these fresh process/thread handles.
    let process = unsafe { OwnedHandle::from_raw_handle(process_info.hProcess as RawHandle) };
    // SAFETY: The primary thread handle is no longer needed and is closed by this owner.
    drop(unsafe { OwnedHandle::from_raw_handle(process_info.hThread as RawHandle) });

    // The child owns inherited copies; closing these parent copies allows pipe EOF to propagate.
    drop(stdin_read);
    drop(stdout_write);
    drop(stderr_write);

    // SAFETY: Each read end is an owned pipe handle and ownership is transferred to File.
    let stdout =
        stdout_read.map(|handle| unsafe { File::from_raw_handle(handle.into_raw_handle()) });
    // SAFETY: Each read end is an owned pipe handle and ownership is transferred to File.
    let stderr = unsafe { File::from_raw_handle(stderr_read.into_raw_handle()) };

    Ok(OwnedChild {
        process,
        id: process_info.dwProcessId,
        stdout,
        stderr: Some(stderr),
    })
}

fn append_quoted_arg(command_line: &mut Vec<u16>, arg: &OsStr) -> io::Result<()> {
    const SLASH: u16 = b'\\' as u16;
    const QUOTE: u16 = b'"' as u16;

    command_line.push(QUOTE);
    let mut wide = arg.encode_wide().peekable();
    while wide.peek().is_some() {
        let mut slashes = 0usize;
        while wide.peek() == Some(&SLASH) {
            wide.next();
            slashes += 1;
        }
        match wide.next() {
            Some(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "process arguments cannot contain NUL",
                ));
            }
            Some(QUOTE) => {
                for _ in 0..slashes {
                    command_line.push(SLASH);
                    command_line.push(SLASH);
                }
                command_line.push(SLASH);
                command_line.push(QUOTE);
            }
            Some(unit) => {
                command_line.extend(std::iter::repeat_n(SLASH, slashes));
                command_line.push(unit);
            }
            None => {
                for _ in 0..slashes {
                    command_line.push(SLASH);
                    command_line.push(SLASH);
                }
            }
        }
    }
    command_line.push(QUOTE);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use std::process::Command;
    use std::time::{SystemTime, UNIX_EPOCH};
    use windows_sys::Win32::Foundation::{GetHandleInformation, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::JobObjects::{
        JobObjectExtendedLimitInformation, QueryInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    #[test]
    fn job_is_kill_on_close_and_noninheritable() {
        let owner = RuntimeOwner::new().unwrap();
        // SAFETY: The all-zero structure is a valid query output buffer.
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        // SAFETY: The job is open and `limits` is a writable buffer of the requested size.
        assert_ne!(
            unsafe {
                QueryInformationJobObject(
                    owner.job.as_raw_handle() as HANDLE,
                    JobObjectExtendedLimitInformation,
                    &mut limits as *mut _ as *mut _,
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                    null_mut(),
                )
            },
            0
        );
        assert_eq!(
            limits.BasicLimitInformation.LimitFlags,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        );
        let mut flags = 0;
        // SAFETY: The job handle is open and `flags` is a writable output value.
        assert_ne!(
            unsafe { GetHandleInformation(owner.job.as_raw_handle() as HANDLE, &mut flags) },
            0
        );
        assert_eq!(flags & HANDLE_FLAG_INHERIT, 0);
    }

    #[test]
    fn invalid_job_assignment_never_starts_the_requested_image() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let filename = format!("imp-runtime-owner-{stamp}.txt");
        let temp = env::var_os("TEMP").expect("TEMP is set on Windows");
        let marker = std::path::PathBuf::from(temp).join(&filename);
        let system_root = env::var_os("SystemRoot").expect("SystemRoot is set on Windows");
        let executable = std::path::PathBuf::from(system_root)
            .join("System32/WindowsPowerShell/v1.0/powershell.exe");
        let script = format!("[IO.File]::WriteAllText(($env:TEMP + '\\{filename}'), 'started')");
        let mut command = Command::new(executable);
        command.args(["-NoProfile", "-NonInteractive", "-Command", script.as_str()]);

        let owner = RuntimeOwner::new().unwrap();
        let mut control = owner
            .spawn(
                command.get_program(),
                command.get_args(),
                RuntimeStdout::Piped,
            )
            .unwrap();
        assert!(control.wait().unwrap().success());
        assert!(marker.exists(), "positive-control command did not execute");
        std::fs::remove_file(&marker).unwrap();

        let result = spawn_in_job(
            INVALID_HANDLE_VALUE,
            command.get_program(),
            command.get_args(),
            RuntimeStdout::Piped,
        );
        if let Ok(mut child) = result {
            let _ = child.kill();
            let _ = child.wait();
            panic!("an invalid job handle unexpectedly started a child");
        }
        assert!(
            !marker.exists(),
            "the child executed despite failed job registration"
        );
    }
}
