"""Squelette commun des nœuds (P1).

Un nœud = un :class:`~common.net.NodeServer` (threads réseau qui remplissent
``inbox``) + une boucle de traitement qui lit ``inbox`` et appelle le handler associé
au type du message. Un seul thread exécute les handlers. Les tâches temps réel
(``self.rt``, voir :mod:`rt.runtime`) tournent dans leurs propres threads : l'état
qu'elles partagent avec les handlers passe par des objets protégés (``FlightTable``,
``QueueMonitor``, puis les moniteurs de P4).

Pour brancher sa logique : sous-classer ``BaseNode`` et enregistrer des handlers avec
``self.on(MessageType.X, fonction)`` dans ``setup()``.
"""

from __future__ import annotations

import queue
import signal
import threading
import traceback
from typing import Any, Callable

from common.logger import CsvLogger
from common.net import Inbox, NodeServer, Sender
from common.protocol import Message
from rt.runtime import NodeRuntime

Handler = Callable[[Message], None]


class BaseNode:
    """Nœud générique : réception, distribution par type, envoi, journal."""

    name: str = ""

    def __init__(self, inbox: Inbox | None = None, port: int | None = None) -> None:
        assert self.name, "définir l'attribut de classe name"
        self.log = CsvLogger(self.name)
        self.sender = Sender(self.log)
        self.server = NodeServer(self.name, self.log, inbox, port=port)
        self.rt = NodeRuntime(self.name, self.log)
        self._handlers: dict[str, Handler] = {}
        self._stop = threading.Event()
        # Les DECISION diffusées sont déjà journalisées (RECV) ; rien d'autre par défaut.
        self.on("DECISION", lambda msg: None)

    # --- À surcharger ---------------------------------------------------

    def setup(self) -> None:
        """Enregistre les handlers et démarre les tâches propres au nœud."""

    def teardown(self) -> None:
        """Arrête les tâches propres au nœud."""

    # --- Outils pour les handlers --------------------------------------

    def on(self, msg_type: str, handler: Handler) -> None:
        self._handlers[msg_type] = handler

    def send(self, dest: str, msg: Message) -> bool:
        return self.sender.send(dest, msg)

    def reply(self, cause: Message, msg_type: str, dest: str,
              payload: dict[str, Any]) -> bool:
        """Envoie un message de la même chaîne (corr_id et t0_ns recopiés)."""
        return self.send(dest, cause.derive(msg_type, self.name, dest, payload))

    def broadcast(self, cause: Message, msg_type: str, payload: dict[str, Any]) -> None:
        self.sender.broadcast(cause.derive(msg_type, self.name, self.name, payload))

    # --- Boucle principale ---------------------------------------------

    def _next(self, timeout: float) -> Message | None:
        try:
            return self.server.inbox.get(timeout=timeout)  # type: ignore[attr-defined]
        except queue.Empty:
            return None  # Queue ; l'EventBuffer de P4 renvoie None au délai

    def dispatch(self, msg: Message) -> None:
        handler = self._handlers.get(msg.type)
        if handler is None:
            self.log.log("UNHANDLED", msg.corr_id, msg.short())
            return
        try:
            handler(msg)
        except Exception:  # un handler fautif ne doit pas tuer le nœud
            self.log.log("HANDLER_ERROR", msg.corr_id,
                         f"{msg.short()} {traceback.format_exc(limit=3)}")

    def stop(self, *_: Any) -> None:
        self._stop.set()

    def run(self) -> None:
        """Démarre le serveur et traite les messages jusqu'à SIGINT/SIGTERM."""
        threading.current_thread().name = f"{self.name}-main"
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        try:
            # Le port n'ouvre qu'une fois le nœud prêt (modèle IA chargé, tâches lancées) :
            # wait_for_nodes ne peut donc pas annoncer un nœud encore en initialisation.
            self.setup()
            self.rt.start()
            self.server.start()
            print(f"[{self.name}] prêt sur le port {self.server.port}", flush=True)
            while not self._stop.is_set():
                msg = self._next(timeout=0.2)
                if msg is not None:
                    self.dispatch(msg)
        finally:
            self.rt.stop()
            self.teardown()
            self.server.stop()
            self.sender.close()
            self.log.log("STOP")
            self.log.close()
