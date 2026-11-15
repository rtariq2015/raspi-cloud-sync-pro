# Raspi Cloud Sync Pro — autonomous development review

Date: 15 November 2026 (America/New_York). Branch: `development`.
Starting application source: `665977bd60e2f9afb6297ba2110b31157eb37f29`.
G4F/Stage 2/Stage 3 commits contain documentation, acceptance evidence and the
offline storage simulations. Stages 3B/3C add source-only capacity validation
and maintenance hold with offline regressions; neither is deployed.
The separately approved Stage 2 maintenance installed the existing three-line
worker fix from that baseline on the Pi. Stage 3B changes no Pi installation.
The G4F documentation checkpoint is `fdb0c0eb2f774bdc1154cafcfcaab088785b4672`.
The latest documentation checkpoint is the Git commit containing this report.
No main merge or release is authorized.

## Executive result

**Stage 3C SOURCE-ONLY PASS:** explicit root-approved session-bound maintenance
waits for genuine Eject, capture, verified cleanup and handoff before active hold.
Unresolved/partial records block export/restart even with admission disabled;
separate unchanged-image release retains read-only checks and interrupted-release
refusal. **902 main tests run: 899 passed, 3 optional local-rclone skips; 36 USB
operator and 15 storage simulations passed.** The working Pi was not accessed
or modified. [Full maintenance report](MAINTENANCE_HOLD_STAGE3C.md).

**Stage 3B source support complete:** legacy 1 GiB approval remains compatible;
8 GiB requires an explicit capacity/layout record and exact image identity.
Shared geometry checks remain behind exclusive ownership and read-only fsck.
Image preparation and manual approval/capture routes use the same capacity
contract. Journals and capture inventories still refuse replacement identities
without rebinding. The live 1 GiB image and services were not accessed or changed.
[Stage 3B changes, tests and remaining prerequisites](STORAGE_CAPACITY_STAGE3B.md).

**G4F PASS:** the ordinary Windows test computer copied one unique synthetic
1 MiB file to CLOUDSTICK. Windows source and USB read-back SHA-256 matched.
Safe Eject triggered the existing detached capture and automatic USB return.
The existing worker uploaded once using the active Production authorization.
A separate Google download matched the Windows original and staged copy.
There are now **19 verified backup records and 95 events**, including all
previous 18 full job records and 90 full events unchanged.

**Roadmap Stage 2 PASS:** after separate owner approval, the exact existing
failure-reporting fix was installed in the protected worker source. Only the
backup worker restarted. Its fresh report shows zero pending and zero failed
jobs. An isolated native Pi fixture retained a terminal failure after idle ticks
and a fresh interpreter restart. Live data, credentials and USB were preserved.

**Stage 3 COMPLETE — planning/simulations only:** measured 47.241 GiB available
to ordinary processes and 815.281 MiB free on Windows CLOUDSTICK. Recommend an
8 GiB separate candidate under the conservative `3C + 5 GiB` additional-space
budget; about 18.241 GiB remains for further retained data/overhead. Actual QA
daily volume is unknown. **15 offline simulations and 73 existing focused
regressions pass.** The current 1 GiB image, services and all 19/95 history rows
remain unchanged. Larger-image controller support, verified maintenance hold,
real disposable exFAT tests and reviewed low-space admission are prerequisites; no live migration was
attempted. [Full Stage 3 report and future approval prompt](STORAGE_CAPACITY_STAGE3.md).

## Checkpoint: Stage 3C safe maintenance hold

- **STATUS:** source-only/offline PASS; no live hold, release or migration.
- **START:** `e4f273da722c1488575dc674be4daa16a358a1ab`.
- **CHANGES:** private maintenance latch and admission lock, controller hold at
  the existing post-capture return boundary, explicit root admin actions and
  handoff-before-active appliance integration. No ownership enum/systemd change.
- **SAFETY:** default gates off; same image/session; actual loaded→empty eject
  observation; no force detach, repair, deletion or automatic hold recovery.
  Pending/active/interrupted records block startup, including with admission off.
- **REVIEW:** two focused correction passes, then independent PASS. One-shot
  records are retained; stale/ambiguous states need review, not deletion/replay.
