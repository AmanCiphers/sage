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

    model_request_timeout: float = 180.0

    # Agentic loop.
    max_iterations: int = 50
    max_tool_result_chars: int = 8000

    # Employee assignment budgets (0/None = unlimited). Iterations and
    # tokens are unlimited by default — tool calls are cheap; wall-clock
    # is not a runaway guard (SAGE can cancel via cancel_task).
    employee_max_iterations: int = 0
    employee_max_tokens: int = 0
    employee_max_wall_seconds: float = 0.0

    # Context / compaction (forever-running chat).
    context_window_tokens: int = 32000
    compaction_threshold_ratio: float = 0.5
    history_fetch_limit: int = 400

    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    nebius_api_key: str | None = None
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1"

    tavily_api_key: str | None = None

    # OS/desktop notifications for scheduled jobs, proactive actions,
    # reminders, and background work. Disable in CI/headless.
    os_notifications_enabled: bool = True

    # Default timezone for cron/scheduled jobs (IANA name, e.g. "Asia/Kolkata").
    timezone: str = "Asia/Kolkata"

    # Scheduler loop check interval (seconds). Lower = more precise one-time
    # reminders; higher = less CPU. Cron jobs are minute-granular anyway, but
    # `run_at` reminders can be sub-minute.
    scheduler_check_interval: int = 5

    # Agent Backends (OpenCode, Hermes)
    opencode_path: str = "opencode"
    hermes_path: str = "hermes"
    opencode_default_model: str = "nebius/deepseek-ai/DeepSeek-V4-Flash-0731"
    hermes_default_model: str = "openai/gpt-4"

    # RAG (Hybrid FTS5 + Embeddings)
    rag_enabled: bool = True
    rag_top_k: int = 5
    rag_fts_weight: float = 0.4
    rag_embedding_weight: float = 0.6
    rag_similarity_threshold: float = 0.65
    rag_exclude_recent_turns: int = 20
    # NVIDIA free tier: nemotron-3-embed-1b (1024 dim)
    # Nebius: Qwen/Qwen3-Embedding-8B (4096 dim, supports Matryoshka)
    rag_embedding_model: str = "nvidia/nemotron-3-embed-1b"
    rag_fallback_model: str = "Qwen/Qwen3-Embedding-8B"
    rag_embedding_dim: int = 1024

    # Feature flags for v2 subsystems (default off, opt-in via env)
    feature_real_tokenizer: bool = False
    feature_parallel_tools: bool = False
    feature_skills: bool = False
    feature_threat_scan: bool = False
    feature_compaction_v2: bool = False
    feature_voice: bool = False

    # Toolset profile (coding, research, writing, verification, organization, full)
    toolset_profile: str = "full"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAGE_",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
