"""Contract-driven inbox-to-expense processing."""

from .models import (
    AnalysisDecision,
    ExpenseAnalysisInput,
    ExpenseAnalysisOutput,
    InboxMessage,
    Transaction,
)
from .normalization import NormalizationError, normalize_gmail_message
from .processor import InboxProcessor

__all__ = [
    "AnalysisDecision",
    "ExpenseAnalysisInput",
    "ExpenseAnalysisOutput",
    "InboxMessage",
    "InboxProcessor",
    "NormalizationError",
    "Transaction",
    "normalize_gmail_message",
]
