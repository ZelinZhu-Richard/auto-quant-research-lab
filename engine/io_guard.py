"""Runtime I/O guard for signal purity (SPEC §2).

A signal that loads data itself (instead of using the panel it is handed)
bypasses truncation entirely and can read close(t+1) — the one leak the
truncate-and-compare check cannot see. Both the S2 harness AND the S3
engine therefore run compute_signal under this guard.

Coverage: builtins.open, io.open, os.open/fdopen, pathlib.Path
open/read_text/read_bytes, pandas readers, pyarrow parquet/dataset/csv
readers, numpy file loaders, socket creation. A signal could still bypass
this with ctypes or other exotica; the structural backstops are the
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


class PurityViolation(EngineError):
    """The signal attempted I/O during compute_signal."""


def _blocked(*_args, **_kwargs):
    raise PurityViolation(
        "signal attempted I/O during compute_signal (purity violation, SPEC §2)"
    )


def _targets() -> list[tuple[object, str]]:
    targets: list[tuple[object, str]] = [
        (builtins, "open"),
        (io, "open"),
        (os, "open"),
        (os, "fdopen"),
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
    return targets


@contextlib.contextmanager
def forbid_io():
    """Every listed entry point raises PurityViolation while active."""
    with contextlib.ExitStack() as stack:
        for obj, name in _targets():
            stack.enter_context(mock.patch.object(obj, name, _blocked))
        yield
