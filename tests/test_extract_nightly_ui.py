import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.extract_nightly_ui import extract_ui


class NightlyPackageTests(unittest.TestCase):
    def test_windows_and_android_frontend_come_from_the_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for platform, prefix in (("windows", "mower\\_internal\\ui\\dist\\"), ("android", "mower/ui/dist/")):
                with self.subTest(platform=platform):
                    path = root / f"{platform}.zip"
                    with ZipFile(path, "w") as archive:
                        archive.writestr(prefix + "relay-ui.json", platform)
                        archive.writestr("mower/unrelated", "skip")
                    out = root / platform
                    extract_ui(path, platform, out)
                    self.assertEqual([p.name for p in out.iterdir()], ["relay-ui.json"])
                    self.assertEqual((out / "relay-ui.json").read_text(), platform)
