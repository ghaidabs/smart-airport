"""Journal CSV commun à tous les nœuds (P1).

Un fichier par nœud, ``logs/<node>.csv``, au format unique
``ts_ns, node, thread, corr_id, event, detail``. ``tools/timeline.py`` fusionne ces
fichiers pour reconstruire la chronologie.

Conventions pour la colonne ``event`` :

- ``SEND`` / ``RECV`` : écrits par ``common/net.py``, ``detail`` = ``TYPE src->dst id=xxxxxxxx`` ;
- ``SEND_FAIL`` : échec d'envoi après les reconnexions ;
- tout autre nom en majuscules pour la logique métier (``DECISION``, ``GATE_DOWN``...).
"""

from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from typing import Any

from common import config

COLUMNS = ("ts_ns", "node", "thread", "corr_id", "event", "detail")


class CsvLogger:
    """Journal CSV thread-safe, en ajout ; une ligne écrite par appel."""

    def __init__(self, node: str, log_dir: Path | None = None) -> None:
        self.node = node
        directory = Path(log_dir or config.LOG_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{node}.csv"
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        self._file = open(self.path, "a", newline="", encoding="utf-8", buffering=1)
        self._writer = csv.writer(self._file)
        self._lock = threading.Lock()
        if new_file:
            self._writer.writerow(COLUMNS)

    def log(self, event: str, corr_id: str = "", detail: Any = "",
            ts_ns: int | None = None) -> None:
        """Écrit une ligne ; ``detail`` non textuel est sérialisé en JSON."""
        if not isinstance(detail, str):
            detail = json.dumps(detail, separators=(",", ":"), default=str)
        row = (ts_ns if ts_ns is not None else time.time_ns(), self.node,
               threading.current_thread().name, corr_id, event, detail)
        with self._lock:
            if not self._file.closed:
                self._writer.writerow(row)

    def close(self) -> None:
        with self._lock:
            self._file.close()
