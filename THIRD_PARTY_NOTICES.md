# Credits and external components

Raspi Cloud Sync Pro derives from **Koseng's**
[RaspiAsUSBStickWithCloudSync](https://github.com/Koseng/RaspiAsUSBStickWithCloudSync).
Its original source/history, README and GPL-3.0 license are retained. `LICENSE`
remains the project's original GPL text; new integrations do not replace it.

**Raspberry Pi Ltd.** created
[rpi-usb-gadget](https://github.com/raspberrypi/rpi-usb-gadget), reviewed at
`05ff0940af823868ff995808286bc0bcca8b86b7`. Copyright © 2025 Raspberry Pi Ltd.,
Apache-2.0. The license is retained in
[licenses/rpi-usb-gadget-Apache-2.0.txt](licenses/rpi-usb-gadget-Apache-2.0.txt).
This project uses it as an external Pi Ethernet provider; no installer, C watcher,
Windows driver or binary has been imported into the shipped runtime.

The optional [smbprotocol](https://github.com/jborean93/smbprotocol) dependency
provides the `smbclient` API. Copyright Jordan Borean and contributors, MIT;
see its [license](https://github.com/jborean93/smbprotocol/blob/master/LICENSE).
It is installed separately, not vendored. Preserve its and its dependencies'
notices when distributing an environment that includes them.

The optional dashboard uses [Flask](https://flask.palletsprojects.com/) (Pallets
contributors, BSD-3-Clause) and [Waitress](https://github.com/Pylons/waitress)
(Pylons contributors, Zope Public License 2.1). These and their dependencies are
installed separately, not vendored. Preserve their license files/notices in any
distributed virtual environment. Dashboard HTML/CSS/JavaScript, the simple project
icon and screenshots of the running QA preview were created for this GPL project;
no external UI assets, fonts or screenshot files from the reference project were
copied.

The Rust staged-file watcher links serde_json and its dependencies under their
upstream MIT/Apache-2.0 licensing; `rust/Cargo.lock` records exact dependency
versions. It uses the Rust standard library. Preserve the corresponding upstream
notices when distributing compiled binaries. The root GPL-3.0 project license
and Koseng attribution remain unchanged.

[rclone](https://rclone.org/licence/) is an external MIT-licensed executable;
Samba, NetworkManager, dnsmasq and the Linux kernel remain independently licensed
system dependencies. No Microsoft driver binaries are redistributed here.

[Cloud Stick Pro](https://github.com/rtariq2015/cloud-stick-pro) remains a separate,
unchanged feature/design reference; no code or assets were imported from it.
