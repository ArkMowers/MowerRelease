import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_frontend import extract_ui, publish_package


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

    def test_package_before_relay_support_skips_r2(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "old.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("mower/ui/dist/index.html", "old frontend")
            with patch("publish_relay_ui.publish_to_r2") as upload:
                publish_package(path, "android")
                upload.assert_not_called()

    def test_release_upload_uses_its_embedded_identifier(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "version.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("mower/ui/dist/relay-ui.json", '{"build":"specific"}')
            with patch("publish_relay_ui.publish_to_r2", side_effect=lambda root: self.assertEqual((root/"relay-ui.json").read_text(), '{"build":"specific"}')) as upload:
                publish_package(path, "android")
                upload.assert_called_once()
