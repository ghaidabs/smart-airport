"""Squelette commun des nœuds (P1).

Un nœud = un :class:`~common.net.NodeServer` (threads réseau qui remplissent
``inbox``) + une boucle de traitement qui lit ``inbox`` et appelle le handler associé
au type du message. Dans les squelettes, un seul thread traite les messages, donc les
handlers n'ont pas besoin de verrou. Quand P3/P5 ajoutent des tâches (threads
périodiques, dispatcher), ce sont eux qui protègent l'état partagé (moniteurs de P4).

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

Handler = Callable[[Message], None]


class BaseNode:
    """Nœud générique : réception, distribution par type, envoi, journal."""

    name: str = ""

    def __init__(self, inbox: Inbox | None = None, port: int | None = None) -> None:
        assert self.name, "définir l'attribut de classe name"
        self.log = CsvLogger(self.name)
        self.sender = Sender(self.log)
        self.server = NodeServer(self.name, self.log, inbox, port=port)
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
        self.server.start()
        self.setup()
        print(f"[{self.name}] prêt sur le port {self.server.port}", flush=True)
        try:
            while not self._stop.is_set():
                msg = self._next(timeout=0.2)
                if msg is not None:
                    self.dispatch(msg)
        finally:
            self.teardown()
            self.server.stop()
            self.sender.close()
            self.log.log("STOP")
            self.log.close()
