#![forbid(unsafe_code)]
//! Ready-directory changes are hints. Only the validated backup worker may read
//! completed payloads and authorize uploads. No disk images or mounts are used.

use std::fs;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::SystemTime;

pub type Result<T> = std::result::Result<T, &'static str>;

pub fn current_uid() -> Result<u32> {
    let status = fs::read_to_string("/proc/self/status").map_err(|_| "identity_unavailable")?;
    status
        .lines()
        .find_map(|line| line.strip_prefix("Uid:"))
        .and_then(|values| values.split_whitespace().nth(1))
        .and_then(|value| value.parse().ok())
        .ok_or("identity_unavailable")
}

pub fn protected_path(path: &Path, directory: bool, uid: u32, private: bool) -> Result<()> {
    if !path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
    {
        return Err("unsafe_path");
    }
    let info = fs::symlink_metadata(path).map_err(|_| "required_path_unavailable")?;
    if info.file_type().is_symlink()
        || (directory && !info.is_dir())
        || (!directory && !info.is_file())
        || (info.uid() != uid && info.uid() != 0)
        || info.mode() & if private { 0o077 } else { 0o022 } != 0
    {
        return Err("unprotected_path");
    }
    for parent in path.ancestors().skip(1) {
        let meta = fs::symlink_metadata(parent).map_err(|_| "required_path_unavailable")?;
        let trusted_sticky = meta.uid() == 0 && meta.mode() & 0o1000 != 0;
        if !meta.is_dir()
            || meta.file_type().is_symlink()
            || (meta.mode() & 0o022 != 0 && !trusted_sticky)
        {
            return Err("unprotected_parent");
        }
    }
    Ok(())
}

#[derive(Debug, PartialEq, Eq)]
pub struct Snapshot {
    entries: Vec<(String, SystemTime)>,
    config_changed: SystemTime,
    config_size: u64,
}

impl Snapshot {
    pub fn candidate_count(&self) -> usize {
        self.entries.len()
    }
}

pub fn ready_name(name: &str) -> bool {
    name.strip_suffix(".ready").is_some_and(|id| {
        id.len() == 32
            && id
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    })
}

pub fn snapshot(staging: &Path, config: &Path) -> Result<Snapshot> {
    let mut entries = Vec::new();
    for entry in fs::read_dir(staging).map_err(|_| "staging_inventory_failed")? {
        let entry = entry.map_err(|_| "staging_inventory_failed")?;
        let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        if !ready_name(&name) {
            continue;
        }
        let info = fs::symlink_metadata(entry.path()).map_err(|_| "staging_inventory_failed")?;
        if info.is_dir() && !info.file_type().is_symlink() {
            if entries.len() >= 100_000 {
                return Err("staging_inventory_limit");
            }
            entries.push((name, info.modified().map_err(|_| "metadata_unavailable")?));
        }
    }
    entries.sort();
    let config = fs::symlink_metadata(config).map_err(|_| "configuration_unavailable")?;
    Ok(Snapshot {
        entries,
        config_changed: config.modified().map_err(|_| "metadata_unavailable")?,
        config_size: config.len(),
    })
}

#[derive(Debug, PartialEq, Eq)]
pub struct WorkerReport {
    pub pending_jobs: u64,
    pub reconciliation_pending: bool,
}

impl WorkerReport {
    pub fn needs_work(&self) -> bool {
        self.pending_jobs > 0 || self.reconciliation_pending
    }
}

pub fn run_worker(runner: &Path, config: &Path) -> Result<WorkerReport> {
    // Direct argv invocation only. The trusted runner uses exec, and systemd's
    // control-group kill policy stops the worker and rclone together on shutdown.
    let mut child = Command::new(runner)
        .arg(config)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "worker_start_failed")?;
    let mut output = Vec::new();
    child
        .stdout
        .take()
        .ok_or("worker_output_unavailable")?
        .take(65_537)
        .read_to_end(&mut output)
        .map_err(|_| "worker_output_unavailable")?;
    let status = child.wait().map_err(|_| "worker_wait_failed")?;
    if !status.success() || output.len() > 65_536 {
        return Err("worker_failed");
    }
    let report: serde_json::Value =
        serde_json::from_slice(&output).map_err(|_| "invalid_worker_report")?;
    let pending_jobs = report
        .get("pending_jobs")
        .and_then(serde_json::Value::as_u64)
        .ok_or("invalid_worker_report")?;
    let reconciliation_pending = match report.get("reconciliation_pending") {
        None => false, // Older workers do not have a reconciliation budget signal.
        Some(value) => value.as_bool().ok_or("invalid_worker_report")?,
    };
    Ok(WorkerReport {
        pending_jobs,
        reconciliation_pending,
    })
}

