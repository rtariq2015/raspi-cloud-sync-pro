# Product plan and research decisions

Updated 2026-11-13. The product is a small Pi-owned file and backup appliance with
one responsive dashboard for desktop, Android and other phone browsers. The
Android delivery starts as a Progressive Web App (PWA), installed from the browser,
rather than requiring a separate Play Store app or VPN client.

## The everyday experience

Connect the Pi with a data-capable USB cable. In the preferred Ethernet/Samba mode,
the computer writes files through authenticated SMB while Linux owns the storage.
The same cable can supply power within the measured host/cable budget. Keep
working while snapshots and cloud jobs run independently. When Internet access
is unavailable, local file access should continue and uploads wait.

Open the dashboard to browse files, preview supported content, download files,
inspect each destination's backup history and see whether network sign-in is
needed. An owner configures the appliance; viewers can browse/download without
changing networks, destinations or source files. Uploaded, staged and remotely
verified are separate states. No automatic original-file deletion is planned.

## Delivery status

| Capability | Current evidence | Next acceptance |
| --- | --- | --- |
| Dashboard and phone layout | Authenticated Flask/Waitress implementation, browser checks, Pi preview | Physical Android PWA installation and HTTPS deployment |
| Owner/viewer access | Hashed passwords, expiring/revocable sessions, CSRF, host restrictions and role tests | Account recovery/invitations and public-deployment review |
| Web uploads/downloads | Pi-tested QA uploads; streamed hashes, retained incomplete batches and successful byte/SHA-256 download checks | Quotas, larger-file/resumable uploads, folder operations |
| Text previews | Bounded 64 KiB text response, rendered as text | PDF/image previews on an isolated content origin |
| USB Ethernet + Samba | Driver/templates/ingestion prototypes and simulated writer locks | Actual authenticated SMB share/lease/deny-write tests |
| Classic mass storage | Exclusive-ownership controller, simulated recovery | Disposable image, clean eject, real kernel/host tests |
| Google Drive + Koofr | Versioned rclone adapters, retry/history and download verification | User account authorization and real remote checks |
| Other servers | Research: WebDAV, SFTP, S3-compatible backends | Typed adapters and enforced TLS/SSH host verification |
| Home Wi-Fi | Repaired physical Pi, five-minute and Wi-Fi-bound Internet tests | Boot and longer endurance tests |
| Enterprise Wi-Fi | PEAP/MSCHAPv2 and EAP-TLS validation | Actual NetworkManager provisioning with approved CA/domain and network access |
| Captive portals | Detection policy/design; uploads pause on portal state | Pi-associated browser login on representative networks |
| Website from elsewhere | Chosen outbound HTTPS tunnel design; no Tailscale required | Tunnel/domain identity, access policy and off-LAN test |
| Another Pi | Python wheel and virtual-environment workflow | Repeatable installer/preflight, service isolation and a second-device trial |

## Website and Android architecture

Keep the app on the Pi at loopback behind a reviewed HTTPS proxy/tunnel. No router
port forwarding or public SSH/Samba listener is needed for the website. A named
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)
and [browser-based Access policy](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)
can give an owner and manager a normal HTTPS URL with browser authentication.
The existing Raspberry Pi Connect shell remains a management/recovery option;
it is not the file dashboard or a replacement for app authorization.

Cloudflare [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
can exercise a temporary development URL without an account or domain. They have
no uptime guarantee and change hostname when restarted. A stable deployment needs
a named tunnel/hostname and an approved access policy. Do not publish unauthenticated
application routes or rely on a hard-to-guess URL as access control.

The PWA shares the same authenticated APIs and UI as desktop. HTTPS is required
for normal remote installation. Its service worker deliberately does not cache
private pages, files or API results. An offline phone is not presented with a
stale “verified” dashboard. A native Android APK, push notifications, background
phone uploads and offline files are separate later features, not delivered by
adding an app icon.

The lightweight server uses [Flask's security controls](https://flask.palletsprojects.com/en/stable/web-security/)
and [Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/) with bounded
threads/connections; there is no development server in the deployment entry point.
Dashboard and backup workers should have separate restricted Unix identities.

## Networks shown in the supplied screenshots

The enterprise screenshots show PEAP with MSCHAPv2, an identity/anonymous identity,
and a server-certificate trust prompt. The separate Android pairing screen is
wireless debugging, not Wi-Fi authentication. No passwords, personal identities,
network names, pairing codes or certificate screenshots are copied into Git.

For enterprise setup, use the administrator-approved CA and authentication-server
domain. NetworkManager provides explicit [802.1X trust/domain settings](https://networkmanager.dev/docs/api/latest/settings-802-1x.html).
Do not silently copy “Trust on First Use” into the Pi, accept arbitrary certificates,
or infer certificate trust from a screenshot. Credentials stay in protected OS
storage and are entered privately. The user's approved networks also need real
on-site acceptance; a home Wi-Fi test cannot establish enterprise compatibility.

For a hotel/guest network, distinguish association, DHCP, portal sign-in, Internet
and destination reachability. NetworkManager's observed portal/limited state and
the [CAPPORT API](https://www.rfc-editor.org/rfc/rfc8908.html) can inform guidance,
but not all portals implement the API. A phone accepting terms often authorizes
the phone rather than the Pi. A supported flow must run the authorized interaction
for the Pi's session, preserve USB access and expire portal cookies. Do not
automatically accept terms, bypass network restrictions, weaken TLS checks or
claim arbitrary JavaScript/CAPTCHA/MFA portals can be automated on this 512 MiB Pi.

## Backup engines and sequencing

Use [rclone Google Drive](https://rclone.org/drive/) and
[WebDAV](https://rclone.org/webdav/) with private OAuth/app-password configuration.
Maintain independent jobs per destination and file content version. Preserve
remote versions and originals. Upload exit success is followed by remote-content
read-back hashing. Network/portal/metered policy is checked before spending an
attempt. Retries and power interruption cannot turn an incomplete job into verified.

[SFTP](https://rclone.org/sftp/) needs a known-hosts policy, and
[S3-compatible storage](https://rclone.org/s3/) needs approved endpoints, credentials
and TLS. Neither should be exposed as a generic unvalidated “any command” field.
Client-side encryption, quotas, scheduled audits, notifications and explicit
restore jobs are useful later additions after the first two backends pass real
account tests. No destination is called supported merely because rclone lists it.

Work order: finish the authenticated Pi dashboard and web-file acceptance; add a
bounded staged-file scheduler; validate USB/Samba ingestion; complete guarded Wi-Fi
setup/portal guidance; verify cloud accounts; test browser-only remote access;
then validate the documented install and rollback on a second Pi. Each tested
increment is committed to development. A release or main-branch merge needs owner
approval. Administrator authentication, cloud/tunnel account sign-in, enterprise
CA details and second-device hardware cannot be invented by the software.