- **TESTS:** 147 focused tests PASS (subset of main); final distinct suites total
  950 passed / 3 optional local-rclone skips. Generated fixtures only; process
  death is not physical power-loss acceptance. Existing 1/8 GiB contracts pass.
- **LIVE:** no access or changes; historical 19/95 checkpoint is not overwritten
  or claimed as a new measurement.
- **COMMIT:** source-only commit containing this report.
- **NEXT:** owner review, then separately authorize low-space admission. No
  Stage 3D/3E or physical migration work has begun.
- **REPORT:** [Stage 3C implementation and tests](MAINTENANCE_HOLD_STAGE3C.md).

## Checkpoint: Stage 3B source capacity support

- **STATUS:** source/offline milestone complete; no deployment or migration.
- **START:** `efc5d08ce0f34378efb14b0838ac4d6217e4ceb9`.
- **CHANGE:** shared strict 1/8 GiB approval/geometry policy; explicit 8 GiB
  preparation and capacity-bound operator plans; no journal schema/rebinding.
- **SAFEGUARDS:** metadata-only live-owner approval; detached read-only fsck
  boundary; exact path/device/inode/size; public build disabled; failure retention.
- **TESTS:** final main discovery 864 run / 861 passed / 3 optional local-rclone
  skips; USB operator 36 passed; Stage 3 simulations 15 passed. Focused 109
  controller and 16 preparation tests are included in the main-suite total.
- **REVIEW:** independent source and test reviews found no remaining concrete
  blocker; real 8 GiB exFAT/Windows acceptance is not established.
- **LIVE SYSTEM:** not accessed or changed; no service, image, cloud or credential
  operation. The live 1 GiB build remains installed.
- **COMMIT:** the source-only Git commit containing this checkpoint.
- **NEXT:** owner review, then separately authorize maintenance-hold development.
  Low-space admission and real filesystem validation still precede migration.
- **REPORT:** [exact contract, code changes, commands and limits](STORAGE_CAPACITY_STAGE3B.md).

## Checkpoint: Stage 3 storage planning and safe simulation

- **STAGE:** practical capacity planning, read-only inventory and disposable tests.
- **OBJECTIVE:** compare 4/8/16 GiB, preserve data/identity/history, prepare
  conservative migration and rollback without changing the working appliance.
- **STATUS:** authorized planning and simulation complete; live migration NOT DONE.
- **WORK COMPLETED:** actual Pi/Windows capacity observation, retained-copy budget,
  source identity/ownership review, 8 GiB recommendation, low-space/recovery plan,
  15 generated temporary-file simulations and independent engineering review.
- **FILES CHANGED:** `tools/acceptance/storage_capacity/test_capacity_simulation.py`,
  one CI discovery command, Stage 3 report/evidence and these progress documents.
  Production code, credentials and Pi services were not modified.
- **TESTS EXECUTED:** 15 new simulations with ResourceWarning errors plus 73
  existing storage/gadget/appliance tests: **88 local focused tests PASS**.
- **HARDWARE TESTS:** read-only Pi metadata/status and ordinary Windows volume
  queries only. No new copies/ejects, larger exFAT formatting/mount, uploads or
  power interruptions. Process-crash fixtures are not physical power-loss proof.
- **EVIDENCE:** [capacity baseline](acceptance/storage-capacity-stage3-2026-11-15.json)
  and [complete report](STORAGE_CAPACITY_STAGE3.md).
- **RISKS:** 1 GiB hard gate remains; 16 MiB existing capture reserve is smaller
  than the proposed appliance margin; retained originals/versions grow without
  automatic deletion. No real daily workload or larger-filesystem acceptance.
- **COMMIT:** Stage 3 source-only checkpoint is the commit containing this report;
  accepted parent `eeb0cc21458da4f28988ccdd6d9f9c200fece1be`.
- **NEXT ACTION:** owner review, then separately authorize only the missing
  size-capable/maintenance-hold/real-exFAT/admission preparation before cutover.
  No further stage has begun.

## Checkpoint: G4F

- **STAGE:** G4F — final Production backup acceptance.
- **OBJECTIVE:** prove the accepted USB → Safe Eject → capture → Production
  Google Drive → independent SHA-256 workflow.
