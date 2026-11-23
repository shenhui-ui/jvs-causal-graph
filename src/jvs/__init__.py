"""JVS: Temporal-Causal Knowledge Graph & Cross-Session Causal QA."""

from jvs.application.engine import CausalEngine
from jvs.config import JVSSettings
from jvs.domain.models import CausalEdge, Event, QAAnswer, Question

__version__ = "2.0.0"

__all__ = [
    "CausalEdge",
    "CausalEngine",
    "Event",
    "JVSSettings",
    "QAAnswer",
    "Question",
    "__version__",
]
