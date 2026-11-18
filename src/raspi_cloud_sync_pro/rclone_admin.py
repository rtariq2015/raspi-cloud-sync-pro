"""Opt-in loopback GUI availability only; never handles rclone credentials."""

import http.client
import re


_OPEN_URL = "http://127.0.0.1:5573/"
_TIMEOUT = 0.75
_HTML_LIMIT = 8192


def _probe(port, path, *, html=False):
    # HTTPConnection neither consults proxy environment variables nor follows
    # redirects. Hosts, ports and paths are fixed by status(), never HTTP input.
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=_TIMEOUT)
    try:
        connection.request("GET", path, headers={"Connection": "close"})
        response = connection.getresponse()
        if html:
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].lower().strip()
            if response.status != 200 or content_type != "text/html":
                return False
            prefix = response.read(_HTML_LIMIT).lstrip().lower()
            return prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")
        # Request no account/configuration data and send no Authorization header.
        # This only establishes an authentication challenge, not valid passwords.
        challenge = response.getheader("WWW-Authenticate", "")
        return (response.status == 401 and len(challenge) <= 256
                and re.fullmatch(r'Basic\s+realm="[^"\r\n]{0,200}"', challenge, re.I) is not None)
    finally:
        connection.close()


def status(enabled=False):
    """Ready means local HTML + API auth challenge; no client tunnel is proved.

    Default disabled performs no socket, subprocess or filesystem operations.
    Even opt-in never authenticates, invokes rclone or reads its configuration.
    """
    result = {"state": "disabled", "open_url": None, "client_tunnel_verified": False}
    if enabled is False:
        return result
    result["state"] = "unavailable"
    if enabled is not True:
        return result
    try:
        if _probe(5573, "/", html=True) and _probe(5572, "/core/version"):
            result.update(state="ready", open_url=_OPEN_URL)
    except (OSError, http.client.HTTPException, ValueError, TypeError, UnicodeError):
        pass
    return result
