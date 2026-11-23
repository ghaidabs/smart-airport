"""Réglages Linux pour se rapprocher du modèle monoprocesseur.

Ces appels n'existent pas sous Windows natif. Ils renvoient alors False, pour
que le code reste importable et testable, mais les mesures du rapport doivent
être faites sous Linux ou WSL2.
"""

from __future__ import annotations

import logging
import os
import sys

LOGGER = logging.getLogger(__name__)
RT_FIFO_ENV = "RT_FIFO"
# Deadline monotonic. T1 et T4 ont D = 100 ms : T1, très court, passe devant.
RT_PRIORITIES = {"T5": 80, "T1": 70, "T4": 60, "T3": 50, "T2": 40}
# Le GIL ignore les priorités : on réduit l'attente d'une tâche prioritaire (5 ms par défaut).
GIL_SWITCH_INTERVAL_S = 0.0005


def pin_to_cpu(cpu: int = 0) -> bool:
    """Épingle le processus sur un cœur. Les threads créés ensuite en héritent."""
    if not hasattr(os, "sched_setaffinity"):
        return False
    try:
        os.sched_setaffinity(0, {cpu})
    except OSError:
        return False
    return True


def enable_fifo() -> bool:
    """Vérifie que les priorités sont autorisées, puis active ``RT_FIFO``.

    Le thread appelant revient en politique normale : seules les tâches sont en FIFO.
    """
    try:
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(max(RT_PRIORITIES.values())))
        os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    except (AttributeError, OSError):
        return False
    os.environ[RT_FIFO_ENV] = "1"
    return True


def apply_task_priority(task_id: str) -> bool:
    """Place le thread courant en ``SCHED_FIFO`` si ``RT_FIFO=1``."""
    priority = RT_PRIORITIES.get(task_id)
    if priority is None or os.environ.get(RT_FIFO_ENV) != "1":
        return False
    try:
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(priority))
    except (AttributeError, OSError) as exc:
        LOGGER.warning("SCHED_FIFO refusé pour %s : %s", task_id, exc)
        return False
    sys.setswitchinterval(GIL_SWITCH_INTERVAL_S)
    return True
