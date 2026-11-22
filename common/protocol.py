"""Protocole de messages entre les nœuds (P1, annexe A du guide).

Un message est un objet JSON avec les champs ``id``, ``type``, ``source``, ``dest``,
``ts_ns``, ``corr_id`` et ``payload``. TCP est un flux sans frontières de messages :
chaque message est donc précédé de sa longueur sur 4 octets (``struct.pack('!I', n)``,
entier non signé, ordre réseau).

Champ optionnel du payload : ``t0_ns``, l'horodatage de l'événement initial, recopié
dans toute la chaîne (voir :meth:`Message.derive`) pour calculer la latence de bout en
bout dans le message ``DECISION``.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from common import config

HEADER = struct.Struct("!I")


class MessageType:
    """Types de messages (annexe A). Des chaînes simples, lisibles dans le JSON."""

    FLIGHT_DELAY = "FLIGHT_DELAY"            # injector -> flight
    GATE_UNAVAILABLE = "GATE_UNAVAILABLE"    # injector -> flight
    QUEUE_UPDATE = "QUEUE_UPDATE"            # injector -> passenger
    RISK_REQUEST = "RISK_REQUEST"            # flight -> ai
    RISK_RESULT = "RISK_RESULT"              # ai -> decision
    SECURITY_ALERT = "SECURITY_ALERT"        # passenger -> decision
    GATE_REQUEST = "GATE_REQUEST"            # decision -> flight
    GATE_ASSIGNED = "GATE_ASSIGNED"          # flight -> decision
    PAX_ACTION = "PAX_ACTION"                # decision -> passenger
    PAX_DONE = "PAX_DONE"                    # passenger -> decision
    DECISION = "DECISION"                    # decision -> tous (diffusion)
    PING = "PING"                            # mesures M7 : aller-retour
    PONG = "PONG"

    ALL: frozenset[str] = frozenset({
        "FLIGHT_DELAY", "GATE_UNAVAILABLE", "QUEUE_UPDATE", "RISK_REQUEST",
        "RISK_RESULT", "SECURITY_ALERT", "GATE_REQUEST", "GATE_ASSIGNED",
        "PAX_ACTION", "PAX_DONE", "DECISION", "PING", "PONG",
    })


class ProtocolError(Exception):
    """Message invalide ou trame corrompue."""


@dataclass
class Message:
    """Un message du système (annexe A)."""

    type: str
    source: str
    dest: str
    payload: dict[str, Any] = field(default_factory=dict)
    corr_id: str = "" # id of the initial event, copied along the whole chain
    id: str = field(default_factory=lambda: str(uuid.uuid4())) # unique id of THIS message # unique id of THIS message
    ts_ns: int = 0  # rempli par stamp() juste avant l'envoi

    def __post_init__(self) -> None: #validation des champs, levée de ProtocolError si invalide
        if self.type not in MessageType.ALL:
            raise ProtocolError(f"type inconnu : {self.type!r}")
        if self.source not in config.SOURCES:
            raise ProtocolError(f"source inconnue : {self.source!r}")
        if self.dest not in config.SOURCES:
            raise ProtocolError(f"destination inconnue : {self.dest!r}")
        if not isinstance(self.payload, dict):
            raise ProtocolError("payload doit être un objet JSON")
        if not self.corr_id:
            self.corr_id = new_corr_id()

    # --- Construction --------------------------------------------------

    def stamp(self) -> "Message":
        """Fixe ``ts_ns`` à l'heure d'envoi (``time.time_ns()``)."""
        self.ts_ns = time.time_ns()
        return self

    def derive(self, type: str, source: str, dest: str,
               payload: dict[str, Any] | None = None) -> "Message":
        """Crée un message de la même chaîne : même ``corr_id``, ``t0_ns`` recopié."""
        body = dict(payload or {})
        if "t0_ns" in self.payload and "t0_ns" not in body:
            body["t0_ns"] = self.payload["t0_ns"]
        return Message(type=type, source=source, dest=dest, payload=body,
                       corr_id=self.corr_id)

    # --- Sérialisation -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        try:
            return cls(type=data["type"], source=data["source"], dest=data["dest"],
                       payload=data.get("payload", {}), corr_id=data["corr_id"],
                       id=data["id"], ts_ns=int(data["ts_ns"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError(f"champ manquant ou invalide : {exc}") from exc

    def encode(self) -> bytes:
        """Trame complète : préfixe de longueur + JSON UTF-8."""
        body = json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")
        if len(body) > config.MAX_MESSAGE_BYTES:
            raise ProtocolError(f"message trop long : {len(body)} octets")
        return HEADER.pack(len(body)) + body

    @classmethod
    def decode(cls, body: bytes) -> "Message":
        """Décode le corps JSON (sans le préfixe)."""
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"JSON invalide : {exc}") from exc
        if not isinstance(data, dict):
            raise ProtocolError("le message doit être un objet JSON")
        return cls.from_dict(data)

    def short(self) -> str:
        """Résumé pour la colonne ``detail`` du journal."""
        return f"{self.type} {self.source}->{self.dest} id={self.id[:8]}"


_counter = 0
_counter_lock = threading.Lock()
_run = uuid.uuid4().hex[:4]


def new_corr_id() -> str:
    """Identifiant d'événement initial, unique par processus : ``evt-<run>-0001``.
    ``_counter += 1`` n'est pas atomique (lecture, addition, écriture) : deux threads
    pourraient lire la même valeur et produire le même identifiant. Le verrou rend
    l'incrément et la lecture indivisibles (section critique).
    """
    global _counter
    with _counter_lock:
        _counter += 1
        n = _counter
        return f"evt-{_run}-{n:04d}"


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """Lit exactement n octets ; None si la connexion se ferme proprement avant."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            if buf:
                raise ProtocolError("connexion fermée au milieu d'une trame")
            return None
        buf.extend(chunk)
    return bytes(buf)


def read_message(sock: socket.socket) -> Message | None:
    """Lit un message complet sur une socket TCP ; None en fin de connexion."""
    header = _recv_exact(sock, HEADER.size)
    if header is None:
        return None
    (length,) = HEADER.unpack(header)
    if length > config.MAX_MESSAGE_BYTES:
        raise ProtocolError(f"longueur annoncée trop grande : {length}")
    body = _recv_exact(sock, length)
    if body is None:
        raise ProtocolError("connexion fermée après l'en-tête")
    return Message.decode(body)
