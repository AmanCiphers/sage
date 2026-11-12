import pytest

from app.tools.models import ToolCapability
from app.tools.shell_parser import _resolve_run_command_capabilities


@pytest.mark.parametrize("prefix", ["", "/usr/bin/", "./", "/opt/homebrew/bin/"])
def test_network_command_classified_regardless_of_path_prefix(prefix):
    """Regression: only the bare token was matched, so a path prefix moved
    ``curl`` from NETWORK into a different capability class."""
    caps = _resolve_run_command_capabilities({"command": f"{prefix}curl http://example.com"})
    assert caps == [ToolCapability.NETWORK]


@pytest.mark.parametrize("prefix", ["", "/bin/", "/usr/bin/"])
def test_system_command_classified_regardless_of_path_prefix(prefix):
    caps = _resolve_run_command_capabilities({"command": f"{prefix}systemctl restart nginx"})
    assert caps == [ToolCapability.SYSTEM]


@pytest.mark.parametrize("prefix", ["", "/bin/", "/usr/bin/"])
def test_read_only_command_classified_regardless_of_path_prefix(prefix):
    caps = _resolve_run_command_capabilities({"command": f"{prefix}ls -la"})
    assert caps == [ToolCapability.READ]


def test_empty_command_is_execute():
    assert _resolve_run_command_capabilities({"command": ""}) == [ToolCapability.EXECUTE]


def test_unknown_command_is_execute():
    assert _resolve_run_command_capabilities({"command": "my-custom-binary"}) == [
        ToolCapability.EXECUTE
    ]
