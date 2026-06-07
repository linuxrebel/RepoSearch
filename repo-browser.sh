#!/bin/bash
# repo-browser: manage the local repo search server

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PIDFILE="/tmp/repo-browser.pid"
PORT=8642

# Load config: /etc/rb.config → local rb.config → defaults from script location
load_config() {
    if [ -f /etc/rb.config ]; then
        CONFIG="/etc/rb.config"
    elif [ -f "$SCRIPT_DIR/rb.config" ]; then
        CONFIG="$SCRIPT_DIR/rb.config"
    else
        CONFIG=""
    fi

    if [ -n "$CONFIG" ]; then
        eval "$(grep -v '^#' "$CONFIG" | grep '=' | sed 's/^/export /')"
    fi

    # Defaults: workDir = script's directory, gitParent = empty (must configure)
    DIR="${workDir:-$SCRIPT_DIR}"
    export gitParent="${gitParent:-}"
}

load_config

start() {
    if running; then
        echo "Already running (PID $(cat "$PIDFILE"))"
        return 1
    fi
    ensure_ollama || return 1
    # Kill any stale process on the port
    local stale_pid
    stale_pid=$(lsof -ti :$PORT 2>/dev/null)
    if [ -n "$stale_pid" ]; then
        kill "$stale_pid" 2>/dev/null
        sleep 1
    fi
    if [ -z "$gitParent" ] && [ -n "$CONFIG" ]; then
        echo "Warning: gitParent not set in config"
    fi
    if [ ! -f "$DIR/repo_search.py" ]; then
        echo "Error: repo_search.py not found in $DIR"
        return 1
    fi
    cd "$DIR" && nohup python3 repo_search.py > /tmp/repo-browser.log 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    if running; then
        echo "Started on http://localhost:$PORT (PID $(cat "$PIDFILE"))"
        if [ -z "$CONFIG" ]; then
            echo ""
            echo "No config file found. Open http://localhost:$PORT"
            echo "and click the gear icon to configure, then:"
            echo "  sudo cp $DIR/rb.config /etc/rb.config"
        fi
    else
        echo "Failed to start — check /tmp/repo-browser.log"
        rm -f "$PIDFILE"
        return 1
    fi
}

stop() {
    if ! running; then
        echo "Not running"
        return 0
    fi
    kill "$(cat "$PIDFILE")" 2>/dev/null
    rm -f "$PIDFILE"
    echo "Stopped"
}

status() {
    if running; then
        echo "Running (PID $(cat "$PIDFILE")) on http://localhost:$PORT"
        echo "Config: ${CONFIG:-none (using defaults)}"
        echo "Git root: ${gitParent:-(not set)}"
        echo "Work dir: $DIR"
        if [ -f "$DIR/repos.db" ]; then
            python3 -c "
import sqlite3
c = sqlite3.connect('$DIR/repos.db')
r = c.execute('SELECT count(*) FROM repos').fetchone()[0]
e = c.execute('SELECT count(*) FROM repo_embeddings').fetchone()[0]
t = c.execute('SELECT count(DISTINCT tag) FROM repo_tags').fetchone()[0]
print(f'  {r} repos, {e} embedded, {t} tags')
c.close()
"
        else
            echo "  No database yet — run: repo-browser.sh rescan"
        fi
    else
        echo "Not running"
    fi
}

ensure_ollama() {
    if [ ! -f "$DIR/ensure_ollama.py" ]; then
        echo "Error: ensure_ollama.py not found in $DIR"
        return 1
    fi
    cd "$DIR" && python3 ensure_ollama.py
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo "Ollama prerequisites not met — aborting."
    fi
    return $rc
}

rescan() {
    if [ -z "$gitParent" ]; then
        echo "gitParent not configured. Set it in /etc/rb.config or via the UI gear icon."
        return 1
    fi
    ensure_ollama || return 1
    echo "Scanning repos..."
    cd "$DIR" && python3 scan_repos.py
    echo ""
    echo "Updating embeddings..."
    cd "$DIR" && python3 embed_repos.py
    echo ""
    if running; then
        echo "Server is running — refresh browser to see changes"
    else
        echo "Server not running — use: repo-browser.sh start"
    fi
}

duplist() {
    if [ -z "$gitParent" ]; then
        echo "gitParent not configured. Set it in /etc/rb.config or via the UI gear icon."
        return 1
    fi
    cd "$DIR" && python3 find-dupe.py
}

running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-}" in
    start)     start ;;
    stop)      stop ;;
    restart)   stop; sleep 1; start ;;
    status)    status ;;
    rescan)    rescan ;;
    duplist)   duplist ;;
    *)
        echo "Usage: repo-browser.sh {start|stop|restart|status|rescan|duplist}"
        exit 1
        ;;
esac
