# Linux protocol fixture only; not a Pi/USB installer.
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
RUN apt-get update \
 && apt-get install -y --no-install-recommends samba samba-common-bin python3 python3-venv \
 && python3 -m venv /fixture-venv \
 && /fixture-venv/bin/pip install --no-cache-dir 'smbprotocol==1.15.0' \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/work/src
ENTRYPOINT ["/fixture-venv/bin/python", "/work/tools/acceptance/samba/protocol_fixture.py"]
