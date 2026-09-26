"""Mirror full Mower packages, publish OTA assets, and update channel indexes."""

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
VERSION = re.compile(r"v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+(?:\.g[0-9a-f]{8})?)?\Z")
OTA_TARGETS = (
    ("windows", "x64", "zip"),
    ("linux", "x64", "tar.gz"),
    ("linux", "arm64", "tar.gz"),
    ("android", "arm64", "zip"),
)
FULL_TARGETS = OTA_TARGETS + (
    ("macos", "x64", "dmg"),
    ("macos", "arm64", "dmg"),
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


def ota_name(before, target, platform, arch):
    suffix = "_v2" if platform in ("windows", "linux") else ""
    return (
        f"arknights-mower-ota_{before.removeprefix('v')}_to_"
        f"{target.removeprefix('v')}_{platform}_{arch}{suffix}.zip"
    )


def asset(release, platform, arch, extension):
    name = asset_name(release["tag_name"], platform, arch, extension)
    return next((a for a in release.get("assets", []) if a["name"] == name), None)


def has_full_assets(release):
    return any(asset(release, *target) for target in FULL_TARGETS)


def download(tag, artifact, directory, *, repo=SOURCE_REPO):
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
                repo,
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


def list_releases(repo):
    releases = []
    page = 1
    while True:
        batch = api(f"repos/{repo}/releases?per_page=100&page={page}")
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return releases


def all_releases():
    releases = list_releases(SOURCE_REPO)
    releases.extend(
        item for item in list_releases(RELEASE_REPO) if channel_of(item) == "dev"
    )
    return sorted(
        (r for r in releases if not r["draft"] and VERSION.fullmatch(r["tag_name"])),
        key=released_at,
        reverse=True,
    )


def release_by_tag(releases, tag):
    return next((r for r in releases if r["tag_name"] == tag), None)


def channel_of(release):
    tag = release["tag_name"]
    if re.search(r"-alpha\.\d+\.g[0-9a-f]{8}$", tag):
        return "dev"
    return "beta" if release["prerelease"] else "stable"


def ota_sources(target, releases, limit):
    channel = channel_of(target)
    return [
        item
        for item in releases
        if released_at(item) < released_at(target)
        and item["tag_name"] != target["tag_name"]
        and (channel != "dev" or channel_of(item) == "dev")
        and (channel == "dev" or channel_of(item) != "dev")
    ][:limit]


def release_body(target):
    return (
        f"Mower {target['tag_name']} 的完整安装包与跨版本 OTA 差异包。原始发布记录："
        f"{target['html_url']} 。\n\n"
        "完整包与差异包均来自官方 Mower Release；安装器会校验 SHA-256。"
    )


def current_release(tag):
    try:
        return api(f"repos/{RELEASE_REPO}/releases/tags/{tag}")
    except subprocess.CalledProcessError:
        # GitHub's lookup by tag excludes drafts, including drafts created by
        # this workflow. The release list includes them so a failed run resumes.
        return next(
            (
                item
                for item in api(f"repos/{RELEASE_REPO}/releases?per_page=100")
                if item["tag_name"] == tag
            ),
            None,
        )


def mirror_full_release(target):
    """Copy every supported full package with its source digest unchanged."""
    tag = target["tag_name"]
    if channel_of(target) == "dev":
        current = current_release(tag)
        if not current or current["draft"] or not has_full_assets(current):
            raise ValueError(f"nightly full package is unavailable: {tag}")
        return current
    current = current_release(tag)
    if current and current.get("draft") and current.get("body") != release_body(target):
        raise ValueError(f"draft release {tag} was not created by this workflow")
    published = {a["name"]: a for a in current["assets"]} if current else {}
    source_assets = [
        item
        for platform, arch, extension in FULL_TARGETS
        if (item := asset(target, platform, arch, extension)) is not None
    ]
    if not source_assets:
        raise ValueError(f"no supported full packages for {tag}")
    with tempfile.TemporaryDirectory(prefix="mower-full-") as temp:
        products = []
        for item in source_assets:
            existing = published.get(item["name"])
            if existing:
                if existing.get("size") != item["size"] or existing.get(
                    "digest"
                ) != item.get("digest"):
                    raise ValueError(f"mirrored package differs: {item['name']}")
                continue
            products.append(download(tag, item, Path(temp)))
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
                release_body(target),
                "--draft",
            ]
            if target["prerelease"]:
                command.append("--prerelease")
            subprocess.run(command, check=True)
        if products:
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
        mirrored = (
            api(f"repos/{RELEASE_REPO}/releases/{current['id']}")
            if current
            else current_release(tag)
        )
        mirrored_assets = {a["name"]: a for a in mirrored["assets"]}
        for item in source_assets:
            copy = mirrored_assets.get(item["name"])
            if (
                not copy
                or copy.get("size") != item["size"]
                or copy.get("digest") != item.get("digest")
            ):
                raise ValueError(
                    f"full package mirror verification failed: {item['name']}"
                )
        if mirrored["draft"]:
            subprocess.run(
                ["gh", "release", "edit", tag, "--repo", RELEASE_REPO, "--draft=false"],
                check=True,
            )
    return api(f"repos/{RELEASE_REPO}/releases/tags/{tag}")


