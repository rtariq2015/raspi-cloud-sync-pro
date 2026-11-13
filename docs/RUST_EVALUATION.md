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
