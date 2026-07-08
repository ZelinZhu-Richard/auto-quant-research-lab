"""Runtime I/O guard for signal purity (SPEC §2).

A signal that loads data itself (instead of using the panel it is handed)
bypasses truncation entirely and can read close(t+1) — the one leak the
truncate-and-compare check cannot see. Both the S2 harness AND the S3
engine therefore run compute_signal under this guard.

Two enforcement layers (see SPEC §2 "Purity enforcement boundary"):
1. Python-level patches of the common read entry points (clear error
   messages for the common mistake).
2. Kernel-level RLIMIT_NOFILE soft limit dropped to 0 for the guarded
   region (POSIX): ANY attempt to allocate a new file descriptor — via
   io.open_code, pyarrow's C++ filesystems, ctypes, sockets, os.listdir's
   opendir, or any other API — fails at the OS level with EMFILE.
   Already-open descriptors keep working, so in-memory pandas/numpy code
   and pytest capture are unaffected.

Accepted residual (documented in SPEC §2): fd-less metadata syscalls such
as os.stat (which cannot read prices), and a signal maliciously raising its
own rlimit back. The threat model is careless generated code, not an
adversary with kernel knowledge; the structural backstops are the
read-only Docker mounts, the egress allowlist, and R2's physical absence
of holdout data.
"""

import builtins
import contextlib
import io
import os
import pathlib
import socket
from unittest import mock

import numpy as np
import pandas as pd

from engine.errors import EngineError


def _prewarm_allowed_libraries() -> None:
    """With RLIMIT_NOFILE=0 active, any LAZY import inside a pandas/numpy
    call (e.g. pandas.core.reshape on the first .unstack) would die on the
    module-file open. Signals may import only pandas/numpy/math (SPEC §2 /
    S2 contract), so import every submodule of both up front — after this,
    nothing the signal can legitimately touch needs a new file descriptor."""
    import importlib
    import pkgutil
    import warnings

    skip_markers = (".tests", "conftest", "__main__", ".f2py", ".distutils",
                    "._pyinstaller", ".setup")
    sink = io.StringIO()
    for package in (np, pd):
        prefix = package.__name__ + "."
        for info in pkgutil.walk_packages(package.__path__, prefix):
            name = info.name
            if any(marker in name for marker in skip_markers):
                continue
            # BaseException: some modules sys.exit() or print on import;
            # nothing they do matters here beyond landing in sys.modules
            with warnings.catch_warnings(), \
                    contextlib.redirect_stdout(sink), \
                    contextlib.redirect_stderr(sink), \
                    contextlib.suppress(BaseException):
                warnings.simplefilter("ignore")
                importlib.import_module(name)


_prewarm_allowed_libraries()

try:
    import resource  # POSIX; absent on Windows

    _HAS_RLIMIT = hasattr(resource, "RLIMIT_NOFILE")
except ImportError:  # pragma: no cover — non-POSIX fallback
    resource = None
    _HAS_RLIMIT = False


class PurityViolation(EngineError):
    """The signal attempted I/O during compute_signal."""


def _blocked(*_args, **_kwargs):
    raise PurityViolation(
        "signal attempted I/O during compute_signal (purity violation, SPEC §2)"
    )


# Library-internal keys pandas/numpy/pyarrow legitimately consult at call
# time. Everything else raises: environment access is nondeterministic
# input (SPEC §2). Extend only via a review gate.
_ENV_ALLOWLIST = frozenset({"PYARROW_IGNORE_TIMEZONE", "PANDAS_COPY_ON_WRITE"})


