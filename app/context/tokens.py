"""Cheap token estimation.

We deliberately avoid a real tokenizer: ~4 characters per token is accurate
enough for compaction thresholds, and compaction is confirmed against real
provider usage when available.
"""

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_message_tokens(message: dict) -> int:
    total = MESSAGE_OVERHEAD_TOKENS + estimate_tokens(message.get("content"))

    tool_calls = message.get("tool_calls")
    if tool_calls:
        total += estimate_tokens(str(tool_calls))

    return total


def estimate_history_tokens(messages: list[dict]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def calibrate_ratio(estimated: int, actual: int) -> float:
    """Compute correction ratio from estimated vs actual token count.

    Returns ratio such that: actual ≈ estimated * ratio.
    """
    if estimated <= 0:
        return 1.0
    return actual / estimated


def apply_calibration(estimated: int, ratio: float) -> int:
    """Apply calibration ratio to an estimate."""
    if ratio <= 0:
        return estimated
    return max(1, int(estimated * ratio))
