import hashlib
import io
import json
import lzma
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bsdiff4
from build_ota import build


class BuildOtaTests(unittest.TestCase):
    @staticmethod
    def runtime_blob(changed=b"same"):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("usr/", b"")
            archive.writestr("usr/local/bin/python3.12", b"python")
            archive.writestr("usr/lib/changed.so", changed)
            archive.writestr(".symlinks.json", b"{}")
        return lzma.compress(output.getvalue(), preset=6)

    def test_changed_large_library_also_uses_binary_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new, delta = (
                root / name for name in ("old.zip", "new.zip", "delta.zip")
            )
            before = os.urandom(512 * 1024)
            after = before[:123456] + b"replacement" + before[123467:]
            name = "mower/_internal/base_library.zip"
            for path, contents in ((old, before), (new, after)):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(name, contents)
            build(
                old,
                new,
                delta,
                from_version="v4.1.6-alpha.9.g12345678",
                to_version="v4.1.6-alpha.9.g87654321",
                platform="windows",
                arch="x64",
            )
            with zipfile.ZipFile(delta) as archive:
                manifest = json.loads(archive.read("ota.json"))
                self.assertEqual(manifest["format"], 2)
                self.assertEqual(
                    bsdiff4.patch(before, archive.read("patch/" + name)), after
                )

    def test_changed_windows_executable_uses_small_verified_binary_patch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new, delta = (
                root / name for name in ("old.zip", "new.zip", "delta.zip")
            )
            before = os.urandom(1024 * 1024)
            after = before[:500000] + b"new build" + before[500009:]
            for path, contents in ((old, before), (new, after)):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("mower/mower.exe", contents)
            build(
                old,
                new,
                delta,
                from_version="v4.1.6-alpha.7",
                to_version="v4.1.6-alpha.8",
                platform="windows",
                arch="x64",
            )
            with zipfile.ZipFile(delta) as archive:
                manifest = json.loads(archive.read("ota.json"))
                self.assertEqual(manifest["format"], 2)
                self.assertNotIn("payload/mower/mower.exe", archive.namelist())
                patch = archive.read("patch/mower/mower.exe")
                self.assertEqual(bsdiff4.patch(before, patch), after)
                self.assertLess(delta.stat().st_size, 10000)

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
                to_version="v4.1.6-alpha.9.g12345678",
                platform="windows",
                arch="x64",
            )
            with zipfile.ZipFile(patch) as archive:
                data = json.loads(archive.read("ota.json"))
                self.assertEqual(data["from"], "4.1.6-alpha.7")
                self.assertEqual(data["to"], "4.1.6-alpha.9.g12345678")
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
                    archive.writestr("python-runtime.zip.xz", self.runtime_blob())
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
                self.assertEqual(data["format"], 2)
                self.assertEqual(data["runtime"]["changed"], [])
                self.assertEqual(
                    set(data["changed"]), {"mower-android.json", "mower/server.py"}
                )

    def test_android_runtime_changes_only_upload_inner_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new, patch = (root / n for n in ("old.zip", "new.zip", "patch.zip"))
            for path, version, runtime in (
                (old, "old", self.runtime_blob(b"old library")),
                (new, "new", self.runtime_blob(b"new library")),
            ):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(
                        "mower-android.json", '{"version":"' + version + '"}'
                    )
                    archive.writestr("python-runtime.zip.xz", runtime)
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
                manifest = json.loads(archive.read("ota.json"))
                self.assertEqual(manifest["format"], 2)
                self.assertEqual(manifest["runtime"]["changed"], ["usr/lib/changed.so"])
                self.assertEqual(
                    archive.read("runtime/usr/lib/changed.so"), b"new library"
                )
                self.assertNotIn("payload/python-runtime.zip.xz", archive.namelist())


if __name__ == "__main__":
    unittest.main()
