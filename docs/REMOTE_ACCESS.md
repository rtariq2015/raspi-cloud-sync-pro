# Remote dashboard access

The dashboard is hosted on the Raspberry Pi. An outbound HTTPS tunnel makes its
login page reachable from another network without router port forwarding or a
VPN app on the visitor's phone. The Pi must have power and a working Internet
connection. The tunnel does not upload your files to Google Drive; backups use
a separate destination and verification workflow.

## Temporary and fixed addresses

Cloudflare Quick Tunnels are useful for testing. Their hostname lasts for that
tunnel process; restarting it creates a different hostname. Cloudflare error
1033 means no healthy tunnel connector is available. Check the Pi's power,
Internet connection, dashboard and connector rather than reflashing its card.

A free ngrok account supplies one assigned `your-assigned-name.ngrok-free.dev`
address that remains the same after agent restarts. Its assigned name cannot be
customized. Bookmark it or add the dashboard to your phone's home screen. The
free service has usage limits and may show a provider warning page before the
dashboard. These are provider behavior, not a replacement for dashboard login.

The current documented free limits are 20,000 HTTP requests and 1 GB of outgoing
data per month. Large remote downloads consume that allowance. Check the account
dashboard before relying on the free plan for regular production use. Local
file storage and the cloud backup engine are separate from this tunnel allowance.

Sources: [ngrok domains](https://ngrok.com/docs/gateway/domains),
[ngrok limits](https://ngrok.com/docs/pricing-limits),
[Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
and [error 1033](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/).

## Appliance deployment requirements

Keep the dashboard bound to loopback, running as its restricted service identity,
with the exact reviewed public HTTPS origin. Keep the connector separate, with
no capabilities, protected system/home mounts and only its private credential
file available through systemd credentials. Disable traffic inspection and avoid
logging tokens, request bodies or file contents. Store credentials outside the
repository; never paste them into an issue, command argument or public guide.

A transient test service disappears after reboot. An appliance needs reviewed
persistent unit files, bounded restart behavior, backups and rollback, then
service-restart and real reboot acceptance. A stable hostname alone does not
provide service availability or prove that startup after reboot works. Do not
restart NetworkManager or change USB settings to repair a dashboard process.

When changing the public hostname, back up configuration and register the new
exact OAuth callback with the cloud provider before enabling account setup.
Previous origin-bound preparation records must not be silently relabeled as
current. Preserve account passwords, grants, prepared files and uploaded files;
request fresh owner decisions when the source binding changes.

See [dashboard](DASHBOARD.md), [current evidence](STATUS.md),
[private Google setup](GOOGLE_HANDOFF.md) and [replication](REPLICATION.md).
Device addresses, account tokens and private deployment records are intentionally
absent from this guide.
