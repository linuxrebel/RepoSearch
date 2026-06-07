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

def _draw_one(stdscr, dupes, group_idx, cursor_path,
              cp_title, cp_hint, cp_header, cp_keep, cp_cursor, cp_dim):
    h, w = stdscr.getmaxyx()
    group = dupes[group_idx]
    total = len(dupes)

    stdscr.erase()

    # Row 0: title + progress
    title = f" Duplicate Repo Cleaner  —  {group_idx + 1} / {total} "
    stdscr.addstr(0, 0, title[:w - 1], cp_title | curses.A_BOLD)

    # Row 1: hints
    hints = " ↑↓ select   Space: mark keep   ←→ prev/next repo   D: done   Q: quit "
    stdscr.addstr(1, 0, hints[:w - 1], cp_hint)

    # Row 2: separator
    stdscr.addstr(2, 0, '─' * (w - 1), cp_dim)

    # Row 3: blank
    # Row 4: repo name
    stdscr.addstr(4, 4, group['name'][:w - 6], cp_header | curses.A_BOLD)

    # Rows 6+: radio options
    for pi, path in enumerate(group['paths']):
        row = 6 + pi
        if row >= h - 1:
            break
        is_keep   = group['keep'] == pi
        is_cursor = cursor_path == pi
        marker = '(*) ' if is_keep else '( ) '
        line = f"  {marker}{path}"
        if len(line) > w - 1:
            line = line[:w - 4] + '...'

        if is_cursor:
            stdscr.addstr(row, 0, line.ljust(w - 1)[:w - 1], cp_cursor)
        elif is_keep:
            stdscr.addstr(row, 0, line[:w - 1], cp_keep)
        else:
            stdscr.addstr(row, 0, line[:w - 1])

    # Footer: progress bar hint
    pct = int((group_idx + 1) / total * 20)
    bar = '█' * pct + '░' * (20 - pct)
    footer = f" [{bar}] {group_idx + 1}/{total} "
    stdscr.addstr(h - 1, 0, footer[:w - 1], cp_dim)

    stdscr.refresh()


def run_tui(dupes):
    """
    Drive the per-repo select → confirm → delete loop.
    Returns (deleted_count, skipped_count) when finished or user quits.
    """
    return curses.wrapper(_tui_main, dupes)


def _tui_main(stdscr, dupes):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()

    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_CYAN, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(6, -1, -1)

    cp_title  = curses.color_pair(1)
    cp_hint   = curses.color_pair(2)
    cp_header = curses.color_pair(3)
    cp_keep   = curses.color_pair(4)
    cp_cursor = curses.color_pair(5)
    cp_dim    = curses.color_pair(6) | curses.A_DIM

    total_deleted = 0
    total_skipped = 0

    for group_idx, group in enumerate(dupes):
        cursor_path = group['keep']

        # ── Selection loop for this repo ──────────────────────────────
        while True:
            _draw_one(stdscr, dupes, group_idx, cursor_path,
                      cp_title, cp_hint, cp_header, cp_keep, cp_cursor, cp_dim)

            key = stdscr.getch()
            n_paths = len(group['paths'])

            if key in (ord('q'), ord('Q'), 3):  # 3 = Ctrl-C
                # Quit — report what was done so far
                return total_deleted, total_skipped

            elif key in (curses.KEY_UP, ord('k')):
                cursor_path = max(0, cursor_path - 1)

            elif key in (curses.KEY_DOWN, ord('j')):
                cursor_path = min(n_paths - 1, cursor_path + 1)

            elif key == ord(' '):
                group['keep'] = cursor_path

            elif key in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT,
                         ord('n'), ord('l')):
                # Confirm selection → proceed to deletion for this repo
                group['keep'] = cursor_path
                break

            elif key in (ord('s'), ord('S')):
                # Skip this repo without deleting anything
                break

            elif key == curses.KEY_RESIZE:
                pass

        # ── Deletion confirmations for this repo ──────────────────────
        keep_path = group['paths'][group['keep']]
        to_delete = [p for p in group['paths'] if p != keep_path]

        if to_delete:
            # Temporarily leave curses for normal terminal I/O
            curses.def_prog_mode()
            curses.endwin()

            print(f"\n  {group['name']}")
            print(f"  Keep : {keep_path}\n")

            try:
                for path in to_delete:
                    ans = input(f"  Delete '{path}'? [y/N] ").strip().lower()
                    if ans == 'y':
                        try:
                            shutil.rmtree(path)
                            print("  Deleted.\n")
                            total_deleted += 1
                        except Exception as e:
                            print(f"  ERROR: {e}\n")
                    else:
                        print("  Skipped.\n")
                        total_skipped += 1

                if group_idx < len(dupes) - 1:
                    input("  Press Enter for next repo...")

            except KeyboardInterrupt:
                print("\nInterrupted.")
                return total_deleted, total_skipped

            # Restore curses
            stdscr.refresh()

    return total_deleted, total_skipped


# ── deletion summary ──────────────────────────────────────────────────────────

def print_summary(deleted, skipped):
    print(f"\nDone: {deleted} deleted, {skipped} skipped.")
    if deleted > 0:
        print("Run 'repo-browser.py rescan' to update the search index.")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    if GIT_ROOT == '':
        print("Error: gitParent not configured. Run 'repo-browser.py start' and set it via the Settings UI.")
        sys.exit(1)

    try:
        print("Scanning for duplicates...")
        dupes = find_dupes_with_paths()

        if not dupes:
            print("No duplicate clones found.")
            return

        deleted, skipped = run_tui(dupes)
        print_summary(deleted, skipped)

    except KeyboardInterrupt:
        # Ensure terminal is restored if curses left it raw
        try:
            curses.endwin()
        except Exception:
            pass
        print("\nInterrupted.")
        sys.exit(0)


if __name__ == '__main__':
    main()
