#!/usr/bin/env python3
"""Fetch the pinned manuals, measurements, test ROMs and source trees into reference/.

Nothing fetched here is committed (see .gitignore). Two pin files drive it:

- ``scripts/pinned_files.json``: one entry per file with its URL, SHA-256 and kind.
  Kinds ``document``, ``data``, ``test-rom`` and ``source-archive`` must match the
  hash exactly; a mismatch is an error and the download is not kept. Kind
  ``live-document`` is a URL that serves the newest revision (Renesas's software
  manual): a mismatch is a warning and the new bytes are kept, because the docs
  cite the revision recorded in the entry's note. A ``.zip`` of kind ``test-rom``
  or ``data`` is unpacked next to itself. A ``source-archive`` has its listed
  ``members`` unpacked into ``extract_to`` with ``strip`` leading path parts removed.
- ``scripts/pinned_repos.json``: one entry per git repository, fetched shallow at the
  pinned commit, sparse where the entry lists gitignore-style paths. Entries marked
  ``optional`` (the 350 MB SH-4 corpus) are fetched only with ``--optional``.

    python scripts/fetch_references.py              # everything missing except optional
    python scripts/fetch_references.py --optional   # also the optional repositories
    python scripts/fetch_references.py --verify     # hash what is present, fetch nothing

The default set is about 60 MB. Building referees from it is scripts/build_referees.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "scripts" / "pinned_files.json"
REPOS = ROOT / "scripts" / "pinned_repos.json"
LIVE = {"live-document"}
UNZIP = {"test-rom", "data"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path) -> None:
    # curl with its own user agent: zenodo.org refuses a bare "Mozilla/5.0", and
    # bitsavers.org refuses curl outright (its file is pinned from a mirror).
    result = subprocess.run(
        ["curl", "-sSfL", "--max-time", "1800", "-o", str(target), url],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise OSError(result.stderr.decode("utf-8", "replace").strip())


def unpack(entry: dict, path: Path) -> None:
    """Unpack a fetched archive as its entry says; nothing outside the target directory."""
    if entry["kind"] in UNZIP and path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            archive.extractall(path.parent)
    if entry["kind"] != "source-archive":
        return
    target = ROOT / entry["extract_to"]
    strip = entry.get("strip", 0)
    wanted = [PurePosixPath(member) for member in entry["members"]]
    with tarfile.open(path) as archive:
        chosen = []
        for info in archive.getmembers():
            name = PurePosixPath(info.name)
            if not any(name == want or want in name.parents for want in wanted):
                continue
            if len(name.parts) <= strip:
                continue
            info.name = str(PurePosixPath(*name.parts[strip:]))
            chosen.append(info)
        archive.extractall(target, members=chosen, filter="data")


def fetch_files(verify_only: bool) -> int:
    problems = 0
    for entry in json.loads(FILES.read_text(encoding="utf-8")):
        path = ROOT / entry["path"]
        live = entry["kind"] in LIVE
        if path.exists():
            if sha256(path) != entry["sha256"]:
                level = "warning" if live else "ERROR"
                print(f"{level}: {entry['path']} does not match its pin")
                problems += not live
            elif entry["kind"] == "source-archive" and not (ROOT / entry["extract_to"]).exists():
                if not verify_only:
                    unpack(entry, path)
                    print(f"unpacked {entry['path']}")
            continue
        if verify_only:
            print(f"missing: {entry['path']}")
            problems += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            partial = Path(handle.name)
        try:
            download(entry["url"], partial)
        except OSError as error:
            partial.unlink(missing_ok=True)
            print(f"ERROR: {entry['url']}: {error}")
            problems += 1
            continue
        digest = sha256(partial)
        if digest != entry["sha256"] and not live:
            partial.unlink()
            print(f"ERROR: {entry['url']} has SHA-256 {digest}, pinned {entry['sha256']}; not kept")
            problems += 1
            continue
        if digest != entry["sha256"]:
            print(f"warning: {entry['path']} changed since the pinned revision")
        partial.replace(path)
        unpack(entry, path)
        print(f"fetched {entry['path']}")
    return problems


def fetch_repos(verify_only: bool, optional: bool) -> int:
    problems = 0
    for entry in json.loads(REPOS.read_text(encoding="utf-8")):
        if entry.get("optional") and not optional:
            continue
        path = ROOT / entry["path"]
        if path.exists():
            head = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if head != entry["rev"]:
                print(f"ERROR: {entry['path']} is at {head}, pinned {entry['rev']}")
                problems += 1
            continue
        if verify_only:
            print(f"missing: {entry['path']}")
            problems += 1
            continue
        path.mkdir(parents=True)
        git = ["git", "-C", str(path)]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "remote", "add", "origin", entry["url"]], check=True)
        if entry["sparse"]:
            # gitignore-style patterns (/dir/, /file), not cone directories.
            subprocess.run(
                [*git, "sparse-checkout", "set", "--no-cone", *entry["sparse"]], check=True
            )
        subprocess.run(
            [*git, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", entry["rev"]],
            check=True,
        )
        subprocess.run([*git, "checkout", "-q", "FETCH_HEAD"], check=True)
        print(f"cloned {entry['path']} at {entry['rev'][:12]}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--verify", action="store_true", help="check what is present; fetch nothing"
    )
    parser.add_argument("--no-repos", action="store_true", help="skip the git repositories")
    parser.add_argument("--optional", action="store_true", help="include optional repositories")
    args = parser.parse_args()
    problems = fetch_files(args.verify)
    if not args.no_repos:
        problems += fetch_repos(args.verify, args.optional)
    print("all pins satisfied" if not problems else f"{problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
