"""CLI configuration and safe bind-host validation for the Kuro API."""

import argparse
import ipaddress


def validate_bind_host(host: str) -> str:
    """Accept loopback IP literals only; names can resolve to network interfaces."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            "API bind host must be a loopback IP address (for example, "
            "127.0.0.1 or ::1); non-loopback binding is disabled because the API "
            "does not provide authentication"
        ) from exc

    if not address.is_loopback:
        raise ValueError(
            "API bind host must be a loopback IP address (for example, "
            "127.0.0.1 or ::1); non-loopback binding is disabled because the API "
            "does not provide authentication"
        )
    return str(address)


def parse_api_server_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kuro API server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="loopback IP address to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="port to listen on (default: 8765)",
    )
    args = parser.parse_args(argv)
    try:
        args.host = validate_bind_host(args.host)
    except ValueError as exc:
        parser.error(str(exc))
    return args
