from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SAGE"
    environment: str = "development"
    debug: bool = True

    database_path: Path = Path("data/sage.db")

    # Root for relative file-tool paths (read/write/list).
    workspace_path: Path = Path("data/workspace")

    min_workers: int = 10
    max_workers: int = 20

    model_request_timeout: float = 60.0

    # Agentic loop.
    max_iterations: int = 8
    max_tool_result_chars: int = 8000

    # Context / compaction (forever-running chat).
    context_window_tokens: int = 32000
    compaction_threshold_ratio: float = 0.5
    history_fetch_limit: int = 400

    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    nebius_api_key: str | None = None
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
