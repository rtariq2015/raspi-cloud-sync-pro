# Pinned uMTP-Responder completion adapter

This is an isolated **source-only experiment** for Raspi Cloud Sync Pro. It does
not replace the accepted 8 GiB USB mass-storage implementation. Nothing here
installs, starts or configures a USB gadget or contacts Google Drive.

Upstream: [Viveris Technologies uMTP-Responder](https://github.com/viveris/uMTP-Responder),
commit [`8f20c6f8d47d1057c9793aa23bd0a678d54084e3`](https://github.com/viveris/uMTP-Responder/tree/8f20c6f8d47d1057c9793aa23bd0a678d54084e3)
(declares v1.10.2). Copyright 2018–2026 Viveris Technologies, principally
Jean-François DEL NERO and upstream contributors. Upstream and this adapter use
**GPL-3.0-or-later**; retain upstream notices and the repository's GPL license.
The original Cloud Stick work and Koseng credits remain unchanged.

`upstream.json` pins the commit and exact patch SHA-256. The patch is maintained
as a diff; the complete upstream source and generated binaries are not vendored.

## Actual change

The original `SendObject` implementation contains unchecked short-write/error
TODOs and returns success without proving that the advertised byte count arrived.
An unchanged upstream binary is therefore insufficient for this experiment.

The patch checks the command/data container, transaction, session/epoch and
advertised length, loops over checked writes, refuses a missing byte, and fsyncs
the held regular file. It checks the named/held inode and invokes a fixed helper
synchronously with a **read-only fd 3**, literal relative path, length and protocol
identifiers. The helper must durably publish the independent existing-schema
ReadyBatch before returning success. No shell or close/quiet-time watcher is used.
Other descriptors and ambient loader/Python environment are excluded from the
helper. Failure or a bounded timeout never produces `MTP_RESPONSE_OK`.

Cancellation already observed before the completion commit prevents publication.
Completion wins the cancellation mutex only after all bytes and fsync succeeded.
A later cancellation, missing acknowledgement or disconnect cannot undo an
already committed independent batch; its outcome can be uncertain to Windows,
and retries rely on the existing backup deduplication. Retained evidence is not
authority for forced retry or deleting data.

Pending object information is invalidated on actual Open/CloseSession, USB MTP
Reset, FunctionFS Disable/Unbind and disconnect/reconnect teardown. An atomic epoch
prevents stale DATA from being reused across a session, even if a numeric session
ID or object handle is reused. Unrelated/interleaved commands clear an armed grant.

Extended partial-edit/BeginEdit/EndEdit/Truncate operations are refused and are not
advertised. Explicit host DeleteObject/rename affects only the MTP export view;
committed private batches outside that view remain retained. Explorer replacement
behavior still needs physical acceptance.

Before any payload write, and before every subsequent write, the responder
checks free space for a **2 GiB reserve + remaining incoming bytes + one full
private snapshot**. Unexpected consumption or ENOSPC fails without a checkpoint;
no original is automatically deleted. This is admission checking rather than a
quota or a guarantee against other processes filling the filesystem. The Python
publisher independently checks the actual private staging filesystem.

The prototype accepts exact, known-length files up to **4,294,967,282 bytes**
(`UINT32_MAX - 13`). It rejects unknown-length sentinels and larger objects before
creating/truncating them. This limit belongs to this bounded adapter, **not to MTP
in general**. Full 64-bit MTP transfer extensions are not implemented here.

## Source-only validation

Prerequisites: an existing C compiler, make, Git and Python 3.12+ for the safe
archive extractor. No package installation is performed. Start with a separately
cloned checkout at the exact pinned commit, then run from this repository:

```sh
python3 tools/experimental/mtp/validate_upstream.py --upstream /path/to/pinned/uMTP-Responder
```

The harness archives the pinned Git object into a fresh private temporary
directory, verifies/applies the patch, builds the full responder, and compiles a
synthetic transport/filesystem fixture against the **actual patched receiver,
completion launcher and session operations**. It never starts the daemon or opens
USB, configfs, loop or block devices. It retains its disposable evidence.

**38 native cases passed** with GCC 13 on the development computer, including
three consecutive full transfers to the same relative name, exact callback
hashes, short-write/EINTR recovery, ENOSPC/zero-write/fsync errors, cancellation,
short/truncated data, disconnect, path replacement, symlinks, malformed lengths,
wrong transactions, unknown/oversized lengths, unsupported edits, helper failure
and timeout, actual Close/OpenSession replay USB epoch invalidation, stale metadata epochs, and low-space admission before
the first write and after simulated mid-transfer consumption.

The default completion callback in this harness is explicitly synthetic. Its
`--helper` option exists only for a separate **temporary fixture launcher** that
exercises the real Python publisher and existing worker. Do not point it at a
live production configuration. The parent `runtime/validate_pipeline.py` combines
that real publisher/broker path with the unchanged staging/backup worker and a
local synthetic provider. Neither fixture proves Windows or Pi USB acceptance.

The upstream example configuration exports `/`; it is **not used or approved**.
Any separately approved fixture must export only its dedicated private synthetic
source directory, with staging outside it. Never export the live root filesystem
or the stable mass-storage image as MTP storage.

The C launcher requires Linux/glibc `posix_spawn_file_actions_addclosefrom_np`.
The native build is not an ARM64 cross-build or a physical FunctionFS test. The
separate acceptance procedure must validate the target's exact kernel modules,
FunctionFS endpoints, Windows inbox MTP binding, power and timeout behavior before
any live gadget switch is approved.
