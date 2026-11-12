# Wi-Fi management design

Phase 1 implements `WifiProfile.validate()` and connectivity/metered upload
policy only. It does not scan, store credentials, change the computer's network,
or claim real Pi network support. OS integration is a future adapter.

## Network types and support boundaries

| Network | Planned handling | Phase 1 validation/policy |
| --- | --- | --- |
| Home WPA2-Personal | Saved profiles, hidden SSIDs, priorities, reconnect and checked activation | SSID byte limits, password references, hidden/priority fields |
| Phone hotspot | User-selectable metered profile, reconnect, pause/resume without spending retry attempts | Metered links blocked until explicitly allowed |
| WPA2-Enterprise PEAP | Identity, CA trust, explicit server-domain constraint, PEAP with MSCHAPv2 and secret-store password | Missing trust/domain/identity or unsupported phase 2 rejected |
| WPA2-Enterprise EAP-TLS | CA/server validation, client certificate and private key in restricted OS storage | Required certificate paths/key reference checked; no plaintext key field |
| Open/captive portal | Detect portal/limited connectivity; guide manual authorized sign-in where supported | Never infer cloud access from association; uploads paused |

Zero 2 W has a 2.4 GHz radio, so hotspot setup must offer a compatible band;
5 GHz-only hotspots are unsuitable. See the
[official specification](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/).
AP/client coexistence, regional configuration and WPA3-only networks must not be
assumed to work. Validate the chosen image, firmware and access points on hardware.

## Proposed NetworkManager boundary

Use the selected Raspberry Pi OS network stack, checking it rather than installing
a competing network manager. Raspberry Pi documents
[NetworkManager-based network configuration](https://www.raspberrypi.com/documentation/computers/configuration.html).
A restricted service should expose scan, list saved profiles, test/activate,
set priority/metered flag and explicitly remove profile actions. The dashboard
must not run shell commands or rewrite OS connection files directly.

Test a new connection with a timed NetworkManager checkpoint and preserve the
previous working profile. Confirm address, DNS, route, observed internet/portal
state and management reachability; roll back on timeout or failed confirmation.
Unrelated saved profiles must remain intact. Keep a documented serial/SSH or
other management recovery path; USB mass storage alone does not provide network
management. Hotspot/AP fallback is conditional on verified radio/driver support.

## Enterprise security

Map validated profiles to NetworkManager's
[802.1X settings](https://networkmanager.dev/docs/api/latest/settings-802-1x.html).
CA verification and server-domain matching are required; do not offer a bypass
when authentication fails. Admin-provided CA/client certificates, private key
permissions, certificate expiry, clock synchronization and EAP method support
need real OS checks. Phase 1 validates structure, not certificate validity or
credential correctness. Certificate/key references do not grant access by themselves.
Other EAP combinations, university onboarding and device-enrolment workflows
require separate implementation/acceptance; there is no universal enterprise preset.

## Captive portals

NetworkManager can report portal or limited connectivity when its connectivity
checks are configured; see its
[connectivity configuration](https://networkmanager.dev/docs/api/latest/NetworkManager.conf.html#id-1.2.3.13).
Check availability and privacy implications on the selected image. A portal
status is not an authentication mechanism or permission to bypass access controls.

Show that sign-in is needed, pause cloud attempts, and explain supported manual
steps. A phone logging into the same SSID often authenticates that phone, not the
Pi. Any future companion browser flow must authenticate the Pi's connection and
handle redirects without sending portal credentials through the backup service.
Portals needing device enrolment, browser-only challenges or unsupported flows
may require a different network or hotspot. Do not automate acceptance, bypass
the portal, weaken cloud TLS checks or retain portal passwords/cookies in Git.

## Secrets, retries and acceptance

Store passwords, OAuth tokens and private keys in restricted OS credential storage.
Profiles contain references only. Logs/history/diagnostics must omit raw values.
The engine defaults to unknown connectivity and pauses without claiming a job;
online state must be supplied by a future observed connectivity adapter. Online
does not guarantee a cloud destination works: provider failures still enter the
bounded retry policy.

Test home reconnection, hotspot disappearance, metered opt-in, incorrect enterprise
credentials, untrusted/expired server certificates, failed activation rollback,
DNS outages, portal login/expiry and management recovery. None has yet been checked
on a physical Zero 2 W for this project.
