"""Execution and paper trading subsystem for SignalGenerator.
"""

from src.execution.types import PaperOrder, PaperOrderStatus, PaperPosition
from src.execution.events import PaperEvent, PaperEventType, PaperEventDispatcher
from src.execution.paper_engine import PaperExecutionEngine

__all__ = [
    "PaperOrder",
    "PaperOrderStatus",
    "PaperPosition",
    "PaperEvent",
    "PaperEventType",
    "PaperEventDispatcher",
    "PaperExecutionEngine",
]
