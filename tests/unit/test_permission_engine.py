import pytest

from app.permissions import PermissionDecision, PermissionEngine
from app.permissions.config import reset_permission_config, set_yolo_mode
from app.permissions.risk import (
    RISK_ALLOW,
    RISK_ASK,
    RISK_DENY,
    RISK_REVIEW,
    classify_command,
    pattern_key_for,
)
from app.permissions.smart import GuardianAssessment
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.workspace import authorize_workspace, clear_authorized_workspace


@pytest.fixture(autouse=True)
def _no_yolo():
    """Force non-YOLO (interactive) mode for these tests so they assert the
    default policy. `.env` may enable SAGE_YOLO_MODE=1 globally; reset it."""
    set_yolo_mode(False)
    clear_authorized_workspace()
    yield
    reset_permission_config()
    clear_authorized_workspace()


def dummy_handler():
    return "executed"


def make_tool(
    name: str = "test_tool",
    classification: ToolClassification = ToolClassification.READ_ONLY,
    destructive: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Test tool",
        classification=classification,
        destructive=destructive,
        employee_eligible=False,
        handler=dummy_handler,
    )


def test_read_only_tool_is_allowed():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.READ_ONLY,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.ALLOWED


def test_non_destructive_local_mutation_is_allowed():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.LOCAL_MUTATION,
        destructive=False,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.ALLOWED


def test_destructive_local_mutation_requires_approval():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.LOCAL_MUTATION,
        destructive=True,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.APPROVAL_REQUIRED


def test_external_side_effect_requires_approval():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.EXTERNAL_SIDE_EFFECT,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.APPROVAL_REQUIRED


def test_destructive_external_side_effect_requires_approval():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.EXTERNAL_SIDE_EFFECT,
        destructive=True,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.APPROVAL_REQUIRED


def test_permission_engine_fails_closed_for_unknown_classification():
    engine = PermissionEngine()

    tool = make_tool()

    # Simulate an invalid classification at runtime.
    tool.classification = "unknown"

    result = engine.check(tool)

    assert result == PermissionDecision.DENIED


def test_risk_classifier_matrix():
    assert classify_command("ls -la")[0] == RISK_ALLOW
    assert classify_command("npm install")[0] == RISK_ALLOW
    assert classify_command("rm -rf /tmp/x")[0] == RISK_ASK
    assert classify_command("sudo apt update")[0] == RISK_ASK
    assert classify_command("rm -rf /")[0] == RISK_DENY
    assert classify_command("rm -rf /home/user")[0] == RISK_DENY
    assert classify_command("mkfs.ext4 /dev/sda1")[0] == RISK_DENY
    assert classify_command("echo $(whoami)")[0] == RISK_REVIEW


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "/bin/rm -rf /",
        "./rm -rf /",
        "cd / && rm -rf /",
        "echo x; rm -rf /",
        "bash -c 'rm -rf /'",
        "rm -rf /etc",
        "rm -rf ~",
    ],
)
def test_hardline_rm_is_denied_wherever_it_appears(command):
    """Regression: the rm hardline rule was anchored to command position, so a
    path prefix, a chained command, or a nested shell downgraded a recursive
    delete of a system root from deny to ask."""
    assert classify_command(command)[0] == RISK_DENY


@pytest.mark.parametrize("command", ["charm hello", "firm -rf", "rm -rf ./build"])
def test_hardline_rm_does_not_match_substrings_or_relative_paths(command):
    """The widened matcher must not fire on command names that merely contain
    'rm', nor on deletes outside the protected roots."""
    assert classify_command(command)[0] != RISK_DENY


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.ssh/id_rsa",
        "less ~/.ssh/id_rsa",
        "head ~/.ssh/id_rsa",
        "grep SECRET .env",
        "base64 ~/.ssh/id_rsa",
        "tar czf /tmp/o.tgz ~/.ssh",
        'cat "$HOME/.ssh/id_rsa"',
        "/usr/bin/cat ~/.ssh/id_rsa",
        "cat ~/.aws/credentials",
        "cp server.pem /tmp/",
    ],
)
def test_secret_file_reads_require_approval_regardless_of_reader(command):
    """Regression: only ``cat|cp|mv|rsync`` used to be flagged, so ``less``,
    ``grep``, ``base64`` and archives of ``~/.ssh`` read keys silently."""
    assert classify_command(command)[0] == RISK_ASK


