import pytest

from server.api_listener import parse_api_server_args


def test_api_server_defaults_to_ipv4_loopback():
    args = parse_api_server_args([])

    assert args.host == "127.0.0.1"
    assert args.port == 8765


def test_api_server_accepts_explicit_ipv4_loopback():
    args = parse_api_server_args(["--host", "127.0.0.1"])

    assert args.host == "127.0.0.1"


def test_api_server_accepts_explicit_ipv6_loopback():
    args = parse_api_server_args(["--host", "::1"])

    assert args.host == "::1"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "localhost"])
def test_api_server_rejects_non_loopback_or_non_ip_hosts(host, capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse_api_server_args(["--host", host])

    assert exc_info.value.code == 2
    assert "non-loopback binding is disabled" in capsys.readouterr().err


def test_api_server_cli_preserves_custom_port_and_parses_host():
    args = parse_api_server_args(["--host", "::1", "--port", "9876"])

    assert args.host == "::1"
    assert args.port == 9876
