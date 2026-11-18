# Phase 1 implementation and limits

This is the historical Phase 1 report. For current USB/Ethernet controllers,
Samba ingestion, runtime gates and first-test instructions, see [PHASE2.md](PHASE2.md).

Phase 1 adds tested library components to the preserved Raspberry Pi foundation.
Version `0.1.0.dev1` is a development version, not a published release. Neither
automatic USB access nor an upload service is enabled by this work.

## Complete source review

Reviewed both Raspberry Pi Python scripts, both OS setup scripts, all three
service units, legacy config, SCP/expect helper and the Windows Dropbox helper,
including its C# entry point, OAuth form, designer/generated resources, project
settings and .NET Framework 4.7.2 configuration. The detailed USB/cloud findings
remain in [UPSTREAM_REVIEW.md](UPSTREAM_REVIEW.md).

The Windows helper opens Dropbox OAuth in a browser, posts an authorization code
with app credentials, and extracts a refresh token using a regex. It lacks robust
JSON parsing, cancellation and network error handling; form text fields expose
entered credentials to someone viewing the screen. It remains unmodified legacy
reference material, not a new setup application. It was not compiled or run on
Linux. No original Dropbox/SCP functionality is silently replaced: the safety
quarantine and reasons are documented, and the source remains in Git history.

## New implementation

See [ARCHITECTURE.md](ARCHITECTURE.md) for each module's responsibilities. The
important changes from upstream are full content-based inventory rather than
timestamp/newest-directory scanning, atomically completed staging rather than a
timeout marker, persistent per-version/per-destination jobs rather than volatile
loops, and independent verification rather than unconditional staging cleanup.

The new provider interface uses rclone for both Koofr's WebDAV backend and Google
Drive. A configured named remote and protected config file are required; secrets
are never passed as command-line arguments. Select an explicit backup directory
and verify its backend/account during later account tests. The adapter does not
infer backend correctness from the remote's name. Live account tests and OAuth/
app-password setup are still pending.

Rclone is an optional external program, not a Python runtime dependency. A local
integration test uses a temporary alias remote, with no cloud account, to exercise
actual commands. Run it with an installed executable:

```sh
RASPI_TEST_RCLONE=/absolute/path/to/rclone PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Without rclone, that one integration test is skipped; the remaining suite runs
with the standard library. No test mounts storage or runs installation commands.
The integration test checks copies, empty/Unicode/shell-like filenames, read-back
hashes, idempotency and corruption detection. It is not Koofr/Drive evidence.

## Safe use during development

The public CLI exposes `status`, `check-config`, and `history DATABASE --limit N`.
History is read-only and does not create a missing database. The config remains
schema version 1 with sync/deletion disabled; this prevents the new library from
accidentally becoming an automatic service. Provider enablement is an explicit
developer API option, false by default. There is no mount/format/install command.

Staging requires a storage ownership object in its staging state. In tests that
state uses simulated evidence over temporary fixture directories only. Supplying
boolean flags in a script is not a safe way to access an actual USB image.
Only a future reviewed gadget adapter can establish those conditions on a Pi.

While physically connected to the computer, the Pi may upload already staged
copies. To discover new USB files it must first coordinate a host flush/eject,
remove host access, stage under exclusive read-only Pi access, then unmount and
re-expose the disk. Retaining the cable can retain power; actual ejection and
re-enumeration behavior must be tested. If the host cannot cooperate, backup of
new files waits. Quiet periods and Pi-side locks cannot prove host consistency.

## Hardware testing still required

| Area | Acceptance evidence needed |
| --- | --- |
| Pi/OS baseline | Exact Zero 2 W revision, OS/kernel/architecture, dwc2/gadget support, exFAT tools, service user and power budget |
| Host ownership | Windows/Linux/macOS flush/eject, closed LUN and drained I/O detection, safe re-enumeration while powered; intended camera/other host separately |
| Filesystem integrity | Dirty volume detection without repair; mount/copy/unmount/detach failures; proof no simultaneous ownership |
| Power/crashes | Restart from every ownership/job state; incomplete files/batches remain recoverable; unknown state blocks image access |
| Storage pressure | Full image, full staging, SQLite write failure, large/deep inventories and source changes; no loss of originals |
| Cloud accounts | Koofr WebDAV and Google Drive auth, large/empty/Unicode/changed files, rate/quota failures, retries and downloaded-content verification |
| Wi-Fi | 2.4 GHz home/hotspot reconnect, metered permission, PEAP/EAP-TLS certificate validation, activation rollback, supported portal login and expiry |
| Endurance | Sustained transfers, memory/temperature, SD wear, long outages, interrupted reads/uploads and multi-day operation |

Before lifting quarantine, implement the missing device/OS adapters and fault
tests, then arrange a separately authorized disposable-data hardware trial.
Nothing in Phase 1 formats or flashes hardware. Cloud Stick Pro remains an
unchanged feature/design reference; no code/assets/history were imported.