@pytest.mark.parametrize(
    "command",
    ["ls ~/.ssh", "cd ~/.ssh", "cat .env.example", "grep x src/.env.sample", "git status"],
)
def test_non_secret_paths_do_not_trigger_secret_rule(command):
    """Listings, navigation and templates must stay silent."""
    risk, reason = classify_command(command)
    assert "secret" not in reason.lower()
    if command != "git status":
        assert risk == RISK_ALLOW


def test_file_write_sensitive_outside_workspace_requires_approval(tmp_path):
    engine = PermissionEngine()
    tool = make_tool(
        name="write_file", classification=ToolClassification.LOCAL_MUTATION
    )

    ordinary = engine.check(tool, {"path": str(tmp_path / "notes.txt")})
    sensitive = engine.check(tool, {"path": str(tmp_path / ".env")})

    assert ordinary == PermissionDecision.ALLOWED
    assert sensitive == PermissionDecision.APPROVAL_REQUIRED


def test_file_write_in_authorized_directory_is_allowed(tmp_path):
    engine = PermissionEngine()
    tool = make_tool(
        name="write_file", classification=ToolClassification.LOCAL_MUTATION
    )
    authorize_workspace(tmp_path, persistent=True)

    result = engine.check(tool, {"path": str(tmp_path / ".env")})

    assert result == PermissionDecision.ALLOWED


def test_normal_command_is_allowed_without_approval():
    engine = PermissionEngine()
    tool = make_tool(name="run_command")

    assert engine.check(tool, {"command": "ls -la"}) == PermissionDecision.ALLOWED


def test_dangerous_command_requires_approval():
    engine = PermissionEngine()
    tool = make_tool(name="run_command")

    assert (
        engine.check(tool, {"command": "rm -rf build"})
        == PermissionDecision.APPROVAL_REQUIRED
    )


def test_hardline_command_is_denied_even_in_yolo():
    set_yolo_mode(True)
    engine = PermissionEngine()
    tool = make_tool(name="run_command")

    assert engine.check(tool, {"command": "rm -rf /"}) == PermissionDecision.DENIED


async def test_session_approval_suppresses_repeat_prompt():
    engine = PermissionEngine()
    tool = make_tool(name="run_command")
    arguments = {"command": "rm -rf /tmp/x"}

    first = await engine.check_async(tool, arguments)
    assert first == PermissionDecision.APPROVAL_REQUIRED

    engine.allow_session(pattern_key_for("run_command", arguments))

    second = await engine.check_async(tool, arguments)
    assert second == PermissionDecision.ALLOWED



class _FakeGuardian:
    def __init__(self, verdict: str):
        self._verdict = verdict

    async def assess(self, **kwargs):
        return GuardianAssessment(verdict=self._verdict)


async def test_ambiguous_command_guardian_approves():
    engine = PermissionEngine()
    engine.smart_guardian = _FakeGuardian("approve")
    tool = make_tool(name="run_command")

    result = await engine.check_async(tool, {"command": "echo $(whoami)"})

    assert result == PermissionDecision.ALLOWED


async def test_ambiguous_command_guardian_denies():
    engine = PermissionEngine()
    engine.smart_guardian = _FakeGuardian("deny")
    tool = make_tool(name="run_command")

    result = await engine.check_async(tool, {"command": "echo $(whoami)"})

    assert result == PermissionDecision.DENIED


def test_arguments_resource_shows_command_and_path():
    from app.conversation.loop import arguments_resource

    cmd_call = {
        "function": {"name": "run_command", "arguments": '{"command": "rm -rf /tmp/x"} '}
    }
    assert arguments_resource(cmd_call) == "rm -rf /tmp/x"

    write_call = {
        "function": {"name": "write_file", "arguments": '{"path": "/tmp/a.env"}'}
    }
    assert arguments_resource(write_call) == "/tmp/a.env"

    query_call = {
        "function": {"name": "session_search", "arguments": '{"query": "hello"}'}
    }
    assert arguments_resource(query_call) == "hello"
