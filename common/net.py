"""Communication TCP entre les nœuds (P1).

- :class:`NodeServer` : un thread d'acceptation, puis un thread de réception par
  connexion. Chaque message reçu est déposé dans ``inbox`` : une ``queue.Queue`` par
  défaut, remplaçable par l'``EventBuffer`` de P4 (même méthode ``put``).
- :class:`Sender` / :func:`send` : connexions persistantes (une par destination),
  reconnexion automatique, ``TCP_NODELAY`` (désactive l'algorithme de Nagle, qui
  retarderait les petits messages pour les regrouper).

Chemin d'un message ::

    nœud A : Sender.send -> verrou de la destination -> ts_ns -> sendall -> SEND
        ~~~ TCP (localhost) ~~~
    nœud B : thread de réception -> read_message -> RECV -> inbox.put
          -> boucle principale du nœud -> handler

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
    """Ce que NodeServer attend de son tampon (Queue ou EventBuffer de P4).

    C'est un « Protocol » (typage structurel) : tout objet qui a une méthode ``put``
    convient, sans héritage. P4 pourra donc passer son ``EventBuffer`` à la place de
    la ``queue.Queue`` sans modifier ce fichier.
    """

    def put(self, item: Any, *args: Any, **kwargs: Any) -> Any: ...


def _tune(sock: socket.socket) -> None:
    """Règle une socket connectée pour la faible latence.

    ``TCP_NODELAY`` désactive l'algorithme de Nagle : sans cette option, TCP retient
    les petits paquets quelques millisecondes pour les regrouper. Nos messages sont
    petits et nos deadlines en ms, donc on les envoie immédiatement.
    """
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class NodeServer:
    """Serveur TCP d'un nœud : reçoit les trames et les dépose dans ``inbox``.

    Threads créés ::

        accept thread  : attend les nouvelles connexions (une seule instance)
        recv thread n  : un par connexion entrante, lit les messages et les dépose

    Un thread par connexion : ``recv()`` est bloquant, ainsi un émetteur lent ne
    retarde jamais les messages des autres.
    """

    def __init__(self, name: str, logger: CsvLogger, inbox: Inbox | None = None,
                 host: str | None = None, port: int | None = None) -> None:
        """Prépare le serveur sans l'ouvrir (voir :meth:`start`).

        :param name: nom du nœud (``flight``, ``ai``...), qui donne le port par défaut.
        :param logger: journal CSV du nœud (lignes ``RECV``, ``DROP``...).
        :param inbox: tampon de réception ; une ``queue.Queue`` si absent.
        :param port: port explicite ; ``0`` laisse l'OS choisir (utilisé par les tests).
        """
        self.name = name
        self.log = logger
        self.inbox: Inbox = inbox if inbox is not None else queue.Queue()
        self._host = host or config.HOST
        self._port = config.PORTS[name] if port is None else port
        self._sock: socket.socket | None = None          # socket d'écoute
        self._conns: set[socket.socket] = set()          # connexions ouvertes
        self._conns_lock = threading.Lock()              # protège _conns (plusieurs threads)
        self._stopping = threading.Event()               # demandé par stop()

    @property
    def port(self) -> int:
        """Port réellement utilisé (utile avec port=0 dans les tests)."""
        assert self._sock is not None, "serveur non démarré"
        return self._sock.getsockname()[1]

    def start(self) -> "NodeServer":
        """Ouvre le port d'écoute et lance le thread d'acceptation.

        Étapes : socket TCP -> option de réutilisation d'adresse -> bind -> listen ->
        thread d'acceptation (daemon : il s'arrête avec le processus).
        Renvoie ``self`` pour pouvoir écrire ``NodeServer(...).start()``.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Permet de relancer le nœud tout de suite sur le même port.
        # Sous Windows, SO_REUSEADDR laisserait un autre processus voler le port :
        # on utilise donc SO_EXCLUSIVEADDRUSE.
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
        """Arrête le serveur.

        Couper puis fermer la socket d'écoute débloque ``accept()`` (fin du thread d'acceptation) ;
        fermer chaque connexion débloque ``recv()`` (fin des threads de réception).
        """
        self._stopping.set()
        if self._sock is not None:
            try:
                # Sous Linux, close() seul ne réveille pas un accept() bloqué : le port
                # resterait occupé et le nœud ne pourrait pas redémarrer dessus.
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # déjà fermée, ou non supporté sur une socket d'écoute (Windows)
            self._sock.close()
        with self._conns_lock:
            for conn in self._conns:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # déjà fermée par le pair
                conn.close()
            self._conns.clear()

    def _accept_loop(self) -> None:
        """Thread d'acceptation : un thread de réception par nouvelle connexion.

        Les threads s'appellent ``<nœud>-recv1``, ``<nœud>-recv2``... : ce sont les
        noms de la colonne ``thread`` du journal.
        """
        assert self._sock is not None
        n = 0
        while not self._stopping.is_set():
            try:
                conn, peer = self._sock.accept()  # bloque jusqu'à une connexion
            except OSError:
                break  # socket fermée par stop()
            _tune(conn)
            n += 1
            with self._conns_lock:
                self._conns.add(conn)
            threading.Thread(target=self._recv_loop, args=(conn, peer),
                             name=f"{self.name}-recv{n}", daemon=True).start()

    def _recv_loop(self, conn: socket.socket, peer: tuple) -> None:
        """Thread de réception : lit les messages d'une connexion jusqu'à sa fermeture.

        Pour chaque message : ligne ``RECV`` (heure d'arrivée, utilisée par
        ``timeline.py`` pour la latence réseau), puis dépôt dans ``inbox``.
        Le traitement métier n'a pas lieu ici mais dans la boucle principale du nœud :
        ce thread reste libre de lire le message suivant.
        """
        try:
            while True:
                try:
                    # Découpage (framing) : 4 octets de longueur, puis le JSON.
                    msg = read_message(conn)
                except ProtocolError as exc:
                    # Trame corrompue : on ne peut plus se resynchroniser sur le
                    # flux, on ferme la connexion (l'émetteur se reconnectera).
                    self.log.log("PROTOCOL_ERROR", detail=f"{peer}: {exc}")
                    break
                except OSError:
                    break  # connexion coupée (ou stop())
                if msg is None:
                    break  # le pair a fermé proprement la connexion
                self.log.log("RECV", msg.corr_id, msg.short())
                # queue.Queue.put renvoie None ; l'EventBuffer de P4 renvoie False
                # quand il rejette un message (tampon plein, politique de rejet).
                if self.inbox.put(msg) is False:
                    self.log.log("DROP", msg.corr_id, msg.short())
        finally:
            with self._conns_lock:
                self._conns.discard(conn)
            conn.close()


