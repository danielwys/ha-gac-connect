"""Copy the canonical library detector for installations still pinned to b1.

Run with --source /path/to/gac-connect/src/gac_connect/capabilities.py.
CI uses --check against the candidate source to prevent a competing implementation.
"""
import argparse
from pathlib import Path

HEADER = "# Generated from gac-connect/src/gac_connect/capabilities.py. Do not edit; run tools/sync_capabilities.py.\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "custom_components/gac_connect/_compat_b1/capabilities.py"
    expected = HEADER + args.source.read_text()
    if args.check:
        if target.read_text() != expected:
            raise SystemExit("Capability backport differs from the canonical library source; regenerate it.")
    else:
        target.write_text(expected)


if __name__ == "__main__":
    main()
