"""Parser helpers for openlifu test-app run log files.

Each run log is produced by a stock :mod:`logging` ``FileHandler`` with
the format::

    HH:MM:SS [+   E.EEEs] LEVEL   logger.name: message

(see ``RUN_LOG_FORMAT`` in :mod:`lifu.lifu_connector`).  Continuation
lines from multi-line messages (notably tracebacks emitted via
``exc_info=True``) carry no timestamp prefix; they are appended to the
preceding record's ``message``.

The functions in this module are deliberately framework-free.  They
return plain Python lists of dicts so the caller can do, for example::

    import pandas as pd
    from logs.parse_logs import parse_log_file
    df = pd.DataFrame.from_records(parse_log_file("run.log"))

without forcing pandas (or anything else) into this project's runtime
dependencies.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time
from typing import Iterable, Iterator


# ``HH:MM:SS [+   E.EEEs] LEVEL   name: message``
# ``elapsed`` is rendered with ``%8.3f`` so it can be padded with spaces.
_LINE_RE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})"
    r"\s+\[\+\s*(?P<elapsed>-?\d+(?:\.\d+)?)s\]"
    r"\s+(?P<level>[A-Z]+)"
    r"\s+(?P<name>[^:]+?):\s?(?P<message>.*)$"
)

# Header banner emitted by ``_open_run_log``.  We pull session/id/file
# out of it so callers can attach those columns to every record.
_HEADER_RE = re.compile(
    r"\[SESSION\]\s+Run log opened:\s+"
    r"session='(?P<session_name>.*?)'\s+"
    r"id='(?P<session_id>.*?)'\s+"
    r"file='(?P<file>.*?)'\s*$"
)

# Filename pattern: YYYYMMDD_<sid>_runNN_HH_MM_SS.log
_FILENAME_RE = re.compile(
    r"^(?P<date>\d{8})_(?P<sid>.+)_run(?P<run>\d+)_"
    r"(?P<hh>\d{2})_(?P<mm>\d{2})_(?P<ss>\d{2})\.log$"
)


def _date_from_filename(path):
    """Best-effort extraction of the run start date from the filename.

    Run logs have only ``HH:MM:SS`` timestamps inside, so to build a
    full ``datetime`` we lean on the date encoded in the filename.
    Returns ``None`` if the filename doesn't match the expected pattern.
    """
    base = os.path.basename(path)
    m = _FILENAME_RE.match(base)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("date"), "%Y%m%d").date()
    except ValueError:
        return None


def _combine(d, t):
    if d is None:
        return None
    return datetime.combine(d, t).isoformat()


def parse_log_lines(lines, run_date=None):
    """Parse an iterable of log lines into a list of record dicts.

    Parameters
    ----------
    lines:
        Iterable of strings (one log line each, with or without trailing
        newlines).
    run_date:
        Optional :class:`datetime.date` for the run.  When supplied the
        returned records include an ISO ``timestamp`` field combining
        this date with each line's wall clock.  When omitted only the
        raw ``time`` (``HH:MM:SS``) string is included.

    Returns
    -------
    list[dict]
        One dict per logical log record.  Continuation lines from
        multi-line messages are joined into the preceding record's
        ``message`` field with ``\\n`` separators.
    """
    records = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            # Blank lines belong to the previous record's message body
            # if there is one (preserves traceback spacing); otherwise
            # they're skipped.
            if records:
                records[-1]["message"] += "\n"
            continue
        m = _LINE_RE.match(line)
        if not m:
            if records:
                records[-1]["message"] += "\n" + line
            # else: stray header text before the first record; drop it.
            continue
        try:
            t = datetime.strptime(m.group("time"), "%H:%M:%S").time()
        except ValueError:
            t = time(0, 0, 0)
        rec = {
            "time": m.group("time"),
            "elapsed_s": float(m.group("elapsed")),
            "level": m.group("level"),
            "logger": m.group("name").strip(),
            "message": m.group("message"),
        }
        ts = _combine(run_date, t)
        if ts is not None:
            rec["timestamp"] = ts
        records.append(rec)
    return records


def parse_log_file(path, attach_session_columns=True):
    """Parse a run log file from disk.

    The run date is taken from the filename (see ``_FILENAME_RE``).
    When ``attach_session_columns`` is true, every returned record also
    carries ``session_name``, ``session_id``, and ``log_file`` fields
    extracted from the opening banner (or filename if the banner is
    missing).
    """
    run_date = _date_from_filename(path)
    with open(path, "r", encoding="utf-8") as f:
        records = parse_log_lines(f, run_date=run_date)

    if not attach_session_columns:
        return records

    session_name = ""
    session_id = ""
    log_file = os.path.abspath(path)
    for rec in records:
        m = _HEADER_RE.search(rec["message"])
        if m:
            session_name = m.group("session_name")
            session_id = m.group("session_id")
            log_file = m.group("file") or log_file
            break

    if not session_id:
        base = os.path.basename(path)
        fm = _FILENAME_RE.match(base)
        if fm:
            session_id = fm.group("sid")

    for rec in records:
        rec["session_name"] = session_name
        rec["session_id"] = session_id
        rec["log_file"] = log_file
    return records


def main(argv=None):
    """Tiny CLI: ``python -m logs.parse_logs path/to/run.log [...]``.

    Prints the parsed records as JSON Lines so the script is useful on
    its own without pandas.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="Run log files to parse.")
    parser.add_argument(
        "--no-session", action="store_true",
        help="Don't attach session_name/session_id/log_file columns.",
    )
    args = parser.parse_args(argv)

    out = sys.stdout
    for p in args.paths:
        for rec in parse_log_file(p, attach_session_columns=not args.no_session):
            out.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
