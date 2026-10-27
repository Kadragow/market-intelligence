"""Version of the analysis rules, stored with every run and assessment.

Bump RULES_VERSION whenever code that computes metrics or statuses changes in a way that
could change a stored assessment (price metrics, macro metrics, data-quality checks,
news selection). Together with the configuration hash it identifies which rules produced
a historical record.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from market_intelligence.config import Settings

# DECISION: a hand-maintained version (date-based) rather than the git commit, so it only
# changes when the rules do, and works without a git checkout.
RULES_VERSION = "2026.10.27"

_RULE_SETTINGS = (
    "price_history_days",
    "stale_after_business_days",
    "price_max_gap_business_days",
    "news_lookback_days",
    "news_max_items_per_source",
    "news_max_items_for_llm",
    "macro_history_days",
)
_RULE_FILES = ("instruments.yaml", "macro.yaml", "sources.yaml")


def config_hash(settings: Settings) -> str:
    """SHA-256 of the configuration files and thresholds that shape the analysis."""
    digest = hashlib.sha256()
    for name in _RULE_FILES:
        path: Path = settings.config_dir / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    values = {name: getattr(settings, name) for name in _RULE_SETTINGS}
    digest.update(json.dumps(values, sort_keys=True).encode())
    return digest.hexdigest()
