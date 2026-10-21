"""Permission system configuration for SAGE."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PermissionConfig(BaseSettings):
    """Permission system configuration loaded from .env"""

    # Bypass modes
    yolo_mode: bool = Field(default=False, description="Global YOLO bypass (env: SAGE_YOLO_MODE)")

    # Approval mode for different contexts
    mode: str = Field(default="interactive", description="approvals.mode: off | interactive | deny")
    single_query_mode: str = Field(default="deny", description="approvals.single_query_mode: approve | deny")  # noqa: E501
    cron_mode: str = Field(default="deny", description="approvals.cron_mode: approve | deny")
    unattended_mode: str = Field(default="deny", description="approvals.unattended_mode: approve | deny")  # noqa: E501

    # Smart guardian LLM
    smart_approval: bool = Field(default=True, description="Enable smart guardian LLM")
    smart_model: str = Field(default="deepseek-ai/DeepSeek-V4-Flash-0731", description="Smart guardian model")  # noqa: E501
    smart_temperature: float = Field(default=0.1, description="Smart guardian temperature")
    smart_max_tokens: int = Field(default=100, description="Smart guardian max tokens")

    # Circuit breaker
    denial_breaker_threshold: int = Field(default=3, description="Consecutive DENY before hard stop (0=disabled)")  # noqa: E501

    # Gateway
    gateway_enabled: bool = Field(default=False, description="Enable gateway callback for Web UI")
    gateway_timeout: int = Field(default=120, description="Gateway approval timeout (seconds)")

    # CLI
    cli_timeout: int = Field(default=120, description="CLI approval timeout (seconds)")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SAGE_",
        extra="ignore",
    )

    @property
    def yolo_active(self) -> bool:
        """Check if YOLO mode is active (global or session)."""
        return self.yolo_mode

    @property
    def is_off_mode(self) -> bool:
        return self.mode == "off"

    @property
    def is_interactive_mode(self) -> bool:
        return self.mode == "interactive"

    @property
    def is_deny_mode(self) -> bool:
        return self.mode == "deny"

    def get_context_mode(self, context: str) -> str:
        """Get approval mode for a specific context."""
        if self.is_off_mode:
            return "off"
        if self.yolo_active:
            return "approve"

        context_map = {
            "single_query": self.single_query_mode,
            "cron": self.cron_mode,
            "unattended": self.unattended_mode,
        }
        return context_map.get(context, self.mode)

    def is_deny_for_context(self, context: str) -> bool:
        return self.get_context_mode(context) == "deny"

    def is_approve_for_context(self, context: str) -> bool:
        return self.get_context_mode(context) == "approve"


# Global instance
_permission_config: "PermissionConfig | None" = None


def get_permission_config() -> PermissionConfig:
    """Get the global permission config instance."""
    global _permission_config
    if _permission_config is None:
        _permission_config = PermissionConfig()
    return _permission_config


def reset_permission_config() -> None:
    """Reset config (for testing)."""
    global _permission_config
    _permission_config = None
