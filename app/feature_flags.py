"""Feature flag system for SAGE v2 subsystems.

All flags default to OFF. Enable via environment variables:
    SAGE_FEATURE_REAL_TOKENIZER=1
    SAGE_FEATURE_PARALLEL_TOOLS=1
    SAGE_FEATURE_SKILLS=1
    SAGE_FEATURE_THREAT_SCAN=1
    SAGE_FEATURE_COMPACTION_V2=1
    SAGE_FEATURE_VOICE=1
"""

from functools import lru_cache
from app.config import settings


class FeatureFlags:
    """Centralized feature flag access with caching."""

    def __init__(self):
        self._cache: dict[str, bool] = {}

    def is_enabled(self, flag: str) -> bool:
        """Check if a feature flag is enabled."""
        if flag not in self._cache:
            # Map flag name to settings attribute
            attr_name = f"feature_{flag.lower()}"
            self._cache[flag] = getattr(settings, attr_name, False)
        return self._cache[flag]

    def require_enabled(self, flag: str) -> None:
        """Raise RuntimeError if flag is not enabled."""
        if not self.is_enabled(flag):
            raise RuntimeError(
                f"Feature '{flag}' is not enabled. "
                f"Set SAGE_FEATURE_{flag.upper()}=1 to enable."
            )

    def clear_cache(self) -> None:
        """Clear the cache (useful for testing)."""
        self._cache.clear()


@lru_cache(maxsize=1)
def get_feature_flags() -> FeatureFlags:
    """Get the global feature flags instance."""
    return FeatureFlags()


# Convenience functions for common checks
def real_tokenizer_enabled() -> bool:
    return get_feature_flags().is_enabled("REAL_TOKENIZER")


def parallel_tools_enabled() -> bool:
    return get_feature_flags().is_enabled("PARALLEL_TOOLS")


def skills_enabled() -> bool:
    return get_feature_flags().is_enabled("SKILLS")


def threat_scan_enabled() -> bool:
    return get_feature_flags().is_enabled("THREAT_SCAN")


def compaction_v2_enabled() -> bool:
    return get_feature_flags().is_enabled("COMPACTION_V2")


def voice_enabled() -> bool:
    return get_feature_flags().is_enabled("VOICE")