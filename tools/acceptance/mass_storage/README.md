# Disposable mass-storage trial

This is a manual host-only trial, not a service or automatic backup. It changes
only one fresh configfs profile. The core hardware gate remains disabled.

Root first reviews and runs `prepare_image.py` separately. It creates only the new
1 GiB partitioned exFAT image at
`/var/lib/raspi-cloud-sync-pro/mass-storage-trial/disk.img`. Existing paths are
never overwritten. Use a protected installed interpreter/package and a protected
copy of these operator scripts. No project script changes boot configuration.
If preparation times out or is interrupted, inspect the retained intent and
read-only loop inventory before any further operation. A loop association may
exist even if its creating command did not return a name. Never retry creation
or detach unrelated loops; the existing trial directory deliberately blocks a rerun.

1. Run `trial.py plan --ssh-peer ACTUAL_SSH_CLIENT_IP` while the current Ethernet
   recovery mode still works. This saves root-private `plan.json` and
   `rollback.json` durably before any device change. The header check is not a
   filesystem-cleanliness test; image preparation performs the clean check.
2. Root must verify a working Wi-Fi SSH session, save and validate the actual
   Ethernet recovery command/module parameters, quiesce USB traffic, and perform
   the separately reviewed temporary driver switch. This operator does not unload
   `g_ether`, load `libcomposite`, change services or edit boot files. Never unload
   a driver with active I/O. The saved plan records existing module parameters,
   ICS state, Wi-Fi profile/address/default route, UDC and image identity.
3. Explicitly approve the saved plan: create root-owned mode0600
   `ATTACH_APPROVED.json` in the trial directory with exactly
   `{"schema_version":1,"approve_host_export":true,"plan_sha256":"SHA256_OF_PLAN_BYTES"}`.
   Run `trial.py attach --ssh-peer ACTUAL_SSH_CLIENT_IP`. Missing approval,
   `g_ether`, another bound gadget, changed image/Wi-Fi or retained attach intent
   refuses a new export. Partially created profiles and all files are retained.
4. Start `trial.py watch-eject --ssh-peer ACTUAL_SSH_CLIENT_IP --timeout 600`
   before Windows safely ejects. It must observe configured+loaded followed by
   configured+empty in that same process. Cable loss disarms it; an empty LUN at
   startup or a timeout never authorizes detachment. A successful observation
   unbinds only this already-empty trial LUN and stops. It never mounts, scans,
   hashes or stages the host filesystem, and never reattaches it.

On every failure, retain the image and records. **Do not clear a loaded LUN or
unbind it to clean up.** The host may still own the image after process exit or a
failure report. Stop further actions and inspect actual host/device state. Only
after verified host release may Root remove this exact trial profile and restore
the separately validated Ethernet recovery configuration. The recorded recovery
procedure is deliberately manual: it does not automatically unload or restore a
driver. If Windows removes the whole device instead of leaving a configured empty
LUN, this observer fails closed and requires manual review.

Local tests (no Pi/root/device operations):

```sh
PYTHONPATH=src .venv/bin/python -W error::ResourceWarning -m unittest discover \
  -s tools/acceptance/mass_storage -p test_trial.py -v
```

These tests establish operator ordering and refusal behavior only. Windows
enumeration, copy integrity, actual safe-eject semantics and later read-only
capture remain native acceptance steps. No cloud or backup success is claimed.
