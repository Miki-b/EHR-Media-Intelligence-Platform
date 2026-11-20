"""Load data/fixtures into the fixture database so search and the UI have data.

    python scripts/load_fixtures.py          # writes data/fixtures.db
    FIXTURE_MODE=1 python -m app.pipeline index
    FIXTURE_MODE=1 make serve

Fixture patients share ids (p-0001...) with the real pipeline output, so they live
in their own database and vector index, selected by FIXTURE_MODE=1. This script
turns fixture mode on by itself; set FIXTURE_MODE=0 explicitly to override.
"""

import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("FIXTURE_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.fixtures import load_fixtures  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    count = load_fixtures()
    print(f"loaded {count} fixture patients into {settings.db_path}")
    if not settings.fixture_mode:
        print("warning: FIXTURE_MODE is off, fixtures were written to the pipeline database")
    return 0


if __name__ == "__main__":
    sys.exit(main())
