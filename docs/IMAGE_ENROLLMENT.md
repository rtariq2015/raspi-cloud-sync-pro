# Stage 3 — Safe Image Enrollment

**The separately approved Pi/Windows 8 GiB upgrade passed physical acceptance on
15 November 2026.** Safe Eject remains required. Public build gates remain disabled.
The source tests and the later live acceptance are distinguished below.

This component enrolls an already created, separately approved 8 GiB exFAT image
after the existing controller has accepted a genuine Windows Safe Eject, completed
capture and handoff, and entered its detached active maintenance hold. It does
not create, format or copy an image, change services, access Google Drive, or
modify retained staging/history. It is part of the existing Stage 3 upgrade.

## Admission and verification

`ImageEnrollment` holds the existing controller and maintenance locks. It requires
the exact active hold ID, matching original journal/inventory/approval, empty
private capture staging, healthy detached gadget, no image mounts or conflicting
loop devices, and explicit action approval. Native partial ownership records refuse
admission and remain untouched.

Only the original 1 GiB and an explicitly approved 8 GiB candidate are supported.
Both are private, singly linked regular files on the same filesystem. Actual
candidate allocation must cover its full apparent size; a sparse image is refused.
The state and image storage filesystem must match. Remaining available space must
cover 21 GiB (the accepted 29 GiB migration envelope minus the already allocated
8 GiB candidate), with at least 1,024 available inodes. Checks repeat before
commit and activation; this supplements the existing capture-space policy.

Verification uses pinned read-only loops, successful `fsck.exfat -n`, existing
MBR/exFAT header/layout validation, verified read-only mounts, and SHA-256 for
every file. A separate bounded directory walk includes empty directories and
checks for changes during inspection. The old and candidate trees must agree
with each other and with the accepted capture inventory. Only verified owned
read-only resources are cleaned up; ambiguity is preserved for review.

The protected verifier adapter forbids writable mounts, filesystem repair,
gadget writes, formatting, provisioning and unrelated commands. Its platform
gate preserves root, Pi Zero 2 W, aarch64 and host-mount-namespace requirements.

### Narrow exception for active OS zram backing

Enrollment permits a loop only when independent read-only kernel evidence proves
it backs an active zram swap device and is unrelated to **both** image inodes.
The kernel configured-loop inventory must exactly match `losetup`, including when
the latter reports no loops. `/proc/swaps`, the zram `backing_dev` and `disksize`,
sysfs major/minor bindings, opened block-device identities, and
`LOOP_GET_STATUS64` must agree. The ioctl is a getter; no swap, loop or device
configuration is changed. Its backing device/inode must equal the identity of an
opened, private, singly linked, root-owned regular backing file. Names, loop
numbers, process labels and a reported zram relationship alone confer no exception.

Unknown loops, either image through any alias, unsafe mounts/holders/partitions,
missing or changing evidence, offsets, size limits and unsupported loop flags
refuse enrollment. Two full observations must agree. Stable fingerprints are
saved in the durable intent and checked again during commit, recovery and
activation, even after the process restarts. Verifying one image still protects
both images, including the archived original. Active swap usage and backing-file
modification times are deliberately excluded because ordinary OS writeback changes
them without changing ownership or identity.

These observations do not freeze kernel configuration or defend against another
administrator deliberately changing devices between observations. Exclusive
administrative control remains a migration precondition. Any changed recorded
identity refuses automatic reconciliation; retain evidence for review. Older
unpublished enrollment intents without the new `os_loops` proof are refused,
rather than silently upgraded. Ordinary 1 GiB operation without an enrollment
transaction is unchanged.

## Durable protocol

1. **Prepare:** create a private unique draft with immutable old/new snapshots of
   configuration, approval, controller journal, capture inventory, maintenance
   hold and session. Bind their hashes to the intent, exact image identities,
   modification times and verified file/directory manifest. Complete and fsync
   the draft, then publish it with a no-clobber directory rename. Interrupted
   drafts block export and remain evidence. Explicit prepare retry performs all
   admission/verification again and retains the previous drafts.
2. **Commit:** persist publishing status before any active changes. Rename the
   original image to a unique rollback path, preserving its inode; then rename
   the candidate to the canonical path. Publish the related records individually
   with durable pending files. **Those separate renames are not one atomic
   transaction.** The intent blocks every normal startup/export path throughout.
   Recheck identities and filesystem contents before recording committed status.
3. **Committed, detached:** the new journal is `enrolled_detached`, and the
   maintenance record is `enrolled`. Neither claims that the candidate was
   released by Windows; neither permits ordinary startup/release. Original
   maintenance evidence remains in the immutable rollback snapshots.
4. **Explicit activation:** recheck approval, resources, allocation, contents,
   space and records. Fsync an irreversible first-export marker **before any
   possible USB exposure**. A scoped internal permit allows only the reviewed
   controller attachment path, which retains its existing read-only checks.
   Record normal host ownership and terminal release only after verified attach.
   Controller service start remains a separate administrative action.