def publish_target(target, releases, source_limit):
    tag = target["tag_name"]
    current = mirror_full_release(target)
    published_names = {a["name"] for a in current["assets"]}

    candidates = ota_sources(target, releases, source_limit)
    if not candidates:
        print(f"No older versions for {tag}; full packages are available")
        return current
    with tempfile.TemporaryDirectory(prefix="mower-ota-") as temp:
        root = Path(temp)
        products = []
        for platform, arch, extension in OTA_TARGETS:
            latest = asset(target, platform, arch, extension)
            if not latest:
                continue
            pending = [
                before
                for before in candidates
                if asset(before, platform, arch, extension)
                and ota_name(before["tag_name"], tag, platform, arch)
                not in published_names
            ]
            if not pending:
                continue
            latest_path = download(
                tag,
                latest,
                root / "target",
                repo=RELEASE_REPO if channel_of(target) == "dev" else SOURCE_REPO,
            )
            for before in pending:
                previous = asset(before, platform, arch, extension)
                name = ota_name(before["tag_name"], tag, platform, arch)
                old_path = download(
                    before["tag_name"],
                    previous,
                    root / "previous",
                    repo=RELEASE_REPO if channel_of(before) == "dev" else SOURCE_REPO,
                )
                output = root / "products" / name
                try:
                    result = build(
                        old_path,
                        latest_path,
                        output,
                        from_version=before["tag_name"],
                        to_version=tag,
                        platform=platform,
                        arch=arch,
                    )
                except ValueError as error:
                    print(f"Skip incompatible OTA {name}: {error}")
                    continue
                finally:
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
    return api(f"repos/{RELEASE_REPO}/releases/tags/{tag}")


def full_asset_records(mirrored):
    return [
        {
            "name": item["name"],
            "size": item["size"],
            "url": item["browser_download_url"],
            "digest": item.get("digest"),
        }
        for item in mirrored["assets"]
        if item["name"].startswith("arknights-mower_")
    ]


def source_record(target, mirrored):
    return {
        "schema": 1,
        "version": target["tag_name"],
        "published_at": target["published_at"],
        "source_release": target["html_url"],
        "notes": target.get("body") or "暂无更新说明",
        "full_assets": full_asset_records(mirrored),
    }


def index_record(target, ota_release, history=()):
    return {
        **source_record(target, ota_release),
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
        "history": [source_record(old, mirrored) for old, mirrored in history],
    }


def save_index(channel, record):
    directory = Path("version")
    directory.mkdir(exist_ok=True)
    (directory / f"{channel}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {}
    for name in ("stable", "beta", "dev"):
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
    parser.add_argument(
        "--mirror-only",
        action="store_true",
        help="Mirror one tag without updating the channel index",
    )
    args = parser.parse_args()
    if not 1 <= args.source_limit <= 20:
        raise ValueError("source limit must be between 1 and 20")
    releases = all_releases()
    if args.tag:
        targets = [release_by_tag(releases, args.tag)]
        if targets[0] is None:
            raise ValueError("tag is not a published Mower Release")
    else:
        stable = api(f"repos/{SOURCE_REPO}/releases/latest")
        targets = [
            release_by_tag(releases, stable["tag_name"]),
            next((r for r in releases if channel_of(r) == "beta"), None),
            next((r for r in releases if channel_of(r) == "dev"), None),
        ]
    for target in targets:
        if target is None:
            continue
        if not has_full_assets(target):
            print(f"Skip {target['tag_name']}: no supported full packages")
            continue
        if args.mirror_only:
            mirror_full_release(target)
            continue
        history = [
            old
            for old in releases
            if channel_of(old) == channel_of(target)
            and released_at(old) < released_at(target)
            and has_full_assets(old)
        ][:6]
        mirrored_history = [(old, mirror_full_release(old)) for old in history]
        ota_release = publish_target(target, releases, args.source_limit)
        if ota_release:
            save_index(
                channel_of(target),
                index_record(target, ota_release, mirrored_history),
            )


if __name__ == "__main__":
    main()
