# Controlled MTP startup preparation

These source-only startup files prepare the existing no-eject MTP prototype
for a separately approved installation and reboot test. They are not an
installer, and none is enabled by this change. The working MTP transfer,
complete-object publisher and Google Drive worker are reused.

Windows sees **CLOUDSTICK as an MTP portable device, not an exFAT drive letter**.
Ordinary Windows acceptance does not establish compatibility with a restricted
Siemens workstation. No clinical scanner testing is authorized.

## Verified starting point

The accepted physical test transferred three consecutive synthetic files
without Safe Eject. Each acquired an independent, durable snapshot and a Google
Drive download with matching SHA-256. The read-only follow-up found 24 verified
backup versions and 120 history events. These are evidence from that observation,
not hardcoded expected totals for future deployment.

The existing MTP broker and responder are static units with `Restart=no`.
The approved startup installation and one reboot subsequently prepared the
unbound gadget, but the broker precheck failed; automatic MTP startup is **not
accepted**. The old mass-storage boot activation was disabled during that
approved installation. Do not infer current service state from this historical
description: a future deployment requires a fresh protected inspection.

The approved disposable comparison on Pi systemd `257.13-1~deb13u1` reproduced
the defect: the privileged `+` broker precheck still received a separate mount
namespace with `ReadWritePaths`, and refused with
`mtp_startup_host_namespace_required`. The same read-only preflight passed in
the host preparation context. The original boot's first refusal code was not
logged; the later comparison reproduced the mechanism, not that lost log.
The source correction below is not installed by editing these templates.

The installed official `rpi-usb-gadget` package is not presently claiming USB:
its ICS service is masked, its legacy Ethernet module-loader file contains only
comments, and `g_ether` is absent. Its login hint checks that the loader file
exists, which is insufficient evidence of an active Ethernet gadget. Do not
run its `on`, `off` or `toggle` commands: they also change boot and networking
configuration. The existing Zero 2 W `[all]` peripheral overlay is retained;
unrelated CM4/CM5 boot sections must remain unchanged.

## Minimum startup chain

The templates are in `tools/experimental/mtp/runtime/startup/`:

1. `raspi-cloud-mtp.target` pulls in the guarded binder.
2. `raspi-cloud-mtp-prepare.service` checks explicit approval, protected pins,
   kernel driver ownership, source/work/staging identities and competing USB
   owners before preparing a fresh **unbound** MTP profile and FunctionFS mount.
3. `raspi-cloud-mtp-host-check.service` runs the same fixed launcher's read-only
   `check` action as a short-lived root oneshot in the host mount namespace.
   `RemainAfterExit=no` prevents caching a successful check. The broker has
   ordered `Requires=` and `After=` dependencies on this check; a failure blocks
   its startup. No condition may turn failed verification into a skipped success.
   Cached successful preparation is insufficient: each new broker activation
   must pull in a fresh check. No `BindsTo=` targets the short-lived check.
4. The existing broker starts only after that successful check. Its executable,
   account, credential masks and shared-publication sandbox remain unchanged;
   it no longer tries to authenticate the host inventory inside its own sandbox.
5. The unchanged unprivileged responder starts after preparation and broker.
   Its new dependency stops it if the broker disappears; it never restarts
   automatically.
6. `raspi-cloud-mtp-bind.service` rechecks the prepared state, broker, responder
   and FunctionFS readiness before binding the exact UDC once.

The fixed root-owned launcher is
`/opt/raspi-cloud-sync-pro/experimental/mtp/start-mtp`, with only `prepare`,
`check` and `bind` actions. Its protected configuration is
`/etc/raspi-cloud-sync-pro/mtp-startup.json`; the separate root-owned
`MTP_STARTUP_APPROVED` record binds the exact approved configuration hash.
Neither file is generated or installed automatically.

The private configuration has exactly these fields: `schema_version` (integer
1), `enabled` (explicit true only after approval), `source_uid`, `source_gid`,
`max_power_ma`, `udc`, `files`, `directories` and `parent_entries`. `files` maps
each reviewed installed executable/module, completion configuration, effective
unit fragment and drop-in to its SHA-256. `directories` pins the device, inode,
UID, GID and full mode of the project parent and the existing export-parent,
export, work and staging directories. `parent_entries` pins the same metadata
for every immediate project-parent entry, including the masked private entries.
Those names remain private. The file must be root-owned, mode `0600`, without
symlink/hard-link aliases, and no larger than 128 KiB. Startup does not generate
new pins from whatever it happens to find.

The approval is exactly `schema_version: 1`, `mtp_startup_approved: true` and
`configuration_sha256` equal to the reviewed configuration's SHA-256. It is also
root-owned `0600`. Boot receipts pin newly created runtime identities to this
approval and the current kernel boot ID; previous `/run` identities are never
carried across reboot. No Google token or account identifier belongs in either
record.