The export latch is enforced by controller startup/maintenance validation, the
controller attachment path, direct Linux backend attachment and protected
appliance configfs writes. It applies even if new enrollment admission is disabled.
Existing 1 GiB behavior remains unchanged when no enrollment state/draft exists.

## Explicit recovery and rollback

Recovery requires the exact transaction ID and explicit approval. It compares
actual image arrangements and active records against the hash-bound snapshots,
then verifies both detached read-only filesystem trees. It does not trust a saved
phase as evidence of ownership or permission to replay a mount/attachment.

- **Complete interrupted commit:** accept only the recognized original/candidate
  inode arrangements and known old/new records; preserve torn pending-file bytes
  under unique interrupted names before publishing verified records.
- **Rollback before export intent:** restore the original inode at its canonical
  path and restore its coherent active-hold records. Retain the candidate and all
  evidence. Return detached; use the existing separately approved unchanged-image
  maintenance release and explicit controller start afterward. Never restore or
  rewind backup history/staged data.
- **After first-export intent:** refuse blind rollback even if attachment failed
  or no user copy was reported. Intent is deliberately conservative: failure does
  not prove the candidate was never visible to Windows.
- **Host reconciliation:** after an interrupted activation, accept only the
  currently observed configured, healthy, candidate-loaded gadget with no local
  resources and matching records. Reconcile host metadata without replaying
  attachment, mounting the host-owned filesystem or claiming host release.
  Otherwise refuse and preserve both images for separate reconciliation.

Malformed/unknown records, changed identities/content, unsafe resources and
unverified partial states remain blocked. There is no force-eject, automatic
repair, automatic retry/export, latch deletion or service-management fallback.

## Operator interface and deployment boundary

The existing appliance launcher dispatches the following explicit actions. Each
also needs `--approve-image-enrollment`; that flag cannot enable public hardware
gates or replace the image/hold approvals.

| Action | Additional required input |
| --- | --- |
| `--prepare-image-enrollment CANDIDATE` | `--candidate-image-approval FILE`, `--maintenance-hold-id ID` |
| `--commit-image-enrollment TRANSACTION_ID` | Exact prepared transaction |
| `--recover-image-enrollment TRANSACTION_ID` | `--enrollment-recovery-action commit`, `rollback` or `host` |
| `--activate-image-enrollment TRANSACTION_ID` | Exact committed detached transaction |

`APPROVED_TEST_BUILD`, `MAINTENANCE_HOLD_ENABLED` and
`IMAGE_ENROLLMENT_ENABLED` are all **False** in the source repository.
Generic `HARDWARE_OPERATIONS_ENABLED` also stays False. Enabling a reviewed
protected Pi copy, installing/pinning it, stopping/starting services, creating or
formatting the candidate, performing real Eject/cutover/export and testing a new
backup require separate owner approval. The later approved live execution is
recorded under Physical acceptance below; ordinary installation does not enable it.

## Test evidence and limits

Tests use temporary ordinary files, synthetic file trees, simulated gadget/loop
boundaries and real process death. Positive unit fixtures explicitly model the
allocation counter for sparse header files; an unmocked negative proves real
sparse candidates are rejected. No formatter, actual loop/mount or Pi is used.
The previously accepted real disposable exFAT rehearsal remains separate evidence.

Tests cover identities/capacities/layout/approvals, manifest and empty-directory
mismatches, low space, unsafe ownership, native/torn partial records, prepare
and commit crash boundaries, startup refusal, retained backup bytes, pre-export
rollback, possible-exposure refusal and verified host reconciliation.

Initial enrollment baseline (`9c0ab7c`), 15 November 2026:

| Suite | Result |
| --- | --- |
| New enrollment tests | 26 passed; includes three prepare and nine commit process-death boundaries |
| New appliance/export boundary tests | 12 passed |
| Complete Python regression discovery | 972 run: 969 passed, 3 optional local-rclone tests skipped because `RASPI_TEST_RCLONE` was unset |
| Guarded USB operator tests | 36 passed |
| Storage-capacity acceptance tests | 35 passed |

The 38 new tests are included in the full Python result, not added again.
Commands (existing development environment; no installs or privileged actions):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python -W error::ResourceWarning -m unittest test_image_enrollment test_image_enrollment_appliance -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/mass_storage -p 'test_*.py' -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/storage_capacity -p 'test_*.py' -q
```

Independent review passed after focused corrections for empty-directory proof,
canonical-absent lock acquisition, allocation/space checks, native pending-record
refusal and complete-draft publication. No safety protection was bypassed.

Zram compatibility correction, 15 November 2026:

| Suite | Result |
| --- | --- |
| New synthetic kernel/loop proof tests | 26 passed; real device calls are mocked |
| New enrollment/zram integration tests | 7 passed, including reopened transaction and interrupted recovery cases |
| Complete Python regression discovery | 1,005 run: 1,002 passed, the same 3 optional local-rclone tests skipped |
| Guarded USB operator tests | 36 passed |
| Storage-capacity acceptance tests | 35 passed |

The 33 new tests are included in the complete Python result. Across the complete
suite and two acceptance suites, 1,073 tests passed and 3 were skipped. The same
commands above apply; focused new coverage can be run with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python -W error::ResourceWarning -m unittest test_zram_loop_verification test_enrollment_zram -q
```

