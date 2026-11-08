import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "blocker"))
import block  # noqa: E402

CFG = """
when mon-fri 19:00-22:00
when sat 22:00-01:00   # wraps past midnight
x.com
https://www.bilibili.com/video/123
"""


class ParseTest(unittest.TestCase):
    def test_parse(self):
        w, d = block.parse_config(CFG)
        self.assertEqual(len(w), 2)
        self.assertEqual(d, ["x.com", "www.x.com", "www.bilibili.com"])

    def test_bad_domain(self):
        with self.assertRaises(block.ConfigError):
            block.parse_config("not a domain")

    def test_days(self):
        self.assertEqual(block.parse_days("mon-fri"), {0, 1, 2, 3, 4})
        self.assertEqual(block.parse_days("sat-mon"), {5, 6, 0})
        self.assertEqual(block.parse_days("daily"), set(range(7)))


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.w, _ = block.parse_config(CFG)

    def at(self, s):
        return block.in_window(self.w, dt.datetime.fromisoformat(s))

    def test_weekday(self):
        self.assertTrue(self.at("2026-11-09T19:00"))   # Mon
        self.assertTrue(self.at("2026-11-09T21:59"))
        self.assertFalse(self.at("2026-11-09T22:00"))
        self.assertFalse(self.at("2026-11-09T18:59"))

    def test_wrap(self):
        self.assertTrue(self.at("2026-11-14T23:30"))   # Sat
        self.assertTrue(self.at("2026-11-15T00:30"))   # Sun early = Sat window
        self.assertFalse(self.at("2026-11-15T01:00"))
        self.assertFalse(self.at("2026-11-15T19:30"))  # Sun evening not listed

    def test_no_windows_always(self):
        self.assertTrue(block.in_window([], dt.datetime(2026, 1, 1, 3, 0)))


class HostsTest(unittest.TestCase):
    BASE = "127.0.0.1 localhost\n::1 localhost\n"

    def test_add_remove_idempotent(self):
        on = block.desired_hosts(self.BASE, ["x.com"], True)
        self.assertIn("0.0.0.0 x.com", on)
        self.assertEqual(block.desired_hosts(on, ["x.com"], True), on)
        self.assertEqual(block.desired_hosts(on, ["x.com"], False), self.BASE)

    def test_apply_end_to_end(self):
        d = tempfile.mkdtemp()
        cfg, hosts = os.path.join(d, "blocklist"), os.path.join(d, "hosts")
        open(cfg, "w").write("x.com\n")
        open(hosts, "w").write(self.BASE)
        args = type("A", (), {"config": cfg, "hosts": hosts, "minutes": 5})()
        block.flush_dns = lambda: None
        block.cmd_apply(args)
        self.assertIn("www.x.com", open(hosts).read())
        block.cmd_pause(args)
        self.assertEqual(open(hosts).read(), self.BASE)
        block.cmd_resume(args)
        self.assertIn("x.com", open(hosts).read())


if __name__ == "__main__":
    unittest.main()
