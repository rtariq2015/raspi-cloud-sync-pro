# Stage 3D — Low-Space Admission Implementation and Acceptance Report

15 November 2026. Source-only development on `development`, starting at
`ace64eb8cc48c46114804d19327f80b3feffdd37`.

**SOURCE-ONLY PASS.** The explicit 8 GiB capture policy is implemented and tested
offline. Nothing was deployed to or read from the live Pi. Its 1 GiB image,
services, networking, credentials, staging and history were not modified. The
19-verified-version/95-event checkpoint is historical, not a current measurement
or an input to any cleanup/reset operation.

## 1. Exact source changes

- **`capture_space.py`**: a fixed, immutable 8 GiB policy and a per-capture guard.
  It validates available blocks, allocation units, inode capacity, destination
  identity, numeric inputs, changed-file plans and remaining copy progress.
- **`staging.py`**: optional admission around the existing changed-file capture;
  checks before batch/payload allocation and copying, during bounded chunks, after flush/fsync and
  before/after private ready publication. Short writes abort. Strong-policy
  ENOSPC becomes a fixed low-space error. The legacy path keeps its original
  reserve and error behavior.
- **`linux_gadget.py`**: one optional settings field with a legacy default, so
  the selected policy participates in existing configuration equality checks.
  No USB-driver behavior changed.
- **`mass_storage_appliance.py`**: strict schema-2 configuration for the named
  policy, binding to the actually approved 8 GiB image, controller wiring and
  fixed, filename-free error messages. Existing hardware/build gates stay off.
- **`gadget.py`**: pass the explicitly selected policy only after genuine release
  and verified read-only ownership; require the pinned image capacity to match.
  Existing recovery, journal and maintenance logic is retained.
- **`config/mass-storage-appliance-8gib.example.json`**: disabled example only.
  The original 1 GiB example is unchanged.
- Two new test files plus necessary existing mock-fixture updates. No history,
  worker, provider, maintenance implementation, dashboard or installer rewrite.

## 2. Admission calculation and margins

The accepted Stage 3 migration envelope was **`3C + 5 GiB`**: new image C,
one changed capture C, interrupted-capture allowance C, 4 GiB operating margin
and 1 GiB migration workspace. It is a **one-time migration planning envelope**.
The existing creation helper's 256 MiB spare-space check remains a new-image
creation check, not approval for a complete migration.

Routine captures do **not** charge the already-created image or migration
workspace again. Existing images, ready versions, incomplete captures, logs and
history already reduce measured availability; they are not subtracted twice.

For the stronger 8 GiB policy, let:

- **B** = the larger of observed filesystem fragment size and block size.
- **P** = sum of each changed file's length rounded up to B; zero-byte files have
  zero payload blocks but still receive metadata/inode allowance.
- **N / D** = changed-file count / distinct parent-directory count.
- **M** = rounded encoded batch-manifest length.
- **H** = `16 MiB + M + 2B × (N + D + 3) + round_B(ceil(P / 100))`.

The initial required available bytes are:

**`P + H + 8 GiB interrupted-capture allowance + 4 GiB operating reserve`.**

During copying, **P becomes remaining rounded output**, credited only in whole
flushed blocks and with the final partial block credited after file fsync and
size/hash verification. H stays as conservative metadata/transaction headroom
through publication. It covers manifest/control work, per-entry/directory
estimates and 1% allocation/extent slack; it is not an exact physical-footprint
model or a promise of SQLite space.

A full changed image therefore needs approximately **20 GiB plus H**, rather
than the 29 GiB migration envelope. A small capture needs 12 GiB plus its changed
output and H. Unchanged files are hashed for the existing comparison but not
charged as new payload. Even an unchanged snapshot must demonstrate the fixed
operating/recovery/control headroom before the controller advances normally.

Measurements use **`f_bavail × f_frsize`**, excluding root-reserved blocks, through
an opened destination-directory descriptor. The guard verifies that the named
directory is still the same device/inode and that allocation geometry/capacity
has not changed. It checks available inodes against unstarted files, uncreated
parent directories, three structural/control entries and a 128-inode margin.

Block/counter fields must be genuine nonnegative integers; allocation units
must be powers of two from 512 bytes through 1 MiB. Impossible ordering, unknown
inode capacity, missing fields, failed observations or totals above signed
63-bit range refuse admission. Python integer arithmetic does not wrap.
Only the existing allowlisted image sizes participate in approval; the stronger
policy itself is fixed to exactly 8 GiB and cannot accept lower reserve values.

## 3. Legacy and 8 GiB configuration compatibility

| Configuration | Approved image | Behavior |
| --- | --- | --- |
| Existing schema 1, implicit `legacy-16mib` | 1 GiB | Existing 16 MiB per-file check and legacy behavior |
| New schema 2, `capture_space_policy: "capacity-reserve-v1"` | 8 GiB | Stronger capacity-aware admission |
| Schema 1/legacy with 8 GiB, or stronger profile with 1 GiB | Either mismatch | Refused; no silent downgrade or migration |
| Unknown policy, extra/missing fields, arbitrary capacity | Any | Refused |

Schema 2 retains all original appliance fields and adds only the exact named
policy. There are no user-configurable reserve amounts. Existing root-private
configuration, exact image device/inode/size and layout approval remain required.
The new policy field is included in `GadgetSettings` equality, so configuration
changes cannot evade the existing `_approved()` recheck.

Stage 3B's 8 GiB image approval/geometry format is preserved. Future protected
8 GiB appliance use additionally needs the explicit schema-2 space configuration;
no such configuration is installed by this stage. Old 1 GiB configuration needs
no edits. Public hardware and maintenance gates remain disabled.

