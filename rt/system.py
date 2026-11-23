"""Réglages Linux pour se rapprocher du modèle monoprocesseur.

Ces appels n'existent pas sous Windows natif. Ils renvoient alors False, pour
que le code reste importable et testable, mais les mesures du rapport doivent
être faites sous Linux ou WSL2.
"""

from __future__ import annotations

import os


def pin_to_cpu(cpu: int = 0) -> bool:
    """Épingle le processus sur un cœur. Les threads créés ensuite en héritent."""
    if not hasattr(os, "sched_setaffinity"):
        return False
    try:
        os.sched_setaffinity(0, {cpu})
    except OSError:
        return False
    return True


def try_sched_fifo(priority: int = 50) -> bool:
    """Tente de passer en ``SCHED_FIFO``. Échoue sans sudo ou CAP_SYS_NICE."""
    if not hasattr(os, "sched_setscheduler"):
        return False
    try:
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(priority))
    except OSError:
        return False
    return True
