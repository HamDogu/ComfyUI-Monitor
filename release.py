#!/usr/bin/env python3
"""Cut a release: bump VERSION, date the CHANGELOG's Unreleased notes, commit everything and tag it.

    py -3 release.py patch    6.1.0 -> 6.1.1   bug fixes only
    py -3 release.py minor    6.1.0 -> 6.2.0   new features
    py -3 release.py major    6.1.0 -> 7.0.0   big or incompatible changes

Write what changed under "## [Unreleased]" in CHANGELOG.md first; those notes become the
release's section and its commit message. Restart the monitor afterwards to show the new version.
"""
import datetime as dt
from pathlib import Path
import re
import subprocess
import sys

BASE = Path(__file__).resolve().parent
UNRELEASED = '## [Unreleased]'


def git(*args):
    return subprocess.run(['git', *args], cwd=BASE, check=True, capture_output=True, text=True).stdout.strip()


def main():
    part = sys.argv[1] if len(sys.argv) > 1 else ''
    if part not in ('major', 'minor', 'patch'):
        sys.exit(__doc__)
    old = (BASE / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', old):
        sys.exit(f'VERSION must look like 1.2.3, not {old!r}')
    major, minor, patch = map(int, old.split('.'))
    new = {'major': f'{major + 1}.0.0', 'minor': f'{major}.{minor + 1}.0', 'patch': f'{major}.{minor}.{patch + 1}'}[part]
    if git('tag', '--list', 'v' + new):
        sys.exit(f'Tag v{new} already exists')

    changelog = (BASE / 'CHANGELOG.md').read_text(encoding='utf-8')
    if UNRELEASED not in changelog:
        sys.exit(f'CHANGELOG.md needs a "{UNRELEASED}" section')
    after = changelog.split(UNRELEASED, 1)[1]
    notes = after.split('\n## ', 1)[0].strip()
    if not notes:
        sys.exit(f'Describe the changes under "{UNRELEASED}" in CHANGELOG.md first')
    changelog = changelog.replace(UNRELEASED, f'{UNRELEASED}\n\n## [{new}] - {dt.date.today().isoformat()}', 1)

    (BASE / 'VERSION').write_text(new + '\n', encoding='utf-8', newline='\n')
    (BASE / 'CHANGELOG.md').write_text(changelog, encoding='utf-8', newline='\n')
    git('add', '-A')
    git('commit', '-m', f'Release v{new}\n\n{notes}')
    git('tag', '-a', 'v' + new, '-m', f'v{new}')
    print(f'Released v{old} -> v{new}. Restart the monitor to serve it.')


if __name__ == '__main__':
    main()
