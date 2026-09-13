from types import SimpleNamespace
from unittest.mock import Mock

from demo_api.faults import cpu


def test_child_exit_and_reinjection_do_not_stall_cpu_counter(monkeypatch):
    def process(pid, created, seconds):
        return SimpleNamespace(
            pid=pid,
            create_time=lambda: created,
            cpu_times=lambda: SimpleNamespace(user=seconds, system=0),
        )

    parent = process(1, 1.0, 10.0)
    children = [process(2, 2.0, 100.0)]
    parent.children = lambda **kwargs: children
    monkeypatch.setattr(cpu.psutil, "Process", lambda pid: parent)
    counter = Mock()
    monkeypatch.setattr(cpu, "DEMO_API_CPU_SECONDS_TOTAL", counter)
    manager = cpu.CpuStressManager()
    manager.update_metrics()
    assert counter.inc.call_args.args == (110.0,)

    # Reaping a long-running fault must still report subsequent parent CPU.
    children.clear()
    parent.cpu_times = lambda: SimpleNamespace(user=11.0, system=0)
    manager.update_metrics()
    assert counter.inc.call_args.args == (1.0,)

    # A new child, even with a reused PID, contributes immediately.
    children.append(process(2, 3.0, 1.0))
    parent.cpu_times = lambda: SimpleNamespace(user=12.0, system=0)
    manager.update_metrics()
    assert counter.inc.call_args.args == (2.0,)
    children[0].cpu_times = lambda: SimpleNamespace(user=2.5, system=0.5)
    manager.update_metrics()
    assert counter.inc.call_args.args == (2.0,)
