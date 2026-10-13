import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import updater as U


class TestUpdater(unittest.TestCase):
    def test_compare(self):
        self.assertGreater(U.vtuple("1.2.10"), U.vtuple("v1.2.9"))
        self.assertEqual(U.vtuple("v1.2.1"), (1, 2, 1))

    def test_site_then_fallback(self):
        with mock.patch.object(U, "_get_json", return_value={"version": "v9.0.0", "url": "https://x/download/SignalPilot-Setup.exe"}):
            self.assertEqual(U.newer_available({}, "1.2.1"), ("9.0.0", "https://x/download/SignalPilot-Setup.exe"))
            self.assertIsNone(U.newer_available({}, "9.0.0"))
        def fake(url):
            if "latest-version" in url: raise OSError("down")
            return {"tag_name": "v1.3.0"}
        with mock.patch.object(U, "_get_json", side_effect=fake), \
             mock.patch.object(U.C, "licence_url", return_value="https://site.example/api/licence"):
            v, url = U.newer_available({}, "1.2.1")
            self.assertEqual(v, "1.3.0"); self.assertEqual(url, "https://site.example/download/SignalPilot-Setup.exe")
        with mock.patch.object(U, "_get_json", side_effect=OSError):
            self.assertIsNone(U.newer_available({}, "1.2.1"))