## 4. Failure, retention and concurrency behavior

The guard rechecks before copying, around each bounded chunk, after file fsync,
before manifest work and immediately before publication. It also checks after
the parent-directory fsync and before returning a changed capture. Remaining
payload is reduced as work finishes; the full batch is not charged anew on
every chunk.

Initial rejection creates no new completed batch. Failure during copying retains
the incomplete directory and source. Known low space or strong-path ENOSPC
reports **`capture_space_low`**; unknown capacity reports
**`capture_space_unknown`**. The appliance maps these to fixed explanations
without filenames, paths or raw exception text. Other I/O failures stop through
the existing conservative error path.

Ownership becomes ERROR; the controller records recovery-required when it can,
does not advance its successful capture baseline and does not force USB return.
No originals, ready versions, incomplete evidence or backup rows are deleted.
Restart does not automatically replay an interrupted capture.

**A late rename/parent-fsync or post-publication error can leave a private
`.ready` directory.** It is retained rather than renamed back or erased.
The protected appliance never hands off a batch when staging raises, so this
private data is recovery evidence, not accepted worker/cloud publication.

**These observations are not reservations or quotas.** Concurrent consumers,
delayed allocation, inode exhaustion and I/O faults can invalidate a previous
reading. Flushing permits conservative progress accounting but does not reserve
blocks; final fsync and publication checks can still fail. Even exact-threshold
initial admission does not promise completion: metadata growth and conservative
partial-block credit can cause a later refusal without an outside writer.
The OS/runtime margin is budgeted headroom, not a physically isolated reserve.

History storage is a separate transaction boundary. A temporary SQLite enqueue
failure rolls back new rows while retaining old rows and staged payloads; after
the injected failure is removed, the same retained batch can enqueue once and
deduplicate the next retry. Existing worker/engine failure behavior is reused.
This does not prove actual full-disk/readonly-filesystem recovery or guarantee
future database commits, including when history resides on another filesystem.

## 5. Maintenance hold interaction

Stage 3C's request, session, ownership and release protections are unchanged.
A pending request never substitutes for Eject. Low space after genuine release
leaves capture/recovery and the hold unresolved; it does not hand off a failed
batch, report active maintenance or reattach USB. An active or interrupted hold
blocks normal startup before capture/admission can begin.

Successful capture can still finish handoff and enter active maintenance when
space is sufficient. Admission is not authority to release a hold. Separate
root approval, exact hold/image/inventory and detached-state checks remain
mandatory for release. No new cancellation, cleanup or migration behavior exists.

## 6. Test commands and exact results

| Suite | Final local result |
| --- | --- |
| Focused space/controller/legacy/hold/engine/worker regressions | **230 passed** |
| Main discovery | **934 run: 931 passed, 3 skipped; zero failures/errors** |
| USB operator/capture | **36 passed** |
| Stage 3 disposable simulations | **15 passed** |

The 230 focused tests are a subset of main discovery. Across the three distinct
suites: **982 passed, 3 skipped**. The skips are optional local-only real-rclone
checks because that executable is unavailable locally. No package was installed
and no real provider or clinical data was used. The final main run used a
programmatic unittest discovery runner with exact JSON skip reporting; the
standard equivalent command is included here.

```bash
PYTHONPATH=src:tests .venv/bin/python -W error::ResourceWarning -m unittest test_capture_space test_capture_space_controller test_storage test_gadget_controller test_linux_gadget test_mass_storage_appliance test_mass_storage_capacity test_mass_storage_image_prepare test_maintenance_hold test_maintenance_appliance test_engine test_staged_worker
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/mass_storage -p 'test_*.py'
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/storage_capacity -p 'test_*.py'
```

The **21 new policy/staging tests and 11 new controller/configuration tests**
cover exact/below thresholds, unavailable and malformed observations, allocation
rounding, inode capacity, strict integers, remaining-progress accounting,
changed-only plans, retained versions/partials, space lost between chunks,
write/fsync/manifest ENOSPC, short writes, pre/post-publication failure,
interruption, history rollback/retry, recovery restart, and pending/active hold.
Existing 1 GiB normal/low-space and 3B/3C regressions pass. All filesystem-space
observations for the new policy are mocked over temporary generated files;
synthetic USB behavior and sparse images are not physical acceptance.

## 7. Independent engineering review

An independent skeptical source/configuration review found no concrete safety
blocker in the formula, integer bounds, retained-file handling, race boundaries,
legacy compatibility or maintenance interaction. One bounded error-handling
refinement added strict progress types, short-write refusal and fixed strong-path
ENOSPC projection; final review passed. No reserve, ownership or approval check
was bypassed to pass tests. Existing historical tests were updated only where
their settings mocks needed the explicit default policy field/constructor input.

## 8. Remaining physical acceptance and next step

No real low-space filesystem, 8 GiB exFAT format/fsck, actual Windows Eject,
hardware hold/release, power loss or Google upload was tested. Future acceptance
must check the actual Pi's available blocks/inodes, delayed-allocation/fsync
behavior, controlled consumption on disposable storage, interrupted capture,
maintenance interaction and retained history. A fresh capacity/workload preflight
and separate approvals still precede any live deployment or migration.

The Stage 3D source-only checkpoint is the Git commit containing this report;
its accepted parent is `ace64eb8cc48c46114804d19327f80b3feffdd37`.
No main merge or release was performed.

**Single next recommended milestone: real disposable 8 GiB exFAT validation and
migration rehearsal**, separately approved and confined to disposable images.
Do not treat this as approval to format/mount anything or replace the live image.
Stage 3D stops here. No deployment or physical migration has begun.
