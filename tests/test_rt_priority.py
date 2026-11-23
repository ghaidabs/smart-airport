"""Priorités SCHED_FIFO, testées sans privilège grâce à un faux appel système."""

import os
import sys

import pytest

from rt import system
from rt.tasks import TASKS_V0


@pytest.fixture
def scheduler(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "sched_setscheduler",
                        lambda pid, policy, param: calls.append((pid, policy,
                                                                 param.sched_priority)),
                        raising=False)
    monkeypatch.delenv(system.RT_FIFO_ENV, raising=False)
    interval = sys.getswitchinterval()
    yield calls
    sys.setswitchinterval(interval)
    os.environ.pop(system.RT_FIFO_ENV, None)


def test_priorities_follow_deadlines():
    deadlines = {task.task_id: task.deadline_ms for task in TASKS_V0}
    ordered = sorted(system.RT_PRIORITIES, key=system.RT_PRIORITIES.get, reverse=True)
    assert ordered == ["T5", "T1", "T4", "T3", "T2"]
    assert [deadlines[t] for t in ordered] == sorted(deadlines[t] for t in ordered)


def test_priority_is_opt_in(scheduler):
    assert system.apply_task_priority("T1") is False
    assert scheduler == []


def test_priority_applies_to_calling_thread(scheduler, monkeypatch):
    monkeypatch.setenv(system.RT_FIFO_ENV, "1")
    assert system.apply_task_priority("T5") is True
    assert scheduler == [(0, os.SCHED_FIFO, 80)]
    assert sys.getswitchinterval() == pytest.approx(system.GIL_SWITCH_INTERVAL_S)


def test_unknown_task_keeps_normal_policy(scheduler, monkeypatch):
    monkeypatch.setenv(system.RT_FIFO_ENV, "1")
    assert system.apply_task_priority("py") is False
    assert scheduler == []


def test_enable_fifo_restores_caller(scheduler):
    assert system.enable_fifo() is True
    assert scheduler == [(0, os.SCHED_FIFO, 80), (0, os.SCHED_OTHER, 0)]
    assert os.environ[system.RT_FIFO_ENV] == "1"


def test_refusal_is_reported(monkeypatch):
    def denied(*_):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "sched_setscheduler", denied, raising=False)
    monkeypatch.delenv(system.RT_FIFO_ENV, raising=False)
    assert system.enable_fifo() is False
    assert system.RT_FIFO_ENV not in os.environ
    monkeypatch.setenv(system.RT_FIFO_ENV, "1")
    assert system.apply_task_priority("T1") is False
