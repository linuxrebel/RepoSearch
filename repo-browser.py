#!/usr/bin/env python3
"""
repo-browser.py — cross-platform launcher for repo-browser (Linux & macOS)

Commands:
  start    — start the search server on :8642
  stop     — stop the server
  restart  — stop + start
  status   — PID, config, repo/embed/tag counts
  rescan   — re-scan repos and re-embed (server stays up)
  duplist  — report duplicate clones to ~/Clone-Duplist.txt
"""

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PIDFILE    = Path('/tmp/repo-browser.pid')
PORT       = 8642


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Read /etc/rb.config or local rb.config; return dict of values + _source."""
    cfg = {'_source': None, 'workDir': str(SCRIPT_DIR), 'gitParent': ''}

    for candidate in (Path('/etc/rb.config'), SCRIPT_DIR / 'rb.config'):
        if candidate.exists():
            cfg['_source'] = str(candidate)
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    cfg[k.strip()] = v.strip()
            break

    cfg.setdefault('workDir', str(SCRIPT_DIR))
    cfg.setdefault('gitParent', '')
    return cfg


# ── Process helpers ───────────────────────────────────────────────────────────

def running() -> bool:
    if not PIDFILE.exists():
        return False
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)   # signal 0 = existence check
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def get_pid() -> int | None:
    try:
        return int(PIDFILE.read_text().strip())
    except Exception:
        return None


def kill_stale_on_port():
    """Terminate any process already holding PORT before we start."""
    try:
        result = subprocess.run(
            ['lsof', '-ti', f':{PORT}'],
            capture_output=True, text=True
        )
        for pid_str in result.stdout.strip().splitlines():
            try:
                os.kill(int(pid_str), signal.SIGTERM)
            except Exception:
                pass
        if result.stdout.strip():
            time.sleep(1)
    except FileNotFoundError:
        pass  # lsof unavailable — best effort


# ── Ollama gate ───────────────────────────────────────────────────────────────

def ensure_ollama(work_dir: str) -> bool:
    script = Path(work_dir) / 'ensure_ollama.py'
    if not script.exists():
        print(f'Error: ensure_ollama.py not found in {work_dir}')
        return False
    ret = subprocess.run([sys.executable, str(script)])
    if ret.returncode != 0:
        print('Ollama prerequisites not met — aborting.')
        return False
    return True


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_start(cfg: dict) -> int:
    if running():
        print(f'Already running (PID {get_pid()})')
        return 1

    if not ensure_ollama(cfg['workDir']):
        return 1

    kill_stale_on_port()

    if not cfg['gitParent'] and cfg['_source']:
        print('Warning: gitParent not set in config')

    server = Path(cfg['workDir']) / 'repo_search.py'
    if not server.exists():
        print(f'Error: repo_search.py not found in {cfg["workDir"]}')
        return 1

    log = open('/tmp/repo-browser.log', 'w')
    proc = subprocess.Popen(
        [sys.executable, str(server)],
        cwd=cfg['workDir'],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    PIDFILE.write_text(str(proc.pid))
    time.sleep(1)

    if running():
        print(f'Started on http://localhost:{PORT} (PID {proc.pid})')
        if not cfg['_source']:
            print(f'\nNo config file found. Open http://localhost:{PORT}')
            print('and click the gear icon to configure, then:')
            print(f'  sudo cp {cfg["workDir"]}/rb.config /etc/rb.config')
    else:
        print('Failed to start — check /tmp/repo-browser.log')
        PIDFILE.unlink(missing_ok=True)
        return 1
    return 0


def cmd_stop(_cfg: dict) -> int:
    if not running():
        print('Not running')
        return 0
    pid = get_pid()
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    PIDFILE.unlink(missing_ok=True)
    print('Stopped')
    return 0


def cmd_restart(cfg: dict) -> int:
    cmd_stop(cfg)
    time.sleep(1)
    return cmd_start(cfg)


def cmd_status(cfg: dict) -> int:
    if running():
        print(f'Running (PID {get_pid()}) on http://localhost:{PORT}')
        print(f'Config:   {cfg["_source"] or "none (using defaults)"}')
        print(f'Git root: {cfg["gitParent"] or "(not set)"}')
        print(f'Work dir: {cfg["workDir"]}')
        db = Path(cfg['workDir']) / 'repos.db'
        if db.exists():
            con = sqlite3.connect(str(db))
            r = con.execute('SELECT count(*) FROM repos').fetchone()[0]
            e = con.execute('SELECT count(*) FROM repo_embeddings').fetchone()[0]
            t = con.execute('SELECT count(DISTINCT tag) FROM repo_tags').fetchone()[0]
            con.close()
            print(f'  {r} repos · {e} embedded · {t} tags')
        else:
            print('  No database yet — run: repo-browser.py rescan')
    else:
        print('Not running')
    return 0


def cmd_rescan(cfg: dict) -> int:
    if not cfg['gitParent']:
        print('gitParent not configured. Set it in /etc/rb.config or via the UI gear icon.')
        return 1
    if not ensure_ollama(cfg['workDir']):
        return 1
    print('Scanning repos...')
    subprocess.run([sys.executable, 'scan_repos.py'], cwd=cfg['workDir'])
    print()
    print('Updating embeddings...')
    subprocess.run([sys.executable, 'embed_repos.py'], cwd=cfg['workDir'])
    print()
    if running():
        print('Server is running — refresh browser to see changes')
    else:
        print('Server not running — use: repo-browser.py start')
    return 0


def cmd_duplist(cfg: dict) -> int:
    if not cfg['gitParent']:
        print('gitParent not configured. Set it in /etc/rb.config or via the UI gear icon.')
        return 1
    subprocess.run([sys.executable, 'find-dupe.py'], cwd=cfg['workDir'])
    return 0


# ── Dispatch ──────────────────────────────────────────────────────────────────

COMMANDS = {
    'start':   cmd_start,
    'stop':    cmd_stop,
    'restart': cmd_restart,
    'status':  cmd_status,
    'rescan':  cmd_rescan,
    'duplist': cmd_duplist,
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        name = Path(sys.argv[0]).name
        print(f'Usage: {name} {{{"|".join(COMMANDS)}}}')
        sys.exit(1)

    cfg = load_config()
    sys.exit(COMMANDS[sys.argv[1]](cfg) or 0)


if __name__ == '__main__':
    main()
