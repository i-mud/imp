#![cfg(all(windows, feature = "runtime-acceptance"))]

#[allow(dead_code)]
#[path = "../src/runtime.rs"]
mod runtime;

use std::ffi::{OsStr, OsString};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::time::{Duration, Instant};

const PROGRAM: &str = "IMP_RESOLUTION_PROGRAM";
const EXPECT_ERROR: &str = "IMP_RESOLUTION_EXPECT_ERROR";
const SENTINEL_ARGS: [&str; 4] = ["sentinel_identity", "--exact", "--ignored", "--nocapture"];

#[test]
#[ignore = "subprocess dispatcher"]
fn sentinel_identity() {
    println!(
        "SENTINEL_IMAGE={}",
        std::env::current_exe().unwrap().display()
    );
}

#[test]
#[ignore = "subprocess dispatcher"]
fn resolution_driver() {
    let program = std::env::var_os(PROGRAM).unwrap();
    let is_shadow_case = program == "ssh" || program == "ssh.exe";
    let is_system_case = program == "whoami";
    let ssh_baseline = if is_shadow_case {
        let output = Command::new(&program).arg("-V").output().unwrap();
        assert!(output.status.success());
        println!(
            "BASELINE_SSH={}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
        Some(output)
    } else {
        None
    };

    let args: Vec<OsString> = if is_shadow_case {
        [OsString::from("-V")].into()
    } else if is_system_case {
        Vec::new()
    } else {
        SENTINEL_ARGS.into_iter().map(OsString::from).collect()
    };
    let baseline = if is_shadow_case {
        None
    } else {
        Some(Command::new(&program).args(&args).output())
    };
    let owner = runtime::RuntimeOwner::new().unwrap();
    let owned = owner.spawn(
        &program,
        args.iter().map(OsString::as_os_str),
        runtime::RuntimeStdout::Piped,
    );

    if std::env::var_os(EXPECT_ERROR).is_some() {
        assert_eq!(
            baseline.unwrap().unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
        assert_eq!(owned.err().unwrap().kind(), std::io::ErrorKind::NotFound);
        println!("BOTH_NOT_FOUND");
        return;
    }

    if let Some(baseline) = baseline {
        let baseline = baseline.unwrap();
        assert!(
            baseline.status.success(),
            "baseline failed: {}",
            String::from_utf8_lossy(&baseline.stderr)
        );
        let mut owned = owned.unwrap();
        let mut stdout = String::new();
        owned
            .stdout
            .as_mut()
            .unwrap()
            .read_to_string(&mut stdout)
            .unwrap();
        let mut stderr = String::new();
        owned
            .stderr
            .as_mut()
            .unwrap()
            .read_to_string(&mut stderr)
            .unwrap();
        assert!(
            owned.wait().unwrap().success(),
            "RuntimeOwner failed: {stderr}"
        );
        if is_system_case {
            assert_eq!(baseline.stdout, stdout.as_bytes());
            println!("SYSTEM_DIRECTORY_PARITY");
        } else {
            let baseline_image = sentinel_image(&baseline.stdout);
            let owner_image = sentinel_image(stdout.as_bytes());
            assert_eq!(baseline_image, owner_image);
            println!("PARITY_IMAGE={baseline_image}");
        }
    } else if let Some(baseline) = ssh_baseline {
        let mut owned = owned.unwrap();
        let mut stdout = String::new();
        owned
            .stdout
            .as_mut()
            .unwrap()
            .read_to_string(&mut stdout)
            .unwrap();
        let mut stderr = String::new();
        owned
            .stderr
            .as_mut()
            .unwrap()
            .read_to_string(&mut stderr)
            .unwrap();
        assert!(
            owned.wait().unwrap().success(),
            "RuntimeOwner ssh -V failed: {stderr}"
        );
        let baseline_identity = String::from_utf8_lossy(&baseline.stderr).trim().to_owned();
        let owner_identity = format!("{stdout}{stderr}").trim().to_owned();
        assert_eq!(baseline_identity, owner_identity);
        println!("OWNER_SSH={owner_identity}");
    }
}

fn sentinel_image(output: &[u8]) -> String {
    String::from_utf8_lossy(output)
        .lines()
        .find_map(|line| line.strip_prefix("SENTINEL_IMAGE="))
        .expect("sentinel image identity missing from child output")
        .to_owned()
}

#[test]
fn command_and_runtime_owner_resolve_executables_the_same_way() {
    let root = tempfile::tempdir().unwrap();
    let test_exe = std::env::current_exe().unwrap();

    let cwd = root.path().join("cwd ü spaces");
    let app = root.path().join("app ü spaces");
    std::fs::create_dir_all(&cwd).unwrap();
    std::fs::create_dir_all(&app).unwrap();
    std::fs::copy(&test_exe, app.join("driver.exe")).unwrap();
    std::fs::copy(&test_exe, cwd.join("ssh.exe")).unwrap();

    let openssh = PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/OpenSSH");
    assert!(
        openssh.join("ssh.exe").is_file(),
        "installed OpenSSH ssh.exe is required"
    );
    for program in ["ssh", "ssh.exe"] {
        let key = run_driver(&app, &cwd, openssh.as_os_str(), OsStr::new(program), false);
        let key = String::from_utf8_lossy(&key.stdout);
        assert!(
            key.contains("BASELINE_SSH=OpenSSH_for_Windows")
                && key.contains("OWNER_SSH=OpenSSH_for_Windows"),
            "Command and RuntimeOwner did not resolve installed OpenSSH: {key}"
        );
    }

    let first = root.path().join("first ü path");
    let second = root.path().join("second path");
    std::fs::create_dir_all(&first).unwrap();
    std::fs::create_dir_all(&second).unwrap();
    std::fs::copy(&test_exe, first.join("precedence.exe")).unwrap();
    std::fs::copy(&test_exe, second.join("precedence.exe")).unwrap();
    let quoted_path = OsString::from(format!("\"{}\";\"{}\"", first.display(), second.display()));
    assert_image_in(
        &run_driver(&app, &cwd, &quoted_path, OsStr::new("precedence"), false),
        &first,
    );
    assert_image_in(
        &run_driver(
            &app,
            &cwd,
            &quoted_path,
            OsStr::new("precedence.exe"),
            false,
        ),
        &first,
    );

    let app_target = app.join("application-target.exe");
    std::fs::copy(&test_exe, &app_target).unwrap();
    assert_image_in(
        &run_driver(
            &app,
            &cwd,
            OsStr::new(""),
            OsStr::new("application-target"),
            false,
        ),
        &app,
    );
    std::fs::copy(&test_exe, first.join("application-target.exe")).unwrap();
    assert_image_in(
        &run_driver(
            &app,
            &cwd,
            &quoted_path,
            OsStr::new("application-target"),
            false,
        ),
        &app_target,
    );
    std::fs::copy(&test_exe, cwd.join("cwd-only.exe")).unwrap();
    run_driver(&app, &cwd, OsStr::new(";;"), OsStr::new("cwd-only"), true);
    assert_image_in(
        &run_driver(&app, &cwd, OsStr::new("."), OsStr::new("cwd-only"), false),
        &cwd.join("cwd-only.exe"),
    );

    let relative = cwd.join("nested ü");
    std::fs::create_dir_all(&relative).unwrap();
    let relative_exe = relative.join("explicit 雪 tool.exe");
    std::fs::copy(&test_exe, &relative_exe).unwrap();
    assert_image_in(
        &run_driver(
            &app,
            &cwd,
            OsStr::new(""),
            OsStr::new("nested ü\\explicit 雪 tool"),
            false,
        ),
        &relative_exe,
    );
    assert_image_in(
        &run_driver(
            &app,
            &cwd,
            OsStr::new(""),
            OsStr::new("nested ü\\explicit 雪 tool.exe"),
            false,
        ),
        &relative_exe,
    );
    assert_image_in(
        &run_driver(&app, &cwd, OsStr::new(""), relative_exe.as_os_str(), false),
        &relative_exe,
    );
    assert_image_in(
        &run_driver(
            &app,
            &cwd,
            OsStr::new(""),
            relative_exe.with_extension("").as_os_str(),
            false,
        ),
        &relative_exe,
    );
    let uppercase_exe = relative.join("upper.EXE");
    std::fs::copy(&test_exe, &uppercase_exe).unwrap();
    assert_image_in(
        &run_driver(
            &app,
            &cwd,
            OsStr::new(""),
            OsStr::new("nested ü\\upper.EXE"),
            false,
        ),
        &uppercase_exe,
    );
    let extensionless = relative.join("extensionless");
    std::fs::copy(&test_exe, &extensionless).unwrap();
    assert_image_in(
        &run_driver(&app, &cwd, OsStr::new(""), extensionless.as_os_str(), false),
        &extensionless,
    );
    let com = first.join("native.com");
    std::fs::copy(&test_exe, &com).unwrap();
    assert_image_in(
        &run_driver(
            &app,
            &cwd,
            first.as_os_str(),
            OsStr::new("native.com"),
            false,
        ),
        &com,
    );
    run_driver(&app, &cwd, first.as_os_str(), OsStr::new("native"), true);
    let long_relative = PathBuf::from("long ".to_owned() + &"x".repeat(190)).join("long.exe");
    let long_exe = cwd.join(&long_relative);
    std::fs::create_dir_all(long_exe.parent().unwrap()).unwrap();
    std::fs::copy(&test_exe, &long_exe).unwrap();
    assert_image_in(
        &run_driver(&app, &cwd, OsStr::new(""), long_relative.as_os_str(), false),
        &long_exe,
    );
    let verbatim = std::fs::canonicalize(&relative_exe).unwrap();
    assert_image_in(
        &run_driver(&app, &cwd, OsStr::new(""), verbatim.as_os_str(), false),
        &relative_exe,
    );
    let system_dir = run_driver(&app, &cwd, OsStr::new(""), OsStr::new("whoami"), false);
    assert!(String::from_utf8_lossy(&system_dir.stdout).contains("SYSTEM_DIRECTORY_PARITY"));
    std::fs::copy(&test_exe, first.join("whoami.exe")).unwrap();
    run_driver(&app, &cwd, first.as_os_str(), OsStr::new("whoami"), false);

    let dotted = root.path().join("dotted extension");
    std::fs::create_dir_all(&dotted).unwrap();
    std::fs::copy(&test_exe, dotted.join("tool.name.exe")).unwrap();
    let dotted_path = dotted.into_os_string();
    let not_found = run_driver(&app, &cwd, &dotted_path, OsStr::new("tool.name"), true);
    assert!(String::from_utf8_lossy(&not_found.stdout).contains("BOTH_NOT_FOUND"));
}

struct Driver(Child);

impl Drop for Driver {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn run_driver(app: &Path, cwd: &Path, path: &OsStr, program: &OsStr, expect_error: bool) -> Output {
    let executable = app.join("driver.exe");
    let mut command = Command::new(executable);
    command
        .args(["resolution_driver", "--exact", "--ignored", "--nocapture"])
        .current_dir(cwd)
        .env(PROGRAM, program)
        .env("PATH", path)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if expect_error {
        command.env(EXPECT_ERROR, "1");
    }
    let mut driver = Driver(command.spawn().unwrap());
    let deadline = Instant::now() + Duration::from_secs(10);
    let status = loop {
        if let Some(status) = driver.0.try_wait().unwrap() {
            break status;
        }
        assert!(
            Instant::now() < deadline,
            "resolution driver timed out for {program:?}"
        );
        std::thread::sleep(Duration::from_millis(20));
    };
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    driver
        .0
        .stdout
        .take()
        .unwrap()
        .read_to_end(&mut stdout)
        .unwrap();
    driver
        .0
        .stderr
        .take()
        .unwrap()
        .read_to_end(&mut stderr)
        .unwrap();
    let output = Output {
        status,
        stdout,
        stderr,
    };
    assert!(
        output.status.success(),
        "resolution driver failed: {}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    println!("{}", String::from_utf8_lossy(&output.stdout));
    output
}

fn assert_image_in(output: &Output, expected: &Path) {
    let output = String::from_utf8_lossy(&output.stdout);
    let image = output
        .lines()
        .find_map(|line| line.strip_prefix("PARITY_IMAGE="))
        .expect("parity image missing from subprocess output");
    let image = std::fs::canonicalize(image).unwrap();
    let canonical_expected = std::fs::canonicalize(expected).unwrap();
    let image = PathBuf::from(image.as_os_str().to_string_lossy().to_lowercase());
    let canonical_expected = PathBuf::from(
        canonical_expected
            .as_os_str()
            .to_string_lossy()
            .to_lowercase(),
    );
    assert!(
        if expected.is_file() {
            image == canonical_expected
        } else {
            image.starts_with(&canonical_expected)
        },
        "expected image at or beneath {}: {output}",
        expected.display()
    );
}

#[test]
fn image_assertion_accepts_path_aliases_but_rejects_wrong_locations() {
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use std::os::windows::process::ExitStatusExt;
    use std::panic::catch_unwind;
    use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;

    let root = tempfile::tempdir().unwrap();
    let expected = root.path().join("expected directory");
    std::fs::create_dir(&expected).unwrap();
    let file = expected.join("fixture.exe");
    std::fs::write(&file, b"fixture").unwrap();
    let output = |image: &Path| Output {
        status: std::process::ExitStatus::from_raw(0),
        stdout: format!("PARITY_IMAGE={}\n", image.display()).into_bytes(),
        stderr: Vec::new(),
    };
    let canonical = std::fs::canonicalize(&file).unwrap();
    assert_image_in(&output(&canonical), &file);
    assert_image_in(&output(&canonical), &expected);
    let wide: Vec<_> = file.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut short = vec![0; 32768];
    // SAFETY: Both buffers are valid; the input is NUL-terminated.
    let length =
        unsafe { GetShortPathNameW(wide.as_ptr(), short.as_mut_ptr(), short.len() as u32) };
    assert!(length > 0 && (length as usize) < short.len());
    let short = PathBuf::from(OsString::from_wide(&short[..length as usize]));
    assert_image_in(&output(&file), &short);
    assert_image_in(&output(&short), &expected);
    eprintln!("alias parity: {} == {}", short.display(), file.display());
    for location in ["wrong-path-entry", "cwd-shadow", "wrong-application"] {
        let directory = root.path().join(location);
        std::fs::create_dir(&directory).unwrap();
        let wrong = directory.join("fixture.exe");
        std::fs::write(&wrong, b"fixture").unwrap();
        assert!(catch_unwind(|| assert_image_in(&output(&wrong), &file)).is_err());
        assert!(catch_unwind(|| assert_image_in(&output(&wrong), &expected)).is_err());
    }
}