class Sender:
    """Envoi vers les nœuds, une connexion persistante par destination.

    On ouvre la connexion TCP au premier envoi vers une destination, puis on la
    réutilise : la poignée de main TCP (SYN, SYN-ACK, ACK) n'est payée qu'une fois,
    et non à chaque message.
    """

    def __init__(self, logger: CsvLogger,
                 addresses: dict[str, tuple[str, int]] | None = None) -> None:
        """
        :param logger: journal CSV de l'émetteur (lignes ``SEND``, ``SEND_FAIL``).
        :param addresses: adresses explicites ``{dest: (hôte, port)}`` ; par défaut
            celles de ``config.PORTS`` (les tests passent un port libre).
        """
        self.log = logger
        self._addresses = addresses
        self._socks: dict[str, socket.socket] = {}       # dest -> socket déjà connectée
        self._locks: dict[str, threading.Lock] = {}      # dest -> verrou d'écriture
        self._locks_guard = threading.Lock()             # protège la création des verrous

    def _address(self, dest: str) -> tuple[str, int]:
        """Adresse (hôte, port) d'une destination."""
        if self._addresses is not None:
            return self._addresses[dest]
        return config.address(dest)

    def _lock_for(self, dest: str) -> threading.Lock:
        """Verrou d'une destination, créé au premier usage.

        La création est elle-même protégée : sans ``_locks_guard``, deux threads
        pourraient créer chacun un verrou différent pour la même destination
        (« check-then-act »), et le verrou ne protégerait plus rien.
        """
        with self._locks_guard:
            return self._locks.setdefault(dest, threading.Lock())

    def _connect(self, dest: str) -> socket.socket:
        """Ouvre une connexion TCP vers ``dest``.

        Délai de connexion de 1 s (un nœud absent ne bloque pas longtemps), puis mode
        bloquant sans délai pour les envois, et ``TCP_NODELAY``.
        """
        sock = socket.create_connection(self._address(dest),
                                        timeout=config.CONNECT_TIMEOUT_S)
        sock.settimeout(None)
        _tune(sock)
        return sock

    def send(self, dest: str, msg: Message) -> bool:
        """Envoie ``msg`` à ``dest`` ; True si la trame est partie.

        Le verrou par destination garantit que deux threads n'entrelacent pas leurs
        trames sur la même connexion. ``ts_ns`` est fixé juste avant l'envoi.

        En cas d'erreur réseau, la socket est jetée et l'envoi est retenté sur une
        nouvelle connexion (reconnexion automatique), au plus ``SEND_RETRIES`` fois.
        Après le dernier échec : ligne ``SEND_FAIL`` et ``False``, sans exception, pour
        qu'un nœud arrêté ne fasse pas tomber les autres.
        """
        msg.dest = dest
        with self._lock_for(dest):
            last_error: Exception | None = None
            for attempt in range(config.SEND_RETRIES):
                try:
                    sock = self._socks.get(dest)
                    if sock is None:  # première fois, ou après une erreur
                        sock = self._socks[dest] = self._connect(dest)
                    # Horodatage au dernier moment : n'inclut ni l'attente du verrou
                    # ni la connexion.
                    msg.stamp()
                    sock.sendall(msg.encode())  # trame complète : longueur + JSON
                    self.log.log("SEND", msg.corr_id, msg.short(), ts_ns=msg.ts_ns)
                    return True
                except OSError as exc:
                    last_error = exc
                    bad = self._socks.pop(dest, None)
                    if bad is not None:
                        bad.close()
                    # Attente croissante avant de retenter : 50, 100, 150 ms.
                    time.sleep(config.RETRY_BACKOFF_S * (attempt + 1))
            self.log.log("SEND_FAIL", msg.corr_id, f"{msg.short()} {last_error}")
            return False

    def broadcast(self, msg: Message, dests: tuple[str, ...] | None = None) -> None:
        """Diffusion (message DECISION) : une copie par nœud, sauf l'émetteur.

        Chaque copie a son propre ``id`` et une vraie destination, ce qui garde le
        format de l'annexe A (pas de destination « tous »). ``corr_id`` est conservé.
        """
        for dest in dests or config.NODES:
            if dest != msg.source:
                copy = Message(type=msg.type, source=msg.source, dest=dest,
                               payload=msg.payload, corr_id=msg.corr_id)
                self.send(dest, copy)

    def close(self) -> None:
        """Ferme toutes les connexions.

        On prend le verrou de chaque destination pour ne pas couper un envoi en cours.
        """
        for dest in list(self._socks):
            with self._lock_for(dest):
                sock = self._socks.pop(dest, None)
                if sock is not None:
                    sock.close()


