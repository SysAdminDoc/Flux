"""VPN bind-address validation and fail-closed interface checks."""

import ipaddress
import socket
from typing import Any, Callable


def _clean_address(value: Any) -> str:
    address = str(value or "").strip()
    if address.startswith("[") and address.endswith("]"):
        address = address[1:-1]
    return address


def resolve_bind_address(value: Any) -> str:
    """Resolve an IPv4/IPv6 literal or hostname to one bindable address."""
    address = _clean_address(value)
    if not address:
        return ""
    try:
        parsed = ipaddress.ip_address(address)
        if parsed.is_unspecified:
            return ""
        return str(parsed)
    except ValueError:
        pass

    try:
        candidates = socket.getaddrinfo(address, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except OSError:
        return ""
    for family, _, _, _, sockaddr in candidates:
        candidate = str(sockaddr[0])
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not parsed.is_unspecified and (family in (socket.AF_INET, socket.AF_INET6)):
            return str(parsed)
    return ""


def build_listen_interfaces(address: Any, port: Any) -> str:
    """Build libtorrent's listen_interfaces value without exposing all NICs."""
    try:
        listen_port = max(1, int(port))
    except (TypeError, ValueError):
        listen_port = 6881

    bind_address = resolve_bind_address(address)
    if not _clean_address(address):
        return f"0.0.0.0:{listen_port},[::0]:{listen_port}"
    if not bind_address:
        # An invalid configured bind must not silently fall back to every NIC.
        return f"127.0.0.1:{listen_port}"
    if ":" in bind_address:
        return f"[{bind_address}]:{listen_port}"
    return f"{bind_address}:{listen_port}"


def local_interface_addresses() -> set[str]:
    """Return addresses currently reported for the local machine."""
    addresses: set[str] = set()
    try:
        hostname = socket.gethostname()
        _, _, host_addresses = socket.gethostbyname_ex(hostname)
        addresses.update(str(address) for address in host_addresses)
    except OSError:
        pass

    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_UNSPEC, socket.SOCK_STREAM
        ):
            if family in (socket.AF_INET, socket.AF_INET6):
                addresses.add(str(sockaddr[0]))
    except OSError:
        pass
    return addresses


def is_bind_address_available(
    value: Any,
    address_provider: Callable[[], set[str]] | None = None,
) -> bool:
    """Return whether the configured bind address is currently assigned."""
    bind_address = resolve_bind_address(value)
    if not bind_address:
        return False
    provider = address_provider or local_interface_addresses
    available = {resolve_bind_address(address) for address in provider()}
    return bind_address in available
