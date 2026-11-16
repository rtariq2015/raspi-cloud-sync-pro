# Isolated no-eject MTP prototype

**Physical update — 15 November 2026:** three consecutive ordinary Windows MTP
transfers passed without Safe Eject, with independent local and Google download
SHA-256 matches. Windows used its built-in portable-device driver; this is not
a drive-letter interface. The earlier source-only record below is retained as
historical implementation evidence. See the [physical receipt](acceptance/mtp-windows-no-eject-2026-11-15.json)
and [controlled automatic-startup preparation](MTP_STARTUP.md).

This experiment implements a **whole-file MTP receive → durable ready batch →
existing backup worker** path. It does not mount or inspect the Windows-owned
8 GiB exFAT image. The transfer path is physically accepted as recorded above;
the new automatic-startup files remain uninstalled pending separate approval.

**Windows interface changes:** the intended view is **This PC → CLOUDSTICK →
CLOUDSTICK**, a portable device/storage exposed through Windows Portable Devices.
It is **not D:, Z:, an exFAT volume, or a USB mass-storage disk**. Microsoft supplies
the MTP class driver; correct descriptors and host policy still need physical
acceptance. Programs that only save to ordinary drive-letter paths cannot use
this interface. Adding MTP alongside a drive letter would not give files copied
to that drive letter MTP completion notifications.

## Implemented path

