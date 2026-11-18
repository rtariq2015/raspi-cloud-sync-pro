# Isolated Samba protocol acceptance fixture

This optional test uses an **actual Samba server** and the project's SMB staging
function in a disposable Linux container. It is separate from unit simulations
and is not Raspberry Pi, Windows, USB, electrical or long-duration acceptance.
It does not enable or bypass any committed hardware-operation gate.

## Run

Docker must already be available. Build from this directory's Dockerfile and run
from the repository root:

```sh
docker build -t raspi-cloud-sync-samba-fixture:trixie tools/acceptance/samba
docker run --rm --network none --pids-limit 128 --memory 256m \
  --security-opt no-new-privileges \
  --mount type=bind,src="$PWD",dst=/work,readonly \
  raspi-cloud-sync-samba-fixture:trixie
```

Do not add `--privileged`, host networking, published ports, device access,
sensitive bind mounts or Docker socket access. The helper refuses to run unless
it is root inside a Docker container with **only loopback** visible. The image
contains Debian Trixie Samba packages and an isolated Python environment with
smbprotocol 1.15.0; it installs nothing on the development host or Pi. The base
image digest is pinned; APT packages and transitive Python dependencies still
track their configured stable package repositories. Record exact versions for
any acceptance run rather than treating this as a fully locked appliance image.

The fixture creates its own Unix identities, temporary passwords, private Samba
passdb/state and disposable share **inside the container**. Credentials are kept
in memory and passed to smbpasswd on stdin, never printed or placed in arguments.
Only fixture users authenticate; no existing account password is used. Samba
listens on `127.0.0.1:1445` in the container's disconnected network namespace.
There is no host listener or live filesystem export.

The final JSON is safe to save as a result record. To inspect a failure, omit
`--rm` and choose a unique container name. Retain the stopped container privately
and inspect only its fixture server log; do not publish its passdb/config/state.
Removing a fixture container removes only that disposable environment, never
source files mounted read-only from this project.

## What it proves

- The negotiated session is encrypted SMB3 or newer.
- A conflicting open write handle prevents staging before a batch is created.
- While staging holds `FILE_SHARE_READ` only, Samba rejects a new write open and
  delete request with `STATUS_SHARING_VIOLATION`.
- The read-only Samba identity cannot write, with `STATUS_ACCESS_DENIED`.
- After writer close, staged bytes and SHA-256 match the known fixture payload.
- An injected client read error retains an incomplete batch without publishing a
  new ready batch. The original remains readable.
- Editing after close produces a new snapshot without changing the prior one.

Expected errors are checked by SMB status code; an unrelated connection/auth
failure cannot count as successful conflict prevention. The test uses a separate
loopback/high-port reader with the same share-access contract as the production
adapter. It calls `stage_smb_version` directly; `LocalSambaReader` remains gated
and the production device/share runtimes remain disabled.

## Recorded result — 13 November 2026

Passed on the x86-64 development host inside the isolated container:

- Samba `4.22.11-Debian-4.22.11+dfsg-0+deb13u1`.
- Python 3.13, smbprotocol 1.15.0; negotiated encrypted SMB 3.1.1 (`0x311`).
- Every conflict, content, interruption-retention and changed-version check above
  passed. No real cloud account was involved.

The first fixture startup revealed the container had no default Samba ncalrpc
runtime directory; the fixture now points that directory into its own temporary
runtime tree. This was a fixture configuration issue, not a Pi repair.

## Remaining actual-Pi trial

A high unprivileged TCP port alone does not provide Samba account provisioning
or Unix identity switching. Samba's official [smbpasswd manual](https://www.samba.org/samba/docs/current/man-html/smbpasswd.8.html)
requires root for adding local passdb accounts; a normal smbd installation uses
root to establish service/Unix-account access. Do not weaken authentication or
make the production share a guest share merely to avoid this setup.

On the verified Pi, first inspect the installed distribution binaries and existing
Samba services. A reviewed isolated server can use loopback and a high port with
its own passdb/config/runtime directory and dedicated no-login identities, without
changing gadget modes or the existing network profiles. Back up affected settings,
prevent package hooks from starting a global service, and never replace a global
Samba config blindly. A root or equivalent container setup is required for that
reviewed account/server provisioning; after setup, the client acceptance can run
unprivileged. Stop only that isolated server on rollback and retain fixture data.

Repeat this contract on the Pi's actual OS/Samba build before accepting the
service. Then separately test the intended Windows host, SMB leases/reconnects,
USB-only local access without Wi-Fi, port exposure restrictions, sustained load,
space pressure and coordinated shutdown. This container test does not establish
folder-level atomicity, arbitrary-client upload completion or safe concurrent
block-filesystem access. See [the Pi test guide](../../../docs/PI_TEST_GUIDE.md).

The official [smbd manual](https://www.samba.org/samba/docs/current/man-html/smbd.8.html)
documents alternate listener ports; Samba's
[smb.conf manual](https://www.samba.org/samba/docs/current/man-html/smb.conf.5.html)
documents interfaces, encryption, access controls and share-mode behavior.