class _BlockedEnviron:
    """Stands in for os.environ/os.environb inside guarded regions:
    allowlisted library-internal keys resolve from a snapshot taken at
    guard entry (str or bytes keys both work, values in the mapping's
    native type); any other read, any write, and any enumeration raises."""

    def __init__(self, snapshot: dict):
        self._snapshot = {}
        for key, value in snapshot.items():
            normalized = self._norm(key)  # None for non-UTF-8 bytes keys,
            if normalized in _ENV_ALLOWLIST:  # which can never be allowlisted
                self._snapshot[normalized] = value

    @staticmethod
    def _norm(key):
        if isinstance(key, bytes):
            try:
                return key.decode()
            except UnicodeDecodeError:
                return None
        return key

    def __getitem__(self, key):
        normalized = self._norm(key)
        if normalized in _ENV_ALLOWLIST:
            return self._snapshot[normalized]
        _blocked()

    def __setitem__(self, _key, _value):
        _blocked()

    def get(self, key, default=None):
        normalized = self._norm(key)
        if normalized in _ENV_ALLOWLIST:
            return self._snapshot.get(normalized, default)
        _blocked()

    def __contains__(self, key):
        normalized = self._norm(key)
        if normalized in _ENV_ALLOWLIST:
            return normalized in self._snapshot
        _blocked()

    def __iter__(self):
        _blocked()

    def __len__(self):
        _blocked()

    def copy(self):
        _blocked()

    def keys(self):
        _blocked()

    def items(self):
        _blocked()

    def values(self):
        _blocked()




def _targets() -> list[tuple[object, str]]:
    targets: list[tuple[object, str]] = [
        (builtins, "open"),
        (io, "open"),
        (io, "open_code"),
        (os, "open"),
        (os, "fdopen"),
        (os, "listdir"),
        (os, "scandir"),
        (pathlib.Path, "open"),
        (pathlib.Path, "read_text"),
        (pathlib.Path, "read_bytes"),
        (socket, "socket"),
        (np, "load"),
        (np, "loadtxt"),
        (np, "fromfile"),
        (np, "genfromtxt"),
    ]
    for name in ("read_parquet", "read_csv", "read_json", "read_pickle",
                 "read_feather", "read_orc", "read_hdf", "read_table",
                 "read_excel", "read_sql", "read_xml", "read_fwf"):
        if hasattr(pd, name):
            targets.append((pd, name))
    with contextlib.suppress(ImportError):
        import pyarrow.parquet as pq
        for name in ("read_table", "ParquetFile", "read_pandas"):
            if hasattr(pq, name):
                targets.append((pq, name))
    with contextlib.suppress(ImportError):
        import pyarrow.dataset as pads
        for name in ("dataset", "FileSystemDataset"):
            if hasattr(pads, name):
                targets.append((pads, name))
    with contextlib.suppress(ImportError):
        import pyarrow.csv as pacsv
        for name in ("read_csv", "open_csv"):
            if hasattr(pacsv, name):
                targets.append((pacsv, name))
    with contextlib.suppress(ImportError):
        import pyarrow as pa
        for name in ("memory_map", "input_stream", "OSFile"):
            if hasattr(pa, name):
                targets.append((pa, name))
    with contextlib.suppress(ImportError):
        import pyarrow.fs as pafs
        for name in ("LocalFileSystem", "FileSystem", "SubTreeFileSystem"):
            if hasattr(pafs, name):
                targets.append((pafs, name))
    return targets


@contextlib.contextmanager
def _no_new_fds():
    """Kernel-level layer: no new file descriptors while active (POSIX).
    Catches every bypass of the Python-level patches — io.open_code,
    pyarrow C++ filesystems, ctypes, sockets — with EMFILE."""
    if not _HAS_RLIMIT:  # pragma: no cover — non-POSIX
        yield
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (0, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


@contextlib.contextmanager
def forbid_io():
    """Every listed entry point raises PurityViolation while active, the
    environment is unreadable, and the kernel refuses new file descriptors
    for everything else."""
    environ_proxy = _BlockedEnviron(dict(os.environ))
    with contextlib.ExitStack() as stack:
        for obj, name in _targets():
            stack.enter_context(mock.patch.object(obj, name, _blocked))
        for name in ("putenv", "unsetenv"):
            stack.enter_context(mock.patch.object(os, name, _blocked))
        stack.enter_context(mock.patch.object(os, "getenv", environ_proxy.get))
        stack.enter_context(mock.patch.object(os, "environ", environ_proxy))
        if hasattr(os, "environb"):
            environb_proxy = _BlockedEnviron(dict(os.environb))
            stack.enter_context(mock.patch.object(os, "environb", environb_proxy))
            if hasattr(os, "getenvb"):
                stack.enter_context(
                    mock.patch.object(os, "getenvb", environb_proxy.get))
        stack.enter_context(_no_new_fds())
        yield
