"""Model B Long vs Short Research Expansion Module.
"""

from src.research.types import (
    EvaluationDecision,
    ResearchVariant,
    ShadowOrderStatus,
    ShadowSignalRecord,
    ShadowVariantEvaluation,
    VARIANT_METADATA,
    VariantPerformanceMetrics,
)
from src.research.rules import VariantRuleEngine
from src.research.storage import ResearchStorage
from src.research.shadow_evaluator import ModelBShadowEvaluator

__all__ = [
    "EvaluationDecision",
    "ResearchVariant",
    "ShadowOrderStatus",
    "ShadowSignalRecord",
    "ShadowVariantEvaluation",
    "VARIANT_METADATA",
    "VariantPerformanceMetrics",
    "VariantRuleEngine",
    "ResearchStorage",
    "ModelBShadowEvaluator",
]
