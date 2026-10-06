use std::{
    fs, io,
    path::{Component, Path, PathBuf},
};

use tauri::{path::BaseDirectory, AppHandle, Manager};

/// Installed resource name. It must differ from the writable `mudlet` directory: the default
/// per-user NSIS install directory is `<local_data>/Imp`, the parent of the writable root.
const RESOURCE_DIR: &str = "mudlet-bundle";
const RUNTIME_DIR: &str = "imp-mudlet-runtime";
const PACKAGE_NAME: &str = "Imp.mpackage";
const CURRENT_FILE: &str = "current.txt";

#[cfg(target_os = "windows")]
const HELPER_NAME: &str = "imp-mudlet-helper.exe";

#[cfg(not(target_os = "windows"))]
const HELPER_NAME: &str = "imp-mudlet-helper";

pub fn provision_helper(app: &AppHandle) -> Result<PathBuf, String> {
    let source = app
        .path()
        .resolve(RESOURCE_DIR, BaseDirectory::Resource)
        .map_err(|error| format!("cannot resolve bundled Mudlet helper: {error}"))?;

    if !bundle_complete(&source) {
        return Err(format!(
            "bundled Mudlet helper is unavailable at {}",
            source.display()
        ));
    }

    let root = writable_root(
        &app.path()
            .local_data_dir()
            .map_err(|error| format!("cannot resolve local data directory: {error}"))?,
    );

    let version = app.package_info().version.to_string();

    provision_from(&source, &root, &version)
        .map_err(|error| format!("cannot provision Mudlet helper: {error}"))
}

/// Stable root searched by the Mudlet package (`imp.lua`) and documented for `Imp.mpackage`.
fn writable_root(local_data: &Path) -> PathBuf {
    local_data.join("Imp").join("mudlet")
}

fn provision_from(source: &Path, root: &Path, version: &str) -> io::Result<PathBuf> {
    if !bundle_complete(source) {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "Mudlet helper bundle is incomplete",
        ));
    }

    // Copying a tree into itself or a descendant recurses until the stack overflows.
    if overlaps(source, root)? {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "Mudlet helper source {} and provisioning root {} overlap",
                source.display(),
                root.display()
            ),
        ));
    }

    let versions = root.join("versions");
    let destination = versions.join(version);
    let package_required = source.join(PACKAGE_NAME).is_file();

    fs::create_dir_all(&versions)?;

    let destination_complete = bundle_complete(&destination)
        && (!package_required || destination.join(PACKAGE_NAME).is_file());

    if !destination_complete {
        if destination.exists() {
            fs::remove_dir_all(&destination)?;
        }

        let staging = root.join(format!(".staging-{version}-{}", std::process::id()));

        if staging.exists() {
            fs::remove_dir_all(&staging)?;
        }

        copy_tree(source, &staging)?;

        if let Err(error) = fs::rename(&staging, &destination) {
            let _ = fs::remove_dir_all(&staging);
            return Err(error);
        }
    }

    let package = destination.join(PACKAGE_NAME);
    if package.is_file() {
        fs::copy(&package, root.join(PACKAGE_NAME))?;
    }

    fs::write(root.join(CURRENT_FILE), format!("versions/{version}\n"))?;

    Ok(destination)
}

fn bundle_complete(path: &Path) -> bool {
    path.join(HELPER_NAME).is_file() && path.join(RUNTIME_DIR).is_dir()
}

/// Whether either path is, or lies beneath, the other after resolution.
///
/// Containment always implies the containing path exists, so it resolves fully through
/// `canonicalize` (symlinks, junctions, 8.3 names, on-disk case, `\\?\` prefix on Windows).
/// Comparison is per component via `Path::starts_with`, never by string prefix.
fn overlaps(a: &Path, b: &Path) -> io::Result<bool> {
    let (a, b) = (resolve(a)?, resolve(b)?);
    Ok(a.starts_with(&b) || b.starts_with(&a))
}

/// Canonicalize the nearest existing ancestor, then append the missing remainder.
///
/// Missing components are created as new directories, so they cannot be links themselves. A
/// `..` in that remainder, however, leads back into existing directories whose later components
/// may be links; it is rejected rather than normalized. (`std::path::absolute` already removes
/// `..` lexically on Windows, which matches how Win32 itself opens such paths.)
fn resolve(path: &Path) -> io::Result<PathBuf> {
    let absolute = std::path::absolute(path)?;
    let mut missing = Vec::new();
    let mut existing = absolute.as_path();
    let mut resolved = loop {
        match fs::canonicalize(existing) {
            Ok(resolved) => break resolved,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                let (Some(parent), Some(name)) =
                    (existing.parent(), existing.components().next_back())
                else {
                    return Err(error);
                };
                missing.push(name);
                existing = parent;
            }
            Err(error) => return Err(error),
        }
    };
    for component in missing.into_iter().rev() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!(
                        "cannot resolve `..` after a missing component in {}",
                        path.display()
                    ),
                ));
            }
            other => resolved.push(other),
        }
    }
    Ok(resolved)
}