# Un Sender par émetteur pour le raccourci send() ci-dessous.
_default_senders: dict[str, Sender] = {}
_default_lock = threading.Lock()


def send(dest: str, msg: Message) -> bool:
    """Raccourci de l'annexe : un Sender par émetteur, créé à la demande.

    L'émetteur est ``msg.source`` : chacun écrit donc dans son propre journal
    (``logs/<source>.csv``). Les nœuds utilisent plutôt ``self.send`` de ``BaseNode``.
    """
    with _default_lock:
        sender = _default_senders.get(msg.source)
        if sender is None:
            sender = _default_senders[msg.source] = Sender(CsvLogger(msg.source))
    return sender.send(dest, msg)


def wait_for_nodes(nodes: tuple[str, ...] = config.NODES, timeout: float = 10.0) -> bool:
    """Attend que tous les nœuds écoutent (utilisé par run_all.sh).

    Tente une connexion sur chaque port jusqu'à ce qu'elle réussisse ; renvoie False
    si un nœud ne répond toujours pas après ``timeout`` secondes.
    """
    deadline = time.monotonic() + timeout
    pending = list(nodes)
    while pending and time.monotonic() < deadline:
        node = pending[0]
        try:
            with socket.create_connection(config.address(node), timeout=0.2):
                pending.pop(0)  # ce nœud écoute, on passe au suivant
        except OSError:
            time.sleep(0.1)
    return not pending


# `python -m common.net [timeout]` : attend les 4 nœuds (code de sortie 0 si prêts).
if __name__ == "__main__":
    ok = wait_for_nodes(timeout=float(sys.argv[1]) if len(sys.argv) > 1 else 10.0)
    print("nœuds prêts" if ok else "nœuds absents", flush=True)
    sys.exit(0 if ok else 1)
