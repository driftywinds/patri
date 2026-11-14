"""Table-driven tests for the NTES normaliser (no network)."""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from server import rail


class DelayTest(unittest.TestCase):
    def test_forms(self):
        cases = {
            "On Time": 0,
            "00:13": 13,
            "1:05": 65,
            "-0:05": -5,
            "-1:30": -90,
            "35": 35,
            "-7": -7,
            "**UA**": None,
            "": None,
            None: None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(rail._delay(raw), expected)


class WhenTest(unittest.TestCase):
    RUN = date(2026, 11, 10)

    def test_clock(self):
        self.assertEqual(rail._when("17:20 10-Nov", self.RUN), datetime(2026, 11, 10, 17, 20))

    def test_new_year_rollover(self):
        got = rail._when("00:30 01-Jan", date(2025, 12, 31))
        self.assertEqual(got, datetime(2026, 1, 1, 0, 30))

    def test_specials(self):
        for text in ("Source", "Destination", "", None, "**UA**"):
            with self.subTest(text=text):
                self.assertIsNone(rail._when(text, self.RUN))

    def test_day_rollover_slot(self):
        self.assertEqual(rail._slot(datetime(2026, 11, 11, 0, 30), self.RUN), {"t": "00:30", "d": 1})


class StateTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(rail._state({"TRUNST": 0}), "yet_to_start")
        self.assertEqual(rail._state({"TRUNST": 1}), "running")
        self.assertEqual(rail._state({"TRUNST": 2}), "finished")
        self.assertEqual(rail._state({"isArrDSTN": True}), "finished")

    def test_missing_flag_is_unknown_not_running(self):
        # The bug that made 22210 unreachable: an empty payload was treated as running.
        self.assertEqual(rail._state({}), "unknown")
        self.assertEqual(rail._state(None), "unknown")


class NextRunTest(unittest.TestCase):
    def test_weekdays(self):
        # 2026-11-10 is a Tuesday; 22210 runs Tue, Sat.
        self.assertEqual(rail._next_run("Tue,sat", date(2026, 11, 10)), date(2026, 11, 14))
        self.assertEqual(rail._next_run("mon", date(2026, 11, 10)), date(2026, 11, 16))

    def test_unknown(self):
        self.assertIsNone(rail._next_run("Daily", date(2026, 11, 10)))
        self.assertIsNone(rail._next_run("", date(2026, 11, 10)))


class LegTest(unittest.TestCase):
    RUN = date(2026, 11, 10)

    def test_delay_from_expected(self):
        leg = rail._leg(datetime(2026, 11, 10, 17, 0), datetime(2026, 11, 10, 17, 12), None, self.RUN)
        self.assertEqual(leg["delay"], 12)
        self.assertEqual(leg["exp"], {"t": "17:12", "d": 0})

    def test_expected_derived_from_reported_delay(self):
        leg = rail._leg(datetime(2026, 11, 10, 17, 0), None, "00:20", self.RUN)
        self.assertEqual(leg["delay"], 20)
        self.assertEqual(leg["exp"]["t"], "17:20")

    def test_missing_everything(self):
        leg = rail._leg(None, None, None, self.RUN)
        self.assertEqual(leg, {"sch": None, "exp": None, "delay": None})


class SearchKeyTest(unittest.TestCase):
    def test_clean_query(self):
        self.assertEqual(rail.clean_query("  Rajdhani  "), "Rajdhani")
        self.assertEqual(rail.clean_query("rajdhani!"), "rajdhani")
        self.assertEqual(rail.clean_query("a" * 80), "a" * 40)


class PageFallbackTest(unittest.TestCase):
    """The screenshot bug: a run that finished days ago was shown as current."""

    today = datetime.now(rail.IST).date()

    def payload(self, state):
        return {
            "STNS": [
                {"Sr": 1, "SC": "AAA", "SN": "A", "STA": "Source", "STD": "10:00 10-Nov", "DIST": 0}
            ],
            "TRUNST": state,
        }

    def test_running_instance_from_days_ago_is_used(self):
        day = self.today - timedelta(days=3)
        with (
            mock.patch.object(rail, "_instances", return_value=[(day, 1)]),
            mock.patch.object(rail, "_fetch", lambda no, d: self.payload(1) if d == day else None),
            mock.patch.object(rail, "_from_schedule", return_value={"source": "schedule"}),
        ):
            out = rail.page("12345")
        self.assertEqual(out["train"]["run_date"], day.isoformat())

    def test_finished_instance_from_days_ago_falls_back_to_schedule(self):
        day = self.today - timedelta(days=3)
        with (
            mock.patch.object(rail, "_instances", return_value=[(day, 2)]),
            mock.patch.object(rail, "_fetch", lambda no, d: self.payload(2) if d == day else None),
            mock.patch.object(rail, "_from_schedule", return_value={"source": "schedule"}),
        ):
            out = rail.page("12345")
        self.assertEqual(out, {"source": "schedule"})


if __name__ == "__main__":
    unittest.main()
