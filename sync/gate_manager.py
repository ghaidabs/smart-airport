"""Gestionnaire de portes (partie P4) : sémaphore compteur + mutex.

* ``_free_gates`` (sémaphore compteur) : nombre de jetons = nombre de portes
  libres. Il permet d'ATTENDRE une porte avec un délai maximal, sans boucle active.
* ``_lock`` (mutex) : protège l'état des portes. Chercher la porte la plus proche
  puis la marquer occupée est un « check-then-act » : sans verrou, deux threads
  pourraient choisir la même porte.

Le sémaphore compte, mais ne dit pas QUELLE porte est libre ; le mutex protège
l'état, mais ne sait pas attendre. Il faut donc les deux.

Invariant : (jetons du sémaphore) = (portes FREE) - (jetons en cours de prise)
- ``_cancel``. ``_cancel`` compense le cas rare où une porte libre devient
indisponible alors que son jeton vient d'être pris par un autre thread.

Règle de conception : sections critiques courtes, aucune E/S ni réseau sous verrou.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from common import config

FREE = "FREE"
OCCUPIED = "OCCUPIED"
UNAVAILABLE = "UNAVAILABLE"


class GateManager:
    """Moniteur thread-safe des portes (libre, occupée, indisponible)."""

    def __init__(
        self,
        gates: tuple[str, ...] = config.GATES,
        distance: Callable[[str, str], float] = config.gate_distance,
        occupied: Optional[dict[str, str]] = None,
    ) -> None:
        """``occupied`` : portes déjà occupées au départ ``{porte: vol}``."""
        occupied = occupied or {}
        self._distance = distance
        self._state = {g: (OCCUPIED if g in occupied else FREE) for g in gates}
        self._flight: dict[str, Optional[str]] = {g: occupied.get(g) for g in gates}
        self._lock = threading.Lock()
        self._cancel = 0  # jetons à avaler (voir l'invariant plus haut)
        self._free_gates = threading.Semaphore(
            sum(1 for s in self._state.values() if s == FREE))

    # ------------------------------------------------------------------
    @staticmethod
    def _acquire(sem: threading.Semaphore, timeout: Optional[float]) -> bool:
        if timeout is None:
            return sem.acquire()
        if timeout <= 0:
            return sem.acquire(blocking=False)
        return sem.acquire(timeout=timeout)

    def reserve(self, flight: str, near_gate: Optional[str] = None,
                timeout: float = 0.1) -> Optional[str]:
        """Réserve de façon atomique la porte libre la plus proche de ``near_gate``.

        Retourne la porte, ou None si aucune n'est libre avant ``timeout``
        (None = attendre sans limite, 0 = ne pas attendre).
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not self._acquire(self._free_gates, remaining):
                return None
            with self._lock:
                if self._cancel > 0:      # ce jeton correspond à une porte retirée
                    self._cancel -= 1
                    continue
                free = [g for g, s in self._state.items() if s == FREE]
                if not free:              # impossible si l'invariant tient
                    raise RuntimeError("jeton obtenu mais aucune porte libre")
                if near_gate is None:
                    gate = min(free)
                else:                     # égalité : ordre alphabétique (déterministe)
                    gate = min(free, key=lambda g: (self._distance(g, near_gate), g))
                self._state[gate] = OCCUPIED
                self._flight[gate] = flight
                return gate

    def release(self, gate: str) -> bool:
        """Libère une porte occupée. False si elle n'était pas occupée."""
        with self._lock:
            if self._state.get(gate) != OCCUPIED:
                return False
            self._state[gate] = FREE
            self._flight[gate] = None
        # L'état change AVANT le jeton : un réserveur ne peut pas obtenir un
        # jeton sans qu'une porte soit réellement libre.
        self._free_gates.release()
        return True

    def set_unavailable(self, gate: str) -> Optional[str]:
        """Marque la porte indisponible. Renvoie le vol à réaffecter, s'il y en a un."""
        with self._lock:
            state = self._state.get(gate)
            if state is None or state == UNAVAILABLE:
                return None
            self._state[gate] = UNAVAILABLE
            if state == FREE:
                # La porte libre disparaît : on retire un jeton (sans attendre).
                if not self._free_gates.acquire(blocking=False):
                    self._cancel += 1  # jeton déjà pris par un réserveur en cours
                return None
            flight, self._flight[gate] = self._flight[gate], None
            return flight  # porte occupée : son jeton avait déjà été pris

    def set_available(self, gate: str) -> bool:
        """Remet en service une porte indisponible. False si elle ne l'était pas."""
        with self._lock:
            if self._state.get(gate) != UNAVAILABLE:
                return False
            self._state[gate] = FREE
            give_token = self._cancel == 0
            if not give_token:
                self._cancel -= 1
        if give_token:
            self._free_gates.release()
        return True

    def snapshot(self) -> dict[str, dict]:
        """Copie cohérente de l'état : ``{porte: {"state", "flight"}}``."""
        with self._lock:
            return {g: {"state": s, "flight": self._flight[g]}
                    for g, s in self._state.items()}