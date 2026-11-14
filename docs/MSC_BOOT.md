# USB storage startup and recovery

Dev22 source is cold-start capable; its native reboot acceptance is pending.
The currently accepted dev21 installation captures only an existing USB profile.
Public hardware flags remain disabled. This is an approved disposable-image test
path, not a general installer or a flashable appliance image.

Inside the exclusive controller session, dev22 permits profile creation only for
its fixed absent configfs name, or reuse of an exact complete empty profile. It
requires the approved image identity, healthy power, no competing gadget module,
one expected UDC, and no image loop or mount. A prior journal must be exactly a
clean HOST state for the same image. Pending journals, ambiguous ownership or any
Root-private unfinished/ready capture stop startup without erasing the evidence.

The controller checks the image read-only, normally detaches its loop, exports
it, and remains unarmed until it observes a fresh configured host with loaded
media. Reboot and a saved journal are never treated as safe-eject evidence.
Dirty or damaged filesystems stop; they are not repaired automatically.

Before native boot testing, the operator must back up each affected Pi file and
service state, verify the Pi and image identity, and inspect all legacy loaders:

1. Preserve the applicable `dwc2,dr_mode=peripheral` firmware setting, Wi-Fi and
   SSH. Leave CM4, CM5 and Pi 5 host-mode sections alone.
2. Remove only the verified legacy `g_ether` boot entry, arrange `libcomposite`
   loading, and disable/mask the previously stopped Pi-side ICS watcher. Do not
   unload drivers while a host owns the image or restart all networking.
3. Check the active initramfs and kernel arguments. An embedded legacy loader
   requires a separately reviewed initramfs update; do not rebuild unnecessarily.
4. Install the reviewed package/approval and unit. The unit requires configfs,
   module loading and the image filesystem. Test adoption first, then enable
   only the validated capture service.
5. Before reboot, confirm all test versions are captured/verified, stop the idle
   observer, and perform normal host eject with the cable connected for power.
   Confirm an empty LUN, no Pi image mount or loop, then reboot only the Pi.
6. Confirm a new boot ID, fresh Wi-Fi SSH, both services, genuine USB enumeration,
   retained verified history and an additional real copy/eject/Google test.

Rollback keeps the image, journals, capture batches and Google configuration.
After a verified host release and clean detached state, stop only the owned
controller, remove only its exact detached configfs profile, restore the saved
loader/unit/service state and use the previously tested Ethernet recovery path.
Never clear a loaded LUN, force unmount, delete a journal to bypass refusal, repair
without review, or expose the live root filesystem.

Native preparation inspected `initramfs8` for the running 6.18.60+rpt-rpi-v8
kernel: no modules-load file or g_ether binary was embedded. The only observed
legacy boot entry was `/etc/modules-load.d/usb-gadget.conf`; the firmware and
kernel command line did not need modification. This inspection alone does not
prove reboot success.
