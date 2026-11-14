# Rust decision and review of the supplied Cloud Stick proposal

Decision: use a small Rust watcher/scheduler, retain the tested Python dashboard
and backup engine, and use rclone/Samba for their respective protocols. This is
a hybrid architecture, not a whole-project rewrite. No language change lifts the
USB filesystem ownership requirements.

## Corrections to the supplied proposal

| Proposal | Engineering decision |
| --- | --- |
| Original Zero W / ARMv6 target | The verified hardware is Zero 2 W on aarch64; build `aarch64-unknown-linux-gnu` |
| Wait eight seconds, then mount the live image read-only | Rejected. Quiet time and read-only flags do not prove host flush or consistency |
| Load g_mass_storage during setup | Rejected beside the working g_ether connection; modes must be exclusive |
| Format /piusb.bin during setup | No formatting/setup script is run or supplied as automatic deployment |
| Rust detects a file has finished by its size | Metadata is a candidate hint, not completion evidence; use a validated ready batch or SMB deny-write handle |
| Drop guard guarantees unmount | Cannot guarantee cleanup after SIGKILL, power loss, kernel errors or failed unmount; ownership recovery remains necessary |
| Ping an Internet IP proves Wi-Fi access | It may use USB/default routing; approved-uplink and metered policy stay in the backup engine |
| Raw rclone copy is sufficient verification | Keep immutable content versions, durable jobs, independent downloaded-content SHA-256 and bounded retries |
| A flag set guarantees less than 80 MiB | Backend, metadata and file counts affect memory; measure the actual workload |
| Rust prevents all leaks/races | Safe Rust addresses memory/data-race classes, not every leak, logical race or filesystem-ownership bug |
| Break system-package protection / beta toolchain | Use a pinned containerized stable compiler and isolated Python environments |

Rust's official [ARM64 Linux target](https://doc.rust-lang.org/rustc/platform-support/aarch64-unknown-linux-gnu.html)
is suitable for this 64-bit installation. The original Zero W's ARMv6 build is
a separate target/acceptance project. We do not retarget the user's device based
on a document describing different hardware.

## Implemented watcher

`rust/` contains `cloudstick-watcher`, using the standard library and serde_json;
there is no async runtime or image mount API. It polls only published ready
directory names and the protected worker configuration. Incomplete/symlink batches
are ignored. The child worker still validates manifests and payload hashes before
queueing; a suffix or metadata event cannot authorize uploading arbitrary bytes.

The watcher invokes a protected, absolute runner with argv, never a shell command
constructed from filenames. The [upload wrapper](../tools/cloudstick/upload.sh)
executes the existing completed-batch worker once. Only a valid pending-job report
allows the watcher to park after the work is drained. Paused/failed/unknown child
outcomes do not become a backup-success claim. A new batch/configuration or pending
jobs or unfinished reconciliation triggers another worker run. `--once` fails
if the child fails or returns an invalid report. SQLite registration receipts avoid repeatedly
hashing an unchanged completed batch across short-lived worker invocations;
the upload engine still rechecks actual bytes before upload/verification.

The inactive [systemd unit](../deploy/systemd/cloudstick.service) uses an
unprivileged backup identity, no capabilities, read-only staging, private writable
queue state and `KillMode=control-group`. No unit is enabled by adding this source.
The watcher has no mounts to clean up. Stopping its unit stops child worker/rclone
processes together; durable jobs then recover conservatively on the next run.

## Reproducible cross-build

The build toolchain is confined to Docker on the development computer; Rust or
linker packages were not installed into the Pi OS or laptop system interpreter.
The [Dockerfile](../tools/rust-build/Dockerfile) uses official Rust 1.100.0 with
the ARM64 target and Debian cross linker. `Cargo.lock` records crate versions.

```sh
docker build -t raspi-cloud-sync-rust-build:1.100.0 tools/rust-build
mkdir -p /tmp/raspi-rust-cargo
docker run --rm --user "$(id -u):$(id -g)" \
  -e CARGO_HOME=/cargo -v /tmp/raspi-rust-cargo:/cargo \
  -v "$PWD":/work -w /work/rust raspi-cloud-sync-rust-build:1.100.0 \
  cargo build --locked --release --target aarch64-unknown-linux-gnu
```

Run `cargo test --locked`, `cargo fmt --all -- --check` and
`cargo clippy --all-targets -- -D warnings` in the same container. Do not run
a binary for the wrong architecture or copy a build onto a live service without
checking its hash and preserving rollback.

## Physical Pi measurements — 2026-11-13

