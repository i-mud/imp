use std::{
    fs, io,
    path::{Path, PathBuf},
};

use tauri::{path::BaseDirectory, AppHandle, Manager};

const RESOURCE_DIR: &str = "mudlet";
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

    let root = app
        .path()
        .local_data_dir()
        .map_err(|error| format!("cannot resolve local data directory: {error}"))?
        .join("Imp")
        .join("mudlet");

    let version = app.package_info().version.to_string();

    provision_from(&source, &root, &version)
        .map_err(|error| format!("cannot provision Mudlet helper: {error}"))
}

fn provision_from(source: &Path, root: &Path, version: &str) -> io::Result<PathBuf> {
    if !bundle_complete(source) {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "Mudlet helper bundle is incomplete",
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