The guard uses a root-only boot receipt at
`/run/raspi-cloud-mtp-startup.json` and the exclusive
`/run/lock/raspi-cloud-mtp-startup.lock`. The existing system module-loader
configuration supplies `libcomposite`; the guard requires ConfigFS USB gadget
support to be present and verifies approved configuration and competing owners
before loading `usb_f_fs`. Missing prerequisites cause refusal rather than a
boot-setting repair. This does not depend on the old MSC service starting first.

The prepare/host-check/bind units intentionally use the host mount namespace. Adding
`ProtectSystem`, `PrivateDevices` or other mount-namespace sandbox options to
them can hide the FunctionFS mount from the existing responder. The existing
broker and responder retain their own separate reviewed restrictions.

The short-lived root prepare/host-check/bind verifiers need `CAP_SYS_PTRACE` to inspect
PID1's mount namespace and the unprivileged responder's `/proc/PID/root` through
Linux's ptrace read-credential checks. It is not granted to either long-running
MTP process. Prepare alone also needs `CAP_SYS_MODULE` for `usb_f_fs`.
The read-only host check has only `CAP_SYS_PTRACE` and `CAP_DAC_OVERRIDE`;
its fixed check action invokes no module loading, mount or USB setup operations.
Only prepare/bind invoke lifecycle actions. Host namespace equality remains
mandatory; this correction introduces no `nsenter` bypass.

FunctionFS uses the existing `mtp` instance and `ffs.mtp` function, with the
existing unprivileged UID/GID, root mode `0500` and endpoint mode `0600`.
Only `ready=1` permits binding. A bound gadget awaiting a Windows host is a valid
boot result: UDC `configured` is required during physical acceptance, not when
the Pi has no USB data host. There is no timing-based file-completion rule.

The Google backup worker stays independent. USB preparation does not require
Internet access, and the existing worker already queues valid ready batches,
pauses according to network policy and retries eligible failures.

## Mutual exclusion and protected configuration

Before live installation, the owner must separately approve disabling only
the old mass-storage appliance's automatic startup, while preserving its image,
configuration, ownership journal, inventory and rollback records. Do not stop a
running controller or release Windows ownership as a side effect of startup.

The source template adds an MSC `ExecCondition` refusing future starts while
`MTP_STARTUP_APPROVED` exists. This is a mode interlock, not a release mechanism.
It neither stops an already-running service nor proves a filesystem is safe.
There is deliberately no `Conflicts=` that could silently stop a USB owner.
MTP preflight must still refuse an enabled/active competing controller, a bound
foreign gadget, unexpected loaded legacy gadget modules or ambiguous resources.

The live broker's tested shared-publication drop-in is machine-specific. Its
exact list of private project entries must **not** be copied into this repository.
The installation approval manifest must pin its exact bytes and the protected
parent/child identities and permissions. Work and staging must resolve to the
same mount instance inside the running broker, rather than merely sharing
`st_dev`. The MTP export remains read-only to the broker; existing unrelated
children remain inaccessible, including worker credentials and the old image.

The owner accepted a bounded shared-parent exception: broker access can create
new sibling names and change parent metadata. Startup does not remove that
limitation or claim an unchanged sandbox. A changed child inventory, unit,
executable, approval or identity requires review instead of an automatic rewrite.
The new drop-ins preserve the tested broker/responder `ExecStart` and sandbox;
they do not embed private paths beyond the fixed public runtime roots.

For this correction, a separately approved deployment must pin the added
`raspi-cloud-mtp-host-check.service` fragment and **every** effective drop-in,
the changed broker startup dependency drop-in, and changed `mtp_startup.py`.
Preflight authenticates the host-check unit just like the other startup units.
Review exact installed paths, modes and SHA-256 values before approving a new
configuration hash and matching `MTP_STARTUP_APPROVED` record. Do not regenerate
or replace either protected record automatically. Existing directory identities,
parent inventory, runtime pins and historical data must remain protected.
An old boot receipt is bound to its configuration hash; do not relabel it for
the new configuration. Re-establish runtime receipt validity only through the
separately approved lifecycle/recovery procedure.

## Refusal and interruption behavior

- Do not mount, read or alter the old Windows-owned exFAT image during MTP
  startup. Do not format any image or create a cloud destination.
- Refuse an existing same-boot MTP profile or unexpected FunctionFS mount rather
  than silently adopting, unbinding or rebuilding it. An interrupted prepare or
  failed bind preserves evidence and requires explicit recovery.
- Preserve incomplete exports and private `.incomplete` batches. A stable size,
  old manifest or reboot is not permission to publish them. The retained failed
  physical-test batch is evidence, not an automatically recoverable job.
- Existing valid `.ready` batches are reconciled by the tested worker's durable
  receipts and deduplication. Lost acknowledgements must not cause deletion or
  blind republishing. Interrupted cloud jobs become retry/failed according to
  the existing locked history recovery, followed by fresh verification.
- These units have no automatic retry, cleanup, fallback or `ExecStop` unbind.
  Ordinary reboot discards volatile ConfigFS/FunctionFS state; durable source,
  staging, credentials and history remain on the microSD card.

