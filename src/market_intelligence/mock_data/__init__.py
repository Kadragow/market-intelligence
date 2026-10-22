"""SYNTHETIC data for `mi weekly --mock`. Not real market data or news.

DECISION: shipped inside the package (not tests/fixtures) so that --mock does not depend
on the test tree. Every price series starts at 100 to make it obviously synthetic.
"""

from pathlib import Path

MOCK_DATA_DIR = Path(__file__).resolve().parent
MOCK_PRICES_DIR = MOCK_DATA_DIR / "prices"
MOCK_RSS_DIR = MOCK_DATA_DIR / "rss"
MOCK_SOURCES_FILE = MOCK_DATA_DIR / "sources.yaml"
