# Stage 3C — Maintenance Hold Implementation and Test Report

15 November 2026. Branch: `development`. Starting accepted checkpoint:
`e4f273da722c1488575dc674be4daa16a358a1ab`.

**SOURCE-ONLY PASS.** Maintenance hold is implemented and tested offline.
It is disabled by default, not installed on the Pi, and has no physical Windows
or Raspberry Pi acceptance. No live device, service, USB drive, credentials,
history or cloud account was accessed or changed. The 19-version/95-event count
remains a historical acceptance checkpoint, not a newly measured count.

## 1. Exact implementation changes

- `maintenance.py`: small root-private administrative request/latch protocol,
  strict bounded records, session nonce, separate short admission lock and
  fsynced publication. Partial or unresolved records block export.
- `gadget.py`: check maintenance before startup/adoption and every attachment;
  intercept the existing final `poll()` → `_attach()` after capture, unmount,
  loop detach and capture-inventory persistence. Add final handoff acknowledgment
  and an explicit unchanged-image release method. `StorageOwnership` itself is
  unchanged: the existing RETURNABLE state is required before active hold.
- `mass_storage_appliance.py`: a disabled maintenance build gate, root/Pi/image
  approval checks for one-shot administrative actions, and active acknowledgment
  only after `handoff_ready_batch()` succeeds. The normal service command remains
  compatible; no new service, timer, dashboard, provider or management framework.
- Two new offline test modules cover controller and actual temporary appliance
  handoff behavior. Existing capacity checks, image preparation, worker/history,
  networking and systemd configuration are unchanged.

The existing unit has `Restart=no` and no ejecting `ExecStop`. On a successful
hold the appliance returns `maintenance_active` and exits normally. It does not
restart itself or invoke systemd. Stopping a loaded controller still does not
force Windows to release storage.
`maintenance_active` describes the USB/capture boundary only. It does not pause
the independent backup worker or authorize migration; future migration preflight
must assess that worker separately.

## 2. How an administrative hold is requested

Both `APPROVED_TEST_BUILD` and `MAINTENANCE_HOLD_ENABLED` remain **False** in
public source. Neither a configuration file nor a CLI flag enables those gates.
Any future protected build/deployment needs separate review and approval.

The source interface, for a future separately approved protected installation,
is an authenticated root action with explicit approval:

```text
python -m raspi_cloud_sync_pro.mass_storage_appliance \
  --request-maintenance-hold --approve-maintenance-action
```

This is an interface reference, **not an instruction to run it on the live Pi**.
The wrapper reuses existing root uid/gid, exact Pi/model/namespace, protected
configuration and image-approval checks. Paths are fixed; no arbitrary device,
image or state path is accepted through the command line.

The running enabled controller publishes a private session nonce. Submission
requires its existing lifetime lock to be held, validates the protected session
record and exact image identity, and durably records a unique pending hold ID.
It does not detach USB or read image contents. The acknowledgment says
`submitted_waiting_for_safe_eject`, not maintenance-ready. A request racing an
attachment gets a busy/refusal result, with no automatic retry.

The controller validates the nonce itself before acting. A stopped controller's
saved challenge is refused; a death immediately after the liveness probe can
leave a pending request, but restart refuses it rather than using stale eject
evidence. No timestamp, quiet period or saved record proves Windows release.

## 3. Clean host release and completion ordering

The existing uninterrupted observation remains mandatory:

1. Configured USB, exact loaded backing image, no local loops/mounts.
2. Later configured USB with an empty LUN in that same controller session.
3. Detach only that already-empty gadget; verify detached state.
4. Existing exact read-only loop, `fsck.exfat -n`, read-only mount and snapshot.
5. Successful unmount, verified loop detach and RETURNABLE ownership.
6. Persist the capture inventory and its digest; suppress automatic reattachment.
7. Complete the root-private ready-batch handoff. Require the private capture
   directory to be empty and the gadget still detached with no loops/mounts.
8. Persist active hold and report `maintenance_active`.

Windows can keep writing while a request is pending; the controller keeps
waiting. Cable disconnect, empty startup, failed checks or timeout never replace
Safe Eject. A request arriving during capture is checked again at the final
return boundary. An already authorized capture finishes before hold; an unfinished
or failed handoff leaves an unresolved hold, not a migration-ready report.

## 4. Preventing automatic reattachment

The decision to hold or return and the entire attachment sequence share the
short maintenance lock with request submission. This prevents a request being
admitted between the last latch check and kernel attachment.

Every `_attach()` checks the latch. Normal startup, loaded-host adoption and
cold profile preparation are also guarded. Existing partial `.pending` records,
bad permissions/links, malformed data, stale sessions or wrong image identities
fail closed. Turning request admission off does not ignore an existing latch.
Normal capture/automatic return remains unchanged when no request exists.

## 5. Durable state and restart behavior

Records remain inside the existing protected controller-state directory:

| Record | Purpose |
| --- | --- |
| `maintenance-session.json` | Exact image and fresh controller-session nonce |
| `maintenance.json` | Hold ID, session, exact image identity, phase and capture-inventory digest |
| Matching `.pending` files | Incomplete publication; any presence blocks automatic export |
| `maintenance.lock` | Short admission/attachment serialization; not proof of host release |

Files are single-link, owner-private mode 0600; the directory must be mode 0700.
Records reject duplicate/unknown fields, invalid types, stale identity and
oversized input. File and directory fsync surround replacement. Existing
controller journal and capture-manifest bindings are preserved.

