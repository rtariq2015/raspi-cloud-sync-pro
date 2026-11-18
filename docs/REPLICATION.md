# Reproduce the dashboard preview on another Pi

This reproduces the **dashboard preview**, not a fully accepted USB/Samba/cloud
appliance. It preserves the existing OS, accounts, files, USB mode and Wi-Fi.
No disk preparation or automatic device installer is provided. Use a separately
prepared Raspberry Pi OS Lite installation with a verified management connection.

## Record the device before installing

On the target Pi record its model, OS/kernel, architecture and Python version.
Check free space and existing network/gadget/service configuration. Keep the
working management connection open. Do not copy another Pi's NetworkManager
profiles, feature-disable bitmask, MAC addresses, SSH host keys or credentials.
The known Wi-Fi correction was specific to a verified kernel/firmware combination.

Clone a reviewed development commit, then install into a fresh virtual environment:

```sh
git clone --branch development https://github.com/rtariq2015/raspi-cloud-sync-pro.git
cd raspi-cloud-sync-pro
git rev-parse HEAD
python3 -m venv .venv
.venv/bin/python -m pip install '.[dashboard]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/raspi-cloud-sync-pro network-status
```

For repeatable deployment, build a wheel from that reviewed commit, record its
SHA-256 and dependency versions, and verify that checksum before installing on
the second Pi. Python code plus web assets are packaged together. Native optional
dependencies and platform-specific firmware are not copied from another machine.

Create a new private state directory and new accounts on the new Pi, using the
[dashboard guide](DASHBOARD.md). The signing key and passwords must be unique;
there is no universal default password. Start on loopback only. Use the device's
verified SSH host key for an SSH forward; never bypass host-key verification.

## Review a system deployment separately

The inactive [dashboard unit](../deploy/systemd/raspi-cloud-dashboard.service)
specifies a dedicated no-login `raspi-dashboard` identity, private writable state,
read-only system/home restrictions and NoNewPrivileges. It has no boot-enable
section. Review the hostname, actual installed paths and permissions before
creating its approval marker or starting it. No service template is an installer.

Back up affected account/service files before adding the dedicated identity or
installing a unit. Install a reviewed wheel into a versioned root-owned directory
under `/opt/raspi-cloud-sync-pro`, with a root-managed `current` link. The dashboard
identity must not belong to sudo, disk or network-administration groups. Keep the
application state under `/var/lib/raspi-cloud-sync-pro/dashboard`, mode 0700, owned
by that identity; auth files must be mode 0600. File uploads must stay outside the
Samba source tree. A later backup identity receives narrowly reviewed access to
completed staging, never browser-supplied arbitrary paths or commands.

Keep public release code traversable/readable by its service identity: root-owned
directories normally use 0755, code/assets 0644 and executables 0755, without group
or world write permission. A restrictive installer umask can otherwise create an
unusable 0700 root-owned venv. Verify app construction as the actual service user
before replacing the working service. Normalize only verified public code, never
recursively widen the whole project prefix, private artifacts, credentials, state
or backups. Private state/configuration remains 0700/0600.

Public browser access requires the reviewed HTTPS hostname/proxy and account
policy from [PRODUCT_PLAN.md](PRODUCT_PLAN.md). Keep the backend loopback-only.
Use `--public-origin` to configure the exact hostname and Secure cookies. Test
login, owner/viewer access, downloads, logout and an off-LAN phone before claiming
remote access works. Named-tunnel account setup is separate from Python packaging.

## Acceptance and rollback

Repeat the disposable QA upload/download/hash and access-control checks on each
device. Confirm USB and Wi-Fi still work. Check memory, storage pressure, reboot
behavior and a normal shutdown before relying on unattended operation. Then
perform [the separate Samba/device tests](PI_TEST_GUIDE.md) and actual test-cloud
account verification. Do not infer these results from a copied SD card or a
successful dashboard login.

Stop only the project dashboard service to revert a preview. Keep its private
state and uploads for inspection; do not delete user files or remove a service
identity while it owns retained data. For a versioned deployment, restore the
previous reviewed `current` target while the app is stopped, then restart the app.
The dashboard installation should have no USB/Wi-Fi change to undo. A second
physical Pi and reboot/HTTPS acceptance are still required for a reproducibility
claim.
