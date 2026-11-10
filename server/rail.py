"""NTES access and normalisation for the web UI."""
import logging
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ntes import NTESClient, NTESError

log = logging.getLogger("patri.rail")

IST = ZoneInfo("Asia/Kolkata")
_client = NTESClient(timeout=20, retries=1)

_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
_STAMP = re.compile(r"(\d{1,2}):(\d{2})\s+(\d{1,2})-([A-Za-z]{3})")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_DELAY = re.compile(r"(\d+):(\d{2})")

_STATE = {0: "yet_to_start", 1: "running", 2: "finished"}


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
        return int(m.group(1)) * 60 + int(m.group(2))
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
    if _flag(raw.get("isArrDSTN")):
        return "finished"
    return _STATE.get(_int(raw.get("TRUNST")), "running")


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


def _fetch(no, day):
    try:
        return _client.live_status(no, day.strftime("%d-%b-%Y"))
    except NTESError as e:
        log.info("ntes %s %s: %s", no, day, e)
        return None


def page(train_no):
    """Running status for the instance in progress right now, else the next one."""
    no = str(train_no).strip()
    today = datetime.now(IST).date()
    run_date = today
    raw = _fetch(no, today)

    if raw is None:
        for offset in (-1, 1):
            day = today + timedelta(days=offset)
            raw = _fetch(no, day)
            if raw is not None:
                run_date = day
                break
    if raw is None:
        raise LookupError(f"NTES has no running data for train {no}")

    # A train that left yesterday is still the one people are waiting for today.
    if run_date == today and _state(raw) == "yet_to_start":
        previous = _fetch(no, today - timedelta(days=1))
        if previous is not None and _state(previous) == "running":
            raw, run_date = previous, today - timedelta(days=1)

    if not raw.get("STNS"):
        raise LookupError(f"NTES returned no route for train {no}")

    return _normalise(no, raw, run_date)


def clean_query(text):
    return re.sub(r"\s+", " ", re.sub(r"[^0-9A-Za-z .-]", " ", str(text or "").strip()))[:40].strip()


def search(query, by="name"):
    q = clean_query(query)
    if len(q) < 2:
        raise ValueError("enter at least 2 characters")
    try:
        payload = _client.search(q) or {}
    except NTESError as e:
        raise LookupError(str(e)) from e

    results = []
    for t in payload.get("Trains") or []:
        number = str(t.get("TrainNumber") or "").strip()
        if not number:
            continue
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

    return {"query": q, "by": by, "results": results[:40], "fetched_at": time.time()}
