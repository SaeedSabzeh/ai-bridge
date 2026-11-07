"""Contain lead processes and descendants in a Windows Job Object.

The bootstrap blocks on stdin until assigned, so provider code cannot run
before containment. No job handle is inherited. Closing the supervisor's
handle kills the tree even if the supervisor itself is terminated.
"""
import ctypes as c
from ctypes import wintypes as w
import os
import time


class BasicLimits(c.Structure):
    _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                ("flags", w.DWORD), ("working_min", c.c_size_t),
                ("working_max", c.c_size_t), ("active_limit", w.DWORD),
                ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class ExtendedLimits(c.Structure):
    _fields_ = [("basic", BasicLimits), ("io", c.c_ulonglong * 6),
                ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]


class Accounting(c.Structure):
    _fields_ = [("times", c.c_longlong * 4), ("faults", w.DWORD),
                ("total", w.DWORD), ("active", w.DWORD), ("terminated", w.DWORD)]


def api():
    if os.name != "nt":
        raise OSError("Lead execution requires Windows Job Objects.")
    k = c.WinDLL("kernel32", use_last_error=True)
    for name, ret, args in [
        ("CreateJobObjectW", w.HANDLE, [c.c_void_p, w.LPCWSTR]),
        ("OpenJobObjectW", w.HANDLE, [w.DWORD, w.BOOL, w.LPCWSTR]),
        ("SetInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]),
        ("QueryInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]),
        ("AssignProcessToJobObject", w.BOOL, [w.HANDLE, w.HANDLE]),
        ("TerminateJobObject", w.BOOL, [w.HANDLE, w.UINT]),
        ("OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
        ("CloseHandle", w.BOOL, [w.HANDLE]),
    ]:
        fn = getattr(k, name)
        fn.restype, fn.argtypes = ret, args
    return k


def checked(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


class Job:
    def __init__(self, run_id, existing=False):
        self.k = api()
        self.name = "Local\\AI-Bridge-" + run_id
        if existing:
            self.handle = self.k.OpenJobObjectW(0x0004 | 0x0008, False, self.name)
            if not self.handle and c.get_last_error() != 2:
                checked(self.handle)
            return
        self.handle = checked(self.k.CreateJobObjectW(None, self.name))
        try:
            if c.get_last_error() == 183:
                raise OSError("The run already has a Windows job.")
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(self.k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def assign(self, pid):
        process = checked(self.k.OpenProcess(0x0001 | 0x0100, False, pid))
        try:
            checked(self.k.AssignProcessToJobObject(self.handle, process))
        finally:
            self.k.CloseHandle(process)

    def active(self):
        if not self.handle:
            return 0
        info = Accounting()
        checked(self.k.QueryInformationJobObject(self.handle, 1, c.byref(info), c.sizeof(info), None))
        return info.active

    def empty(self):
        if self.active():
            checked(self.k.TerminateJobObject(self.handle, 125))
        end = time.monotonic() + 10
        while self.active():
            if time.monotonic() > end:
                raise OSError("Could not confirm that the Windows job stopped. Takeover is blocked.")
            time.sleep(.05)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
