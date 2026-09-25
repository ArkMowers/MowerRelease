"""Build a file-level Mower OTA from two published portable archives."""

import argparse
import hashlib
import json
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_FILES = 50000
MAX_UNPACKED = 8 * 1024**3
VERSION = re.compile(r"v?\d+\.\d+\.\d+(?:-alpha\.\d+)?\Z")


def safe_name(name):
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or ":" in name
        or not name.startswith("mower/")
        or path.as_posix() != name
    ):
        raise ValueError(f"unsafe package path: {name}")
    return name


def digest(stream):
    result = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        result.update(chunk)
    return result.hexdigest()


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.is_zip = self.path.suffix == ".zip"
        self.archive = (
            zipfile.ZipFile(self.path)
            if self.is_zip
            else tarfile.open(self.path, "r:gz")  # noqa: SIM115 - closed by __exit__
        )
        self.members = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.archive.close()

    def open(self, name):
        return (
            self.archive.open(self.members[name])
            if self.is_zip
            else self.archive.extractfile(self.members[name])
        )

    def files(self):
        result = {}
        members = self.archive.infolist() if self.is_zip else self.archive.getmembers()
        if len(members) > MAX_FILES:
            raise ValueError("too many archive entries")
        total = 0
        for member in members:
            name = member.filename if self.is_zip else member.name.removeprefix("./")
            if name.endswith("/") or (not self.is_zip and member.isdir()):
                continue
            safe_name(name)
            if name in result:
                raise ValueError(f"duplicate package path: {name}")
            mode = (
                (member.external_attr >> 16) & 0o777
                if self.is_zip
                else member.mode & 0o777
            )
            mode = mode or 0o644
            if not self.is_zip and member.issym():
                target = member.linkname
                if (
                    not target
                    or PurePosixPath(target).is_absolute()
                    or ".." in PurePosixPath(target).parts
                ):
                    raise ValueError(f"unsafe symlink: {name}")
                result[name] = {"type": "symlink", "target": target}
            elif (self.is_zip and stat.S_ISLNK(member.external_attr >> 16)) or (
                not self.is_zip and not (member.isfile() or member.islnk())
            ):
                raise ValueError(f"unsupported package entry: {name}")
            else:
                total += member.file_size if self.is_zip else member.size
                if total > MAX_UNPACKED:
                    raise ValueError("package expands beyond 8 GiB")
                self.members[name] = member
                with self.open(name) as source:
                    result[name] = {
                        "type": "file",
                        "sha256": digest(source),
                        "mode": mode,
                    }
        return result


def build(source, target, output, *, from_version, to_version, platform, arch):
    if not VERSION.fullmatch(from_version) or not VERSION.fullmatch(to_version):
        raise ValueError("invalid release version")
    if platform not in ("windows", "linux") or arch not in ("x64", "arm64"):
        raise ValueError("unsupported OTA platform")
    output = Path(output)
    with Archive(source) as before, Archive(target) as after:
        old_files = before.files()
        new_files = after.files()
        changed = sorted(
            name for name, info in new_files.items() if info != old_files.get(name)
        )
        manifest = {
            "kind": "mower-ota",
            "format": 1,
            "from": from_version.lstrip("v"),
            "to": to_version.lstrip("v"),
            "platform": platform,
            "arch": arch,
            "files": new_files,
            "changed": changed,
        }
        temporary = output.with_suffix(output.suffix + ".tmp")
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as patch:
                patch.writestr(
                    "ota.json",
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                for name in changed:
                    if new_files[name]["type"] != "file":
                        continue
                    with (
                        after.open(name) as src,
                        patch.open("payload/" + name, "w", force_zip64=True) as dst,
                    ):
                        while chunk := src.read(1024 * 1024):
                            dst.write(chunk)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "changed": len(changed),
        "total": len(new_files),
        "bytes": output.stat().st_size,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--arch", required=True)
    args = parser.parse_args()
    print(
        build(
            args.source,
            args.target,
            args.output,
            from_version=args.from_version,
            to_version=args.to_version,
            platform=args.platform,
            arch=args.arch,
        )
    )


if __name__ == "__main__":
    main()
