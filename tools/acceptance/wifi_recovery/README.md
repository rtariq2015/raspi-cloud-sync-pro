# Focused, opt-in Wi-Fi recovery trial

This operator addresses a blocked USB-to-Google test: the Pi stopped responding
over Wi-Fi and required an owner-approved power cycle. The previous boot journal
was unavailable afterward, so the cause remains **unknown**. This is a bounded
saved-profile retry, not a Wi-Fi driver repair or a general network manager.

The approved native trial now passes: Windows copied and safely ejected a 1 MiB
file while Wi-Fi was disconnected; it remained queued locally with zero upload
attempts. The recovery operator reactivated the approved saved profile, and the
backup worker uploaded and downloaded-hash verified the file. The independent
reconnect failsafe was unused, the USB/core processes stayed running, and 443
seconds of subsequent observations stayed healthy. The tested timer is enabled
on that Pi only; another reboot of the timer was not performed.
[Evidence and exact rollback](../../../docs/acceptance/pi-wifi-recovery-guard-dev22-2026-11-14.json).

The public `APPROVED_TEST_BUILD = False` gate is checked before configuration or
hardware probes. The example configuration is also disabled. Neither systemd
example is installed or enabled by the repository. There is no environment or
command-line override. A separately reviewed, root-protected lab copy and a
root-owned mode `0600` `/etc/raspi-cloud-sync-pro/wifi-recovery.json` are required
for an approved Pi trial. Use the actual saved profile UUID privately; the example
contains a placeholder and no credentials.

The operator accepts only Raspberry Pi Zero 2 W with its existing root filesystem
on `mmcblk0p2`, the `wlan0` interface, three failed samples and a five-minute
cooldown. It reads only selected NetworkManager fields and rfkill status. An
eligible failure is either:

- A managed, radio-enabled, unblocked `wlan0` that is disconnected, with the
  approved saved wireless profile still available.
- The approved profile is connected with an IPv4 address/gateway, but a
  Wi-Fi-bound gateway ping fails **and** that gateway's neighbour state is
  `FAILED`. The four-second ping deadline allows the usual three ARP probes;
  `INCOMPLETE`, unavailable observations and tool/permission errors remain unknown.

Another active profile, connecting/transitional states, radio-off, rfkill,
unmanaged or unknown state never triggers activation. DNS, ISP or captive-portal
failure alone never triggers recovery. A reachable/stale gateway neighbour with
no ICMP reply is insufficient evidence. No public Internet probe is used.

After three consecutive eligible samples, the operator checks again and durably
records the cooldown **before** running its only network mutation:

```text
nmcli --wait 25 connection up uuid APPROVED_UUID ifname wlan0
```

It never disconnects interfaces explicitly, enables radios, restarts
NetworkManager, reloads drivers, changes USB settings, alters passwords or reboots.
The private state uses atomic replacement and fsync, with no-follow, ownership,
mode and schema checks. Ambiguous/corrupt state refuses. Counters reset across
boots; unchanged healthy state does not cause repeated SD writes. Output contains
fixed status codes and counters, never raw provider output or passwords.

This is an explicit **always-on approved-profile** policy. Before maintenance,
stop both the timer and any active oneshot, confirm they are inactive, then make
intentional Wi-Fi changes. Turning the radio off is also a refusal condition.
For rollback, stop/disable the owned timer and restore the backed-up disabled
configuration/unit files. Keep state and diagnostic evidence; unrelated profiles
and USB services need no rollback because this operator does not modify them.

Before native installation, back up affected settings and verify source hashes,
configuration ownership, unit syntax and the actual saved profile. The example
service has no capabilities and uses `NoNewPrivileges` and a read-only system;
only its state directory is writable. Verify gateway ping in that **exact service
context**: systems with restrictive ping socket permissions may report unknown.
The root-device check uses read-only sysfs identity so no block-device access is
needed with `PrivateDevices=yes`.

Then perform one controlled Wi-Fi-only disconnect with an independently scheduled
approved-profile reconnect failsafe. Observe the recovery timer itself reconnect,
retain a queued USB file, and verify its Google backup while USB stays available.
Do not claim recovery based on a successful `nmcli` exit alone. Persistent driver,
power or hardware faults may remain unrecoverable; no automatic power-cycle or
radio-reset fallback is provided.

Local simulated checks, with no hardware/network operations:

```sh
python3 -W error::ResourceWarning -m unittest discover \
  -s tools/acceptance/wifi_recovery -p 'test_*.py' -v
```

NetworkManager documents its [observation and activation commands](https://networkmanager.dev/docs/api/latest/nmcli)
and [device field examples](https://www.networkmanager.dev/docs/api/1.42.4/nmcli-examples.html).
Linux documents the usual [three multicast neighbour probes](https://docs.kernel.org/networking/ip-sysctl.html#mcast-solicit-integer).
