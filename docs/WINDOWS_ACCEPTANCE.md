# Final Windows hardware check

The Pi now passes the USB-to-Google workflow, real restart and real Wi-Fi outage
tests on the available Zorin laptop. Windows-specific acceptance remains open.
No Windows application or USB networking is required for the intended interface.

The folder **Windows-Test-Files** is already on CLOUDSTICK. It contains a small
instruction file and a disposable 64 MiB file. Their expected hashes are retained
privately, so no terminal commands or private documents are needed from you.

1. When you are at the Windows computer, say **Ready for the Windows test** in
   the active project conversation or [progress issue](https://github.com/rtariq2015/raspi-cloud-sync-pro/issues/2).
   Keep the cable connected for now. The engineer must first arrange normal
   Linux Eject and a clean Pi shutdown before moving its power cable.
2. After shutdown is confirmed, leave the SD card in the Pi and move the USB
   cable to Windows. Use the Pi's USB **data** socket nearest the HDMI socket.
   That same cable powers the Pi. Do not use its power-only socket for data.
3. Wait for **CLOUDSTICK** in File Explorer. Windows assigns the drive letter;
   it need not be Z:.
4. Select **Windows-Test-Files**, copy it, then paste it into the same CLOUDSTICK
   drive. Windows should create a second folder, usually with “Copy” in its name.
5. Wait for copying to finish. Right-click **CLOUDSTICK → Eject**, and keep the
   USB cable connected for power. Report **Windows copied and ejected**, or the
   exact error. Do not send passwords.

The engineer will check the real loaded→empty handoff, captured hashes against
the prepared source files, automatic Google upload/download verification,
retained originals and drive reappearance. Windows-specific results will not be
inferred from Linux tests. GitHub replies can be read during active work; they
are not an automatic command bridge into an inactive coding session.

The installation remains a development trial using a dedicated 1 GiB image.
Fully automatic capture without Eject, Windows suspend/power behavior, and
permanent Google authorization are separate acceptance limits. No main merge or
release is authorized by this check.