The compiled ARM64 watcher is 398,048 bytes. A private disposable-fixture trial on
the Zero 2 W reported one thread, VmRSS/VmHWM **1,796 KiB** after the child had
finished. An incomplete batch did not trigger the fixture; a ready directory
triggered it once; unchanged completed work did not retrigger on the next interval.
No image, USB setting, Wi-Fi setting or cloud account was used in this trial.

For context, the existing warm Python dashboard reported VmRSS 34,856 KiB and
VmHWM 66,564 KiB. Twenty same-Pi loopback login GETs had median 6.88 ms and maximum
117.58 ms. **These are different components doing different work**, not a fair
Rust-versus-Python web-server benchmark or whole-appliance memory total.

The watcher result supports using Rust for the always-idle scheduler. It does
not establish that rclone, Samba, network firmware, hashing or a full appliance
will stay below a particular total RAM value. Sustained-transfer, retry, radio
coexistence, power failure and end-to-end cloud account tests remain necessary.


## End-to-end follow-up

The revised scheduler passed six Rust tests (four unit, two CLI), formatting,
clippy and ARM64 release build. The binary remains 398,048 bytes; new SHA-256:
`93c16d59ffda4281d8d5a218392b0c67e8db2cf6e1c78922d60113cbd4384d17`.
On the Pi, it invoked the actual Python CLI worker and ARM64 rclone against two
temporary local alias destinations. Three files produced six read-back verified
jobs; an unapproved-uplink policy first paused them without spending attempts.
Incomplete data and all originals stayed retained, repeat runs deduplicated, and
a controlled process-group stop followed by another run retained completed work.
This does not simulate electrical power failure or accept a real cloud account.
See [the result record](acceptance/pi-worker-2026-11-13.json).

## Paused-worker optimization — 2026-11-13

The Rust scheduler now waits 60 seconds before launching another worker for an
unchanged, policy-paused queue or a failed child. Its monotonic clock is unaffected
by wall-clock corrections. New ready batches or a changed protected configuration
bypass that delay at the next filesystem poll. Unfinished reconciliation and
online/legacy reports keep the existing poll cadence. Invalid child output remains
an error, never a successful backup.

Ten Rust tests, formatting, clippy and the ARM64 build pass. A virtual five-minute
test using actual fixture child processes reduced launches from 31 to 6 at a
ten-second poll. This is a simulated clock, not five minutes of measured hardware
energy use.

A separate physical Pi comparison used a five-second poll and a twelve-second
steady window per binary: the previous scheduler launched its fixture three times;
the new one launched once. New ready work woke it in 3.033 seconds and a config
change in 5.055 seconds. Both parents measured 1,784 KiB RSS/high-water RSS and one
thread. The new 398,048-byte ARM64 binary has SHA-256
`b559ae8a1056c387b8cfd336561984bceb75492b08e8a426c07cf4bf82c9a0e2`.
See [the retained-fixture result](acceptance/pi-rust-paused-2026-11-13.json).

This reduces unnecessary process launches. It does not measure watts, change
network settings or establish total appliance CPU savings; directory polling is
unchanged. Without a new batch/config change, an approved connection returning
can take up to the 60-second backoff plus the configured poll interval to be
noticed. Ready batches must remain retained for pending-job recovery.

Further Rust work should follow profiles: inventory scanning or streamed hashing
are possible candidates if sustained file-count/transfer tests identify them as
bottlenecks. Keep protocol handling in rclone/Samba and the tested dashboard in
Python until a representative comparison justifies moving another component.

## CI executable-fixture isolation — 2026-11-14

The [push run for `49eeb8d`](https://github.com/rtariq2015/raspi-cloud-sync-pro/actions/runs/43563276469)
failed while starting a freshly rewritten test runner. An isolated local copy
reproduced `ETXTBSY` (OS error 26) on its sixth parallel test-suite invocation.
A sibling process launch can inherit a writable runner descriptor until its
exec, even when that descriptor has `CLOEXEC`; this is the mechanism described in
[Rust issue 114554](https://github.com/rust-lang/rust/issues/114554).

The executable fixtures now share a test-only mutex covering script writes and
child launches within each test process. A Linux regression holds a runner open
for writing, verifies the existing `worker_start_failed` result, then closes the
writer and requires a valid pending-job report. Production watcher behavior and
its failure/backoff policy remain unchanged. No Pi binary was replaced for this
test-fixture change.

All eleven Rust tests, formatting and clippy pass with the pinned toolchain.
The unit and CLI suites also passed 1,000 repetitions with eight test threads
per suite (11,000 test cases), following the reproduced failure on the unchanged
fixtures.