- **STATUS:** PASS within this one-file, one-host scope.
- **WORK COMPLETED:** actual Windows source creation, file copy, source/USB
  hashing, guarded normal media eject, passive Pi observation, backup/history
  verification, independent cloud download and final Windows retention check.
- **FILES CHANGED:** sanitized acceptance JSON, this cumulative report and the
  current project-status summary. Private helpers and evidence remain outside Git.
- **TESTS EXECUTED:** the physical test above; no full local 898-test rerun.
- **PASS/FAIL:** all final physical acceptance predicates passed.
- **HARDWARE TESTS:** Raspberry Pi Zero 2 W and the existing ordinary Windows 11
  Home test PC. No Siemens or other clinical system was used.
- **EVIDENCE:** [sanitized physical acceptance](acceptance/pi-windows-production-google-g4f-2026-11-15.json).
- **RISKS:** one file does not prove endurance, hospital authorization, scanner
  compatibility, permanent grant lifetime or retention of every historical object.
- **COMMIT:** application `665977b`; documentation checkpoint is this report's commit.
- **NEXT ACTION:** the reporting-fix preparation below; no extra cloud test.

The read-only preflight checked the exact existing Windows USB/volume identity,
healthy exFAT/removable status, approximately 816 MiB free on the 1 GiB image,
about 47 GiB free for local staging, connected built-in Wi-Fi, SSH, healthy
services, no rclone upload, no capture and the same Google account/folder.
The initial worker sample included an unclassified short-lived extra process;
neither sample had rclone or nonverified jobs, and the final sample contained
only the idle main worker. An extra helper alone was not treated as an upload.

The observer recorded loaded → configured empty LUN → USB detached → staging
while detached → loaded/configured host return. The existing controller performed
its previously accepted read-only capture; the observer changed no gadget state.
The Windows eject automation used an administrator token to lock, flush and eject
the exact non-system USB volume. It requested neither forced eject nor power-off.
This is not evidence of no-admin scanner compatibility.

The new job made one attempt and recorded exactly:
`queued → uploading → uploaded → verifying → verified`.
No other new or duplicate jobs appeared. Old full job/event rows were compared,
not just counts. The independent verification streamed one 1,048,576-byte object
from Google and computed SHA-256 separately from the worker's verification.

The first independent verifier stopped because its short-lived access token had
expired after the successful upload. One focused harness correction renewed
access in memory through the existing Production refresh grant. It preserved
the scope, refresh token and operational configuration bytes, then completed
the bounded download. There was no second physical copy, backup upload, consent
flow, service restart or authorization replacement.

A separate skeptical agent reviewed the cross-artifact hashes, sizes, paths,
event order and history invariants and found the scoped acceptance supported.
This was an internal review, not a review by the owner's separate ChatGPT chat.

## Historical checkpoint: roadmap Stage 2 preparation

- **STAGE:** deploy backup-failure reporting — offline preparation only.
- **OBJECTIVE:** prepare the already committed durable `failed_jobs` counter.
- **STATUS:** PARTIAL; deployment BLOCKED at the explicit approval gate.
- **WORK COMPLETED:** inspected `665977b`, exact installed worker source, launcher,
  report shape and actual dashboard binding; reviewed rollback and compatibility.
- **FILES CHANGED:** this deployment plan and progress documentation only.
- **TESTS EXECUTED:**
  `PYTHONPATH=src:tests .venv/bin/python -m unittest test_staged_worker test_dashboard test_engine`.
- **PASS/FAIL:** **57 tests passed** in the current local run.
- **HARDWARE TESTS:** read-only installation inspection; no live failure injected.
- **EVIDENCE:** source diff, existing regression tests and protected private
  installation observations. G4F's live state remained 19 verified jobs/95 events.
- **RISKS:** an older dashboard reader rejects the new field if later connected;
  avoid changing its report binding as part of this minimal deployment.
- **COMMIT:** reuse `665977bd60e2f9afb6297ba2110b31157eb37f29`.
- **NEXT ACTION:** obtain approval for the narrowly defined worker maintenance.

### Confirmed installation and compatibility