Two independent source reviews checked kernel evidence and transaction/rollback
integration. A missing global-inventory completeness check was corrected and
tested; no further concrete safety blocker was found. New tests retain Python
3.9 compatibility. This is source evidence, not physical 8 GiB acceptance.

## Physical acceptance

**Completed 15 November 2026, 12:34 EST (17:34 UTC).** The owner explicitly
approved the protected deployment, genuine Windows Safe Eject, separate-image
formatting/migration/enrollment/export and one synthetic Google backup test.
This used the ordinary Windows test computer and the existing Pi Zero 2 W;
no clinical scanner or patient data was used for the acceptance file.

Deployed source: `8e3afad74acbb823d5180aeed3c5ab3f6ec18b2e`.
The new protected controller contains 48 modules: the verified previous package
with nine reviewed controller modules added/replaced and exactly the three
approved appliance/maintenance/enrollment gates enabled. Generic hardware
operations remain disabled. The existing backup-worker source, unit, Google
configuration, destination and Production refresh grant were preserved.

| Physical check | Observed result |
| --- | --- |
| Initial state | Original 1 GiB image and 19 verified jobs/95 events intact; healthy Wi-Fi, SSH and power; approximately 47.24 GiB available |
| Protected deployment | Fresh private rollback records saved; new controller warm-adopted the unchanged original image; worker paused |
| Genuine maintenance hold | Windows identity, exclusive lock, cache flush and ordinary media eject passed; Pi independently completed capture/handoff, detached and exited successfully into active hold |
| Separate candidate | Exactly 8,589,934,592 bytes; 8,589,938,688 allocated bytes; exFAT formatting, nonrepairing filesystem and geometry checks passed |
| Migration | All 18 files and 8 directories, totaling 214,963,601 file bytes, matched by path/size/SHA-256; old image strictly read-only and unchanged |
| Resource cleanup | Verified normal unmount/detach; no image-owned loops/mounts remained; the unrelated active OS zram proof was unchanged |
| Enrollment | Durable prepare/commit passed; original inode retained at its unique rollback path; new approval, journal and inventory binding validated |
| Activation | Explicit export passed; pinned controller immediately restarted and verified warm adoption/polling |
| Windows capacity | Healthy removable MBR/exFAT CLOUDSTICK on D:; disk 8,589,934,592 bytes, filesystem 8,586,788,864 bytes |
| End-to-end test | One unique 1,048,576-byte synthetic Windows file; Safe Eject automatically captured it and returned the drive; only that new file was admitted for backup |
| Google verification | Production upload verified on its first attempt; separate read-only download matched the Windows SHA-256 |
| Historical preservation | All previous 19 job rows and 95 event rows remained identical; read-only remote listing confirmed all 19 previous objects plus the new object |
| Final state | 20 verified jobs, 100 events, zero pending/failed/unexpected jobs; both services running and enabled; approximately 39.24 GiB available; healthy power and management access |

The synthetic Windows, staged and independently downloaded object shared SHA-256:

```text
3bb9bc42efb83b3183c5a6b0ef2e0d6d609827fb3b3430964b91b85abee5ce5a
```

Operator-only corrections were needed during observation: a manifest query used
the wrong field name, and a queue query initially compared batch paths instead
of the existing `UNIQUE(destination, relative, sha256)` rule. The worker stayed
paused until the existing deduplication rule proved exactly one new job. The
separate downloader initially used an optional filter that rclone rejects for
a single-file `cat`; removing that filter made the read-only download pass.
These corrections changed no production code or credentials and caused no extra
uploads, deletion, device recovery or rollback.

The original image, private source/configuration/state rollback records,
transaction evidence, retained staging and cloud versions remain preserved.
After first export, the 8 GiB image may contain newer Windows writes: **never
blindly restore the 1 GiB image or rewind history.** Preserve both and reconcile
newer files first if recovery becomes necessary.

This acceptance did not reboot the Pi, cut power, fill the drive to capacity,
exercise every Windows version, or test a hospital scanner. Enabled services and
their installed source/configuration were checked; reboot and power-loss behavior
were not physically retested. Process crashes and fsync tests do not prove
microSD power-loss durability. Safe Eject remains mandatory, originals are not
automatically deleted, and development-release limitations remain.
