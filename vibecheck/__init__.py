"""vibecheck: static analysis for detecting AI-generated code anti-patterns."""

from vibecheck.models import Config, Finding, RuleMeta, ScanResult, Severity

__version__ = "0.1.0"

__all__ = [
    "Config",
    "Finding",
    "RuleMeta",
    "ScanResult",
    "Severity",
    "__version__",
]