The active worker loads the root-protected fixed source directory through the
existing launcher at:

`/opt/raspi-cloud-sync-pro/qa/mass-storage-worker-dev22/launcher.py`

The pre-deployment inspection found that its installed
`site/raspi_cloud_sync_pro/staged_worker.py` exactly matched the parent of
`665977b` and lacked the new counter. The then-current report likewise omitted
`failed_jobs`: absence meant **unknown**, not zero. The later approved deployment
below installed the fixed source and accepted a fresh explicit zero count.

Observed source hashes for the future comparison:

| Source | SHA-256 |
| --- | --- |
| Retained pre-fix rollback source | `b4cd852d32f91ba78e0557696cbfdcfcc1d69da4e8b7d45c117fd46aaa292bf6` |
| Now-installed fixed worker from `665977b` | `38e4ce0484b23af375fbdcc64eba7fed3e433e0eb44612dd2717ef85a7650679` |

The live source is root-owned, mode `0644`. The launcher adds only this fixed
source directory to the module path; it has no per-file hash manifest to update.
The change is the existing three-line, destination-scoped SQL count, not a retry
or schema migration. Credentials, provider and destination options are unchanged.

The live older dashboard reader does not accept the added field, but its actual
service has no `--worker-state` binding and does not read the core history/report.
Therefore this deployment needs neither a dashboard patch nor dashboard restart.
Do not connect it to this report without a separately reviewed compatible reader.
The Rust reader ignores additional JSON fields and schedules pending work, not
terminal failures; no Rust binary replacement is needed.

### Reviewed maintenance procedure — subsequently executed

1. Recheck Pi health, host-owned USB state, no capture, no active rclone, idle
   worker, current history and the exact installed source/launcher hashes. Stop
   if any state has changed unexpectedly. Record the current valid baseline;
   never require the historical 18-job count after G4F added job 19.
2. With explicit approval, stop **only**
   `raspi-cloud-mass-storage-backup.service`. Require inactive/dead, `MainPID=0`
   and no remaining worker/rclone processes. Keep the controller running.
3. Create a new root-private `0700` rollback directory with `0600` copies of the
   exact installed worker source, its uid/gid/mode, launcher/unit state and a
   canonical read-only snapshot of current jobs, events and scheduler receipts.
   Record credential/config hashes without printing credentials. Keep all older
   rollback records. No schema, history, staging or credential replacement.
4. Atomically install **only** the reviewed `staged_worker.py` from `665977b`,
   preserving root ownership and mode `0644`; flush the file and parent directory.
   Keep every other module, launcher, executable and configuration unchanged.
5. Start only the backup worker. Require a new stable process and fresh successful
   status with `pending_jobs=0` and `failed_jobs=0` for the present all-verified
   history. Compare every existing job, event and receipt, the unchanged
   destination/config/credential bindings, and the still-host-owned USB state.
   Run the existing failure/idle/reopen regression against the installed module
   with an isolated credential-free temporary fixture. For native process-restart
   evidence, reopen that fixture in a fresh process and confirm its failed count,
   attempts and payload remain unchanged. Keep all fixture state separate from
   live worker data. No new cloud upload or synthetic failure in live history
   is authorized.
6. If startup or validation fails, stop only that worker, confirm its children
   exited, atomically restore the immediately preceding worker source with the
   original owner/mode, restart it and verify fresh health. Never restore a
   history snapshot, rewind jobs, delete staged data or revoke authorization.

The 57 local preparation tests prove a permanent fake-provider failure stays counted after
idle ticks and database reopen, with one attempt, no second upload and unchanged
payload. They also cover destination scoping, retries, legacy/new report parsing,
invalid failure counters and engine history behavior. These are isolated tests,
not a deliberately failed live backup. Installed-source and fresh-process native
acceptance were subsequently completed as recorded below.

## Checkpoint: roadmap Stage 2 approved deployment

- **STAGE:** deploy backup-failure reporting.
- **OBJECTIVE:** activate the existing durable terminal-failure counter.
- **STATUS:** PASS for the reviewed worker-only maintenance.
- **WORK COMPLETED:** confirmed the exact old source and healthy idle 19/95
  baseline; stopped only the backup worker; created a fresh protected source/state
  rollback; atomically installed only the reviewed module preserving root `0644`;
  restarted only that worker and accepted its fresh stable report.
