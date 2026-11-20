"""Load data/fixtures into the SQLite store so search and the UI have data.

    python scripts/load_fixtures.py

Use this until Dev A's pipeline stages produce real bundles and summaries.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fixtures import load_fixtures  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    count = load_fixtures()
    print(f"loaded {count} fixture patients")
    return 0


if __name__ == "__main__":
    sys.exit(main())
