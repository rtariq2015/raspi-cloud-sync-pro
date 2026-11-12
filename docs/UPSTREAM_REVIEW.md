# Upstream review and provenance

Review date: 2026-11-12. Upstream author: **Koseng**.
Source: <https://github.com/Koseng/RaspiAsUSBStickWithCloudSync>.
Baseline commit: `1bc57020851edf2860f0b44ea4a0ecd7e2904615` (`Add usb cable info`).
This commit remains in the project history; no history is replaced or squashed.
`LICENSE` remains byte-for-byte unchanged and `README.upstream.md` is an exact
copy of the README at that commit. The `upstream` Git remote is kept.

The following behavior describes the original implementation at that baseline.
The current checkout quarantines its runtime, as described afterward.

## Existing USB mass-storage behavior

The Bullseye and Bookworm scripts in `src/home/pi/` differ mainly in boot paths
and Python dependency installation. They edit boot configuration for `dwc2`,
create `/piusb.bin` using `dd`, partition it, format its first partition as exFAT,
and enable three systemd units. The image is 3072 MiB; rerunning setup overwrites
an existing image without an existence check. The scripts assume the `pi` user,
hard-coded paths, sudo access, and available partition/formatting tools. They lack
a complete dependency preflight and reliable error handling.

`usb-storage.service` loads `g_mass_storage` with `/piusb.bin`, `removable=y`, and
`ro=0`, presenting a writable disk to the host. `Type=idle` affects command
dispatch timing; it does not establish safe ownership or readiness ordering.
The upstream README targets Zero W and suggests Zero 2 W compatibility; this
project has not verified that compatibility on real hardware.

`copy_new_usb_files.py` obtains a loop device, assumes partition `p1`, and polls
the image's modification time every three seconds. On a change, it mounts exFAT
at `/mnt/usb_share` with a default writable mount, scans for files newer than
the previous timestamp, and copies into `transfer/<timestamp>/`, preserving
relative paths. It scans root files but descends only through the two most
recently modified directories at each level. This is not a complete inventory.
An image timestamp at startup is the initial cursor, so existing files can be
missed. Image/directory timestamps do not establish completed host writes.
Unchecked mount/unmount failures, partial copies, and advancing timestamps can
leave stale mounts or miss data. Forced unmount is also used.

## Existing automatic upload behavior

`upload_new_files.py` polls staging folders every 4.5 seconds. It waits for a
`copyingActive` marker but gives up after about 31 seconds, then proceeds anyway.
This marker is neither an exclusive lock nor proof of a completed batch.

Dropbox uses the Python SDK, app credentials and a refresh token, reading a whole
file into memory and uploading with overwrite mode. Unbounded video files can
exhaust Pi memory. SCP uses `expect` and a shell glob; the helper embeds a password
and automatically accepts host-key prompts. Its exit behavior does not reliably
prove transfer success. The caller originally logged the exit code without
failing the batch. Neither provider independently checks remote contents or
maintains a durable job manifest.

The original uploader unconditionally called `shutil.rmtree(folder)` after
processing, including when both destinations were disabled. Failed SCP could
therefore discard the only staged copy. Exceptions end the loop;
`Restart=always` may repeatedly restart it.

## Hazards and local safety changes

| Original path | Hazard | Current mitigation |
| --- | --- | --- |
| `check_and_delete_on_usb` | Deletes oldest USB files above `KeepMaxFilesOnUSB` (default 600), without backup evidence | Non-mutating compatibility function; retention settings ignored |
| `get_all_file_entries_delete_empty_dirs` | Deletes empty directories during enumeration | Directory deletion removed |
| Uploader's `shutil.rmtree` | Deletes staging with no provider or failed SCP | Removed; staging retained |
| Setup scripts | Recreate/format image, change OS settings, enable services | Error and exit before original commands |
| Copier mount | Opens host-writable image locally | `mount()` refuses all calls; entry points blocked |
| Service activation | Exposes image or starts legacy loops | All three `ExecStart` commands replaced by `/usr/bin/false`; automatic restarts disabled |
| SCP helper | Password in source, host-key auto-acceptance, shell interpolation | Helper exits before transfer; Python checks process failure if called directly |

Both Python scripts now have guarded `main()` functions and import without
reading an image, creating logs, or starting loops. Dropbox is imported only by
its legacy upload helper. A code-only quarantine constant prevents both loops
starting; config values and environment variables cannot lift it. Legacy example
Dropbox sync is off. These are narrow patches to retained upstream files, not a
claim those helpers are a safe supported runtime. Original versions remain
accessible with `git show 1bc5702:src/home/pi/copy_new_usb_files.py` and other paths.

Historical `doc/` guides and the upstream README still include old installation
instructions and destructive command examples. They are provenance, not current
deployment instructions. Existing installations or copies outside this checkout
are not changed by these safeguards.

## Reference project boundary

The complete Phase 1 source review also covers the Windows helper and generated
project resources. Its findings and preservation boundaries are recorded in
[PHASE1.md](PHASE1.md). Phase 1 adds a separate tested core; it does not lift any
legacy hardware/setup quarantine or modify the retained Windows application.

[Cloud Stick Pro's README](https://github.com/rtariq2015/cloud-stick-pro/blob/main/README.md)
was reviewed read-only for guided setup, saved networks, separate uploaded and
verified states, diagnostics, and Overview/Wi-Fi/Cloud/History navigation. It
targets different hardware. No code, assets, history or credentials were copied,
and no changes were made to its local or remote repository.
