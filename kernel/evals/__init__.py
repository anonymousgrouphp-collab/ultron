"""kernel/evals — Phase Q1: eval harness maturity.

Provides enhanced eval capabilities:
1. Per-task regression detection
2. Model comparison reporting
3. Performance metrics tracking
"""

from kernel.evals.comparison import ModelComparator

__all__ = ["ModelComparator"]
