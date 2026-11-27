"""Tampon d'événements borné producteur-consommateur (partie P4).

Le thread réseau (producteur) dépose les messages reçus, les threads de
tâches (consommateurs) les retirent.

Primitives utilisées :
    * ``_slots``  : sémaphore compteur = nombre de places LIBRES ;
    * ``_items``  : sémaphore compteur = nombre de places OCCUPÉES ;
    * ``_lock``   : mutex qui protège la file interne (``deque``).

Les sémaphores gèrent l'attente sans boucle active ; le mutex garantit
qu'un seul thread modifie la file à la fois.

Politique de saturation (tampon plein) :
    * message critique   -> remplace le plus ancien message NON critique ;
    * message non critique -> attend au plus ``timeout`` puis est rejeté ;
    * si tout le tampon est critique, un message critique attend aussi.

Règle de conception : aucune entrée-sortie ni appel réseau sous verrou.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Optional

# Types de messages (annexe A) considérés comme critiques.
CRITICAL_TYPES = frozenset({"SECURITY_ALERT", "GATE_REQUEST", "GATE_UNAVAILABLE"})


def default_is_critical(msg: Any) -> bool:
    """Un message est critique si son ``type`` est dans CRITICAL_TYPES.

    Accepte un objet avec attribut ``type`` (classe Message de P1) ou un dict.
    """
    mtype = msg.get("type") if isinstance(msg, dict) else getattr(msg, "type", None)
    return mtype in CRITICAL_TYPES


class EventBuffer:
    """Tampon FIFO borné, thread-safe, avec rejet des non-critiques d'abord."""

    def __init__(
        self,
        capacity: int = 64,
        is_critical: Callable[[Any], bool] = default_is_critical,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity doit être >= 1")
        self._capacity = capacity
        self._is_critical = is_critical
        self._queue: deque[tuple[Any, bool]] = deque()
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(capacity)  # places libres
        self._items = threading.Semaphore(0)         # places occupées
        # Compteurs (protégés par _lock) : servent au test de charge x2.
        self._accepted = 0
        self._rejected = 0
        self._evicted = 0
        self._consumed = 0
        self._high_water = 0

    # ------------------------------------------------------------------
    @staticmethod
    def _acquire(sem: threading.Semaphore, timeout: Optional[float]) -> bool:
        """acquire() avec ``timeout`` : None = bloquant, 0 = sans attente."""
        if timeout is None:
            return sem.acquire()
        if timeout <= 0:
            return sem.acquire(blocking=False)
        return sem.acquire(timeout=timeout)

    def _append(self, msg: Any, critical: bool) -> None:
        """Ajoute à la file. À appeler sous _lock."""
        self._queue.append((msg, critical))
        self._accepted += 1
        self._high_water = max(self._high_water, len(self._queue))

    # ------------------------------------------------------------------
    def put(self, msg: Any, timeout: Optional[float] = None) -> bool:
        """Dépose ``msg``. Retourne True si accepté, False si rejeté.

        ``timeout`` : None = attend indéfiniment une place, 0 = ne bloque
        jamais (recommandé pour le thread réseau), sinon durée max en s.
        """
        critical = self._is_critical(msg)
        deadline = None if timeout is None else time.monotonic() + timeout

        # 1) Chemin rapide : une place est libre.
        if self._slots.acquire(blocking=False):
            with self._lock:
                self._append(msg, critical)
            self._items.release()
            return True

        # 2) Tampon plein : un critique prend la place du plus ancien
        #    non-critique. Le nombre d'éléments ne change pas, donc les
        #    sémaphores restent justes.
        if critical:
            with self._lock:
                for i, (_, is_crit) in enumerate(self._queue):
                    if not is_crit:
                        del self._queue[i]
                        self._evicted += 1
                        self._append(msg, critical)
                        return True

        # 3) On attend une place (au plus ``timeout``).
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if self._acquire(self._slots, remaining):
            with self._lock:
                self._append(msg, critical)
            self._items.release()
            return True

        with self._lock:
            self._rejected += 1
        return False

    def get(self, timeout: Optional[float] = None) -> Any:
        """Retire le plus ancien message ; None si rien avant ``timeout``."""
        if not self._acquire(self._items, timeout):
            return None
        with self._lock:
            msg, _ = self._queue.popleft()
            self._consumed += 1
        self._slots.release()
        return msg

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)

    def stats(self) -> dict[str, int]:
        """Compteurs pour les expériences (charge x2) et les tests."""
        with self._lock:
            return {
                "capacity": self._capacity,
                "size": len(self._queue),
                "accepted": self._accepted,
                "rejected": self._rejected,
                "evicted": self._evicted,
                "consumed": self._consumed,
                "high_water": self._high_water,
            }