pub fn validated_paths(staging: &Path, runner: &Path, config: &Path) -> Result<()> {
    let uid = current_uid()?;
    protected_path(staging, true, uid, false)?;
    protected_path(runner, false, uid, false)?;
    protected_path(config, false, uid, true)?;
    Ok(())
}

pub fn path_argument(value: Option<String>) -> Result<PathBuf> {
    value
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .ok_or("absolute_path_required")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "raspi-rust-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::SeqCst)
            ));
            fs::create_dir(&path).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
            Self(path)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn only_published_names_are_candidates() {
        assert!(ready_name(&format!("{}.ready", "a".repeat(32))));
        for name in [
            "piusb.bin",
            "a.ready",
            "../secret.ready",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.incomplete",
        ] {
            assert!(!ready_name(name));
        }
    }

    #[test]
    fn snapshot_ignores_incomplete_files_and_symlinks() {
        let fixture = Fixture::new();
        let config = fixture.0.join("worker.json");
        fs::write(&config, "{}").unwrap();
        fs::create_dir(fixture.0.join(format!("{}.ready", "a".repeat(32)))).unwrap();
        fs::create_dir(fixture.0.join(format!("{}.incomplete", "b".repeat(32)))).unwrap();
        std::os::unix::fs::symlink(
            &fixture.0,
            fixture.0.join(format!("{}.ready", "c".repeat(32))),
        )
        .unwrap();
        assert_eq!(snapshot(&fixture.0, &config).unwrap().candidate_count(), 1);
    }

    #[test]
    fn private_configuration_and_regular_runner_are_required() {
        let fixture = Fixture::new();
        let config = fixture.0.join("worker.json");
        fs::write(&config, "{}").unwrap();
        fs::set_permissions(&config, fs::Permissions::from_mode(0o600)).unwrap();
        protected_path(&config, false, current_uid().unwrap(), true).unwrap();
        fs::set_permissions(&config, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(protected_path(&config, false, current_uid().unwrap(), true).is_err());
        assert!(protected_path(&fixture.0, false, current_uid().unwrap(), false).is_err());
    }

    #[test]
    fn worker_must_report_pending_jobs_without_claiming_upload_success() {
        let fixture = Fixture::new();
        let runner = fixture.0.join("runner.sh");
        fs::write(
            &runner,
            "#!/bin/sh\nprintf '%s\\n' '{\"pending_jobs\":2}'\n",
        )
        .unwrap();
        fs::set_permissions(&runner, fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(
            run_worker(&runner, &fixture.0.join("worker.json")).unwrap(),
            WorkerReport {
                pending_jobs: 2,
                reconciliation_pending: false,
            }
        );
        fs::write(
            &runner,
            "#!/bin/sh\nprintf '%s\\n' '{\"pending_jobs\":0,\"reconciliation_pending\":true}'\n",
        )
        .unwrap();
        assert!(
            run_worker(&runner, &fixture.0.join("worker.json"))
                .unwrap()
                .needs_work()
        );
        fs::write(&runner, "#!/bin/sh\nprintf '%s\\n' '{\"pending_jobs\":0,\"reconciliation_pending\":\"false\"}'\n").unwrap();
        assert_eq!(
            run_worker(&runner, &fixture.0.join("worker.json")),
            Err("invalid_worker_report")
        );
        fs::write(&runner, "#!/bin/sh\nprintf '%s\\n' '{\"uploaded\":true}'\n").unwrap();
        assert_eq!(
            run_worker(&runner, &fixture.0.join("worker.json")),
            Err("invalid_worker_report")
        );
        fs::write(&runner, "#!/bin/sh\nexit 1\n").unwrap();
        assert_eq!(
            run_worker(&runner, &fixture.0.join("worker.json")),
            Err("worker_failed")
        );
    }
}
