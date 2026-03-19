import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlparse


_PRIVATE_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "host.docker.internal",
    "gateway.docker.internal",
    "metadata",
    "metadata.google.internal",
    "kubernetes",
    "kubernetes.default",
}

_PRIVATE_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
    ".home.arpa",
)


def _is_public_ip(value: str) -> bool:
    try:
        return bool(ipaddress.ip_address(value).is_global)
    except ValueError:
        return False


def _host_looks_private(hostname: str) -> bool:
    host = (hostname or "").strip().rstrip(".").lower()
    if not host:
        return True
    if host in _PRIVATE_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in _PRIVATE_SUFFIXES)


@lru_cache(maxsize=512)
def _resolved_to_private_ip(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    except Exception:
        return False

    addresses = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = sockaddr[0]
        if ip not in addresses:
            addresses.append(ip)

    if not addresses:
        return False

    return any(not _is_public_ip(ip) for ip in addresses)


def is_public_http_url(url: str, *, resolve_dns: bool = True) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return False

    if _host_looks_private(hostname):
        return False

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return not (resolve_dns and _resolved_to_private_ip(hostname))

    return _is_public_ip(hostname)
