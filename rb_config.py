"""Shared config loader for repo-browser."""
import os

CONFIG_PATHS = [
    '/etc/rb.config',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rb.config'),
]

def load_config():
    """Load config from /etc/rb.config or local fallback."""
    cfg = {}
    for path in CONFIG_PATHS:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, val = line.split('=', 1)
                        cfg[key.strip()] = val.strip()
            break
    return cfg

def get_git_root():
    cfg = load_config()
    return cfg.get('gitParent', '/home/james/git')

def get_work_dir():
    cfg = load_config()
    configured = cfg.get('workDir', '')
    if configured and os.path.isdir(configured):
        return configured
    # Fallback: use the directory containing rb_config.py (SCRIPT_DIR)
    return os.path.dirname(os.path.abspath(__file__))

def get_db_path():
    return os.path.join(get_work_dir(), 'repos.db')

def get_config_source():
    """Return which config file is in use."""
    for path in CONFIG_PATHS:
        if os.path.exists(path):
            return path
    return None
