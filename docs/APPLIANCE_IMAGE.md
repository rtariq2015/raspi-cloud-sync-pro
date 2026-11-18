# Ready-to-flash appliance image and in-device updates

The intended end-user product is a customized official Raspberry Pi OS Lite
image for the **Zero 2 W / aarch64**, with Raspi Cloud Sync Pro and its reviewed
dependencies already installed. This is an appliance configuration of the
supported OS, not a new kernel or an independent Linux distribution.

It is a planned delivery stage. No image has been built, released or flashed,
and the current working card must not be replaced by this work.

## Build choice and provenance

Evaluate Raspberry Pi's official
[rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen) for a declarative
Trixie ARM64 appliance build; source review at
`fb44a4cde4ac54847ef1966957149e2c2b716fdf`. Its README provides a Trixie minbase
configuration, supports custom layers/hooks and documents a native ARM64 Debian
build environment. The build needs filesystem namespace capabilities. This is
not an instruction to run its dependency installer, privileged build or flashing
commands on the user's laptop/current Pi.

[pi-gen](https://github.com/RPi-Distro/pi-gen) remains the official Raspberry Pi
OS distribution builder and an alternative baseline. Pin/review the chosen
builder, package repositories, kernel/firmware and exact project artifacts. Build
on a dedicated disposable ARM64 build worker, not on a valuable card or an
unreviewed raw disk. Record the software bill of materials, license notices,
source commit, artifact hashes and hardware acceptance results.

## What the finished image should prepare

- The lightweight Rust staged-file scheduler, Python web/control components,
  rclone, NetworkManager and the reviewed official USB Ethernet provider.
- Separate restricted service identities and private state/credential paths.
- One reviewed USB mode, secure file sharing and a USB-local management path.
- Dashboard/PWA assets, setup/recovery workflow and a trusted application-update
  verification key.
- No personal Wi-Fi profiles, enterprise certificates, cloud tokens, tunnel
  account identity, reused SSH host keys, shared signing secrets or universal
  administrator password.

First boot must create unique device identity/host keys and let the owner claim
the device, choose credentials and supply their own network/cloud settings.
Image customization cannot know those settings in advance. Claim/bootstrap
material needs a private per-device delivery method and expires after claiming.
A network-only USB device may still need a Windows RNDIS driver or administrator
approval on managed computers; an image cannot remove that host restriction.

## Updates from the dashboard

Application updates and OS/firmware updates are different operations:

1. An owner checks an approved channel for signed application metadata. The
   updater verifies the trusted signature, version/architecture, size and SHA-256;
   a checksum from an untrusted manifest alone is insufficient.
2. Stage into a new root-owned version directory. Keep user data, credentials
   and queue state outside application versions. Never accept arbitrary URLs,
   shell commands or writable filesystem destinations from a web request.
3. Drain active file work, take the required database/state backup, apply only
   compatible migrations, then switch versions atomically.
4. Require a health check; restore the previous version on failure. Keep rollback
   artifacts until the new version is accepted. Do not delete original files.

The unprivileged web process requests a narrow updater operation; it must not
become a root command console. Signing keys belong in protected release tooling,
not the image or repository. Release publishing still requires owner approval.

OS/kernel/firmware upgrades need a tested maintenance path because they can
change Wi-Fi, gadget behavior and the meaning of driver feature masks. The known
SAE workaround must be reviewed across kernel series. Do not run blanket
unattended upgrades from the dashboard and call that safe rollback. Whole-system
A/B updates using a framework such as [RAUC](https://rauc.readthedocs.io/en/latest/)
need an explicitly designed boot/data layout and power-loss/bootloader tests;
they are not achieved by installing an updater package on the current card.

## Acceptance before distributing an image

Complete the file/backup/network acceptance matrix first. Then test the generated
image on a second disposable-data Pi: first boot without Wi-Fi, account claiming,
USB access, Android/desktop login, real cloud verification, normal restart,
offline operation, low storage and failed/interrupted update rollback. Confirm
new devices do not share credentials or host identity. No automatic release,
flashing operation or successful second-device result is claimed by this plan.
