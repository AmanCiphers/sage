"""Deterministic outcome verification (ArchitectureV2 §60–61).

The executor's own claim ("done") is not trusted: verification re-checks
the actual world state (does the file exist? does it hold the requested
content? did the command exit 0?) and records `passed` / `failed` /
`unverified` per task or operation.

No LLM verifier in v1 — deterministic checks cover every tool we run
in the background or behind an approval today (D23).
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Verification(BaseModel):
    """Result of re-checking an executed action against real state."""

    status: str = "unverified"  # "passed" | "failed" | "unverified"
    checks: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def detail(self) -> str:
        parts = []
        for check in self.checks:
            if check.get("ok"):
                parts.append(f"{check['check']}: ok")
            else:
                note = check.get("detail", "")
                parts.append(f"{check['check']}: FAIL" + (f" ({note})" if note else ""))
        return "; ".join(parts)


Verifier = Callable[[dict, Any], list[dict[str, Any]]]


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": bool(ok), "detail": detail}


def _parse(output: Any) -> Any:
    if isinstance(output, str):
        try:
            return json.loads(output)
        except (ValueError, TypeError):
            return None
    return output


def _verify_write_file(arguments: dict, output: Any) -> list[dict]:
    from app.tools.file_tools import resolve_path

    path = resolve_path(arguments.get("path", ""))
    exists = path.is_file()
    checks = [_check("file_exists", exists, str(path))]
    if exists:
        actual = path.read_text(encoding="utf-8", errors="replace")
        expected = arguments.get("content", "")
        checks.append(
            _check(
                "content_matches",
                actual == expected,
                f"expected {len(expected)} chars, found {len(actual)}",
            )
        )
    return checks


def _verify_patch(arguments: dict, output: Any) -> list[dict]:
    from app.tools.file_tools import resolve_path

    path = resolve_path(arguments.get("path", ""))
    exists = path.is_file()
    checks = [_check("file_exists", exists, str(path))]
    if exists:
        content = path.read_text(encoding="utf-8", errors="replace")
        old = arguments.get("old_text", "")
        new = arguments.get("new_text", "")
        if not arguments.get("replace_all"):
            checks.append(
                _check("old_text_absent", old not in content, "stale text still present")
            )
        checks.append(_check("new_text_present", new in content, "expected text missing"))
    return checks


def _verify_run_command(arguments: dict, output: Any) -> list[dict]:
    data = output if isinstance(output, dict) else None
    if data is None:
        return []
    if data.get("error"):
        return [_check("no_error", False, str(data["error"]))]
    exit_code = data.get("exit_code")
    if exit_code is None:
        return []
    return [_check("exit_zero", exit_code == 0, f"exit_code={exit_code}")]


_VERIFIERS: dict[str, Verifier] = {
    "write_file": _verify_write_file,
    "patch": _verify_patch,
    "run_command": _verify_run_command,
}


def has_verifier(tool_name: str) -> bool:
    """Check if a tool has a registered verifier."""
    return tool_name in _VERIFIERS


def verify(tool_name: str, arguments: dict[str, Any], output: Any) -> Verification:
    """Re-check the real outcome of an executed tool call.

    Tools without a registered verifier are reported honestly as
    `unverified` — never silently upgraded to `passed`.
    """

    verifier = _VERIFIERS.get(tool_name)
    if verifier is None:
        return Verification(status="unverified")

    try:
        checks = verifier(arguments, _parse(output))
    except Exception as exc:  # noqa: BLE001 — verification must not crash tasks
        logger.warning("verifier_error tool=%s error=%s", tool_name, exc)
        return Verification(
            status="unverified",
            checks=[_check("verifier_ran", False, str(exc))],
        )

    if not checks:
        return Verification(status="unverified")

    failed = [c for c in checks if not c["ok"]]
    return Verification(
        status="failed" if failed else "passed",
        checks=checks,
    )
