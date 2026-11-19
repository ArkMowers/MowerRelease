"""Extract a Nightly package's static frontend from its platform-specific layout."""

import shutil
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def extract_ui(archive, platform, destination):
    prefix = {"windows": "mower/_internal/ui/dist/", "android": "mower/ui/dist/"}[platform]
    with ZipFile(archive) as bundle:
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
    if not (destination / "relay-ui.json").is_file():
        raise ValueError(f"{platform} package lacks its frontend build identifier")


if __name__ == "__main__":
    extract_ui(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
