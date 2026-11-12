"""Non-secret Wi-Fi profile validation and upload policy; no OS network changes."""

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Optional


class Security(str, Enum):
    PERSONAL = "wpa2_personal"
    ENTERPRISE = "wpa2_enterprise"
    OPEN = "open"


class Connectivity(str, Enum):
    UNKNOWN = "unknown"
    OFFLINE = "offline"
    LIMITED = "limited"
    PORTAL = "portal_login_required"
    ONLINE = "online"


@dataclass(frozen=True)
class WifiProfile:
    ssid: str
    security: Security
    priority: int = 0
    metered: bool = False
    hidden: bool = False
    password_ref: Optional[str] = None
    identity: Optional[str] = None
    eap: Optional[str] = None
    ca_certificate: Optional[str] = None
    server_domain: Optional[str] = None
    client_certificate: Optional[str] = None
    private_key_ref: Optional[str] = None
    phase2: Optional[str] = None

    def validate(self):
        if not isinstance(self.ssid, str) or not self.ssid or "\0" in self.ssid \
                or len(self.ssid.encode("utf-8")) > 32:
            raise ValueError("SSID must contain 1 to 32 UTF-8 bytes without NUL")
        if not isinstance(self.security, Security) or type(self.priority) is not int \
                or not -999 <= self.priority <= 999 \
                or type(self.metered) is not bool or type(self.hidden) is not bool:
            raise ValueError("invalid Wi-Fi profile options")
        for value in (self.password_ref, self.identity, self.eap, self.ca_certificate,
                      self.server_domain, self.client_certificate, self.private_key_ref, self.phase2):
            if value is not None and (not isinstance(value, str) or not value or "\0" in value):
                raise ValueError("invalid Wi-Fi authentication field")
        enterprise = (self.identity, self.eap, self.ca_certificate, self.server_domain,
                      self.client_certificate, self.private_key_ref, self.phase2)
        if self.security == Security.ENTERPRISE:
            if not all((self.identity, self.ca_certificate, self.server_domain)):
                raise ValueError("enterprise requires identity, CA certificate and server domain")
            if not PurePosixPath(self.ca_certificate).is_absolute() or len(self.server_domain) > 253 \
                    or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", self.server_domain) \
                    or ".." in self.server_domain:
                raise ValueError("enterprise requires an absolute CA path and explicit server domain")
            if self.eap == "peap":
                if not self.password_ref or self.phase2 != "mschapv2" \
                        or self.client_certificate or self.private_key_ref:
                    raise ValueError("PEAP requires a password reference and MSCHAPv2")
            elif self.eap == "tls":
                if not self.client_certificate or not self.private_key_ref or self.password_ref or self.phase2:
                    raise ValueError("EAP-TLS requires client certificate and private key reference")
                if not PurePosixPath(self.client_certificate).is_absolute():
                    raise ValueError("client certificate path must be absolute")
            else:
                raise ValueError("supported enterprise methods are PEAP and EAP-TLS")
        else:
            if any(value is not None for value in enterprise):
                raise ValueError("enterprise fields require enterprise security")
            if self.security == Security.PERSONAL and not self.password_ref:
                raise ValueError("personal Wi-Fi requires a password reference")
            if self.security == Security.OPEN and self.password_ref is not None:
                raise ValueError("open Wi-Fi cannot have a network password reference")
        return self


def upload_allowed(connectivity: Connectivity, *, metered=False, allow_metered=False) -> bool:
    if not isinstance(connectivity, Connectivity) or type(metered) is not bool \
            or type(allow_metered) is not bool:
        raise ValueError("invalid connectivity policy")
    return connectivity == Connectivity.ONLINE and (not metered or allow_metered)
