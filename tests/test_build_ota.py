import hashlib
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_ota import build


class BuildOtaTests(unittest.TestCase):
    def test_windows_delta_has_changed_files_and_complete_target_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new, patch = (root / n for n in ("old.zip", "new.zip", "patch.zip"))
            with zipfile.ZipFile(old, "w") as archive:
                archive.writestr("mower/keep.txt", b"same")
                archive.writestr("mower/changed.txt", b"old")
                archive.writestr("mower/deleted.txt", b"delete")
            with zipfile.ZipFile(new, "w") as archive:
                archive.writestr("mower/keep.txt", b"same")
                archive.writestr("mower/changed.txt", b"new")
                archive.writestr("mower/added.txt", b"added")
            build(
                old,
                new,
                patch,
                from_version="v4.1.6-alpha.7",
                to_version="v4.1.6-alpha.8",
                platform="windows",
                arch="x64",
            )
            with zipfile.ZipFile(patch) as archive:
                data = json.loads(archive.read("ota.json"))
                self.assertEqual(data["from"], "4.1.6-alpha.7")
                self.assertEqual(
                    data["changed"], ["mower/added.txt", "mower/changed.txt"]
                )
                self.assertEqual(
                    set(data["files"]),
                    {"mower/keep.txt", "mower/changed.txt", "mower/added.txt"},
                )
                self.assertEqual(archive.read("payload/mower/changed.txt"), b"new")
                self.assertNotIn("payload/mower/keep.txt", archive.namelist())
                self.assertEqual(
                    data["files"]["mower/keep.txt"]["sha256"],
                    hashlib.sha256(b"same").hexdigest(),
                )

    def test_linux_delta_carries_safe_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new, patch = (
                root / n for n in ("old.tar.gz", "new.tar.gz", "patch.zip")
            )
            for path, value in ((old, b"old"), (new, b"new")):
                with tarfile.open(path, "w:gz") as archive:
                    data = root / "lib.so.1"
                    data.write_bytes(value)
                    archive.add(data, arcname="mower/lib.so.1")
                    link = tarfile.TarInfo("mower/lib.so")
                    link.type = tarfile.SYMTYPE
                    link.linkname = "lib.so.1"
                    archive.addfile(link)
            build(
                old,
                new,
                patch,
                from_version="v4.1.6-alpha.7",
                to_version="v4.1.6-alpha.8",
                platform="linux",
                arch="x64",
            )
            with zipfile.ZipFile(patch) as archive:
                data = json.loads(archive.read("ota.json"))
                self.assertEqual(
                    data["files"]["mower/lib.so"],
                    {"type": "symlink", "target": "lib.so.1"},
                )
                self.assertEqual(data["changed"], ["mower/lib.so.1"])

    def test_android_reuses_unchanged_runtime_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new, patch = (root / n for n in ("old.zip", "new.zip", "patch.zip"))
            for path, version in ((old, "old"), (new, "new")):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(
                        "mower-android.json", '{"version":"' + version + '"}'
                    )
                    archive.writestr(
                        "python-runtime.zip.xz", b"same compressed runtime"
                    )
                    archive.writestr("mower/server.py", version)
            build(
                old,
                new,
                patch,
                from_version="v4.1.6-alpha.7",
                to_version="v4.1.6-alpha.8",
                platform="android",
                arch="arm64",
            )
            with zipfile.ZipFile(patch) as archive:
                data = json.loads(archive.read("ota.json"))
                self.assertIn("python-runtime.zip.xz", data["files"])
                self.assertNotIn("python-runtime.zip.xz", data["changed"])
                self.assertEqual(
                    set(data["changed"]), {"mower-android.json", "mower/server.py"}
                )


if __name__ == "__main__":
    unittest.main()
