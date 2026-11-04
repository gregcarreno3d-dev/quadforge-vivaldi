"""Time and memory measurement, so every speed claim can be checked with numbers.

Memory is read from the operating system for the whole Blender process
(Linux: /proc, Windows: psapi, macOS: getrusage). Every reader returns ``None``
instead of failing, because measuring must never break a remesh.
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

BYTES_PER_MB = 1024.0 * 1024.0


def _linux_status_mb(field_name: str):
    try:
        with open("/proc/self/status", "r", encoding="ascii", errors="ignore") as status_file:
            for line in status_file:
                if line.startswith(field_name + ":"):
                    return float(line.split()[1]) / 1024.0      # value is in kB
    except OSError:
        pass
    return None


def _windows_memory_mb(peak: bool):
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return None
        return (counters.PeakWorkingSetSize if peak else counters.WorkingSetSize) / BYTES_PER_MB
    except Exception:  # noqa: BLE001
        return None


def current_memory_mb():
    """Memory used by the Blender process right now (MB), or ``None`` if unknown."""
    if sys.platform.startswith("linux"):
        return _linux_status_mb("VmRSS")
    if sys.platform == "win32":
        return _windows_memory_mb(peak=False)
    return None


def peak_memory_mb():
    """Highest memory the Blender process has used so far (MB), or ``None`` if unknown."""
    if sys.platform.startswith("linux"):
        return _linux_status_mb("VmHWM")
    if sys.platform == "win32":
        return _windows_memory_mb(peak=True)
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return usage / BYTES_PER_MB if sys.platform == "darwin" else usage / 1024.0
    except Exception:  # noqa: BLE001
        return None


@dataclass
class StageRecord:
    name: str
    seconds: float
    memory_after_mb: object = None          # float or None


@dataclass
class Profiler:
    """Collects one record per named stage."""

    records: list = field(default_factory=list)
    _start: float = field(default_factory=time.perf_counter)

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.records.append(StageRecord(name, time.perf_counter() - started, current_memory_mb()))

    @property
    def total_seconds(self) -> float:
        return time.perf_counter() - self._start

    def as_dict(self) -> dict:
        return {
            "total_seconds": round(self.total_seconds, 3),
            "peak_memory_mb": peak_memory_mb(),
            "stages": [{"name": r.name, "seconds": round(r.seconds, 3),
                        "memory_after_mb": None if r.memory_after_mb is None else round(r.memory_after_mb, 1)}
                       for r in self.records],
        }

    def summary_lines(self) -> list:
        """Human-readable lines, one per stage."""
        return [f"{r.name}: {r.seconds:.2f} s" for r in self.records]
