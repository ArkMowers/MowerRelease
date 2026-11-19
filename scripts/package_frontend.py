"""Publish the frontend shipped in a verified Windows or Android package."""

import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def extract_ui(archive, platform, destination):
    prefix = {"windows": "mower/_internal/ui/dist/", "android": "mower/ui/dist/"}[platform]
    with ZipFile(archive) as bundle:
        if not any(entry.filename.replace("\\", "/") == prefix + "relay-ui.json" for entry in bundle.infolist()):
            return False  # Packages built before relay support have no build ID.
        for member in bundle.infolist():
            name = member.filename.replace("\\", "/")
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            relative = PurePosixPath(name[len(prefix):])
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid relative frontend path")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("xb") as out:
                shutil.copyfileobj(source, out)
    return True


def publish_package(archive, platform):
    from publish_relay_ui import publish_to_r2

    with tempfile.TemporaryDirectory(prefix="mower-packaged-ui-") as temp:
        if extract_ui(archive, platform, Path(temp)):
            publish_to_r2(Path(temp))


if __name__ == "__main__":
    publish_package(Path(sys.argv[1]), sys.argv[2])
