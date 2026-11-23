"""Rend le dépôt importable depuis tests/ (``from common import ...``)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
