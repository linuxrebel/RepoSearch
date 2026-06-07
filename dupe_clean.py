#!/usr/bin/env python3
"""
dupe_clean.py — Interactive TUI for cleaning up duplicate repo clones.

  ↑/↓   navigate
  Space  mark path to KEEP (others will be deleted)
  D      proceed to deletion confirmation
  Q      quit without changes
"""

import curses
import os
import shutil
import subprocess
import sys
from rb_config import get_git_root

GIT_ROOT = get_git_root()


# ── repo discovery (mirrors find-dupe.py logic) ──────────────────────────────

def _get_remote_url(repo_path):
    try:
        r = subprocess.run(
            ['git', '-C', repo_path, 'config', '--get', 'remote.origin.url'],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _normalize_url(url):
    norm = url.lower().rstrip('/').replace('.git', '')
    norm = norm.replace('git@github.com:', 'https://github.com/')
    norm = norm.replace('git@gitlab.com:', 'https://gitlab.com/')
    return norm


def _find_repos(base_path):
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


def find_dupes_with_paths():
    """Return list of dicts: {name, paths: [full_path,...], keep: int}."""
    repos = _find_repos(GIT_ROOT)
    url_map = {}
    no_url = {}

    for rpath in repos:
        name = os.path.basename(rpath)
        url = _get_remote_url(rpath)
        if url:
            norm = _normalize_url(url)
            url_map.setdefault(norm, []).append(rpath)
        else:
            no_url.setdefault(name, []).append(rpath)

    dupes = []
    for norm_url, paths in url_map.items():
        if len(paths) > 1:
            name = os.path.basename(paths[0])
            sorted_paths = sorted(paths)
            best = max(sorted_paths, key=lambda p: p.count(os.sep))
            dupes.append({
                'name': name,
                'paths': sorted_paths,
                'keep': sorted_paths.index(best),
            })

    for name, paths in no_url.items():
        if len(paths) > 1:
            sorted_paths = sorted(paths)
            best = max(sorted_paths, key=lambda p: p.count(os.sep))
            dupes.append({
                'name': name,
                'paths': sorted_paths,
                'keep': sorted_paths.index(best),
            })

    dupes.sort(key=lambda x: x['name'].lower())
    return dupes


# ── TUI ───────────────────────────────────────────────────────────────────────

def _build_items(dupes):
    """Flatten groups into a list of display items."""
    items = []
    radio_indices = []  # positions in items[] that are radio buttons
    for gi, group in enumerate(dupes):
        items.append({'type': 'header', 'group': gi})
        for pi, path in enumerate(group['paths']):
            radio_indices.append(len(items))
            items.append({'type': 'radio', 'group': gi, 'path_idx': pi})
        items.append({'type': 'spacer'})
    return items, radio_indices


def _draw(stdscr, items, radio_indices, dupes, cursor_radio, scroll_top,
          cp_title, cp_hint, cp_header, cp_keep, cp_cursor, cp_dim):
    h, w = stdscr.getmaxyx()
    content_h = h - 4

    stdscr.erase()

    # Row 0: title
    title = f" Duplicate Repo Cleaner  —  {len(dupes)} repos with duplicates "
    stdscr.addstr(0, 0, title[:w - 1], cp_title | curses.A_BOLD)

    # Row 1: hints
    hints = " ↑↓ navigate   Space: keep this copy   D: delete others   Q: quit "
    stdscr.addstr(1, 0, hints[:w - 1], cp_hint)

    # Row 2: separator
    stdscr.addstr(2, 0, '─' * (w - 1), cp_dim)

    # Scroll so cursor item is visible
    cursor_item_idx = radio_indices[cursor_radio]
    while cursor_item_idx < scroll_top:
        scroll_top -= 1
    while cursor_item_idx >= scroll_top + content_h:
        scroll_top += 1

    # Content rows
    row = 3
    for item_idx in range(scroll_top, len(items)):
        if row >= h - 1:
            break
        item = items[item_idx]

        if item['type'] == 'header':
            name = dupes[item['group']]['name']
            stdscr.addstr(row, 2, name[:w - 4], cp_header | curses.A_BOLD)

        elif item['type'] == 'radio':
            gi = item['group']
            pi = item['path_idx']
            path = dupes[gi]['paths'][pi]
            is_keep = dupes[gi]['keep'] == pi
            is_cursor = radio_indices[cursor_radio] == item_idx

            marker = '(*) ' if is_keep else '( ) '
            line = f"  {marker}{path}"
            # Truncate with ellipsis if too wide
            if len(line) > w - 1:
                line = line[:w - 4] + '...'

            if is_cursor:
                stdscr.addstr(row, 0, line.ljust(w - 1)[:w - 1], cp_cursor)
            elif is_keep:
                stdscr.addstr(row, 0, line[:w - 1], cp_keep)
            else:
                stdscr.addstr(row, 0, line[:w - 1])

        # spacer: blank line
        row += 1

    # Footer
    footer = f" {cursor_radio + 1}/{len(radio_indices)} "
    stdscr.addstr(h - 1, 0, footer[:w - 1], cp_dim)

    stdscr.refresh()
    return scroll_top  # may have been adjusted


def run_tui(dupes):
    """Run the TUI. Returns updated dupes list, or None if user quit."""
    return curses.wrapper(_tui_main, dupes)


def _tui_main(stdscr, dupes):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()

    # Color pairs
    curses.init_pair(1, curses.COLOR_CYAN, -1)          # title
    curses.init_pair(2, curses.COLOR_YELLOW, -1)         # hints
    curses.init_pair(3, curses.COLOR_CYAN, -1)           # group header
    curses.init_pair(4, curses.COLOR_GREEN, -1)          # keep path
    curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_WHITE)  # cursor row
    curses.init_pair(6, -1, -1)                          # dim

    cp_title  = curses.color_pair(1)
    cp_hint   = curses.color_pair(2)
    cp_header = curses.color_pair(3)
    cp_keep   = curses.color_pair(4)
    cp_cursor = curses.color_pair(5)
    cp_dim    = curses.color_pair(6) | curses.A_DIM

    items, radio_indices = _build_items(dupes)
    cursor_radio = 0
    scroll_top = 0

    while True:
        scroll_top = _draw(stdscr, items, radio_indices, dupes,
                           cursor_radio, scroll_top,
                           cp_title, cp_hint, cp_header, cp_keep, cp_cursor, cp_dim)

        key = stdscr.getch()

        if key in (ord('q'), ord('Q')):
            return None

        elif key in (ord('d'), ord('D')):
            return dupes

        elif key in (curses.KEY_UP, ord('k')):
            if cursor_radio > 0:
                cursor_radio -= 1

        elif key in (curses.KEY_DOWN, ord('j')):
            if cursor_radio < len(radio_indices) - 1:
                cursor_radio += 1

        elif key == ord(' '):
            item = items[radio_indices[cursor_radio]]
            dupes[item['group']]['keep'] = item['path_idx']
            # Auto-advance to next radio after selection
            if cursor_radio < len(radio_indices) - 1:
                cursor_radio += 1

        elif key == curses.KEY_RESIZE:
            pass  # just redraw on next loop


