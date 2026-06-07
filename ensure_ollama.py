#!/usr/bin/env python3
"""
ensure_ollama.py — called by repo-browser.sh before any embedding operation.

Checks (in order):
  1. ollama binary is installed  → installs via official curl script if missing
  2. ollama service is reachable → starts `ollama serve` in background if not
  3. nomic-embed-text is present → pulls the model if missing

Exits 0 on success, non-zero on unrecoverable failure.
"""

import json
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

MODEL       = 'nomic-embed-text'
OLLAMA_API  = 'http://localhost:11434'
INSTALL_URL = 'https://ollama.com/install.sh'


def ok(msg):    print(f'  ✓ {msg}')
def info(msg):  print(f'  → {msg}')
def err(msg):   print(f'  ✗ {msg}', file=sys.stderr)


# ── 1. Binary present? ───────────────────────────────────────────────────────

def ollama_installed() -> bool:
    return shutil.which('ollama') is not None


def install_ollama():
    info('ollama not found — installing via curl...')
    ret = subprocess.run(
        f'curl -fsSL {INSTALL_URL} | sh',
        shell=True
    )
    if ret.returncode != 0:
        err('Ollama installation failed.')
        sys.exit(1)
    if not ollama_installed():
        err('Installed, but ollama binary still not found in PATH. '
            'You may need to open a new shell or add /usr/local/bin to PATH.')
        sys.exit(1)
    ok('Ollama installed.')


# ── 2. Service reachable? ────────────────────────────────────────────────────

def ollama_reachable() -> bool:
    try:
        urlopen(f'{OLLAMA_API}/api/tags', timeout=3)
        return True
    except URLError:
        return False


def start_ollama():
    info('Ollama service not running — starting in background...')
    subprocess.Popen(
        ['ollama', 'serve'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for i in range(15):
        time.sleep(1)
        if ollama_reachable():
            ok('Ollama service started.')
            return
        if i % 5 == 4:
            info(f'Waiting for Ollama... ({i+1}s)')
    err('Ollama service did not become reachable within 15 seconds.')
    sys.exit(1)


# ── 3. Model present? ────────────────────────────────────────────────────────

def model_present() -> bool:
    try:
        resp = urlopen(f'{OLLAMA_API}/api/tags', timeout=5)
        data = json.loads(resp.read())
        return any(MODEL in m.get('name', '') for m in data.get('models', []))
    except Exception:
        return False


def pull_model():
    info(f'Pulling {MODEL} (this may take a few minutes on first run)...')
    ret = subprocess.run(['ollama', 'pull', MODEL])
    if ret.returncode != 0:
        err(f'Failed to pull {MODEL}.')
        sys.exit(1)
    ok(f'{MODEL} ready.')


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print('Checking Ollama prerequisites...')

    if not ollama_installed():
        install_ollama()
    else:
        ok('ollama binary found.')

    if not ollama_reachable():
        start_ollama()
    else:
        ok('Ollama service reachable.')

    if not model_present():
        pull_model()
    else:
        ok(f'{MODEL} model present.')

    print('Ollama ready.\n')


if __name__ == '__main__':
    main()
