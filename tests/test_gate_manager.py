"""Tests des invariants du GateManager (P4)."""
import random
import threading

from sync.gate_manager import FREE, OCCUPIED, UNAVAILABLE, GateManager


def drain(gm):
    """Réserve tant qu'il reste une porte (sans attendre) ; renvoie le nombre."""
    n = 0
    while gm.reserve("drain", timeout=0.0) is not None:
        n += 1
    return n


def test_scenario_tu701_tu702():
    """G12 en panne : TU701 obtient G15 (la plus proche de G03), puis TU702 G18."""
    gm = GateManager()
    assert gm.set_unavailable("G12") is None
    assert gm.reserve("TU701", near_gate="G03") == "G15"
    assert gm.reserve("TU702", near_gate="G03") == "G18"


def test_none_when_full_then_release():
    gm = GateManager(gates=("G15",))
    assert gm.reserve("A", timeout=0.0) == "G15"
    assert gm.reserve("B", timeout=0.05) is None
    assert gm.release("G15") is True
    assert gm.reserve("B", timeout=0.05) == "G15"
    assert gm.release("G18") is False  # porte inconnue / non occupée


def test_waiting_reserver_is_woken_by_release():
    gm = GateManager(gates=("G15",))
    gm.reserve("A")
    threading.Timer(0.05, gm.release, args=("G15",)).start()
    assert gm.reserve("B", timeout=1.0) == "G15"


def test_set_unavailable_occupied_returns_flight():
    gm = GateManager(occupied={"G12": "TU701"})
    assert gm.set_unavailable("G12") == "TU701"
    assert gm.snapshot()["G12"]["state"] == UNAVAILABLE
    assert drain(gm) == 3  # G15, G18, G20


def test_set_unavailable_free_reduces_capacity():
    gm = GateManager()
    gm.set_unavailable("G15")
    assert drain(gm) == 3
    assert gm.set_available("G15") is True
    assert drain(gm) == 1


def test_no_double_assignment_10_threads():
    """10 threads, 4 portes, sans libération : 4 succès, 4 portes distinctes."""
    for _ in range(200):
        gm = GateManager()
        barrier = threading.Barrier(10)
        results = []
        lock = threading.Lock()

        def worker(i):
            barrier.wait()
            g = gm.reserve(f"F{i}", near_gate="G03", timeout=0.0)
            with lock:
                results.append(g)

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        got = [g for g in results if g is not None]
        assert len(got) == 4 and len(set(got)) == 4


def test_token_count_consistent_after_random_stress():
    """Après un mélange aléatoire d'opérations, jetons == portes libres."""
    rng = random.Random(42)
    gm = GateManager()
    held = []
    held_lock = threading.Lock()
    seeds = [rng.random() for _ in range(6)]

    def worker(seed):
        r = random.Random(seed)
        for _ in range(400):
            op = r.choice(["reserve", "release", "down", "up"])
            g = r.choice(["G12", "G15", "G18", "G20"])
            if op == "reserve":
                got = gm.reserve("X", near_gate="G03", timeout=0.0)
                if got:
                    with held_lock:
                        held.append(got)
            elif op == "release":
                gm.release(g)
            elif op == "down":
                gm.set_unavailable(g)
            else:
                gm.set_available(g)

    ts = [threading.Thread(target=worker, args=(s,)) for s in seeds]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    free = sum(1 for v in gm.snapshot().values() if v["state"] == FREE)
    assert drain(gm) == free                      # jetons == portes libres
    assert gm.reserve("Z", timeout=0.0) is None   # et plus rien ensuite
    assert all(v["state"] in (OCCUPIED, UNAVAILABLE) for v in gm.snapshot().values())