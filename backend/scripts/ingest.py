"""Convenience wrapper: ``python scripts/ingest.py --input ...``.

Keeps ``backend/`` on ``sys.path`` so the ``app`` package resolves without an
editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
