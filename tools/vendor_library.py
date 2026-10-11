"""Bundle a committed library snapshot, or verify the bundled files without Git/network access.

Update: python tools/vendor_library.py --source ../gac-connect [--ref COMMIT]
Verify: python tools/vendor_library.py --check [--source ../gac-connect]
With --check and --source, the default ref is the recorded commit, not source HEAD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tomllib

TARGET = Path(__file__).resolve().parents[1] / "custom_components/gac_connect/_vendor"
PROVENANCE = "provenance.json"
SOURCE_URL = "https://github.com/leecaochang/gac-connect"
UPSTREAM_URL = "https://github.com/wattferry/gac-connect"
SOURCE_PREFIX = "src/gac_connect/"
EXCLUDED = ["cli.py"]  # Optional standalone CLI; never imported by the runtime package.


def git(source: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(source), *args], stderr=subprocess.PIPE)


def snapshot(source: Path, ref: str) -> dict[str, bytes]:
    """Read Git objects so uncommitted edits cannot masquerade as a recorded commit."""
    commit = git(source, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()
    project = tomllib.loads(git(source, "show", f"{commit}:pyproject.toml").decode())["project"]
    paths = git(source, "ls-tree", "-r", "--name-only", commit, "--", SOURCE_PREFIX).decode().splitlines()
    files = {
        "gac_connect/" + path.removeprefix(SOURCE_PREFIX): git(source, "show", f"{commit}:{path}")
        for path in paths if path.removeprefix(SOURCE_PREFIX) not in EXCLUDED
    }
    if "gac_connect/__init__.py" not in files:
        raise ValueError("Source commit has no gac_connect package")
    files["gac_connect/LICENSE"] = git(source, "show", f"{commit}:LICENSE")
    files["__init__.py"] = (
        '"""Generated library provenance. Update with tools/vendor_library.py; do not edit."""\n'
        f"LIBRARY_VERSION = {project['version']!r}\n"
        f"LIBRARY_COMMIT = {commit!r}\n"
        f"LIBRARY_SOURCE = {SOURCE_URL!r}\n"
    ).encode()
    provenance = {
        "format": 1,
        "source": SOURCE_URL,
        "upstream": UPSTREAM_URL,
        "commit": commit,
        "version": project["version"],
        "requires_python": project["requires-python"],
        "dependencies": project["dependencies"],
        "excluded": EXCLUDED,
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files[PROVENANCE] = (json.dumps(provenance, indent=2) + "\n").encode()
    return files


def read_bundle(target: Path) -> dict[str, bytes]:
    """Ignore only interpreter caches; unexpected source files must be reviewed."""
    result = {}
    for path in target.rglob("*"):
        relative = path.relative_to(target)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError(f"Bundled path must not be a symlink: {relative}")
        if path.is_file():
            result[relative.as_posix()] = path.read_bytes()
    return result


def verify(target: Path) -> dict:
    files = read_bundle(target)
    provenance = json.loads(files.pop(PROVENANCE))
    if provenance["format"] != 1:
        raise ValueError("Unsupported provenance format")
    for name in provenance["sha256"]:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError(f"Invalid bundled path: {name}")
    actual = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    if actual != provenance["sha256"]:
        changed = sorted(name for name in actual.keys() | provenance["sha256"].keys()
                         if actual.get(name) != provenance["sha256"].get(name))
        raise ValueError("Bundled files differ from provenance: " + ", ".join(changed))
    return provenance


def synchronize(source: Path | None, target: Path, *, check: bool, ref: str | None = None) -> None:
    previous = read_bundle(target)
    provenance = verify(target) if previous else None
    if check and provenance is None:
        raise ValueError("No bundled library found")
    if source is None:
        if not check:
            raise ValueError("--source is required when updating the bundle")
        return
    selected_ref = ref or (provenance["commit"] if check else "HEAD")
    expected = snapshot(source, selected_ref)
    if check:
        if previous != expected:
            raise ValueError("Bundled library differs from the selected source commit; regenerate it")
        return
    # Verify the old snapshot first. Refuse to overwrite hand edits or unexpected files.
    # Remove only obsolete files belonging to that verified snapshot.
    for name in previous.keys() - expected.keys():
        (target / name).unlink()
    for name, data in expected.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Local canonical library Git repository")
    parser.add_argument("--ref", help="Committed source revision (default: HEAD for updates, recorded commit for checks)")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.ref and args.source is None:
        parser.error("--ref requires --source")
    try:
        synchronize(args.source, TARGET, check=args.check, ref=args.ref)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as err:
        parser.exit(1, f"Library vendoring failed: {err}\n")
    provenance = verify(TARGET)
    print(f"{'Verified' if args.check else 'Bundled'} library {provenance['version']} at {provenance['commit']}")


if __name__ == "__main__":
    main()
