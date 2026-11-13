# Phase 2 validation record

Date: 2026-11-12. Linux x86-64 development environment, Python 3.12.3.
Package `0.1.0.dev2`; target branch `development`. Hardware gate remains false.

| Check | Result and limits |
| --- | --- |
| Automated suite | 111 tests pass locally, including all 62 Phase 1 tests and real rclone against a temporary local alias |
| Controller simulations | Session-bound eject, quiet host, link loss, startup-empty LUN refusal, dirty filesystem, power/profile/RO faults, detach/mount/unmount/reattach failures, interruption, resource recovery and controller contention |
| Linux adapter | Production gates before mutations/config reads, detached-profile creation, driver/composite rejection, image identity, partition/device numbers, RO flags, non-repairing fsck command and no force-eject/unmount |
| Samba ingestion | Active writer deferral, deny-write/delete snapshot contract, successful close before publication, interruption/short read/low space, queue reconciliation, changed versions, Wi-Fi/cloud outage and approved/metered policy |
| Official provider | Pinned source reviewed; offline adapter commands tested only with a fake executor; no upstream installer executed |
| Network and templates | NM/IP/DHCP/Samba policies structurally tested; real dnsmasq syntax parser passes without opening a server |
| systemd | Static-unit syntax passes on temporary copies with executable substitutions; no unit installation, start, enable or restart |
| CLI | Status, device mode plan, read-only diagnostics and disabled controller/share/device gates checked |
| Packaging/provenance | Wheel build/install, current core and GPL/Apache notices; original README/LICENSE exact upstream bytes, ancestry retained |
| Documentation/Git | Current relative links resolve; Python compilation, shell parsing and staged whitespace checks pass |

The official provider was reviewed at
`05ff0940af823868ff995808286bc0bcca8b86b7`. Its Apache license text is preserved,
but no provider installer/watcher/driver binary is vendored. Cloud Stick Pro was
not modified. Root `LICENSE` and `README.upstream.md` remain unchanged.

Rclone 1.76.0 integration uses only a temporary local alias remote, not a cloud
account. SMB tests use simulated server handles and a mocked client API, not an
actual Samba/Windows connection. `testparm` is unavailable on the development
machine; full Samba parameter/runtime validation belongs to the approved Pi.
NetworkManager profiles and firewall rules were not loaded. The systemd parser
check does not establish installed paths, account permissions or device readiness.

No real USB image was mounted, no block device formatted, no Pi flashed, no USB
or network settings changed, no service enabled, and no installation script run.
Read-only diagnostics correctly report this development machine is not a ready Pi.
The firmware power API is unavailable here; voltage/current adequacy is unknown.

GitHub Actions covers Python 3.9, 3.12 and 3.13, with the local rclone integration
enabled on 3.12. Local execution used 3.12; remote results belong to the associated
commit's workflow run. Optional SMB deployment requires Python 3.10 or newer.

Actual host-eject/kernel-unbind behavior, electrical stability, Windows RNDIS
binding, DHCP, authenticated SMB3, lease/share-mode enforcement, ext4/SD durability,
enterprise/managed host restrictions and real cloud accounts remain unverified.
The [first Pi trial guide](PI_TEST_GUIDE.md) defines the separate approval and
acceptance steps. Simulations are not evidence of safe concurrent block access.

## Connectivity follow-up — 2026-11-13

Added `network-status`, a read-only CLI inventory of selected non-secret USB/Wi-Fi
device fields, IPv4 default routes and the official USB auto-switcher. Six new
regression tests cover existing USB client addressing, route priority/ties,
malformed/multipath routes, missing tools, timeouts, unknown observations and CLI
output without hardware authorization. All 117 tests run successfully locally;
the optional real-rclone integration is skipped because rclone is unavailable.
No cloud-account or real Samba test is claimed.

Executed the new diagnostic function in memory on the verified Zero 2 W through
the existing management session, without installing or enabling the project. It
correctly observed connected USB and Wi-Fi interfaces, USB as the preferred IPv4
route, the active official profile auto-switcher, and the mismatch with the fixed
USB-local share configuration. The command changed no network/device settings.
Also exercised the CLI on the development laptop; missing target interfaces and
an unavailable provider service remained unknown rather than ready.

The separate manual OS connectivity repair and five-minute Wi-Fi evidence are
recorded in [PI_CONNECTIVITY.md](PI_CONNECTIVITY.md). They update the management
baseline, not the earlier device/SMB/cloud acceptance limits. Reboot persistence,
authenticated Wi-Fi SSH login and project runtime deployment remain untested.
