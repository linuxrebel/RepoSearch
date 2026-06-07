#!/usr/bin/env python3
"""
ensure_ollama.py — called by repo-browser.sh on start and before embedding.

Checks (in order):
  1. ollama binary installed  → prompts to install via official curl script
  2. ollama service reachable → starts quietly in background (no prompt needed)
  3. nomic-embed-text present → prompts to pull (~274 MB)

If the user declines any prompt, prints manual instructions and exits non-zero.
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


def ok(msg):   print(f'  ✓ {msg}')
def info(msg): print(f'  → {msg}')
def err(msg):  print(f'  ✗ {msg}', file=sys.stderr)


def ask(question, default_yes=True) -> bool:
    """Prompt the user. Default answer is shown in caps. Returns True for yes."""
    hint = '[Y/n]' if default_yes else '[y/N]'
    try:
        answer = input(f'  {question} {hint}: ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer == '':
        return default_yes
    return answer in ('y', 'yes')


# ── 1. Binary present? ───────────────────────────────────────────────────────

def ollama_installed() -> bool:
    return shutil.which('ollama') is not None


def install_ollama():
    print()
    print('  Ollama is not installed. It is required for semantic search.')
    if not ask('Install Ollama now? (uses: curl -fsSL https://ollama.com/install.sh | sh)'):
        print()
        err('Ollama not installed. To install manually:')
        print('    curl -fsSL https://ollama.com/install.sh | sh', file=sys.stderr)
        sys.exit(1)
    print()
    ret = subprocess.run('curl -fsSL https://ollama.com/install.sh | sh', shell=True)
    if ret.returncode != 0 or not ollama_installed():
        err('Installation failed. Try manually:')
        print('    curl -fsSL https://ollama.com/install.sh | sh', file=sys.stderr)
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
            info(f'Waiting for Ollama service... ({i+1}s)')
    err('Ollama service did not become reachable within 15 seconds.')
    err('Try running: ollama serve')
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
    print()
    print(f'  The {MODEL} embedding model is not installed (~274 MB download).')
    print( '  It is required for semantic search.')
    if not ask(f'Pull {MODEL} now?'):
        print()
        err(f'{MODEL} not pulled. To pull manually:')
        print(f'    ollama pull {MODEL}', file=sys.stderr)
        sys.exit(1)
    print()
    ret = subprocess.run(['ollama', 'pull', MODEL])
    if ret.returncode != 0:
        err(f'Failed to pull {MODEL}. Try manually: ollama pull {MODEL}')
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
