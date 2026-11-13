use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT: AtomicU64 = AtomicU64::new(0);

struct Fixture(PathBuf);

impl Fixture {
    fn new(runner: &str) -> Self {
        let root = std::env::temp_dir().join(format!(
            "raspi-watcher-cli-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::SeqCst)
        ));
        fs::create_dir(&root).unwrap();
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
        let staging = root.join("staging");
        fs::create_dir(&staging).unwrap();
        fs::create_dir(staging.join(format!("{}.ready", "a".repeat(32)))).unwrap();
        fs::write(root.join("worker.json"), "{}").unwrap();
        fs::set_permissions(root.join("worker.json"), fs::Permissions::from_mode(0o600)).unwrap();
        fs::write(root.join("runner.sh"), runner).unwrap();
        fs::set_permissions(root.join("runner.sh"), fs::Permissions::from_mode(0o700)).unwrap();
        Self(root)
    }

    fn run(&self) -> std::process::Output {
        Command::new(env!("CARGO_BIN_EXE_cloudstick-watcher"))
            .arg("--staging")
            .arg(self.0.join("staging"))
            .arg("--runner")
            .arg(self.0.join("runner.sh"))
            .arg("--config")
            .arg(self.0.join("worker.json"))
            .arg("--once")
            .output()
            .unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn once_returns_failure_when_child_fails_or_report_is_invalid() {
    for runner in [
        "#!/bin/sh\nexit 7\n",
        "#!/bin/sh\nprintf '%s\\n' '{\"uploaded\":true}'\n",
    ] {
        let output = Fixture::new(runner).run();
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
    }
}

#[test]
fn once_keeps_reconciliation_signal_when_no_jobs_have_been_registered() {
    let output = Fixture::new(
        "#!/bin/sh\nprintf '%s\\n' '{\"pending_jobs\":0,\"reconciliation_pending\":true}'\n",
    )
    .run();
    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["pending_jobs"], 0);
    assert_eq!(report["reconciliation_pending"], true);
}
