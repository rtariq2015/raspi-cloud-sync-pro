# Capture completed SMB files into the local queue

`smb_capture.SmbCaptureSettings.load(path)` and `run_smb_capture(settings,
once=True)` provide a capture-only runtime. The default is disabled. This API
does not create providers, upload files, configure Samba or networking, enable a
service, change USB modes, or enable the global hardware gate. There is no CLI or
production deployment in this change.

The ordinary `raspi-backup` identity loads and freshly checks a nonsecret,
root-owned settings file, a separate approval file, and the existing device
policy. These files must be readable by that identity, for example root-owned
0644 files beneath root-owned 0755 directories. Every ancestor must be protected
against group/other writes; symlinks and hardlinked authority files are refused.
Root-private 0600 files beneath root0700 directories would be unreadable here.
The SMB password is separate: a 0600 file in the capture identity's own 0700
state directory, with exactly `username` (`raspi-backup`) and `password` fields.
No password belongs in the public settings, argv, environment, or status.

An enabled settings file has these exact fields; paths and subnet below are
examples, not installed resources:

```json
{
  "schema_version": 1,
  "enabled": true,
  "approval_file": "/etc/raspi-cloud-sync-pro/SMB_CAPTURE_APPROVED",
  "device_policy": "/etc/raspi-cloud-sync-pro/device-policy.json",
  "share_name": "CloudSync",
  "share_root": "/srv/raspi-cloud-sync-pro/share",
  "staging_directory": "/var/lib/raspi-cloud-sync-pro/smb-staging",
  "state_directory": "/var/lib/raspi-cloud-sync-pro/smb-capture",
  "credentials_file": "/var/lib/raspi-cloud-sync-pro/smb-capture/smb.json",
  "usb_address": "172.19.80.1/28",
  "usb_profile_uuid": "01234567-89ab-cdef-0123-456789abcdef",
  "interval": 5,
  "max_file_bytes": 104857600,
  "capture_timeout": 300
}
```

The separate approval is exactly
`{"schema_version":1,"smb_capture_approved":true}`. A disabled settings file
needs only `schema_version:1` and `enabled:false`. The approved USB address must
be an RFC1918 host address with prefix /24 through /30. The runtime verifies
Raspberry Pi/Linux identity, ordinary backup UID with zero effective/permitted/
inheritable/ambient/bounding capabilities, `NoNewPrivs=1`, and `UMask=0077`. It checks
`g_ether` without conflicting gadgets, an inactive and disabled/masked/absent
ICS auto-switcher, the exact USB NetworkManager profile and manual address, no
USB gateway/DNS, and no overlapping Wi-Fi subnet. None of these checks mutates
the host. Wi-Fi connectivity is unnecessary for local capture.
This runtime supports the `g_ether` configuration only; a configfs Ethernet
gadget is refused and requires separate qualification.

The approved share must be an ext4 directory on a writable non-loop filesystem,
0750, owned by a distinct ordinary writer identity with the capture process's
group. Its parent and ancestors are root protected. Staging and state are
separate existing 0700 directories owned by the capture identity. A future
capture unit may select the share's read group for that unit only; adding a
permanent supplementary group to the Google worker identity is unnecessary.
Samba must separately enforce the read-only backup account, encrypted SMB3,
writer-only modification, no symlinks/wide links, and USB-only client access.
Local writers bypassing Samba are outside this contract. This API does not
validate or deploy the Samba server configuration.

Each bounded inventory supplies candidate metadata only. The existing
`LocalSambaReader` opens the selected relative path through loopback SMB with
encryption and `FILE_SHARE_READ`, denying concurrent write/delete opens. The
specific authority and kernel/filesystem checks run again before each open.
`stage_smb_version` streams and hashes the bytes, fsyncs payload, manifest and
directories, and publishes `.ready` only after successful SMB CLOSE. An open
writer, revoked approval, disconnect or failed close defers the file; partial
data stays `.incomplete`. A published file version is a snapshot, not an atomic
snapshot of an entire folder.

A private SQLite index skips unchanged captured versions across restarts. It
independently validates a remembered batch on each runtime start. Changed
source versions produce new batches; damaged older batches remain retained and
may cause recapture. A crash after publication but before index commit can
leave an extra valid batch and cause a duplicate capture on retry. Originals,
older versions, partials and queue entries are never automatically deleted.
Concurrent capture processes cannot hold the same index lock.
A durable scan cursor rotates the bounded candidate list after every attempt,
so busy or oversized files cannot indefinitely hide later completed files,
including across runtime restarts.

Work is limited to 4,096 inventory entries, depth eight, 32 captures per scan,
and a configured file limit up to 16 GiB. The default file limit is 100 MiB;
larger configured limits are not a claim of live acceptance. The transfer
budget is checked around SMB reads/seeks; an in-flight SMB operation remains
subject to the existing SMB client's timeout. Fixed read-only host probes have
their own three-second and 8 KiB output bounds. No hard wall-clock bound is
claimed for kernel filesystem operations.

The public result contains counts and `capture_state`, with `uploads_enabled`,
`cloud_accounts_verified` and `automatic_deletion` always false. It contains no
paths, usernames or raw errors. These batches can feed a separately authorized
backup worker under the backup UID. Google Test/Enable, per-run Wi-Fi
confinement, cloud admission, dashboard listing/downloads, Windows Explorer
behavior, live USB sharing and production activation remain separate work.
The earlier Pi acceptance covered an isolated encrypted loopback Samba fixture;
this new runtime has local tests, not live USB or Windows acceptance.
