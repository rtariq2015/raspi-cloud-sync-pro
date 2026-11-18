"""Read-only network inventory; observations never authorize uploads or reconfiguration."""

import ipaddress
import json
import os
import re
import subprocess

USB_SHARE_ADDRESS = "10.12.194.1/28"


def network_status():
    """Inspect existing USB/Wi-Fi access without scans, probes, secrets or mutations."""
    def query(args, accepted=(0,)):
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=5, check=False,
                env={**os.environ, "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
            )
            return result.stdout.strip() if result.returncode in accepted else None
        except (OSError, subprocess.TimeoutExpired):
            return None  # Never include raw command errors in a diagnostic report.

    interfaces = {}
    for name in ("usb0", "wlan0"):
        output = query(["nmcli", "-g", "GENERAL.TYPE,GENERAL.STATE,IP4.ADDRESS",
                        "device", "show", name])
        record = {"type": None, "state_code": None, "ipv4_addresses": None}
        try:
            lines = output.splitlines()
            if lines[0] not in ("ethernet", "wifi", "802-11-wireless"):
                raise ValueError()
            state = int(lines[1].split()[0])
            addresses = [str(ipaddress.IPv4Interface(line)) for line in lines[2:] if line]
            record = {"type": lines[0], "state_code": state, "ipv4_addresses": addresses}
        except (AttributeError, ValueError, IndexError):
            pass
        interfaces[name] = record

    routes = None
    try:
        data = json.loads(query(["ip", "-json", "-4", "route", "show", "default"]))
        if not isinstance(data, list):
            raise ValueError()
        parsed = []
        for route in data:
            name, metric = route["dev"], route.get("metric", 0)
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", name) \
                    or type(metric) is not int or metric < 0 or "nexthops" in route \
                    or route.get("type", "unicast") != "unicast":
                raise ValueError()
            parsed.append({"interface": name, "gateway": str(ipaddress.IPv4Address(route["gateway"])),
                           "metric": metric})
        routes = parsed
    except (TypeError, ValueError, KeyError):
        pass
    selected = None
    if routes:
        best = min(route["metric"] for route in routes)
        candidates = [route for route in routes if route["metric"] == best]
        if len(candidates) == 1:
            selected = candidates[0]

    cached = query(["nmcli", "-g", "CONNECTIVITY", "general"])
    if cached not in ("full", "limited", "portal", "none", "unknown"):
        cached = "unknown"
    watcher = query(["systemctl", "is-active", "rpi-usb-gadget-ics.service"], accepted=(0, 3))
    if watcher not in ("active", "inactive", "failed"):
        watcher = "unknown"
    usb = interfaces["usb0"]
    checks = {"usb_share_address_matches": None if usb["ipv4_addresses"] is None else
              usb["type"] == "ethernet" and usb["state_code"] == 100
              and usb["ipv4_addresses"] == [USB_SHARE_ADDRESS],
              "official_auto_switcher_inactive": None if watcher == "unknown" else watcher != "active"}
    notices = []
    if checks["usb_share_address_matches"] is False:
        notices.append("Existing USB addressing differs from the share runtime; preserve access and review offline.")
    if watcher == "active":
        notices.append("The official auto-switcher can change USB profiles; review before serving files.")
    if selected and selected["interface"] == "usb0":
        notices.append("USB is the preferred IPv4 route; connected Wi-Fi does not select Wi-Fi for cloud uploads.")
    return {
        "interfaces": interfaces, "ipv4_default_routes": routes,
        "preferred_ipv4_route": selected, "cached_global_connectivity": cached,
        "official_auto_switcher": watcher, "expected_usb_share_address": USB_SHARE_ADDRESS,
        "share_network_checks": checks,
        "share_network_prerequisites_match": all(value is True for value in checks.values()),
        "notices": notices, "hardware_operations_authorized": False,
        "limitations": ["Cached global connectivity does not prove Wi-Fi Internet or cloud access.",
                        "IPv6, VPNs and policy routing are not evaluated; this is not an egress guarantee.",
                        "No authenticated SSH, Samba, DHCP-server or cloud test is performed.",
                        "No settings, services, routes or radio state were changed; credentials were not queried."],
    }
