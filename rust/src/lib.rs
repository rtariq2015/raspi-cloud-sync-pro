#![forbid(unsafe_code)]
//! Ready-directory changes are hints. Only the validated backup worker may read
//! completed payloads and authorize uploads. No disk images or mounts are used.

use std::fs;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime};

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
    pub network_policy_allows_upload: Option<bool>,
}

pub const PAUSED_BACKOFF: Duration = Duration::from_secs(60);

/// Scheduling uses elapsed monotonic time, never wall-clock timestamps. The
/// filesystem poll remains short so new ready work/configuration bypasses a
/// paused/error delay. This class neither observes nor changes any network.
#[derive(Default)]
pub struct Scheduler {
    pending: bool,
    next_check: Duration,
}

impl Scheduler {
    pub fn should_run(&self, now: Duration, candidates: usize, changed: bool) -> bool {
        candidates > 0 && (changed || (self.pending && now >= self.next_check))
    }

    pub fn record_report(&mut self, now: Duration, report: &WorkerReport) {
        self.pending = report.needs_work();
        self.next_check = if self.pending
            && !report.reconciliation_pending
            && report.network_policy_allows_upload == Some(false)
        {
            now.saturating_add(PAUSED_BACKOFF)
        } else {
            now
        };
    }

    pub fn record_error(&mut self, now: Duration) {
        self.pending = true;
        self.next_check = now.saturating_add(PAUSED_BACKOFF);
    }
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
    let network_policy_allows_upload = match report.get("network_policy_allows_upload") {
        None => None, // Legacy minimal reports retain the original polling cadence.
        Some(value) => Some(value.as_bool().ok_or("invalid_worker_report")?),
    };
    Ok(WorkerReport {
        pending_jobs,
        reconciliation_pending,
        network_policy_allows_upload,
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
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    // A concurrent fork can retain another test's writable runner FD until its
    // exec, even with CLOEXEC. Serialize executable writes and child launches
    // together so a freshly closed fixture cannot spuriously fail with ETXTBSY.
    static RUNNER_PROCESSES: Mutex<()> = Mutex::new(());

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
        let _process_fixture = RUNNER_PROCESSES.lock().unwrap();
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
                network_policy_allows_upload: None,
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

    #[cfg(target_os = "linux")]
    #[test]
    fn writable_runner_still_fails_closed_then_runs_after_writer_closes() {
        let _process_fixture = RUNNER_PROCESSES.lock().unwrap();
        let fixture = Fixture::new();
        let runner = fixture.0.join("runner.sh");
        fs::write(
            &runner,
            "#!/bin/sh\nprintf '%s\\n' '{\"pending_jobs\":2}'\n",
        )
        .unwrap();
        fs::set_permissions(&runner, fs::Permissions::from_mode(0o700)).unwrap();
        let writer = fs::OpenOptions::new().write(true).open(&runner).unwrap();
        assert_eq!(
            run_worker(&runner, &fixture.0.join("unused.json")),
            Err("worker_start_failed")
        );
        drop(writer);
        assert_eq!(
            run_worker(&runner, &fixture.0.join("unused.json"))
                .unwrap()
                .pending_jobs,
            2
        );
    }

    #[test]
    fn paused_pending_work_waits_but_changed_work_and_online_reports_do_not() {
        let mut schedule = Scheduler::default();
        let mut report = WorkerReport {
            pending_jobs: 5,
            reconciliation_pending: false,
            network_policy_allows_upload: Some(false),
        };
        schedule.record_report(Duration::ZERO, &report);
        assert!(!schedule.should_run(Duration::from_secs(10), 1, false));
        assert!(!schedule.should_run(Duration::from_secs(59), 1, false));
        assert!(schedule.should_run(Duration::from_secs(60), 1, false));
        // Either a configuration change or a newly published ready batch is
        // represented by changed=true and must bypass a paused delay.
        assert!(schedule.should_run(Duration::from_secs(10), 1, true));
        assert!(!schedule.should_run(Duration::from_secs(60), 0, true));
        report.network_policy_allows_upload = Some(true);
        schedule.record_report(Duration::from_secs(60), &report);
        assert!(schedule.should_run(Duration::from_secs(70), 1, false));
        report.network_policy_allows_upload = None;
        schedule.record_report(Duration::from_secs(70), &report);
        assert!(schedule.should_run(Duration::from_secs(80), 1, false));
        report.pending_jobs = 0;
        schedule.record_report(Duration::from_secs(80), &report);
        assert!(!schedule.should_run(Duration::from_secs(1000), 1, false));
    }

    #[test]
    fn errors_back_off_without_success_and_pending_reconciliation_is_not_delayed() {
        let mut schedule = Scheduler::default();
        schedule.record_error(Duration::from_secs(100));
        assert!(!schedule.should_run(Duration::from_secs(159), 1, false));
        assert!(schedule.should_run(Duration::from_secs(160), 1, false));
        assert!(schedule.should_run(Duration::from_secs(110), 1, true));
        schedule.record_report(
            Duration::from_secs(110),
            &WorkerReport {
                pending_jobs: 0,
                reconciliation_pending: true,
                network_policy_allows_upload: Some(false),
            },
        );
        assert!(schedule.should_run(Duration::from_secs(120), 1, false));
    }

    #[test]
    fn paused_fixture_reduces_actual_child_invocations_under_a_virtual_clock() {
        let _process_fixture = RUNNER_PROCESSES.lock().unwrap();
        fn runner(fixture: &Fixture) -> PathBuf {
            let path = fixture.0.join("runner.sh");
            fs::write(&path, "#!/bin/sh\nprintf 'call\\n' >> \"${0%/*}/calls\"\nprintf '%s\\n' '{\"pending_jobs\":5,\"network_policy_allows_upload\":false}'\n").unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
            path
        }
        let baseline = Fixture::new();
        let baseline_runner = runner(&baseline);
        let optimized = Fixture::new();
        let optimized_runner = runner(&optimized);
        let mut schedule = Scheduler::default();
        // Exercise actual child processes while advancing a synthetic monotonic
        // clock; this is not a five-minute hardware/power measurement.
        for second in (0..=300).step_by(10) {
            run_worker(&baseline_runner, &baseline.0.join("unused.json")).unwrap();
            let now = Duration::from_secs(second);
            if schedule.should_run(now, 1, second == 0) {
                let report =
                    run_worker(&optimized_runner, &optimized.0.join("unused.json")).unwrap();
                schedule.record_report(now, &report);
            }
        }
        let before = fs::read_to_string(baseline.0.join("calls"))
            .unwrap()
            .lines()
            .count();
        let after = fs::read_to_string(optimized.0.join("calls"))
            .unwrap()
            .lines()
            .count();
        assert_eq!((before, after), (31, 6));
        println!(
            "{{\"fixture\":\"stable_paused_queue\",\"virtual_seconds\":300,\"poll_seconds\":10,\"baseline_child_invocations\":{before},\"optimized_child_invocations\":{after},\"energy_measured\":false}}"
        );
    }
}