Latch phases distinguish `pending`, `capturing`, `captured`, `active`,
`releasing` and `released`. The controller journal supplies operation/resource
intent, including `recovery_required` on an interrupted operation. The latch is
separate, so a recovery-journal write cannot erase maintenance intent.

**Pending, active or interrupted hold blocks ordinary startup after a process
restart or reboot, even if new request admission is disabled.** No eject arming
is replayed. The service may report review-required and remain stopped; it does
not automatically recreate a profile, finish capture, resume release or export.
Recorded active state is the last completed state, not independent proof of
current hardware state. Physical state must be rechecked for any release.

## 6. Separate safe release procedure

The source release interface is a different explicitly approved root action:

```text
python -m raspi_cloud_sync_pro.mass_storage_appliance \
  --release-maintenance-hold EXACT_HOLD_ID --approve-maintenance-action
```

It requires the controller lifetime lock to be available, an active matching hold,
unchanged approved image device/inode/size, the exact active controller journal,
unchanged capture-inventory digest, empty private staging, healthy power/profile,
detached gadget, empty backing LUN and no owned loop or mount. An incorrect,
missing, stale or repeated release is refused. It never force-detaches a host.

The controller durably writes `releasing` **before** using its existing
read-only filesystem check and loop-detach/attach sequence. Only after verified
host attachment and a durable host journal does it write `released`.
If anything fails, the unresolved record remains. In particular, a crash after
attachment can leave Windows owning the image: never force-eject it to retry.

Successful release is one-shot and returns `released_host_owned`. Restarting
the capture service is a further explicit administrative action; the release
command does not start it. Normal later capture/reattachment then works again.
Completed maintenance records are retained; this deliberately small protocol
does not rotate/erase records or admit a second maintenance cycle automatically.
Changed-image enrollment, latch cleanup and migration rollback are outside 3C.

After a reboot the configfs profile may no longer exist. A release cannot prove
the expected safe profile in that case and refuses; recreating it needs a
separately reviewed recovery procedure. An older controller build may ignore
the latch, so downgrading while maintenance is unresolved is unsupported.

## 7. Automated tests and independent review

| Group | Final local result |
| --- | --- |
| Focused hold/appliance/controller/capacity/ownership/backend | **147 passed** |
| Final main test discovery | **902 run: 899 passed, 3 skipped; no failures/errors** |
| Existing USB operator/capture tests | **36 passed** |
| Stage 3 disposable migration simulations | **15 passed** |

The 147 focused tests are part of the main suite, not additional to it. Across
the three distinct suites, **950 passed / 3 skipped**. The skips are optional
local-only real-rclone tests because that executable is unavailable locally.
No tool installation or cloud account was used. The final main run used unittest
discovery with a JSON summary; its standard equivalent command is below.

```bash
PYTHONPATH=src:tests .venv/bin/python -W error::ResourceWarning -m unittest test_maintenance_hold test_maintenance_appliance test_gadget_controller test_mass_storage_appliance test_mass_storage_capacity test_storage test_linux_gadget
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/mass_storage -p 'test_*.py'
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tools/acceptance/storage_capacity -p 'test_*.py'
```

The **26 new controller-hold tests and 12 new appliance tests** include pending
host I/O, valid/late requests, capture/handoff ordering, permissions, duplicate
requests, stale challenges, incorrect identities, admission conflicts, interrupted
cleanup, low space, partial record writes and unsafe release. POSIX child
`os._exit` tests cover durable pending/capturing/captured/active/releasing states
and blocked fresh startup with simulated loaded or detached hardware. A failure
after attachment but before completion preserves the loaded host and unresolved
release. Source, retained batches, temporary history rows and capture inventory
remain intact.

An independent skeptical review completed two focused correction passes:
stopped-controller admission/pending-wait handling, then challenge validation
before export and explicit RETURNABLE acknowledgment. Final review found no
remaining concrete safety blocker. No protection was disabled to obtain PASS.

## 8. Legacy and expanded capacity results

Existing 1 GiB normal capture/return and default-disabled behavior pass, including
the existing appliance, ownership, recovery and operator regressions. The 3B
strict size/layout/approval tests remain intact.

Generated sparse 1 GiB and 8 GiB fixtures pass the same approved capacity contract,
hold, handoff and unchanged-image release. These are simulated USB/header
fixtures, not a valid formatted 8 GiB filesystem or physical Windows test.
Changing from the held 1 GiB image to a different 8 GiB image is deliberately
refused by this release protocol until a separate migration enrollment is reviewed.

## 9. Remaining risks, commit and next prerequisite

No actual Pi deployment, Windows Eject, 8 GiB format/fsck, physical power loss,
USB re-enumeration or live hold/release was tested. Process interruption and
fsync fault injection do not prove microSD/controller durability. Root and the
approved controller exclusively administer the gadget; manual edits/deletion
of maintenance records are unsupported and do not establish safe ownership.
Stale or interrupted records require review; there is no automatic recovery
or cleanup. Existing low-space reserves remain unchanged.

The source-only Stage 3C checkpoint is the Git commit containing this report,
with accepted parent `e4f273da722c1488575dc674be4daa16a358a1ab`.
No main merge or release is authorized or performed.

**Single next recommended prerequisite: reviewed low-space admission for the
future 8 GiB appliance.** Actual disposable exFAT validation, deployment approval
and physical acceptance still precede any real migration. No Stage 3D/3E work
or migration has begun. Stage 3C stops here for owner review.