1. A patch to pinned [Viveris uMTP-Responder](https://github.com/viveris/uMTP-Responder)
   validates a complete `SendObject` container: current session/epoch, armed
   transaction, opcode, exact advertised length and every received/written byte.
   Short writes are completed; zero writes, early EOF, cancellation, disconnect,
   stale sessions, invalid lengths and storage errors refuse completion.
2. The receiver flushes the source file, checks its held identity and synchronously
   invokes one fixed helper with a **read-only descriptor**, not a shell command.
   No close event, unchanged size or quiet timer grants completion.
3. The unprivileged responder sends that descriptor to a narrow authenticated Unix
   socket broker. It never runs as the backup account or receives Google credentials.
4. The broker verifies the peer, descriptor, source path/identity and available
   space. It copies into a distinct inode under a private `.incomplete` batch,
   hashes and independently rechecks it, flushes payload/manifest/directories,
   and transfers ownership while the batch remains inaccessible to the worker.
5. Linux `renameat2(RENAME_NOREPLACE)` atomically publishes a UUID `.ready` batch.
   Both parent directories are flushed before acknowledgment. The manifest is the
   existing schema 1 `ReadyBatch` format. A private `mtp-completion.json` records
   the protocol identifiers; it is not uploaded as a user file.
6. The existing `StagedWorker`, `History`, `BackupEngine` and provider perform
   deduplication, offline queuing, retries, upload verification and history.
   They are unchanged. No new cloud-upload engine or rclone installation exists.

The original relative filename stays unchanged. New contents under the same name
create a new version; identical retransmission uses the existing
`UNIQUE(destination,relative,sha256)` rule. Every accepted snapshot remains
independent of later MTP overwrites, renames or deletion of the export-view file.
No automatic pruning of sources, incomplete evidence, ready batches or history
is introduced.

**Commit/cancel boundary:** cancellation/reset observed before whole-object commit
prevents admission. After every byte is received and durable commit wins, a late
cancel or lost response cannot undo a retained complete snapshot. A helper timeout
or disconnect after publication can therefore leave a valid ready batch despite
a host error. Preserve it; a retry is deduplicated. Never delete it or claim that
an absent host acknowledgment proves no backup occurred.

## Files and disabled runtime examples

- `tools/experimental/mtp/upstream.json`: exact upstream commit and patch SHA-256.
- `umtp-completion.patch`: receiver/completion hook and lifecycle safeguards.
- `validate_upstream.py`, `sendobject_fixture.c`: real patched C build and synthetic
  protocol/IO fault fixtures. The actual responder daemon is **never started**.
- `runtime/validate_pipeline.py`: real C receiver → real descriptor broker → ready
  batches → unchanged worker → independently hash-checked **local synthetic** remote.
- `src/raspi_cloud_sync_pro/mtp_ingest.py`, `mtp_broker.py`: isolated ingestion and
  one-operation local broker, with no USB or cloud administration.
- `runtime/`: fixed future-install launchers and examples only. The broker unit
  has an approval condition and no `[Install]` section. The JSON example is disabled
  and has deliberately unset UID/GID values. No installation script is provided.

Runtime roots must be precreated, nonoverlapping and on the same filesystem:

| Root | Required ownership/mode |
| --- | --- |
| Disposable MTP export directory | Dedicated responder UID, 0700 |
| Private completion work directory | Broker/root, 0700 |
| Existing backup staging directory | Existing backup UID, 0700 |
| Socket parent, outside export | Root:responder primary group, 0750; protected ancestors |
| Unix socket | Root:responder primary group, 0660 |
| Broker configuration | Root-owned regular file, 0600; protected ancestors |

The broker authenticates the responder's effective UID with `SO_PEERCRED`; the
client authenticates the root server. Merely setting uMTP's file-owner override
while running its process as root is insufficient and is correctly refused.
The static broker unit uses AF_UNIX only, a private network/devices namespace,
restricted writable paths and an inaccessible backup-worker credential directory.

**Never copy the upstream default configuration:** it exports `/` and other system
directories. The supplied example exports only the disposable MTP directory.
Do not execute upstream gadget setup scripts against the working USB configuration.

## Source-only checks

Use the already available compiler, Python and a pinned upstream checkout:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_mtp*.py' -v
python3 tools/experimental/mtp/validate_upstream.py --upstream /path/to/uMTP-Responder
python3 tools/experimental/mtp/runtime/validate_pipeline.py --upstream /path/to/uMTP-Responder
```

The build harness exports the pinned Git commit, validates the patch checksum,
builds the whole daemon without running it, and executes only synthetic fixtures.
Artifacts go to new private temporary directories; no loop devices, mounts,
gadget operations, real credentials or cloud API calls are used. Retain the printed
evidence paths. The pipeline fixture exercises three consecutive same-name,
different-content transfers with no Eject operation. Timings measure development
computer IO and the first manually invoked worker tick, **not Pi USB throughput
or Google latency**. Physical upload latency also includes the existing worker's
configured polling interval and the network.

See the [sanitized source acceptance receipt](acceptance/mtp-source-prototype-2026-11-15.json)
for exact results. Passing synthetic checks is not Windows hardware acceptance.

## Compatibility and bounded limits

- Official Raspberry Pi guidance lists the Zero 2 W USB data port as OTG-capable.
  Debian 13 provides an arm64 uMTP package, establishing userspace viability;
  this prototype instead builds its explicitly pinned patched upstream source.
- The original read-only source check found `usb_f_fs` for the Pi's installed
  `6.18.60+rpt-rpi-v8` aarch64 kernel with matching version metadata. It was not
  loaded at that checkpoint. The later physical receipt establishes tested
  ConfigFS/FunctionFS initialization, endpoint permissions and Windows binding;
  automatic reconstruction after reboot is still a separate acceptance gate.
- This prototype accepts **known-size full objects only**, at most
  **4,294,967,282 bytes** (32-bit container header/sentinel reserved). Unknown-size,
  partial-object editing and truncation are refused. This is an implementation
  limit, not a universal limit of MTP or Windows. Metadata boundary fixtures do not
  constitute a multi-gigabyte physical transfer test.
- Ordinary synthetic names are used for initial acceptance. A top-level filename
  beginning `--` is currently refused by the fixed helper's argument parser; no
  unsafe fallback is attempted. This is a known prototype naming limitation.
- A fixed responder-side reserve and the broker's configured reserve protect
  space admission. Complete and incomplete retained data still consume finite
  space; a full device must refuse new data. Nothing deletes originals to recover
  space. Keep both reserves aligned in an approved build/configuration.
- USB data and power can share the Pi's data socket; MTP does not change the
  electrical requirements. Removing power can interrupt any transfer. Arbitrary
  power-loss safety and sustained throughput are not established by these fixtures.
- Restricted Windows policies can block WPD/MTP independently of mass storage.
  Siemens X.cite compatibility is **UNKNOWN**. If its exporter requires a drive
  letter or its policy denies portable devices, MTP **cannot satisfy all of the
  original restricted-workstation requirements**. No policy bypass or network
  substitution is implemented.

## One physical acceptance test — approval required

The experiment is source-prepared. The following is a single disposable test,
not permission to perform live operations:

1. Approve exact temporary installation/account/path permissions, the patched
   arm64 build and temporary gadget switch on the ordinary Windows test computer.
   Preserve Google credentials and all verified backup versions/history (the last
   accepted baseline was 21 versions),
   retained staging, OS configuration and rollback records. Recheck actual counts.
2. Verify exact current image/gadget identity, healthy power/Wi-Fi/SSH, idle capture
   and backup state and sufficient space. Obtain one genuine release of the
   **old mass-storage mode** before changing USB functions. This transition release
   is separate from the MTP no-eject file-transfer acceptance.
3. Retain the existing image as a fallback without migration. Use a clean disposable
   **Pi-owned directory** for the first MTP test; no formatting of the microSD or
   old image is necessary. Its data may be disposable, but an image currently owned
   by Windows is never opened concurrently. Repurposing that image later would need
   an approved exclusive-access operation, not this prototype's ingestion code.
4. Start only approved experimental components with the dedicated responder UID;
   initialize FunctionFS descriptors/strings and verify readiness before binding.
   Run **either** stable mass storage **or** experimental MTP, never both on the
   unvalidated single controller. Keep Wi-Fi management and the existing Google
   worker/configuration unchanged.
5. Verify Windows automatically uses its built-in MTP/WPD driver, displays
   CLOUDSTICK as a portable device, and requires no added Windows application/driver.
   Refuse a host requiring a policy/driver workaround; do not touch Siemens.
6. Generate three unique synthetic files (for example two 1 MiB and one 64 MiB),
   record Windows SHA-256 hashes, and copy them consecutively through File Explorer
   **without Eject or cable removal**. Confirm exact local snapshots, the existing
   worker's verified jobs, independent Google downloads/hashes, time to upload, and
   continued device availability. Check all previous records remain intact.
7. A failure stops the experiment. Keep ready/incomplete evidence and both storage
   locations. Restore the saved stable gadget configuration only after stopping
   experimental transfers and confirming exclusive release; never force a shared
   filesystem or roll back history/credentials. A successful ordinary Windows test
   still does not authorize clinical scanner use.

The original source checkpoint performed no live operations. The later approved
physical test is recorded above. Stop for owner review before installing or
activating the new automatic-startup configuration.

## Primary references and credits

- [Viveris uMTP-Responder](https://github.com/viveris/uMTP-Responder) — pinned source
  and GPL-3.0 notices retained in the patch/provenance; no unpinned binary installer.
- [Linux FunctionFS](https://docs.kernel.org/usb/functionfs.html) and
  [FunctionFS/configfs testing](https://docs.kernel.org/usb/gadget-testing.html#ffs-function).
- [Raspberry Pi OTG guidance](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009276-WP-1-Using%20OTG%20mode%20on%20Raspberry%20Pi%20SBCs.pdf),
  [Debian Trixie arm64 package](https://packages.debian.org/trixie/umtp-responder).
- [Microsoft MTP driver guidance](https://learn.microsoft.com/en-us/windows-hardware/drivers/portable/guidance-for-the-hardware-vendor),
  [MTP class installation](https://learn.microsoft.com/en-us/windows-hardware/drivers/portable/the-mtp-setup-information---inf--file),
  [WPD storage properties](https://learn.microsoft.com/en-us/windows/win32/wpd_sdk/storage-properties),
  [WPD restrictions](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-storage).

Koseng's original source/history/credits and the project's GPL-3.0 license remain
unchanged. This experiment extends the existing project rather than replacing it.