fn copy_tree(source: &Path, destination: &Path) -> io::Result<()> {
    fs::create_dir_all(destination)?;

    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let target = destination.join(entry.file_name());

        if file_type.is_dir() {
            copy_tree(&entry.path(), &target)?;
        } else if file_type.is_file() {
            fs::copy(entry.path(), target)?;
        } else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "unsupported Mudlet helper resource: {}",
                    entry.path().display()
                ),
            ));
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(name: &str) -> PathBuf {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();

        let path =
            std::env::temp_dir().join(format!("imp-mudlet-{name}-{}-{nonce}", std::process::id()));

        fs::create_dir_all(&path).unwrap();
        path
    }

    fn make_bundle(path: &Path, marker: &str) {
        fs::create_dir_all(path.join(RUNTIME_DIR)).unwrap();
        fs::write(path.join(HELPER_NAME), b"helper").unwrap();
        fs::write(path.join(RUNTIME_DIR).join("marker.txt"), marker).unwrap();
        fs::write(path.join(PACKAGE_NAME), format!("package-{marker}")).unwrap();
    }

    /// Every path beneath `path` with each regular file's bytes; links are recorded, not followed.
    fn tree(path: &Path) -> Vec<(PathBuf, Option<Vec<u8>>)> {
        let mut entries = Vec::new();
        for entry in fs::read_dir(path).unwrap() {
            let entry = entry.unwrap();
            let (path, file_type) = (entry.path(), entry.file_type().unwrap());
            if file_type.is_dir() {
                entries.extend(tree(&path));
            }
            let bytes = file_type.is_file().then(|| fs::read(&path).unwrap());
            entries.push((path, bytes));
        }
        entries.sort();
        entries
    }

    /// Overlap must fail before anything is created and leave the source untouched.
    fn assert_rejected(source: &Path, root: &Path, watched: &Path) {
        let before = tree(watched);
        let error = provision_from(source, root, "0.3.1").unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{error}");
        assert_eq!(tree(watched), before);
    }

    #[test]
    fn released_nsis_collision_is_rejected_without_staging() {
        // v0.3.1: resource `mudlet/` and writable root both resolved to %LOCALAPPDATA%\Imp\mudlet.
        let temp = temp_root("same-tree");
        let local_data = temp.join("Local");
        let source = local_data.join("Imp").join("mudlet");
        make_bundle(&source, "v1");

        assert_rejected(&source, &writable_root(&local_data), &temp);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn root_beneath_source_is_rejected_without_staging() {
        let temp = temp_root("root-beneath");
        let source = temp.join("Imp").join("mudlet");
        make_bundle(&source, "v1");

        assert_rejected(&source, &source.join("provisioned"), &temp);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn source_beneath_root_is_rejected_without_staging() {
        let temp = temp_root("source-beneath");
        let root = temp.join("Imp");
        let source = root.join("mudlet");
        make_bundle(&source, "v1");

        assert_rejected(&source, &root, &temp);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn normalized_alias_of_the_source_is_rejected() {
        let temp = temp_root("alias");
        let source = temp.join("Imp").join("mudlet");
        make_bundle(&source, "v1");
        fs::create_dir(temp.join("Imp").join("sibling")).unwrap();

        // Not lexically beneath the source; only resolving `sibling/..` reveals the overlap.
        let alias = temp.join("Imp").join("sibling").join("..").join("mudlet");
        assert!(!alias.starts_with(&source));
        assert_rejected(&source, &alias.join("provisioned"), &temp);
        fs::remove_dir_all(temp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn parent_after_missing_component_is_rejected() {
        // Review reproduction: `missing/..` returns into existing directories, where `link`
        // leads back into the source. Lexically popping `missing` hid that overlap.
        let temp = temp_root("missing-parent");
        let source = temp.join("source");
        make_bundle(&source, "v1");
        std::os::unix::fs::symlink(&source, temp.join("link")).unwrap();
        let root = temp
            .join("missing")
            .join("..")
            .join("link")
            .join("provisioned");

        assert_rejected(&source, &root, &temp);
        fs::remove_dir_all(temp).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_alias_of_the_source_is_rejected() {
        let temp = temp_root("symlink");
        let source = temp.join("Imp").join("mudlet");
        make_bundle(&source, "v1");
        std::os::unix::fs::symlink(&source, temp.join("link")).unwrap();

        assert_rejected(&source, &temp.join("link").join("provisioned"), &temp);
        fs::remove_dir_all(temp).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn case_and_separator_aliases_of_the_source_are_rejected() {
        let temp = temp_root("windows-alias");
        let source = temp.join("Imp").join("mudlet");
        make_bundle(&source, "v1");
        let alias = PathBuf::from(
            source
                .to_string_lossy()
                .replace('\\', "/")
                .replace("Imp", "IMP")
                .replace("mudlet", "MUDLET"),
        );

        assert_rejected(&source, &alias.join("provisioned"), &temp);
        fs::remove_dir_all(temp).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn verbatim_source_and_ordinary_root_are_compared_after_resolution() {
        // Tauri may report the resource directory as `\\?\C:\...` while the writable root is
        // built from an ordinary `C:\...` path; the released collision paired exactly these.
        let temp = temp_root("verbatim");
        let local_data = temp.join("Local");
        let ordinary = local_data.join("Imp").join("mudlet");
        make_bundle(&ordinary, "v1");
        let source = fs::canonicalize(&ordinary).unwrap();
        assert!(source.to_string_lossy().starts_with(r"\\?\"));
        assert!(!source.starts_with(&ordinary) && !ordinary.starts_with(&source));

        assert_rejected(&source, &writable_root(&local_data), &temp);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn default_nsis_layout_provisions_into_a_disjoint_root() {
        // Per-user NSIS installs into <local_data>/Imp; resources sit beneath that directory.
        let temp = temp_root("nsis-layout");
        let local_data = temp.join("Local");
        let source = local_data.join("Imp").join(RESOURCE_DIR);
        let root = writable_root(&local_data);
        make_bundle(&source, "v1");
        let before = tree(&source);

        assert!(!overlaps(&source, &root).unwrap());
        let destination = provision_from(&source, &root, "0.3.2").unwrap();

        assert_eq!(destination, root.join("versions").join("0.3.2"));
        assert!(bundle_complete(&destination));
        assert_eq!(
            fs::read_to_string(root.join(CURRENT_FILE)).unwrap(),
            "versions/0.3.2\n"
        );
        assert_eq!(tree(&source), before);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn bundle_config_installs_the_resource_dir_provisioning_reads() {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(
            config["bundle"]["resources"]["resources/mudlet/"],
            format!("{RESOURCE_DIR}/")
        );
        assert_ne!(Path::new(RESOURCE_DIR), Path::new("mudlet"));
    }

    #[test]
    fn provisions_version_and_current_pointer() {
        let temp = temp_root("provision");
        let source = temp.join("source");
        let root = temp.join("install");

        make_bundle(&source, "v1");

        let destination = provision_from(&source, &root, "0.1.0").unwrap();

        assert!(bundle_complete(&destination));
        assert_eq!(
            fs::read_to_string(root.join(CURRENT_FILE)).unwrap(),
            "versions/0.1.0\n"
        );
        assert_eq!(
            fs::read_to_string(root.join(PACKAGE_NAME)).unwrap(),
            "package-v1"
        );

        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn same_version_repairs_install_missing_package() {
        let temp = temp_root("repair-package");
        let source = temp.join("source");
        let root = temp.join("install");

        make_bundle(&source, "v1");

        let destination = provision_from(&source, &root, "0.1.0").unwrap();

        fs::remove_file(destination.join(PACKAGE_NAME)).unwrap();
        fs::remove_file(root.join(PACKAGE_NAME)).unwrap();

        let repaired = provision_from(&source, &root, "0.1.0").unwrap();

        assert_eq!(repaired, destination);
        assert_eq!(
            fs::read_to_string(destination.join(PACKAGE_NAME)).unwrap(),
            "package-v1"
        );
        assert_eq!(
            fs::read_to_string(root.join(PACKAGE_NAME)).unwrap(),
            "package-v1"
        );

        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn new_version_preserves_old_version_and_moves_pointer() {
        let temp = temp_root("update");
        let source = temp.join("source");
        let root = temp.join("install");

        make_bundle(&source, "v1");
        let first = provision_from(&source, &root, "0.1.0").unwrap();

        fs::remove_dir_all(&source).unwrap();
        make_bundle(&source, "v2");

        let second = provision_from(&source, &root, "0.2.0").unwrap();

        assert!(bundle_complete(&first));
        assert!(bundle_complete(&second));
        assert_eq!(
            fs::read_to_string(second.join(RUNTIME_DIR).join("marker.txt")).unwrap(),
            "v2"
        );
        assert_eq!(
            fs::read_to_string(root.join(CURRENT_FILE)).unwrap(),
            "versions/0.2.0\n"
        );
        assert_eq!(
            fs::read_to_string(first.join(PACKAGE_NAME)).unwrap(),
            "package-v1"
        );
        assert_eq!(
            fs::read_to_string(root.join(PACKAGE_NAME)).unwrap(),
            "package-v2"
        );

        fs::remove_dir_all(temp).unwrap();
    }
}
