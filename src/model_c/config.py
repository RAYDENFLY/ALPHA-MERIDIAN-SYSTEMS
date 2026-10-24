"""Model C — Configuration Specification.

Phase C1: Prospective Collection-Only Pipeline.
This configuration governs the read-only prospective event collector for Pump.fun tokens.
It does NOT contain wallet credentials, private keys, or trading parameters.
"""

from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


class ModelCCollectorConfig(BaseModel):
    """Configuration for the Model C prospective data collector."""

    # Network Endpoints (Read-Only)
    rpc_http_url: str = Field(
        default="https://api.mainnet-beta.solana.com",
        description="Public or dedicated Solana JSON-RPC HTTP endpoint (read-only)",
    )
    rpc_ws_url: str = Field(
        default="wss://api.mainnet-beta.solana.com",
        description="Public or dedicated Solana WebSocket endpoint for logsSubscribe",
    )
    api_key_env_var: str = Field(
        default="SOLANA_RPC_API_KEY",
        description="Environment variable name for optional RPC API key",
    )

    # Storage Settings
    storage_path: str = Field(
        default="data/model_c_research.db",
        description="Local SQLite database path for raw and normalized research events",
    )

    # Collector Execution Constraints
    enabled: bool = Field(
        default=False,
        description="Master enable flag. Safe default is False to prevent unprompted background execution.",
    )
    program_id: str = Field(
        default="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        description="Verified Pump.fun smart contract address on Solana Mainnet",
    )
    commitment: str = Field(
        default="confirmed",
        description="Solana commitment level for logsSubscribe ('confirmed' or 'finalized')",
    )

    # Reconnection & Backoff Policy
    max_reconnect_attempts: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum consecutive reconnect attempts before entering degraded state",
    )
    initial_backoff_seconds: float = Field(
        default=1.0,
        ge=0.001,
        le=10.0,
        description="Initial backoff delay in seconds upon disconnect",
    )
    max_backoff_seconds: float = Field(
        default=60.0,
        ge=0.01,
        le=300.0,
        description="Maximum backoff ceiling in seconds",
    )
    backoff_jitter: bool = Field(
        default=True,
        description="Whether to apply random jitter (+-20%) to backoff intervals to prevent thundering herd",
    )

    # Queue & Backpressure
    queue_max_size: int = Field(
        default=10000,
        ge=100,
        le=100000,
        description="Bounded in-memory queue capacity to prevent memory exhaustion during event spikes",
    )

    # Execution Bounds (Optional for controlled runs and testing)
    max_collection_duration_seconds: Optional[int] = Field(
        default=None,
        description="Optional execution time limit in seconds (useful for test runs)",
    )
    max_event_count: Optional[int] = Field(
        default=None,
        description="Optional maximum number of events to process before graceful shutdown",
    )

    # Sampling Policy
    sampling_policy_version: str = Field(
        default="1.0-universal-discovery",
        description="Active sampling policy identifier",
    )

    def get_effective_ws_url(self) -> str:
        """Construct the WebSocket URL with API key if configured via environment, without logging."""
        import os
        api_key = os.environ.get(self.api_key_env_var) or os.environ.get("HELIUS_API_KEY")
        if not api_key:
            return self.rpc_ws_url

        url = self.rpc_ws_url
        if "api-key=" not in url and "api_key=" not in url:
            separator = "&" if "?" in url else "?"
            return f"{url}{separator}api-key={api_key}"
        return url

    def ensure_storage_dir(self) -> Path:
        """Ensure the local directory containing storage_path exists."""
        p = Path(self.storage_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
