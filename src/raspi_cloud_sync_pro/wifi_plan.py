"""Non-secret NetworkManager provisioning plans; no activation or credential writes."""

import json
from dataclasses import fields
from pathlib import Path

from .config import _unique_object
from .errors import BackupError
from .network import Security, WifiProfile


def load_wifi_profile(path):
    try:
        data = json.loads(Path(path).read_text(), object_pairs_hook=_unique_object)
        if set(data) != {"schema_version", "profile"} or type(data["schema_version"]) is not int \
                or data["schema_version"] != 1 or not isinstance(data["profile"], dict) \
                or not {"ssid", "security"} <= set(data["profile"]) \
                or set(data["profile"]) - {field.name for field in fields(WifiProfile)}:
            raise ValueError()
        profile = dict(data["profile"])
        profile["security"] = Security(profile["security"])
        return WifiProfile(**profile).validate()
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise BackupError("invalid_wifi_profile_plan") from exc


def wifi_plan(profile):
    """Describe nmcli-compatible properties and secret references, never a shell command."""
    profile.validate()
    properties = {"connection.type": "802-11-wireless", "connection.interface-name": "wlan0",
                  "connection.autoconnect": "no", "connection.autoconnect-priority": profile.priority,
                  "connection.metered": "yes" if profile.metered else "no",
                  "802-11-wireless.ssid": profile.ssid, "802-11-wireless.hidden": "yes" if profile.hidden else "no",
                  "ipv4.method": "auto", "ipv4.route-metric": 600, "ipv6.method": "auto"}
    secrets = {}
    if profile.security is Security.PERSONAL:
        properties["802-11-wireless-security.key-mgmt"] = "wpa-psk"
        secrets["802-11-wireless-security.psk"] = profile.password_ref
    elif profile.security is Security.ENTERPRISE:
        properties.update({"802-11-wireless-security.key-mgmt": "wpa-eap", "802-1x.eap": profile.eap,
                           "802-1x.identity": profile.identity, "802-1x.ca-cert": profile.ca_certificate,
                           "802-1x.domain-suffix-match": profile.server_domain})
        if profile.anonymous_identity:
            properties["802-1x.anonymous-identity"] = profile.anonymous_identity
        if profile.eap == "peap":
            properties["802-1x.phase2-auth"] = profile.phase2
            secrets["802-1x.password"] = profile.password_ref
        else:
            properties["802-1x.client-cert"] = profile.client_certificate
            secrets["802-1x.private-key"] = profile.private_key_ref
    return {"changes_applied": False, "properties": properties, "secret_references": secrets,
            "requires_private_secret_entry": bool(secrets), "captive_portal_possible": True,
            "portal_status": "not_observed",
            "activation_requirements": ["Verified Pi and preserved USB management access",
                                        "Approved CA/server identity for enterprise; never disable certificate validation",
                                        "NetworkManager checkpoint and tested rollback before Wi-Fi activation",
                                        "Interface-bound gateway, DNS, Internet and management tests",
                                        "Explicit portal interaction for this Pi when required"],
            "limitations": ["A plan does not create or activate a profile or verify a certificate.",
                            "Passwords and private keys must be resolved through a secure OS credential workflow.",
                            "No driver reload, boot change, global network restart or router security change."]}
