#!/usr/bin/env python3
"""Install the LinkView desktop launcher for the current checkout and user."""
import argparse
from pathlib import Path
import sys


def exec_argument(value):
    # Escape Exec reserved characters, then the desktop string's backslashes.
    value = str(value).replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return '"' + value.replace('\\', '\\\\') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write to this path instead of installing')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    destination = args.output or Path.home() / '.local/share/applications/linkview.desktop'
    content = (root / 'linkview.desktop.in').read_text()
    content = content.replace('@PYTHON@', exec_argument(sys.executable))
    content = content.replace('@APP@', exec_argument(root / 'linkview.py'))
    content = content.replace('@ICON@', str(root / 'linkview.svg').replace('\\', '\\\\'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content)
    print(f'Wrote {destination}')


if __name__ == '__main__':
    main()
