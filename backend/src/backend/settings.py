"""Application settings from environment."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = Field(default="", description="OpenRouter API key")
    openrouter_model: str = "openai/gpt-4o"
    openrouter_referer: str = "https://localhost"
    openrouter_title: str = "Skyview"
    guardrail_model: str = Field(
        default="",
        description="Model for input/output guardrails; empty = same as openrouter_model",
    )
    chat_max_tool_rounds: int = 12

    solana_rpc_url: str = "https://api.devnet.solana.com"

    usdc_mint: str = Field(
        default="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPZmZWeQ5s",
        description="USDC (or test USDC) mint for the active cluster",
    )
    sol_mint: str = "So11111111111111111111111111111111111111112"
    mcp_node_command: str = "node"
    mcp_entry: str = Field(
        default="",
        description="Path to solana-mcp-minimal build/index.js (empty = auto)",
    )

    poll_interval_sec: float = 35.0
    rebalance_drift_threshold: float = 0.05
    trade_slippage_bps: int = 150
    use_coingecko_sol_price: bool = True

    persist_conversations: bool = Field(
        default=True,
        description="Store transcripts locally or in S3; set false to use client-only history",
    )
    use_s3: bool = False
    s3_bucket: str = Field(
        default="",
        description="S3 bucket for session JSON (when use_s3 is true)",
    )
    memory_dir: str = Field(
        default="data/memory",
        description="Directory for session JSON when not using S3 (created if missing)",
    )
    chat_memory_max_messages: int = 30

    cors_origin_regex: str = Field(
        default=(
            r"http://(127\.0\.0\.1|localhost|[0-9.]+):3000"
            r"|https?://[a-z0-9.\-]+\.s3-website-[a-z0-9\-]+\.amazonaws\.com"
            r"|https?://[a-z0-9][a-z0-9-]*\.[a-z0-9-]+\.awsapprunner\.com"
        )
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def refresh_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
