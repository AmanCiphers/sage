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

    # RAG Reranker (cross-encoder)
    rag_rerank_enabled: bool = False
    rag_rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rag_rerank_top_n: int = 20
    rag_rerank_batch_size: int = 16

    # Feature flags for v2 subsystems (default off, opt-in via env)
    feature_real_tokenizer: bool = False
    feature_parallel_tools: bool = False
    feature_skills: bool = False
    feature_threat_scan: bool = False
    feature_compaction_v2: bool = False
    feature_voice: bool = False
    feature_llm_verifier: bool = False
    feature_memory_v2: bool = False

    # --- Voice module (SAGE_FEATURE_VOICE=1 to enable) -----------------------
    # Resource-light defaults for Apple-Silicon/CPU: lazy-loaded, small models,
    # push-to-talk by default (wake word is opt-in to keep idle CPU low).
    voice_sample_rate: int = 16000
    voice_frame_ms: int = 30
    voice_silence_ms: int = 700  # trailing silence before a turn is finalized
    voice_max_turn_seconds: float = 60.0

    # STT (faster-whisper, CTranslate2). Keep the model small for low load.
    voice_asr_model: str = "base.en"
    voice_asr_device: str = "cpu"
    voice_asr_compute_type: str = "int8"

    # VAD: energy-based by default (zero download); Silero ONNX used if the
    # model file is present at voice_model_dir/silero_vad.onnx.
    voice_vad_threshold: float = 0.02
    voice_use_silero: bool = False

    # Wake word (opt-in). openWakeWord ships pretrained keywords; "sage" needs
    # a custom model, so default stays off and PTT is primary.
    voice_wake_enabled: bool = False
    voice_wake_keyword: str = "hey_jarvis"

    # TTS: Chatterbox (local server) primary, Piper fallback.
    voice_chatterbox_url: str = "https://tts.thecloverforge.com/tts"
    voice_tts_primary: str = "chatterbox"
    voice_tts_fallback: str = "piper"
    voice_piper_voice: str = "en_US-lessac-medium"

    # Chunked TTS. The server splits text into small pieces and crossfades
    # them, but a single request still returns one merged WAV. Splitting the
    # text here lets playback start after the first piece.
    voice_tts_client_chunking: bool = True
    voice_tts_chunk_chars: int = 220
    voice_tts_prefetch: int = 2
    voice_tts_timeout_seconds: float = 30.0

    # Conversational line (option a): a concise spoken line distilled from the
    # full written response. Uses an LLM call unless disabled (then it falls
    # back to the final sentence of the response).
    voice_spoken_llm: bool = True

    # Where downloaded voice models live.
    voice_model_dir: Path = Path("data/voice")

    # Toolset profile (coding, research, writing, verification, organization, full)
    toolset_profile: str = "full"

    # Disable workspace authorization checks (for testing)
    disable_workspace_auth: bool = False

    # Full-machine access: absolute paths are allowed anywhere on the host.
    # Sensitive files still require approval unless inside an authorized
    # workspace (see app/permissions/risk.py).
    full_machine_access: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAGE_",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
