"""Token estimation with real tokenizer support.

Uses Qwen3-Embedding-8B tokenizer (same as embeddings) for accurate counting.
Falls back to chars/4 estimation when tokenizer unavailable or feature disabled.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from app.config import settings
from app.feature_flags import real_tokenizer_enabled

# Fallback constants (used when real tokenizer unavailable)
CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4


def _fallback_estimate_tokens(text: str | None) -> int:
    """Fallback: ~4 characters per token."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def _fallback_estimate_message_tokens(message: dict) -> int:
    """Fallback message token estimation."""
    total = MESSAGE_OVERHEAD_TOKENS + _fallback_estimate_tokens(message.get("content"))

    tool_calls = message.get("tool_calls")
    if tool_calls:
        total += _fallback_estimate_tokens(str(tool_calls))

    return total


@lru_cache(maxsize=1)
def _get_tokenizer():
    """Get the tokenizer instance (lazy-loaded, cached).
    
    Returns None if tokenizer unavailable or feature disabled.
    """
    if not real_tokenizer_enabled():
        return None
    
    try:
        from transformers import AutoTokenizer
        # Use the same tokenizer as embeddings (Qwen3-Embedding-8B)
        tokenizer = AutoTokenizer.from_pretrained(
            "Qwen/Qwen3-Embedding-8B",
            trust_remote_code=True,
        )
        return tokenizer
    except Exception:
        # Tokenizer unavailable - fall back to estimation
        return None


def estimate_tokens(text: str | None) -> int:
    """Estimate tokens in text using real tokenizer or fallback."""
    if not text:
        return 0
    
    tokenizer = _get_tokenizer()
    if tokenizer is None:
        return _fallback_estimate_tokens(text)
    
    try:
        return len(tokenizer.encode(text))
    except Exception:
        return _fallback_estimate_tokens(text)


def estimate_message_tokens(message: dict) -> int:
    """Estimate tokens in a message using real tokenizer or fallback."""
    tokenizer = _get_tokenizer()
    if tokenizer is None:
        return _fallback_estimate_message_tokens(message)
    
    try:
        total = MESSAGE_OVERHEAD_TOKENS
        
        content = message.get("content")
        if content:
            total += len(tokenizer.encode(content))
        
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += len(tokenizer.encode(str(tool_calls)))
        
        return max(1, total)
    except Exception:
        return _fallback_estimate_message_tokens(message)


def estimate_history_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in message history."""
    tokenizer = _get_tokenizer()
    if tokenizer is None:
        return sum(_fallback_estimate_message_tokens(m) for m in messages)
    
    try:
        total = 0
        for m in messages:
            total += estimate_message_tokens(m)
        return total
    except Exception:
        return sum(_fallback_estimate_message_tokens(m) for m in messages)


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