"""Model C — Pump.fun Research Module.

Phase C1: Prospective Collection-Only Pipeline & Schema Definition.
Strictly non-executable research schema and read-only event ingestion.
Zero trading strategies, zero execution pathways, zero wallet connections.
"""

from src.model_c.config import ModelCCollectorConfig
from src.model_c.event_parser import PumpFunEventParser, ParsedEventResult
from src.model_c.health import ModelCHealthMonitor
from src.model_c.storage import ModelCStorage
from src.model_c.collector import ModelCProspectiveCollector
from src.model_c.types import (
    ModelCLifecycleState,
    DataFeasibilityStatus,
    DataSourceType,
    PumpFunEventType,
    EventParseStatus,
    TokenQualityStatus,
    CollectorHealthStatus,
    PumpFunProgramConstants,
    RawEventRecord,
    NormalizedTokenRecord,
    NormalizedTradeRecord,
    DataGapRecord,
)

__version__ = "0.1.0-c1"

__all__ = [
    "ModelCCollectorConfig",
    "PumpFunEventParser",
    "ParsedEventResult",
    "ModelCHealthMonitor",
    "ModelCStorage",
    "ModelCProspectiveCollector",
    "ModelCLifecycleState",
    "DataFeasibilityStatus",
    "DataSourceType",
    "PumpFunEventType",
    "EventParseStatus",
    "TokenQualityStatus",
    "CollectorHealthStatus",
    "PumpFunProgramConstants",
    "RawEventRecord",
    "NormalizedTokenRecord",
    "NormalizedTradeRecord",
    "DataGapRecord",
]
