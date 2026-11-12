# Architecture and project structure

## Implemented foundation

`src/raspi_cloud_sync_pro/` is the only installable Python package. Its CLI reports
static release capabilities and validates a versioned non-secret JSON schema.
It has no third-party runtime dependencies and performs no hardware or cloud
discovery. A successful config check establishes schema validity only.

Schema version 1 requires exactly `schema_version`, `sync`, and `safety`. Providers
are `disabled`, `koofr_webdav`, and `google_drive_rclone` for planning;
`sync.enabled` and `safety.automatic_deletion` must be JSON boolean `false`.
Unknown/missing fields, duplicate keys, wrong types and premature enablement are
errors. Validation errors do not echo supplied values.

Legacy sources stay in `src/home/pi/` and `src/DropboxHelper/`, excluded from
package discovery. They are reference material with safety patches, not the
new service implementation. `doc/` is historical; `docs/` is current.

## Intended runtime boundaries

Add modules with implemented/tested behavior rather than shipping empty adapters.

| Future component | Responsibility |
| --- | --- |
| Storage controller | Own all gadget/loop/mount operations; exclusive handoff from `SAFETY.md`; sole privileged storage boundary |
| Stager | Complete inventory, safe paths, streamed hashes, atomic immutable batches; never read host-owned storage |
| Job store | SQLite transactions, file versions, per-provider attempts/results and crash recovery on the Pi filesystem |
| Providers | Upload/check ready batches, cancellation, timeouts, retry classification and bounded memory |
| Scheduler | Durable queue, backoff, network readiness and space backpressure; no deletion |
| Network service | Supported OS network-manager interface, narrow privileges and rollback |
| Dashboard API | Authenticated status/validated commands; no arbitrary shell access or raw secrets |
| Dashboard client | Accessible Overview/Wi-Fi/Cloud/History views; clear ownership and backup states |

Providers receive staging paths/manifests, never the USB image. Identify a file
version by its relative path, size and content hash, not timestamps alone.
Persist staged → uploading → uploaded → verified, with failed/retry states and
the provider-specific verification method. Only content checks produce verified.

Use versioned remote namespaces to retain earlier file versions. Although
[`rclone copy`](https://rclone.org/commands/rclone_copy/) does not delete unrelated
destination files, it can replace matching paths. Define version naming/conflict
behavior before handling changed files. Do not run `rclone sync`, `move`, `delete`
or `purge` during backup.

## Raspberry Pi Zero 2 W constraints

Use a Python 3.9+ standard-library core, one bounded transfer initially, streaming
file I/O, rotating logs, explicit space limits and lightweight local UI assets.
Measure memory, sustained transfers, temperature, Wi-Fi recovery and power loss
on hardware. OS image selection and validation of gadget, exFAT and networking
tools remain future work; the retained setup is not a supported installer.
