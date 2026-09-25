"""Publish direct portable OTA assets and a small channel index."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from build_ota import build

SOURCE_REPO = "ArkMowers/arknights-mower"
RELEASE_REPO = "ArkMowers/MowerRelease"
VERSION = re.compile(r"v\d+\.\d+\.\d+(?:-alpha\.\d+)?\Z")
TARGETS = (
    ("windows", "x64", "zip"),
    ("linux", "x64", "tar.gz"),
    ("linux", "arm64", "tar.gz"),
    ("android", "arm64", "zip"),
)


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True).strip()


def api(path):
    return json.loads(gh("api", path))


def released_at(release):
    try:
        return datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def asset_name(tag, platform, arch, extension):
    return f"arknights-mower_{tag.removeprefix('v')}_{platform}_{arch}.{extension}"


def asset(release, platform, arch, extension):
    name = asset_name(release["tag_name"], platform, arch, extension)
    return next((a for a in release.get("assets", []) if a["name"] == name), None)


def download(tag, artifact, directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / artifact["name"]
    if not path.exists():
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                SOURCE_REPO,
                "--pattern",
                artifact["name"],
                "--dir",
                str(directory),
            ],
            check=True,
        )
    expected = artifact.get("digest") or ""
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
        raise ValueError(f"Missing digest for {artifact['name']}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != artifact["size"] or actual != expected[7:]:
        raise ValueError(f"Release asset checksum mismatch: {artifact['name']}")
    return path


def all_releases():
    releases = api(f"repos/{SOURCE_REPO}/releases?per_page=100")
    return sorted(
        (r for r in releases if not r["draft"] and VERSION.fullmatch(r["tag_name"])),
        key=released_at,
        reverse=True,
    )


def release_by_tag(releases, tag):
    return next((r for r in releases if r["tag_name"] == tag), None)


def publish_target(target, releases, source_limit):
    tag = target["tag_name"]
    current = None
    try:
        current = api(f"repos/{RELEASE_REPO}/releases/tags/{tag}")
        if current.get("draft"):
            raise ValueError(f"draft release {tag} needs manual review")
    except subprocess.CalledProcessError:
        pass
    published_names = {a["name"] for a in current["assets"]} if current else set()

    candidates = [
        r
        for r in releases
        if released_at(r) < released_at(target) and r["tag_name"] != tag
    ][:source_limit]
    if not candidates:
        print(f"No older versions for {tag}")
        return current
    with tempfile.TemporaryDirectory(prefix="mower-ota-") as temp:
        root = Path(temp)
        products = []
        for platform, arch, extension in TARGETS:
            latest = asset(target, platform, arch, extension)
            if not latest:
                continue
            latest_path = download(tag, latest, root / "target")
            for before in candidates:
                previous = asset(before, platform, arch, extension)
                if not previous:
                    continue
                name = (
                    f"arknights-mower-ota_{before['tag_name'][1:]}_to_{tag[1:]}_"
                    f"{platform}_{arch}.zip"
                )
                if name in published_names:
                    continue
                old_path = download(before["tag_name"], previous, root / "previous")
                output = root / "products" / name
                result = build(
                    old_path,
                    latest_path,
                    output,
                    from_version=before["tag_name"],
                    to_version=tag,
                    platform=platform,
                    arch=arch,
                )
                old_path.unlink()
                if output.stat().st_size >= latest["size"] * 0.85:
                    print(f"Skip oversized OTA {name}: {result['bytes']} bytes")
                    output.unlink()
                else:
                    print(f"Built {name}: {result}")
                    products.append(output)
        if not products:
            print(f"No useful OTA packages for {tag}")
            return current

        body = (
            f"Mower {tag} 的跨版本 OTA 差异包。完整安装包与更新说明见 "
            f"https://github.com/{SOURCE_REPO}/releases/tag/{tag} 。\n\n"
            "客户端会校验起点文件与完整目标目录；无法应用时改用主仓库完整包。"
        )
        if current is None:
            command = [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                RELEASE_REPO,
                "--title",
                tag,
                "--notes",
                body,
                "--draft",
            ]
            if target["prerelease"]:
                command.append("--prerelease")
            subprocess.run(command, check=True)
        subprocess.run(
            [
                "gh",
                "release",
                "upload",
                tag,
                "--repo",
                RELEASE_REPO,
                *map(str, products),
            ],
            check=True,
        )
        if current is None:
            subprocess.run(
                ["gh", "release", "edit", tag, "--repo", RELEASE_REPO, "--draft=false"],
                check=True,
            )
    return api(f"repos/{RELEASE_REPO}/releases/tags/{tag}")


def index_record(target, ota_release):
    return {
        "schema": 1,
        "version": target["tag_name"],
        "published_at": target["published_at"],
        "source_release": target["html_url"],
        "full_assets": [
            {
                "name": a["name"],
                "size": a["size"],
                "url": a["browser_download_url"],
                "digest": a.get("digest"),
            }
            for a in target["assets"]
            if a["name"].startswith("arknights-mower_")
        ],
        "ota_assets": [
            {
                "name": a["name"],
                "size": a["size"],
                "url": a["browser_download_url"],
                "digest": a.get("digest"),
            }
            for a in ota_release["assets"]
            if a["name"].startswith("arknights-mower-ota_")
        ],
    }


def save_index(channel, record):
    directory = Path("version")
    directory.mkdir(exist_ok=True)
    (directory / f"{channel}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {}
    for name in ("stable", "beta"):
        path = directory / f"{name}.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            summary[name] = {
                "version": data["version"],
                "index": f"https://raw.githubusercontent.com/{RELEASE_REPO}/main/version/{name}.json",
            }
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Only publish one specific main repository tag")
    parser.add_argument("--source-limit", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.source_limit <= 20:
        raise ValueError("source limit must be between 1 and 20")
    releases = all_releases()
    if args.tag:
        targets = [release_by_tag(releases, args.tag)]
        if targets[0] is None:
            raise ValueError("tag is not a published Mower Release")
    else:
        targets = [
            next((r for r in releases if r["prerelease"] == pre), None)
            for pre in (False, True)
        ]
    for target in targets:
        if target is None:
            continue
        ota_release = publish_target(target, releases, args.source_limit)
        if ota_release:
            save_index(
                "beta" if target["prerelease"] else "stable",
                index_record(target, ota_release),
            )


if __name__ == "__main__":
    main()
