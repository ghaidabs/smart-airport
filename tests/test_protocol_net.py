"""Tests du protocole et de la couche réseau (P1)."""

from __future__ import annotations

import queue
import socket
import threading
import struct

import pytest

from common.logger import CsvLogger
from common.net import NodeServer, Sender
from common.protocol import Message, ProtocolError, read_message


def make(n: int = 0) -> Message:
    return Message(type="FLIGHT_DELAY", source="injector", dest="flight",
                   payload={"flight": "TU701", "delay_min": 20 + n, "t0_ns": 1})


def test_roundtrip_and_prefix():
    msg = make().stamp()
    frame = msg.encode()
    (length,) = struct.unpack("!I", frame[:4])
    assert length == len(frame) - 4
    back = Message.decode(frame[4:])
    assert back == msg


def test_validation():
    with pytest.raises(ProtocolError):
        Message(type="NOPE", source="injector", dest="flight")
    with pytest.raises(ProtocolError):
        Message(type="FLIGHT_DELAY", source="mars", dest="flight")
    with pytest.raises(ProtocolError):
        Message.decode(b'{"type": "FLIGHT_DELAY"}')


def test_derive_keeps_chain():
    msg = make()
    child = msg.derive("RISK_REQUEST", "flight", "ai", {"flights": []})
    assert child.corr_id == msg.corr_id
    assert child.payload["t0_ns"] == 1
    assert child.id != msg.id


def test_corr_ids_unique_across_threads():
    """8 threads créent 2000 chaînes chacun en même temps : aucun doublon."""
    from concurrent.futures import ThreadPoolExecutor

    from common.protocol import new_corr_id

    with ThreadPoolExecutor(8) as pool:
        batches = list(pool.map(lambda _: [new_corr_id() for _ in range(2000)], range(8)))
    ids = [i for batch in batches for i in batch]
    assert len(set(ids)) == len(ids) == 16000


def test_framing_survives_split_and_coalesced_reads():
    """Deux trames envoyées octet par octet, puis collées : TCP ne garde pas les limites."""
    a, b = socket.socketpair()
    try:
        frames = make(1).stamp().encode() + make(2).stamp().encode()
        received: list = []
        # Lecture concurrente : sans elle, le tampon de la socket se remplit et sendall bloque.
        reader = threading.Thread(
            target=lambda: received.extend(read_message(b) for _ in range(3)), daemon=True)
        reader.start()
        for i in range(len(frames)):
            a.sendall(frames[i:i + 1])
        a.close()
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert received[0].payload["delay_min"] == 21
        assert received[1].payload["delay_min"] == 22
        assert received[2] is None
    finally:
        b.close()


def test_server_and_sender_with_reconnect(tmp_path):
    log = CsvLogger("flight", tmp_path)
    inbox: queue.Queue = queue.Queue()
    server = NodeServer("flight", log, inbox, port=0).start()
    port = server.port
    sender = Sender(CsvLogger("injector", tmp_path), {"flight": ("127.0.0.1", port)})
    try:
        for i in range(50):
            assert sender.send("flight", make(i))
        got = [inbox.get(timeout=2).payload["delay_min"] for _ in range(50)]
        assert got == [20 + i for i in range(50)]  # ordre conservé

        # Le serveur redémarre : l'envoi suivant doit se reconnecter.
        server.stop()
        server = NodeServer("flight", log, inbox, port=port).start()
        delivered = sum(sender.send("flight", make(100 + i)) for i in range(3))
        assert delivered >= 2
        assert inbox.get(timeout=2).payload["delay_min"] >= 100
    finally:
        sender.close()
        server.stop()
        log.close()
    text = (tmp_path / "flight.csv").read_text(encoding="utf-8")
    assert text.startswith("ts_ns,node,thread,corr_id,event,detail")
    assert "RECV" in text
