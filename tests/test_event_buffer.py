"""Tests des invariants de EventBuffer (P4)."""
import threading
import time

from sync.event_buffer import EventBuffer


def msg(i, critical=False):
    return {"id": i, "type": "SECURITY_ALERT" if critical else "QUEUE_UPDATE"}


def test_fifo():
    buf = EventBuffer(capacity=4)
    for i in range(3):
        assert buf.put(msg(i))
    assert [buf.get(0)["id"] for _ in range(3)] == [0, 1, 2]


def test_get_timeout_returns_none():
    assert EventBuffer(2).get(timeout=0.05) is None


def test_non_critical_rejected_when_full():
    buf = EventBuffer(capacity=2)
    assert buf.put(msg(0)) and buf.put(msg(1))
    assert buf.put(msg(2), timeout=0.0) is False
    assert buf.stats()["rejected"] == 1


def test_critical_evicts_oldest_non_critical():
    buf = EventBuffer(capacity=2)
    buf.put(msg(0))
    buf.put(msg(1))
    assert buf.put(msg(99, critical=True), timeout=0.0) is True
    assert [buf.get(0)["id"], buf.get(0)["id"]] == [1, 99]
    assert buf.stats()["evicted"] == 1


def test_critical_waits_when_all_critical():
    buf = EventBuffer(capacity=1)
    buf.put(msg(0, critical=True))
    assert buf.put(msg(1, critical=True), timeout=0.05) is False


def test_blocked_producer_wakes_up():
    buf = EventBuffer(capacity=1)
    buf.put(msg(0))
    threading.Timer(0.05, buf.get).start()
    assert buf.put(msg(1), timeout=1.0) is True


def test_no_loss_multithread():
    """accepted == consumed + evicted (+ reste) : aucune perte hors rejet."""
    buf = EventBuffer(capacity=8)
    n_prod, per_prod = 4, 500
    got = []
    got_lock = threading.Lock()
    stop = threading.Event()

    def producer(base):
        for k in range(per_prod):
            buf.put(msg(base * 10_000 + k, critical=(k % 5 == 0)), timeout=0.01)

    def consumer():
        while not (stop.is_set() and len(buf) == 0):
            m = buf.get(timeout=0.01)
            if m is not None:
                with got_lock:
                    got.append(m["id"])

    consumers = [threading.Thread(target=consumer) for _ in range(3)]
    producers = [threading.Thread(target=producer, args=(b,)) for b in range(n_prod)]
    for t in consumers + producers:
        t.start()
    for t in producers:
        t.join()
    stop.set()
    for t in consumers:
        t.join()

    s = buf.stats()
    assert len(got) == len(set(got))                        # pas de doublon
    assert s["accepted"] + s["rejected"] == n_prod * per_prod
    assert s["accepted"] == s["consumed"] + s["evicted"] + s["size"]
    assert s["consumed"] == len(got)
    assert s["high_water"] <= s["capacity"]