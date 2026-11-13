# Pi connectivity baseline and next acceptance step

Recorded 2026-11-13. The existing Raspberry Pi Zero 2 W Rev 1.0 runs Raspberry Pi
OS Trixie, aarch64, kernel `6.18.60+rpt-rpi-v8`. Existing `g_ether` USB networking
provides the protected management connection. No OS reflash is needed to continue
development. No project device controller, Samba server or cloud worker was
activated by this connectivity work.

## Observed Wi-Fi result

Testing on home Wi-Fi found repeated SAE authentication timeouts on a 2.4 GHz
WPA2/WPA3 transition network, before DHCP. A private credential comparison matched
the working laptop's credential; power-saving and profile-level PMF tests did not
fix the failure. This resembles [Raspberry Pi Linux issue 7634](https://github.com/raspberrypi/linux/issues/7634).

A backed-up, Pi-specific override of the existing official brcmfmac module's
feature mask removed SAE_EXT advertising. Its bit position was verified against
the running driver's debugfs feature table. The same saved credential then
connected using WPA2-PSK/CCMP with management-frame protection negotiated. No
custom driver, firmware download, package downgrade or router change was needed.
Do not turn this into a global installer default: numeric feature masks depend
on the kernel's feature enumeration and need review before a kernel-series change.

Evidence collected after the repair:

- DHCP address, gateway and DNS assigned on wlan0; controlled Wi-Fi reconnect
  succeeded and profile/device autoconnect were enabled.
- Five-minute observation (300.3 seconds): all 21 samples remained associated;
  21/21 Wi-Fi-bound gateway pings succeeded; signal ranged from -62 to -59 dBm.
- No reconnects or relevant driver/power errors in that observation; firmware
  throttling flags were zero. This is not an electrical power-budget measurement.
- Wi-Fi-bound Internet ping, DNS query and HTTPS succeeded. The final HTTPS
  request returned HTTP 200 with normal TLS verification and wlan0's source IP.
- USB addressing, its active profile and its preferred default route remained
  intact. Power saving returned to its original setting.
- The laptop reached the Wi-Fi SSH port and received an OpenSSH banner. A new
  authenticated Wi-Fi SSH login and boot-time autoconnect were not tested.

Private addresses, MAC addresses, SSIDs, credentials and account backups are not
part of this repository's baseline record. The operator's private repair report
and root-only Pi backup directory contain the exact settings and rollback script.

## Inspect without replacing working access

```sh
PYTHONPATH=src python3 -m raspi_cloud_sync_pro network-status
```

This command only reads selected NetworkManager device fields, cached global
connectivity, IPv4 default routes and `rpi-usb-gadget-ics.service` state. It does
not scan, probe an Internet endpoint, read credentials, change profiles, restart
services or load/unload modules. Unavailable or malformed observations stay
unknown. Tied/multipath routes do not produce an asserted preferred uplink.
See the [official nmcli reference](https://networkmanager.dev/docs/api/latest/nmcli.html)
for the read-only device and cached connectivity queries.

The existing USB client link differs from the prototype share runtime's fixed
`10.12.194.1/28` USB-local configuration. The checker reports this mismatch; it
does not resolve it by changing the Pi. Likewise, a lower-metric USB default
route remains preferred even when Wi-Fi is connected. Cached global `full`
connectivity does not prove Wi-Fi egress. The worker may correctly pause under a
Wi-Fi-only policy if USB is the preferred route. Do not approve USB Internet
sharing implicitly or change routes to conceal this distinction.

## Next: disposable-data USB Ethernet/Samba acceptance

Continue with the tested Ethernet/Samba architecture: Linux owns the filesystem,
and the computer writes through authenticated SMB rather than a shared block
device. Local transfers should work without Wi-Fi; staged cloud jobs must wait
for an approved Internet connection. Mass storage remains a separate eject-based
mode.

Before enabling a file-transfer test:

1. Review and approve the disposable share, host, credentials, service identities
   and narrowly scoped test build described in [PI_TEST_GUIDE.md](PI_TEST_GUIDE.md).
2. Reconcile the existing USB client network with the runtime's fixed local-only
   policy, with a tested fallback management path. Never replace it during active
   transfers. Do not run two gadget providers or the profile auto-switcher beside
   a fixed-address share without an approved design.
3. Validate Samba configuration, access controls and server-enforced deny-write/
   delete handles using disposable files. Confirm originals remain untouched and
   stable staged versions survive a worker interruption and Wi-Fi outage.
4. Only after local acceptance, use approved test cloud accounts for Koofr WebDAV
   and Google Drive, including independent read-back hashes and offline retries.

All existing source-code gates remain disabled. This baseline establishes working
management connectivity, not a completed backup appliance or a release.


## Later development-session observation — 2026-11-13

Authenticated SSH over home Wi-Fi is now established and has been used for the
isolated dashboard/worker trials. The earlier banner-only limitation above is
historical. The target still reports the same Zero 2 W model and kernel.

During the dev4 follow-up, the existing official `rpi-usb-gadget` 1.0.6
`rpi-usb-gadget-ics.service` repeatedly logged DHCP-client attempts and GLib
`g_strjoinv` assertions. USB carrier stayed present, but its IPv4 address changed
between local fallback and no IPv4 address. A generic autoconnected Ethernet
profile was observed on usb0; this is evidence to investigate, not a proven root
cause. Home Wi-Fi remained connected and supplied the default route.

No project trial command modified profiles, routes, gadget drivers or the
watcher. Do not claim USB management works merely from carrier or an earlier IP.
Before Samba deployment, inspect the existing Ethernet profile matching and the
watcher's current state, keep authenticated Wi-Fi management open, back up
settings, and perform a separately reviewed USB-only repair with rollback.
Never restart all networking or remove Wi-Fi access to make that repair.

The operator confirmed that the Pi's data cable is connected to a Windows
computer, not the development laptop. A thirty-second passive capture on the Pi
observed four outbound USB DHCP Discover packets and no host DHCP replies.
Carrier stayed up, receive counters did not advance and Wi-Fi management stayed
connected. No packets were saved or transmitted by the observer. This narrows
the next test to Windows adapter/DHCP state and USB link behavior; it does not
establish a Windows driver, firewall, cable or Pi profile root cause. Do not enable
Internet sharing or change host security settings to hide the missing evidence.

A follow-up correlated NetworkManager's own journal with its persistent profile:
at 14:41:33 EST, policy auto-activated the generic `netplan-eth0` profile on
`usb0`. Its Netplan source has an empty interface match. The official watcher
leaves other named active profiles alone, so this generic profile can occupy the
USB interface in DHCP-client mode. This interaction is proven; it does not explain
all absent host traffic or establish a sole root cause. No profile was changed.
The next Windows observation is the RNDIS device status and that adapter's
DHCP/media/address details, without enabling sharing or changing security.
