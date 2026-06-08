# repo-browser -- Shared configuration loader
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
