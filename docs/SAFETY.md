# Storage safety and hardware gate

This checkout must not be deployed to hardware yet. Legacy setup scripts,
Python entry points, SCP helper and service units are deliberately blocked.
Automatic USB file deletion, empty-directory deletion and staging cleanup are
removed. No configuration switch restores deletion or runtime activation.

## Why simultaneous access is unsafe

The USB host owns its filesystem caches. A second mount on the Pi does not share
those caches. Host writes can change file data, directory entries and allocation
metadata while the Pi reads them, producing incomplete files or inconsistent
metadata. A second writable mount can corrupt the filesystem. The kernel's
[mass-storage documentation](https://docs.kernel.org/usb/mass-storage.html)
warns against modifying backing storage from other processes and says buffered
host writes make reads of a writable LUN undefined.

A read-only Pi mount prevents Pi writes but cannot make host writes consistent.
A quiet timeout, unchanged size/mtime, Python lock, Pi-side `sync`, or copying a
live image does not prove the host flushed its caches. `removable=y` allows media
ejection; it does not itself eject the media.

## Planned exclusive ownership protocol

The initial mode requires a deliberate host handoff. This is a design requirement,
not an implementation in this release.

1. **HOST_OWNED:** expose the writable gadget image. The Pi never mounts, scans,
   repairs or prunes it. Uploads can use previously completed staging batches
   on the Pi's separate filesystem.
2. **HOST_RELEASED:** ask the host/application to finish writes, flush and safely
   eject/unmount. Observe supported ejection or an explicitly coordinated release.
   If release cannot be established, defer backup. Cable removal alone does not
   prove a clean filesystem.
3. **PI_OWNED:** close the LUN or unbind the gadget after coordinated release;
   confirm no in-flight gadget I/O and no host access. Acquire a single storage
   controller lock. Check dirty/damaged state without repair; stop on uncertainty.
4. **STAGING:** attach a read-only loop device and read-only mount only after
   exclusive ownership. Fully enumerate files, reject unsafe paths/links, stream
   copies into an incomplete batch, and record sizes/content hashes. Flush files
   and manifest, then atomically publish a ready batch on the Pi filesystem.
5. **RETURN_TO_HOST:** successfully unmount, detach the loop and release all
   references before re-exposing the image. Failure keeps the image unavailable
   and records an error. Never force unmount to continue or expose a mounted image.
6. **UPLOAD:** use immutable ready batches; record upload and independent
   verification results for each file version and provider. Cloud latency need
   not keep USB unavailable once staging finishes.

Host release must precede detachment: abruptly disconnecting a busy gadget can
lose buffered data. Test power continuity and re-enumeration on the intended host,
including camera systems. Hosts unable to flush and release cannot safely use
this initial backup mode. Continuous simultaneous access requires a different
coordinated architecture; even an atomic block snapshot may contain an inconsistent
host filesystem.

## Retention and recovery

Retain originals and staging. A process exit, HTTP success or matching size does
not prove identical remote contents. WebDAV may need a streamed read-back hash
when no useful remote hash is available. Never label size-only checks verified.
If staging fills, pause new staging work and report the condition. Do not delete
oldest files for space. Retention would require a separately reviewed policy,
per-file/per-provider evidence, recovery tests and deliberate user action. It is
outside the initial runtime. No automatic filesystem repair is permitted.

## Before a future hardware trial

- Implement/test exclusive ownership and crash recovery. Cover failed host release,
  mount, copy, flush, unmount and re-enumeration; unknown state must block access.
- Prove actual host flush/eject behavior, complete enumeration, interrupted work,
  low space, power-loss recovery and authentication errors preserve originals.
- Keep gadget privileges in a narrow controller and secrets out of logs. Review
  scripts and units before lifting quarantine.
- Use disposable data for a separately authorized Zero 2 W trial. No formatting,
  flashing, service installation or hardware testing belongs to this preparation.
