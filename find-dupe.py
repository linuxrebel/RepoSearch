#!/usr/bin/env python3
# repo-browser -- Duplicate clone reporter
# Copyright (C) 2026 James Sparenberg
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
find-dupe.py — Report duplicate repo clones under gitParent.
Detects repos with the same remote URL in different directories.
Output: ~/Clone-Duplist.txt
"""

import os
import subprocess
from rb_config import get_git_root

GIT_ROOT = get_git_root()


def get_remote_url(repo_path):
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'config', '--get', 'remote.origin.url'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def normalize_url(url):
    norm = url.lower().rstrip('/').replace('.git', '')
    norm = norm.replace('git@github.com:', 'https://github.com/')
    norm = norm.replace('git@gitlab.com:', 'https://gitlab.com/')
    return norm


def relative_loc(path):
    """Return the directory under gitParent (e.g. '3rdparty' or 'security-automation')."""
    rel = os.path.relpath(path, GIT_ROOT)
    parts = rel.split(os.sep)
    if len(parts) > 1:
        return parts[0]
    return '.'


def find_repos(base_path):
    repos = []
    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in {'.git', 'node_modules'}]
        depth = root.replace(base_path, '').count(os.sep)
        if depth > 2:
            dirs.clear()
            continue
        if root != base_path and os.path.exists(os.path.join(root, '.git')):
            repos.append(root)
            dirs.clear()
    for entry in os.listdir(base_path):
        full = os.path.join(base_path, entry)
        if os.path.isdir(full) and os.path.exists(os.path.join(full, '.git')):
            if full not in repos:
                repos.append(full)
    return repos


def find_dupes():
    repos = find_repos(GIT_ROOT)
    url_map = {}
    no_url = {}

    for rpath in repos:
        name = os.path.basename(rpath)
        url = get_remote_url(rpath)
        if url:
            norm = normalize_url(url)
            url_map.setdefault(norm, []).append(rpath)
        else:
            no_url.setdefault(name, []).append(rpath)

    dupes = []
    for norm_url, paths in url_map.items():
        if len(paths) > 1:
            name = os.path.basename(paths[0])
            locs = [relative_loc(p) for p in sorted(paths)]
            dupes.append((name, locs))

    for name, paths in no_url.items():
        if len(paths) > 1:
            locs = [relative_loc(p) for p in sorted(paths)]
            dupes.append((name, locs))

    dupes.sort(key=lambda x: x[0].lower())
    return dupes


def write_report(dupes):
    out_path = os.path.join(os.path.expanduser('~'), 'Clone-Duplist.txt')

    if not dupes:
        with open(out_path, 'w') as f:
            f.write('No duplicate clones found.\n')
        print(f'No duplicates found. Report: {out_path}')
        return

    # Calculate column widths
    name_w = max(len(d[0]) for d in dupes)
    name_w = max(name_w, len('App Name'))

    # Find max number of locations
    max_locs = max(len(d[1]) for d in dupes)

    # Header
    loc_headers = [f'Loc{i+1}' for i in range(max_locs)]
    loc_widths = []
    for i in range(max_locs):
        col_w = len(loc_headers[i])
        for d in dupes:
            if i < len(d[1]):
                col_w = max(col_w, len(d[1][i]))
        loc_widths.append(col_w)

    with open(out_path, 'w') as f:
        # Header
        header = f'{"App Name":<{name_w}}'
        for i in range(max_locs):
            header += f'  {loc_headers[i]:<{loc_widths[i]}}'
        f.write(header.rstrip() + '\n')
        f.write('-' * len(header.rstrip()) + '\n')

        # Rows
        for name, locs in dupes:
            row = f'{name:<{name_w}}'
            for i in range(max_locs):
                loc = locs[i] if i < len(locs) else ''
                row += f'  {loc:<{loc_widths[i]}}'
            f.write(row.rstrip() + '\n')

    print(f'{len(dupes)} duplicate(s) found. Report: {out_path}')


if __name__ == '__main__':
    dupes = find_dupes()
    write_report(dupes)
