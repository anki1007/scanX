"""
NSE/BSE Institutional Earnings Intelligence Agent.

A modular, risk-first system that detects earnings beats, scores them across
fundamental, technical, institutional and options dimensions, and turns the
strongest, PEAD-confirmed setups into actionable (analysis-only) trade plans.

Public surface:
    from earnings_intel import Pipeline, DEFAULTS
"""
from __future__ import annotations

from .config import DEFAULTS, Settings
from .pipeline import Pipeline

__all__ = ["Pipeline", "Settings", "DEFAULTS", "__version__"]
__version__ = "0.1.0"
