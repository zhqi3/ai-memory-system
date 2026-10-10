"""Invoke MOSS in the explicitly selected trusted checkout; no host defaults."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    if not args.repo.is_absolute() or '..' in args.repo.parts:
        parser.error('--repo must be an explicit absolute trusted checkout')
    command = args.repo / 'tools/moss.py'
    if not command.is_file():
        parser.error('Selected checkout does not contain tools/moss.py')
    return subprocess.run([sys.executable, '-B', str(command), *remaining]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
