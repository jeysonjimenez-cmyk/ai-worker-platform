"""
Tests for T4.2 — SSRF validation and size limit in the audio downloader.
All network calls are mocked; no real HTTP happens.
"""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from worker_whisper.downloader import (
    DEFAULT_MAX_BYTES,
    FileTooLargeError,
    SSRFError,
    _validate_ssrf,
    download,
)


# ─── _validate_ssrf unit tests ────────────────────────────────────────────────

def test_https_public_ip_passes():
    """A public https:// URL with a routable IP must pass."""
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("203.0.113.1", 0))]
        _validate_ssrf("https://example.com/audio.mp3")  # must not raise


def test_http_scheme_blocked():
    with pytest.raises(SSRFError, match="must use https"):
        _validate_ssrf("http://example.com/audio.mp3")


def test_ftp_scheme_blocked():
    with pytest.raises(SSRFError, match="must use https"):
        _validate_ssrf("ftp://example.com/audio.mp3")


def test_rfc1918_10_blocked():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("10.0.0.1", 0))]
        with pytest.raises(SSRFError, match="blocked IP"):
            _validate_ssrf("https://internal.example.com/audio.mp3")


def test_rfc1918_172_blocked():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("172.16.0.5", 0))]
        with pytest.raises(SSRFError, match="blocked IP"):
            _validate_ssrf("https://host.example.com/audio.mp3")


def test_rfc1918_192_blocked():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("192.168.1.100", 0))]
        with pytest.raises(SSRFError, match="blocked IP"):
            _validate_ssrf("https://host.example.com/audio.mp3")


def test_loopback_blocked():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
        with pytest.raises(SSRFError, match="blocked IP"):
            _validate_ssrf("https://host.example.com/audio.mp3")


def test_tailscale_100_64_blocked():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("100.64.0.1", 0))]
        with pytest.raises(SSRFError, match="blocked IP"):
            _validate_ssrf("https://host.example.com/audio.mp3")


def test_tailscale_100_127_blocked():
    """100.127.255.255 is still inside 100.64.0.0/10."""
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("100.127.255.255", 0))]
        with pytest.raises(SSRFError, match="blocked IP"):
            _validate_ssrf("https://host.example.com/audio.mp3")


def test_dns_resolution_failure_blocked():
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
        with pytest.raises(SSRFError, match="cannot resolve"):
            _validate_ssrf("https://nonexistent.invalid/audio.mp3")


def test_tailscale_outside_range_passes():
    """100.128.0.1 is outside 100.64.0.0/10 — must pass SSRF."""
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(None, None, None, None, ("100.128.0.1", 0))]
        _validate_ssrf("https://example.com/audio.mp3")  # must not raise


# ─── download() size limit tests ──────────────────────────────────────────────

def _mock_stream(chunks: list[bytes], headers: dict | None = None):
    """Build a minimal mock for httpx.stream(). Returns context manager mock."""
    resp = MagicMock()
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    resp.iter_bytes = MagicMock(return_value=iter(chunks))
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_download_content_length_exceeds_limit(tmp_path):
    dest = tmp_path / "audio.mp3"
    mock_resp = _mock_stream([], headers={"content-length": "1000"})

    with patch("socket.getaddrinfo") as mock_dns, \
         patch("httpx.stream", return_value=mock_resp):
        mock_dns.return_value = [(None, None, None, None, ("203.0.113.1", 0))]
        with pytest.raises(FileTooLargeError, match="Content-Length"):
            download("https://example.com/audio.mp3", dest, max_bytes=500)


def test_download_stream_exceeds_limit(tmp_path):
    dest = tmp_path / "audio.mp3"
    # 3 chunks of 200 bytes; limit is 500 bytes → third chunk pushes past limit
    chunks = [b"x" * 200, b"x" * 200, b"x" * 200]
    mock_resp = _mock_stream(chunks, headers={})

    with patch("socket.getaddrinfo") as mock_dns, \
         patch("httpx.stream", return_value=mock_resp):
        mock_dns.return_value = [(None, None, None, None, ("203.0.113.1", 0))]
        with pytest.raises(FileTooLargeError, match="exceeded size limit"):
            download("https://example.com/audio.mp3", dest, max_bytes=500)


def test_download_within_limit(tmp_path):
    dest = tmp_path / "audio.mp3"
    data = b"hello audio"
    mock_resp = _mock_stream([data], headers={})

    with patch("socket.getaddrinfo") as mock_dns, \
         patch("httpx.stream", return_value=mock_resp):
        mock_dns.return_value = [(None, None, None, None, ("203.0.113.1", 0))]
        written = download("https://example.com/audio.mp3", dest, max_bytes=DEFAULT_MAX_BYTES)

    assert written == len(data)
    assert dest.read_bytes() == data


def test_download_ssrf_blocked_before_http_call(tmp_path):
    dest = tmp_path / "audio.mp3"
    with pytest.raises(SSRFError):
        download("http://example.com/audio.mp3", dest)
    # File must not have been created
    assert not dest.exists()
