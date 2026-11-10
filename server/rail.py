"""NTES access and normalisation for the web UI."""
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ntes import NTESClient, NTESError

log = logging.getLogger("patri.rail")

IST = ZoneInfo("Asia/Kolkata")
_client = NTESClient(timeout=20, retries=1)

# One shared cap on NTES calls: the upstream has no public API and bans by IP.
_NTES = threading.BoundedSemaphore(int(os.environ.get("NTES_CONCURRENCY", "4")))
_counters = {"calls": 0, "last_ok": 0.0}
_counters_lock = threading.Lock()

_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
_STAMP = re.compile(r"(\d{1,2}):(\d{2})\s+(\d{1,2})-([A-Za-z]{3})")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_DELAY = re.compile(r"(-?\d+):(\d{2})")
_DAYNAME = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")

_STATE = {0: "yet_to_start", 1: "running", 2: "finished"}

_DAYS = {name: i for i, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}


def stats():
    with _counters_lock:
        return dict(_counters)


def _call(fn, *args):
    """Run one NTES call under the global concurrency cap."""
    with _NTES:
        with _counters_lock:
            _counters["calls"] += 1
        result = fn(*args)
        with _counters_lock:
            _counters["last_ok"] = time.time()
        return result


def _when(text, run_date):
    """'17:20 10-Nov' -> datetime. None for Source / Destination / **UA**."""
    m = _STAMP.search(str(text or ""))
    if not m:
        return None
    hour, minute, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    month = _MONTHS.get(m.group(4).title())
    if not month:
        return None
    year = run_date.year + (1 if month == 1 and run_date.month == 12 else 0)
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _day(text):
    """'10-Nov-2026' -> date."""
    m = _DAYNAME.match(str(text or "").strip())
    if not m:
        return None
    month = _MONTHS.get(m.group(2).title())
    if not month:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def _clock(text):
    m = _CLOCK.search(str(text or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _delay(value):
    s = str(value if value is not None else "").strip()
    if not s or "UA" in s:
        return None
    if "on time" in s.lower():
        return 0
    m = _DELAY.search(s)
    if m:
        minutes = abs(int(m.group(1))) * 60 + int(m.group(2))
        return -minutes if m.group(1).startswith("-") else minutes
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else None


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _flag(value):
    return str(value).strip().lower() in ("true", "1", "yes")


def _state(raw):
    if raw is None:
        return "unknown"
    if _flag(raw.get("isArrDSTN")):
        return "finished"
    run_state = _int(raw.get("TRUNST"))
    if run_state is None:
        return "unknown"
    return _STATE.get(run_state, "unknown")


def _next_run(days_of_run, after):
    """'Tue,sat' -> the next date after `after` that the train actually runs."""
    wanted = {
        _DAYS[token.strip().lower()[:3]]
        for token in str(days_of_run or "").split(",")
        if token.strip().lower()[:3] in _DAYS
    }
    if not wanted:
        return None
    for step in range(1, 15):
        day = after + timedelta(days=step)
        if day.weekday() in wanted:
            return day
    return None


def _pretty_days(days_of_run):
    parts = [p.strip().title() for p in str(days_of_run or "").split(",") if p.strip()]
    return ", ".join(parts)


def _slot_from_clock(text, day):
    t = str(text or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if not m:
        return None
    return {"t": f"{int(m.group(1)):02d}:{m.group(2)}", "d": max(0, (_int(day) or 1) - 1)}


def _slot(moment, run_date):
    if not moment:
        return None
    return {"t": moment.strftime("%H:%M"), "d": (moment.date() - run_date).days}


def _leg(sched, expected, reported, run_date):
    delay = None
    if sched and expected:
        delay = round((expected - sched).total_seconds() / 60)
    if delay is None:
        delay = _delay(reported)
    # NTES sometimes omits ETA/ETD (e.g. after leaving the last station) but
    # still reports the delay; keep an adjusted time on screen either way.
    if expected is None and sched and delay:
        expected = sched + timedelta(minutes=delay)
    return {"sch": _slot(sched, run_date), "exp": _slot(expected, run_date), "delay": delay}


def _normalise(no, raw, run_date):
    stops = []
    for item in raw.get("STNS") or []:
        sta = _when(item.get("STA"), run_date)
        std = _when(item.get("STD"), run_date)
        eta = _when(item.get("ETA"), run_date)
        etd = _when(item.get("ETD"), run_date)
        stops.append(
            {
                "seq": _int(item.get("Sr")) or 0,
                "code": str(item.get("SC") or "").strip(),
                "name": str(item.get("SN") or "").strip(),
                "dist": _int(item.get("DIST")),
                "pf": str(item.get("PF") or "").strip() or None,
                "arrived": _flag(item.get("ISA")),
                "departed": _flag(item.get("ISD")),
                "arr": _leg(sta, eta, item.get("DARR"), run_date),
                "dep": _leg(std, etd, item.get("DDEP"), run_date),
            }
        )
    stops.sort(key=lambda s: s["seq"])

    return {
        "train": {
            "no": str(raw.get("TN") or no).strip(),
            "name": str(raw.get("TNM") or "").strip(),
            "source": raw.get("SRC"),
            "source_name": raw.get("SRCN"),
            "dest": raw.get("DSTN"),
            "dest_name": raw.get("DSTNN"),
            "distance": _int(raw.get("TTLDIST")),
            "run_date": run_date.isoformat(),
        },
        "status": {
            "state": _state(raw),
            "delay": _delay(raw.get("LDEL")),
            "last_station": str(raw.get("LSTNN") or "").strip() or None,
            "last_code": str(raw.get("LSTN") or "").strip() or None,
            "last_update": _clock(raw.get("LTIME")),
            "next_station": str(raw.get("NSTNN") or "").strip() or None,
            "next_code": str(raw.get("NSTN") or "").strip() or None,
            "note": str(raw.get("LUPDFULL") or raw.get("LASTUPD") or "").strip() or None,
            "message": str(raw.get("AlertMsg") or "").strip() or None,
            "arrived_dest": _flag(raw.get("isArrDSTN")),
        },
        "stops": stops,
        "source": "ntes",
        "fetched_at": time.time(),
    }


def _from_schedule(no, today):
    """No run in the last/next day: fall back to the timetable and say when it next runs."""
    try:
        data = _call(_client.schedule, str(no)) or {}
    except NTESError as e:
        raise LookupError(str(e)) from e

    raw_stops = data.get("stations") or []
    if not raw_stops:
        raise LookupError(f"NTES has no schedule for train {no}")

    days = data.get("DaysOfRun") or ""
    next_run = _next_run(days, today)
    if next_run is None and "daily" in days.lower():
        next_run = today
    stops = []
    for item in raw_stops:
        stops.append(
            {
                "seq": _int(item.get("Sr")) or 0,
                "code": str(item.get("StationCode") or "").strip(),
                "name": str(item.get("StationName") or "").strip(),
                "dist": _int(item.get("Distance")),
                "pf": None,
                "arrived": False,
                "departed": False,
                "arr": {
                    "sch": _slot_from_clock(item.get("STA"), item.get("Day")),
                    "exp": None,
                    "delay": None,
                },
                "dep": {
                    "sch": _slot_from_clock(item.get("STD"), item.get("Day")),
                    "exp": None,
                    "delay": None,
                },
            }
        )
    stops.sort(key=lambda s: s["seq"])

    return {
        "train": {
            "no": str(data.get("TrainNumber") or no).strip(),
            "name": str(data.get("TrainName") or "").strip(),
            "source": data.get("Source"),
            "source_name": data.get("SourceName"),
            "dest": data.get("Destination"),
            "dest_name": data.get("DestinationName"),
            "distance": _int(stops[-1]["dist"]) if stops else None,
            "run_date": (next_run or today).isoformat(),
        },
        "status": {
            "state": "not_running",
            "delay": None,
            "last_station": None,
            "last_code": None,
            "last_update": None,
            "next_station": None,
            "next_code": None,
            "note": str(data.get("AlertMsg") or "").strip() or None,
            "message": None,
            "arrived_dest": False,
            "runs": _pretty_days(days),
            "next_run": next_run.isoformat() if next_run else None,
        },
        "stops": stops,
        "source": "ntes-schedule",
        "fetched_at": time.time(),
    }


def _fetch(no, day):
    try:
        raw = _call(_client.live_status, str(no), day.strftime("%d-%b-%Y"))
    except NTESError as e:
        log.info("ntes %s %s: %s", no, day, e)
        return None
    # A date the train does not run comes back with no stops, not an error.
    if not raw or not raw.get("STNS"):
        return None
    return raw


def _instances(no):
    """(start date, NTES status) pairs; the fix for multi-day journeys."""
    try:
        info = _call(_client.train_info, str(no)) or {}
    except NTESError:
        return []
    out = []
    for inst in info.get("vInstanceList") or []:
        day = _day(inst.get("startDate"))
        if day:
            out.append((day, _int(inst.get("trainStatus"))))
    return out


def page(train_no):
    """The run in progress, else the nearest one around today."""
    no = str(train_no).strip()
    today = datetime.now(IST).date()
    instances = _instances(no)

    # A journey can be 3+ days, so yesterday's run may still be the live one.
    running = sorted([d for d, status in instances if status == 1], reverse=True)
    for day in running[:2]:
        raw = _fetch(no, day)
        if raw is not None:
            return _normalise(no, raw, day)

    raw = _fetch(no, today)
    if raw is not None:
        if _state(raw) == "yet_to_start":
            prev = _fetch(no, today - timedelta(days=1))
            if prev is not None and _state(prev) == "running":
                return _normalise(no, prev, today - timedelta(days=1))
        return _normalise(no, raw, today)

    # Dates NTES itself reported, nearest first.
    for day in sorted({d for d, _ in instances}, key=lambda d: abs((d - today).days))[:3]:
        if day == today:
            continue
        raw = _fetch(no, day)
        if raw is not None:
            return _normalise(no, raw, day)

    for offset in (-1, 1):
        day = today + timedelta(days=offset)
        raw = _fetch(no, day)
        if raw is not None:
            return _normalise(no, raw, day)

    return _from_schedule(no, today)


def exceptions(train_no):
    """Cancellations / diversions / reschedules, or []."""
    try:
        data = _call(_client.exceptions, str(train_no)) or {}
    except NTESError as e:
        log.info("ntes exceptions %s: %s", train_no, e)
        return []
    out = []
    for row in data.get("Exceptions") or []:
        kind = str(row.get("ExceptionType") or row.get("ExceptionTypeDesc") or "").strip()
        if not kind:
            continue
        out.append(
            {
                "date": str(row.get("ExceptionDate") or "").strip() or None,
                "type": kind,
                "note": str(row.get("ExceptionTypeDesc") or "").strip() or None,
            }
        )
    return out


def clean_query(text):
    return re.sub(r"\s+", " ", re.sub(r"[^0-9A-Za-z .-]", " ", str(text or "").strip()))[:40].strip()


def search(query):
    q = clean_query(query)
    if len(q) < 2:
        raise ValueError("enter at least 2 characters")
    try:
        payload = _call(_client.search, q) or {}
    except NTESError as e:
        raise LookupError(str(e)) from e

    results = []
    seen = set()
    for t in payload.get("Trains") or []:
        number = str(t.get("TrainNumber") or "").strip()
        if not number or number in seen:
            continue
        seen.add(number)
        results.append(
            {
                "no": number,
                "name": str(t.get("TrainName") or "").strip(),
                "type": str(t.get("Type") or "").strip() or None,
                "source": t.get("Source"),
                "source_name": t.get("SourceName"),
                "dest": t.get("Destination"),
                "dest_name": t.get("DestinationName"),
            }
        )
    if q.isdigit():
        results.sort(key=lambda r: (r["no"] != q, r["name"]))

    return {"query": q, "results": results[:40], "fetched_at": time.time()}
