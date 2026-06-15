"""Audio downloader with SSRF validation and size limit.

Enforces the same policy as the Go ssrf package (F1):
  - Only https:// allowed
  - RFC 1918 blocked: 10/8, 172.16/12, 192.168/16
  - Loopback blocked: 127/8, ::1/128
  - Link-local blocked: 169.254/16
  - Tailscale blocked: 100.64.0.0/10
"""
from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
]

DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


class SSRFError(ValueError):
    pass


class FileTooLargeError(ValueError):
    pass


def _validate_ssrf(url: str) -> None:
    """Raise SSRFError if url fails the SSRF policy."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SSRFError(f"audio_url must use https, got scheme={parsed.scheme!r}")

    host = parsed.hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError(f"cannot resolve host {host!r}: {exc}") from exc

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise SSRFError(f"audio_url resolves to blocked IP {ip} ({net})")


def download(url: str, dest: str | Path, max_bytes: int = DEFAULT_MAX_BYTES) -> int:
    """Download url to dest, enforcing SSRF policy and size limit.

    Returns bytes written.
    Raises SSRFError before downloading if the URL fails policy.
    Raises FileTooLargeError if Content-Length exceeds limit or stream grows past it.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    # ALLOW_HTTP_AUDIO bypasses SSRF for local e2e testing — never set in production.
    if not os.getenv("ALLOW_HTTP_AUDIO"):
        _validate_ssrf(url)

    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as resp:
        resp.raise_for_status()

        content_length = resp.headers.get("content-length")
        if content_length is not None and int(content_length) > max_bytes:
            raise FileTooLargeError(
                f"audio file Content-Length {int(content_length)} exceeds limit {max_bytes} bytes"
            )

        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                written += len(chunk)
                if written > max_bytes:
                    raise FileTooLargeError(
                        f"audio file exceeded size limit of {max_bytes} bytes during download"
                    )
                f.write(chunk)

    return written
