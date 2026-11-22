"""Communication TCP entre les nœuds (P1).

- :class:`NodeServer` : un thread d'acceptation, puis un thread de réception par
  connexion. Chaque message reçu est déposé dans ``inbox`` : une ``queue.Queue`` par
  défaut, remplaçable par l'``EventBuffer`` de P4 (même méthode ``put``).
- :class:`Sender` / :func:`send` : connexions persistantes (une par destination),
  reconnexion automatique, ``TCP_NODELAY`` (désactive l'algorithme de Nagle, qui
  retarderait les petits messages pour les regrouper).

Limite connue : si le pair meurt, le premier ``sendall`` peut encore réussir (les
octets partent dans le tampon du noyau) et le message est perdu ; l'erreur n'apparaît
qu'à l'envoi suivant, qui déclenche alors la reconnexion.
"""

from __future__ import annotations

import queue
import socket
import sys
import threading
import time
from typing import Any, Protocol

from common import config
from common.logger import CsvLogger
from common.protocol import Message, ProtocolError, read_message


class Inbox(Protocol):
    """Ce que NodeServer attend de son tampon (Queue ou EventBuffer de P4)."""

    def put(self, item: Any, *args: Any, **kwargs: Any) -> Any: ...


def _tune(sock: socket.socket) -> None:
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class NodeServer:
    """Serveur TCP d'un nœud : reçoit les trames et les dépose dans ``inbox``."""

    def __init__(self, name: str, logger: CsvLogger, inbox: Inbox | None = None,
                 host: str | None = None, port: int | None = None) -> None:
        self.name = name
        self.log = logger
        self.inbox: Inbox = inbox if inbox is not None else queue.Queue()
        self._host = host or config.HOST
        self._port = config.PORTS[name] if port is None else port
        self._sock: socket.socket | None = None
        self._conns: set[socket.socket] = set()
        self._conns_lock = threading.Lock()
        self._stopping = threading.Event()

    @property
    def port(self) -> int:
        """Port réellement utilisé (utile avec port=0 dans les tests)."""
        assert self._sock is not None, "serveur non démarré"
        return self._sock.getsockname()[1]

    def start(self) -> "NodeServer":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._port))
        sock.listen()
        self._sock = sock
        threading.Thread(target=self._accept_loop, name=f"{self.name}-accept",
                         daemon=True).start()
        self.log.log("LISTEN", detail=f"{self._host}:{self.port}")
        return self

    def stop(self) -> None:
        self._stopping.set()
        if self._sock is not None:
            self._sock.close()
        with self._conns_lock:
            for conn in self._conns:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
            self._conns.clear()

    def _accept_loop(self) -> None:
        assert self._sock is not None
        n = 0
        while not self._stopping.is_set():
            try:
                conn, peer = self._sock.accept()
            except OSError:
                break  # socket fermée par stop()
            _tune(conn)
            n += 1
            with self._conns_lock:
                self._conns.add(conn)
            threading.Thread(target=self._recv_loop, args=(conn, peer),
                             name=f"{self.name}-recv{n}", daemon=True).start()

    def _recv_loop(self, conn: socket.socket, peer: tuple) -> None:
        try:
            while True:
                try:
                    msg = read_message(conn)
                except ProtocolError as exc:
                    self.log.log("PROTOCOL_ERROR", detail=f"{peer}: {exc}")
                    break
                except OSError:
                    break
                if msg is None:
                    break
                self.log.log("RECV", msg.corr_id, msg.short())
                if self.inbox.put(msg) is False:  # EventBuffer : rejet
                    self.log.log("DROP", msg.corr_id, msg.short())
        finally:
            with self._conns_lock:
                self._conns.discard(conn)
            conn.close()


class Sender:
    """Envoi vers les nœuds, une connexion persistante par destination."""

    def __init__(self, logger: CsvLogger,
                 addresses: dict[str, tuple[str, int]] | None = None) -> None:
        self.log = logger
        self._addresses = addresses
        self._socks: dict[str, socket.socket] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _address(self, dest: str) -> tuple[str, int]:
        if self._addresses is not None:
            return self._addresses[dest]
        return config.address(dest)

    def _lock_for(self, dest: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(dest, threading.Lock())

    def _connect(self, dest: str) -> socket.socket:
        sock = socket.create_connection(self._address(dest),
                                        timeout=config.CONNECT_TIMEOUT_S)
        sock.settimeout(None)
        _tune(sock)
        return sock

    def send(self, dest: str, msg: Message) -> bool:
        """Envoie ``msg`` à ``dest`` ; True si la trame est partie.

        Le verrou par destination garantit que deux threads n'entrelacent pas leurs
        trames sur la même connexion. ``ts_ns`` est fixé juste avant l'envoi.
        """
        msg.dest = dest
        with self._lock_for(dest):
            last_error: Exception | None = None
            for attempt in range(config.SEND_RETRIES):
                try:
                    sock = self._socks.get(dest)
                    if sock is None:
                        sock = self._socks[dest] = self._connect(dest)
                    msg.stamp()
                    sock.sendall(msg.encode())
                    self.log.log("SEND", msg.corr_id, msg.short(), ts_ns=msg.ts_ns)
                    return True
                except OSError as exc:
                    last_error = exc
                    bad = self._socks.pop(dest, None)
                    if bad is not None:
                        bad.close()
                    time.sleep(config.RETRY_BACKOFF_S * (attempt + 1))
            self.log.log("SEND_FAIL", msg.corr_id, f"{msg.short()} {last_error}")
            return False

    def broadcast(self, msg: Message, dests: tuple[str, ...] | None = None) -> None:
        """Diffusion (message DECISION) : une copie par nœud, sauf l'émetteur."""
        for dest in dests or config.NODES:
            if dest != msg.source:
                copy = Message(type=msg.type, source=msg.source, dest=dest,
                               payload=msg.payload, corr_id=msg.corr_id)
                self.send(dest, copy)

    def close(self) -> None:
        for dest in list(self._socks):
            with self._lock_for(dest):
                sock = self._socks.pop(dest, None)
                if sock is not None:
                    sock.close()


_default_senders: dict[str, Sender] = {}
_default_lock = threading.Lock()


def send(dest: str, msg: Message) -> bool:
    """Raccourci de l'annexe : un Sender par émetteur, créé à la demande."""
    with _default_lock:
        sender = _default_senders.get(msg.source)
        if sender is None:
            sender = _default_senders[msg.source] = Sender(CsvLogger(msg.source))
    return sender.send(dest, msg)


def wait_for_nodes(nodes: tuple[str, ...] = config.NODES, timeout: float = 10.0) -> bool:
    """Attend que tous les nœuds écoutent (utilisé par run_all.sh)."""
    deadline = time.monotonic() + timeout
    pending = list(nodes)
    while pending and time.monotonic() < deadline:
        node = pending[0]
        try:
            with socket.create_connection(config.address(node), timeout=0.2):
                pending.pop(0)
        except OSError:
            time.sleep(0.1)
    return not pending


if __name__ == "__main__":
    ok = wait_for_nodes(timeout=float(sys.argv[1]) if len(sys.argv) > 1 else 10.0)
    print("nœuds prêts" if ok else "nœuds absents", flush=True)
    sys.exit(0 if ok else 1)
