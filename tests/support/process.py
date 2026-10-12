"""Process measurements for tests and benchmarks."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path


def rss_bytes(pid: int) -> int:
    """Resident memory of a process (working set on Windows)."""
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        handle = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), f"cannot open process {pid}")
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                raise OSError(ctypes.get_last_error(), f"cannot read memory of process {pid}")
        finally:
            kernel32.CloseHandle(handle)
        return int(counters.WorkingSetSize)
    if Path(f"/proc/{pid}/status").exists():
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    output = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
    return int(output.stdout.strip()) * 1024


def cpu_seconds(pid: int) -> float:
    """User + kernel CPU time a process has used so far."""
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            raise OSError(ctypes.get_last_error(), f"cannot open process {pid}")
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise OSError(ctypes.get_last_error(), f"cannot read times of process {pid}")
        finally:
            kernel32.CloseHandle(handle)

        def seconds(value: wintypes.FILETIME) -> float:
            return ((value.dwHighDateTime << 32) | value.dwLowDateTime) / 1e7

        return seconds(times[2]) + seconds(times[3])
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    ticks = int(fields[11]) + int(fields[12])
    import os

    return ticks / os.sysconf("SC_CLK_TCK")