Startup refusals now log only a fixed allowlisted code, such as
`mtp_startup_host_namespace_required` or `mtp_startup_lock_busy`, and retain exit
status 1. Unknown/dependency/OS/input errors receive fixed generic codes. No
exception message, traceback, command output, private path or credential is
printed. The lock, approval, filesystem ownership and identity checks remain.

The old producer has `Requires=` and `After=` on the broker: a normal explicit
broker stop/restart propagates to it. The new startup dependency also deliberately
stops the producer on broker failure. Do not use an unattended broker restart
as Wi-Fi or USB recovery. Any intervention involving a running producer first
requires an idle-transfer check and owner-approved maintenance. Stopping a
systemd unit is not proof that Windows has finished transferring.

## Separate live approval and acceptance

The next approval must identify exact source/unit hashes and authorize only:

1. Fresh protected preflight of Pi health, power, space, USB ownership, current
   history, active jobs, approved runtime and unchanged Google configuration.
2. Protected backups of existing units, drop-ins, enablement, startup settings,
   image identity, ownership records and history. No retained evidence deletion.
3. Installation of the reviewed launcher/module, new static units and dependency
   drop-ins, plus locally generated root-only configuration/approval records.
   Preserve the accepted machine-specific broker sandbox without widening it.
4. Explicit exclusion of the old MSC boot service and enablement of the reviewed
   MTP target. The current running MTP system must not be restarted just to
   install files; the owner must approve any operation interrupting USB.
5. One controlled normal Pi reboot while no copy/capture/upload is active,
   with ordinary Windows test PC connected and stable power. No forced power
   cycle or automatic reboot retry.
6. After boot, read-only verification of Wi-Fi/SSH, the guarded startup order,
   actual broker mount view and permissions, responder UID/capabilities,
   FunctionFS endpoints, exact UDC and Windows built-in MTP recognition.
7. Only if separately included in that approval: one fresh synthetic transfer,
   automatic ready publication and existing-worker cloud backup, followed by
   independent local/cloud SHA-256 comparison and unchanged historical records.

The offline tests must first cover exclusion, changed pins/identities, incomplete
startup, FunctionFS readiness and bind refusal without performing real mount,
ConfigFS, service or cloud operations. Review the unit graph for ordering cycles
and verify that only the MTP target has an install target. Existing MTP publisher
and worker regression tests remain applicable.

Acceptance requires automatic startup after the approved reboot, no competing
gadget owner, Windows portable-device availability, protected ready publication,
unchanged credentials/destination/history and no unintended uploads/deletions.
Source tests alone do not establish reboot or power-loss acceptance.

The namespace correction adds an opt-in real-systemd regression using only
uniquely named disposable units and synthetic private roots. It checks repeated
broker activation/restart, cached preparation, refusal propagation, actual mount
namespaces and the broker's masked credential fixture. It never contacts the Pi,
binds USB or invokes the cloud worker. Ordinary local test discovery skips that
privileged fixture; the Python 3.12 CI job runs it explicitly on its isolated
GitHub runner. These results do not replace a separately approved Pi startup
acceptance test.

## Source verification — 15 November 2026

The final local regression run completed **1,155 tests: 1,152 passed and three
optional integrations skipped**. The strict MTP subset passed all **109 tests**,
including **67 new startup tests**. These use disposable files, in-memory USB
adapters and mocked Linux process/mount views; they do not start gadgets or
contact Google. The unit suite also runs `systemd-analyze verify` against an
isolated filesystem tree, without starting or enabling services.

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -p 'test_mtp*.py' -v
```

An independent review caught the verifier-only `CAP_SYS_PTRACE` requirement;
the prepared units and regression assertions now include it. Review found no
additional concrete source blocker. The tested broker/responder binaries,
completion protocol and worker were not changed. Live installation, boot
enablement, reboot and a new Google upload remain **unperformed** and require
the separate approval described above.

Reviewed source bytes for the namespace correction (not live approval records):

| Runtime file | SHA-256 |
| --- | --- |
| mtp_startup.py | `b90262d77e305f2c1f3db7e691b115da6e2a2b39e021b18d2203cdbdda030651` |
| broker startup drop-in | `44cd731753d0899547a92d029605e860f7bcf7242cd53e9a6b64cca4452841e5` |
| new host-check unit | `8270c66afe10107de671372a5fff9fbe9ad54172c2c0e9d13c033accecac7b87` |

Local regression before committing completed 1,186 tests: 1,182 passed and four
optional integrations skipped. The guarded operator suites passed another
97 tests (36 mass-storage, 26 Wi-Fi recovery, 35 storage-capacity). The privileged
systemd fixture is a separate mandatory CI check, not part of those local passes.

## Rollback boundary

Before reboot, retain exact original unit files/drop-ins and enablement state so
the startup-only changes can be restored under approved maintenance. Do not
automatically restart services after restoring files.

After MTP has accepted files, retain all exports, ready batches and history.
Restoring the old MSC image is a **separate USB mode switch**, requiring Windows
release, quiescent producer/broker, verified unbinding and unchanged original
image identity. It is not an automatic boot fallback and must not discard newer
MTP data. If startup or acceptance fails, stop and report the exact refused
condition; do not bypass the guard or silently export the old drive.