# ── deletion ─────────────────────────────────────────────────────────────────

def confirm_and_delete(dupes):
    to_delete = []
    for group in dupes:
        keep_path = group['paths'][group['keep']]
        for path in group['paths']:
            if path != keep_path:
                to_delete.append((path, keep_path))

    if not to_delete:
        print("Nothing to delete.")
        return

    print(f"\n{len(to_delete)} path(s) staged for deletion:\n")
    deleted = 0
    skipped = 0
    errors = 0

    for path, keep_path in to_delete:
        print(f"  Keep : {keep_path}")
        ans = input(f"  Delete '{path}'? [y/N] ").strip().lower()
        if ans == 'y':
            try:
                shutil.rmtree(path)
                print("  Deleted.\n")
                deleted += 1
            except Exception as e:
                print(f"  ERROR: {e}\n")
                errors += 1
        else:
            print("  Skipped.\n")
            skipped += 1

    print(f"Done: {deleted} deleted, {skipped} skipped" +
          (f", {errors} errors" if errors else "") + ".")
    if deleted > 0:
        print("Run 'repo-browser.py rescan' to update the search index.")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    if GIT_ROOT == '':
        print("Error: gitParent not configured. Run 'repo-browser.py start' and set it via the Settings UI.")
        sys.exit(1)

    print("Scanning for duplicates...")
    dupes = find_dupes_with_paths()

    if not dupes:
        print("No duplicate clones found.")
        return

    result = run_tui(dupes)

    if result is None:
        print("Cancelled — no changes made.")
        return

    confirm_and_delete(result)


if __name__ == '__main__':
    main()
