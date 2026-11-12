# Official Raspberry Pi Ethernet gadget review

Reviewed <https://github.com/raspberrypi/rpi-usb-gadget> at commit
`05ff0940af823868ff995808286bc0bcca8b86b7`, default branch `pios/trixie`.
Source was cloned read-only under an ignored development tool directory. No
installer, package hooks, driver installer or build script was executed.

## Implementation findings

- The [control script](https://github.com/raspberrypi/rpi-usb-gadget/blob/05ff0940af823868ff995808286bc0bcca8b86b7/rpi-usb-gadget)
  configures `dwc2,dr_mode=peripheral`, a `g_ether` modules-load entry and two
  NetworkManager profiles on `usb0`. Its `on` command also starts its ICS watcher.
  It is an Ethernet implementation; it does not implement mass-storage ownership
  or safe file backup.
- [g_ether options](https://github.com/raspberrypi/rpi-usb-gadget/blob/05ff0940af823868ff995808286bc0bcca8b86b7/g_ether.conf)
  identify the official device with VID `2E8A`, PID `0013`. The kernel gadget
  supplies RNDIS/CDC-ECM; target-host binding and Pi kernel configuration still
  need physical checks. This phase does not substitute an untested NCM profile.
- The shared NM profile serves DHCP/NAT on `10.12.194.1/28`. The client profile
  accepts DHCP/default routing from a host sharing Internet. The
  [libnm/GLib watcher](https://github.com/raspberrypi/rpi-usb-gadget/blob/05ff0940af823868ff995808286bc0bcca8b86b7/ics-watch.c)
  probes host gateways with ARP and can deactivate one profile and activate the
  other. Profile changes can interrupt an active SMB transfer.
- The [package metadata/hooks](https://github.com/raspberrypi/rpi-usb-gadget/tree/05ff0940af823868ff995808286bc0bcca8b86b7/debian)
  target Raspberry Pi OS packaging, depend on NetworkManager/arping/systemd, and
  perform udev/serial configuration during installation. A package install is
  not a read-only evaluation; it belongs only to an approved Pi provisioning step.

## Integration choice

Use the official package externally for the Ethernet gadget and preserve its
identity/driver pairing. Do not vendor its root installer or start both it and
our mass-storage controller. Do not unload a gadget driver during active I/O.

For stable file sharing, the proposed default replaces automatic client/shared
switching with a dedicated local-only NetworkManager profile and USB-bound DHCP.
The Pi keeps `10.12.194.1/28`; DHCP clients receive `.2`–`.14`, with no router/DNS
advertisement. Neither the Windows default route nor Internet sharing needs to
change. `deploy/` contains independently authored reference configurations.
They are not installed automatically.

Before serving files, disable/mask the official ICS watcher and disable its
profiles' autoconnect on the approved Pi; then activate only the reviewed local
profile. Do not run official `on` again during use, because it recreates/re-enables
its profile policy. Opt-in host ICS is a separate managed policy and requires
different reviewed configuration; this phase's fixed-address service intentionally
rejects that runtime configuration rather than switching addresses mid-transfer.

## Windows 11

The [official Windows documentation and INF](https://github.com/raspberrypi/rpi-usb-gadget/tree/05ff0940af823868ff995808286bc0bcca8b86b7/windows)
describe metadata binding to Microsoft's inbox RNDIS components for the official
VID/PID, an administrator-installed signed catalog, and untested ARM64 support.
An INF alone is not a trusted signed installation package. Use an official signed
distribution and verify publisher/trust with the administrator. Do not disable
Secure Boot, enable test signing or bypass device-installation policy.

Microsoft now [recommends USB NCM for new hardware](https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/supported-usb-classes).
That is a future compatibility option, not evidence that this official RNDIS/ECM
provider automatically works as NCM. CDC-ECM is useful for Linux/macOS; do not
promise driverless ECM on Windows 11.

Corporate policy may block USB NICs, RNDIS, driver installation, DHCP, SMB/445,
workgroup credentials/NTLM or drive mapping. A firewall/VPN may overlap the USB
subnet or filter local traffic. Obtain IT approval or use an approved host/mode;
the project cannot bypass these controls. Guest SMB/SMB1 and policy weakening are
not workarounds. Automatic network discovery is unnecessary: after approved
driver binding and DHCP, map `\\10.12.194.1\CloudSync` with named credentials.

## Power and offline operation

Use the Zero 2 W USB data/OTG micro-USB port, not PWR IN, and a data-capable cable.
The same connection can carry data and supply power. It cannot promise sufficient
power from every port, hub, cable, sleeping host or managed docking station.
Measure the exact configuration; a descriptor or healthy firmware flag is not a
current/voltage budget measurement. Host suspend or power-off may stop backups.
Do not improvise dual-power/back-power wiring to overcome this limitation.

USB Ethernet and local SMB do not require Wi-Fi, DNS or host ICS. Cloud uploading
requires a separately approved working Internet route. The Pi queues snapshots
locally during outages and retains originals.

## Attribution and licenses

Copyright © 2025 Raspberry Pi Ltd.; official source is Apache-2.0. Its license is
preserved in `licenses/rpi-usb-gadget-Apache-2.0.txt`; the provider remains an
external dependency. Its Windows metadata does not grant rights to redistribute
Microsoft driver binaries. Raspi Cloud Sync Pro retains Koseng's upstream credits
and unchanged GPL-3.0 license. See [third-party notices](../THIRD_PARTY_NOTICES.md).
