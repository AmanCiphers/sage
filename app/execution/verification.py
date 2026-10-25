"""Outcome verification (ArchitectureV2 §60–62).

Two-rung verification:
  1. Deterministic checks (file exists, content matches, exit code 0)
  2. LLM verifier for complex outcomes (objective achieved, quality, etc.)

The executor's own claim ("done") is not trusted: verification re-checks
the actual world state and records `passed` / `failed` / `unverified`
per task or operation.
"""  # noqa: RUF002

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.feature_flags import compaction_v2_enabled
from app.inference.manager import InferenceManager
from app.inference.models import InferenceRequest, ModelRequirements

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


# LLM-based verifier for complex outcomes
async def _llm_verify(
    tool_name: str,
    arguments: dict[str, Any],
    output: Any,
    objective: str | None = None,
    inference_manager: InferenceManager | None = None,
) -> list[dict[str, Any]]:
    """Use LLM to verify complex outcomes against objective."""
    if inference_manager is None:
        return [_check("llm_verifier_available", False, "No inference manager")]

    # Build verification prompt
    objective_text = objective or f"Tool {tool_name} completed successfully"
    
    prompt = f"""Verify the outcome of a tool execution against its objective.

Tool: {tool_name}
Arguments: {json.dumps(arguments, default=str)}
Objective: {objective_text}
Output: {json.dumps(output, default=str)[:2000]}

Check if the outcome satisfies the objective. Consider:
1. Does the output indicate successful completion?
2. Are there errors or failures indicated?
3. Does the result align with what was requested?

Return a JSON object with:
- "passed": boolean
- "checks": array of {{"check": string, "ok": boolean, "detail": string}}
- "reasoning": brief explanation

Be strict but fair. If uncertain, mark as unverified."""

    try:
        response = await inference_manager.generate(
            InferenceRequest(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
            ),
            requirements=None,
        )
        
        result = json.loads(response.content)
        checks = []
        for check in result.get("checks", []):
            checks.append(_check(
                check.get("check", "llm_check"),
                check.get("ok", False),
                check.get("detail", "")
            ))
        
        # Add overall LLM verdict
        checks.insert(0, _check(
            "llm_verdict",
            result.get("passed", False),
            result.get("reasoning", "")[:200]
        ))
        
        return checks
    except Exception as exc:
        logger.warning("llm_verifier_error error=%s", exc)
        return [_check("llm_verifier_error", False, str(exc))]


# Registry of deterministic verifiers
_VERIFIERS: dict[str, Callable[[dict, Any], list[dict]]] = {
    "write_file": _verify_write_file,
    "patch": _verify_patch,
    "run_command": _verify_run_command,
}

# Tools that can use LLM verification (complex outcomes)
_LLM_VERIFIABLE_TOOLS: set[str] = {
    "run_opencode",
    "run_hermes",
    "analyze",
    "web_search",
    "browse_url",
}


def has_verifier(tool_name: str) -> bool:
    """Check if a tool has a registered deterministic verifier."""
    return tool_name in _VERIFIERS


def can_llm_verify(tool_name: str) -> bool:
    """Check if a tool can use LLM verification."""
    return tool_name in _LLM_VERIFIABLE_TOOLS


async def verify(
    tool_name: str,
    arguments: dict[str, Any],
    output: Any,
    objective: str | None = None,
    inference_manager: InferenceManager | None = None,
) -> "Verification":
    """Re-check the real outcome of an executed tool call.

    Two-rung verification:
    1. Deterministic checks (file exists, content matches, exit code 0)
    2. LLM verification for complex outcomes (if enabled and applicable)

    Tools without a registered verifier are reported honestly as
    `unverified` — never silently upgraded to `passed`.
    """

    # Rung 1: Deterministic checks
    verifier = _VERIFIERS.get(tool_name)
    if verifier is None:
        deterministic_checks = []
    else:
        try:
            deterministic_checks = verifier(arguments, _parse(output))
        except Exception as exc:
            logger.warning("verifier_error tool=%s error=%s", tool_name, exc)
            deterministic_checks = [_check("verifier_ran", False, str(exc))]

    if not deterministic_checks:
        return Verification(status="unverified")

    failed = [c for c in deterministic_checks if not c["ok"]]
    if failed:
        return Verification(status="failed", checks=deterministic_checks)

    # Rung 2: LLM verification (if enabled, applicable, and deterministic passed)
    if (settings.feature_llm_verifier 
        and can_llm_verify(tool_name) 
        and inference_manager is not None):
        
        llm_checks = await _llm_verify(
            tool_name=tool_name,
            arguments=arguments,
            output=output,
            objective=objective,
            inference_manager=inference_manager,
        )
        
        if llm_checks:
            llm_failed = [c for c in llm_checks if not c["ok"]]
            if llm_failed:
                return Verification(
                    status="failed",
                    checks=deterministic_checks + llm_checks,
                )

    return Verification(
        status="passed",
        checks=deterministic_checks,
    )