- **FILES CHANGED:** one installed Pi module, the sanitized evidence below and
  this existing cumulative report/progress page. No new application source.
- **TESTS EXECUTED:** two local fixture interpreters before deployment and two
  native Pi interpreters after deployment; prior 57 focused preparation tests.
- **PASS/FAIL:** PASS. Live report: `pending_jobs=0`, `failed_jobs=0`.
- **HARDWARE TESTS:** native fixture executed as `raspi-backup` using the exact
  installed module, an explicitly fake provider, temporary state and Python
  socket creation blocked. A permanent failure stayed `failed_jobs=1`, `pending_jobs=0`
  after idle and fresh-process reopen, with one attempt, one fake upload call,
  identical full job/event rows and unchanged payload hash. No live failed job.
- **EVIDENCE:** [Pi deployment and isolated restart acceptance](acceptance/pi-failure-reporting-stage2-2026-11-15.json).
- **RISKS:** this does not inject or demonstrate a real cloud outage. The older
  dashboard remains unbound to the core report; no UI upgrade is claimed.
- **COMMIT:** deployed existing source from `665977b`; this report's commit
  records deployment evidence. Rollback was not needed.
- **NEXT ACTION:** roadmap Stage 3 storage-capacity planning, before any approved
  physical migration. No source, history or credential rollback was performed.

All 19 live job rows, 95 events, receipts and rejection rows remained identical.
Configuration, credentials, unit files and launcher hashes were unchanged, as
were the Pi boot and controller/dashboard process IDs. The source tree comparison
found only the intended module changed. USB remained configured and host-owned,
with no capture or rclone activity. No new cloud operation or upload was run.

The historical full baseline was 898 Python tests, 11 Rust tests and five CI
jobs at the earlier source checkpoint. Those were not rerun locally here.
No cargo executable was available on the current PATH or known earlier tool
locations; no toolchain was installed. The existing Rust code/test was reviewed.
Normal GitHub push checks are separate. The G4F checkpoint `fdb0c0e` was later
observed passing all five jobs in [its completed development run](https://github.com/rtariq2015/raspi-cloud-sync-pro/actions/runs/43680693940):
Samba protocol, Rust, and Python 3.9/3.12/3.13. This is current CI evidence for
that exact checkpoint, separate from the 57 focused local tests and Pi fixtures.

## Remaining work and approval boundary

During G4F and Stage 2 offline preparation, no live service was stopped/restarted
and no source was deployed. During the later specifically approved Stage 2
maintenance, only the backup worker restarted and only its one reviewed module
changed. USB/network settings, boot, storage size and formatting remained
unchanged throughout. G4E's existing Production credentials/rollback remain intact. The G4F
verifier made only read-only Drive operations and one in-memory access refresh;
the already-running worker made the one specifically approved new backup.

The 1 GiB storage limit remains. Reliability/endurance, practical storage
capacity and later installation acceptance remain unfinished Version 1 work.
No clinical deployment is authorized; organization/vendor and data-handling
approvals remain prerequisites. The Stage 2 approval gate was satisfied before
maintenance. No live storage migration is authorized.

No-eject operation remains unimplemented and unproven. No completed research was
reopened, no experimental implementation was attempted, and no roadmap change
is needed. Preserve Safe Eject as the accepted Version 1 completion contract.

**Exactly one next major task:** roadmap Stage 3 storage-capacity planning and
disposable simulation. No actual image resize/replacement, service restart,
credential change, cloud operation or scanner access is authorized by that plan.

### Ready-to-paste next-stage prompt

> Continue roadmap Stage 3 only: inspect current capacity read-only, calculate
> image/staging/retention reserves, compare a new image with migration, and
> simulate using disposable fixtures. Preserve the accepted Safe Eject system,
> the deployed reporting fix, Production credentials and all 19 backups/95 events.
> Do not resize, replace, mount or format live storage, restart services, change
> USB/networking, create cloud operations or touch the clinical scanner. Prepare
> the minimum safe migration/rollback proposal and stop before live changes.
