"""
Windows Job Objects — OS-level process resource caps via ctypes.
No external dependencies. Gracefully no-ops on Linux / macOS and when a process
is already in a Job Object (common on Windows 8+).
"""
import ctypes
import logging
import platform
from typing import Dict

log = logging.getLogger("job_objects")

IS_WINDOWS = platform.system() == "Windows"

_active_jobs: Dict[int, int] = {}  # pid → HANDLE (kept alive so the cap persists)

if IS_WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_ALL_ACCESS                    = 0x1F0FFF
    JOB_OBJECT_LIMIT_PROCESS_MEMORY       = 0x00000100
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE   = 0x00002000
    JobObjectExtendedLimitInformation     = 9
    JobObjectCpuRateControlInformation    = 15
    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE   = 0x1
    JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
    ERROR_ACCESS_DENIED                   = 5

    class _IOCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOps",    ctypes.c_uint64), ("WriteOps",   ctypes.c_uint64),
            ("OtherOps",   ctypes.c_uint64), ("ReadBytes",  ctypes.c_uint64),
            ("WriteBytes", ctypes.c_uint64), ("OtherBytes", ctypes.c_uint64),
        ]

    # Natural-alignment padding is inserted by ctypes between DWORD and SIZE_T fields.
    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTime", ctypes.c_int64),
            ("PerJobUserTime",     ctypes.c_int64),
            ("LimitFlags",         ctypes.c_uint32),
            ("MinWorkingSet",      ctypes.c_size_t),   # ctypes pads 4 bytes before this
            ("MaxWorkingSet",      ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity",           ctypes.c_size_t),   # ctypes pads 4 bytes before this
            ("PriorityClass",      ctypes.c_uint32),
            ("SchedulingClass",    ctypes.c_uint32),
        ]

    class _ExtLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimit",         _BasicLimit),
            ("IoInfo",             _IOCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit",     ctypes.c_size_t),
            ("PeakProcessMemory",  ctypes.c_size_t),
            ("PeakJobMemory",      ctypes.c_size_t),
        ]

    class _CpuRate(ctypes.Structure):
        _fields_ = [
            ("ControlFlags", ctypes.c_uint32),
            ("CpuRate",       ctypes.c_uint32),  # units: 1/100 percent; 10000 == 100%
        ]


def _noop(reason: str) -> Dict:
    return {"ok": False, "reason": reason}


def apply_memory_cap(pid: int, max_bytes: int) -> Dict:
    """Assign a hard memory limit to a process via a Windows Job Object."""
    if not IS_WINDOWS:
        return _noop("not Windows")
    hProc = _k32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not hProc:
        return _noop(f"OpenProcess error {ctypes.get_last_error()}")
    hJob = _k32.CreateJobObjectW(None, None)
    if not hJob:
        _k32.CloseHandle(hProc)
        return _noop(f"CreateJobObject error {ctypes.get_last_error()}")

    info = _ExtLimit()
    info.BasicLimit.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.ProcessMemoryLimit = max_bytes
    if not _k32.SetInformationJobObject(
        hJob, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        _k32.CloseHandle(hJob); _k32.CloseHandle(hProc)
        return _noop(f"SetInformationJobObject error {ctypes.get_last_error()}")

    ok = _k32.AssignProcessToJobObject(hJob, hProc)
    _k32.CloseHandle(hProc)
    if not ok:
        err = ctypes.get_last_error()
        _k32.CloseHandle(hJob)
        if err == ERROR_ACCESS_DENIED:
            return _noop("process already in a Job Object (Windows 8+ nested-job limitation)")
        return _noop(f"AssignProcessToJobObject error {err}")

    _active_jobs[pid] = hJob
    log.info("Memory cap applied: PID %d → %d MB", pid, max_bytes // 1048576)
    return {"ok": True, "max_mb": max_bytes // 1048576}


def apply_cpu_rate(pid: int, rate_pct: int) -> Dict:
    """Apply a CPU rate hard cap (0–100 %) to a process via a Windows Job Object."""
    if not IS_WINDOWS:
        return _noop("not Windows")
    hProc = _k32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not hProc:
        return _noop(f"OpenProcess error {ctypes.get_last_error()}")
    hJob = _k32.CreateJobObjectW(None, None)
    if not hJob:
        _k32.CloseHandle(hProc)
        return _noop(f"CreateJobObject error {ctypes.get_last_error()}")

    # Keep the job alive when the last handle is closed only if we own the job.
    ext = _ExtLimit()
    ext.BasicLimit.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    _k32.SetInformationJobObject(
        hJob, JobObjectExtendedLimitInformation, ctypes.byref(ext), ctypes.sizeof(ext)
    )

    cpu = _CpuRate()
    cpu.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
    cpu.CpuRate = max(1, min(10000, rate_pct * 100))
    if not _k32.SetInformationJobObject(
        hJob, JobObjectCpuRateControlInformation, ctypes.byref(cpu), ctypes.sizeof(cpu)
    ):
        _k32.CloseHandle(hJob); _k32.CloseHandle(hProc)
        return _noop(f"SetInformationJobObject (CPU rate) error {ctypes.get_last_error()}")

    ok = _k32.AssignProcessToJobObject(hJob, hProc)
    _k32.CloseHandle(hProc)
    if not ok:
        err = ctypes.get_last_error()
        _k32.CloseHandle(hJob)
        if err == ERROR_ACCESS_DENIED:
            return _noop("process already in a Job Object (Windows 8+ nested-job limitation)")
        return _noop(f"AssignProcessToJobObject error {err}")

    _active_jobs[pid] = hJob
    log.info("CPU rate cap applied: PID %d → %d%%", pid, rate_pct)
    return {"ok": True, "cpu_rate_pct": rate_pct}


def release_cap(pid: int):
    """Release the Job Object for a PID. The process limit is removed when the handle closes."""
    hJob = _active_jobs.pop(pid, None)
    if hJob and IS_WINDOWS:
        _k32.CloseHandle(hJob)


def list_capped() -> Dict[int, bool]:
    """Return the set of PIDs currently under a Job Object cap."""
    return {pid: True for pid in _active_jobs}
