#!/bin/bash
# repo-browser: manage the local repo search server

CONFIG="/etc/rb.config"
if [ ! -f "$CONFIG" ]; then
    # Fallback: config next to this script
    SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
    CONFIG="$SCRIPT_DIR/repo-browser/rb.config"
    if [ ! -f "$CONFIG" ]; then
        echo "No config found. Expected /etc/rb.config"
        echo "Run the app and use the gear icon to generate one."
        exit 1
    fi
fi

# Source config
eval "$(grep -v '^#' "$CONFIG" | grep '=' | sed 's/^/export /')"
DIR="${workDir:?workDir not set in $CONFIG}"
DB="$DIR/repos.db"
PIDFILE="/tmp/repo-browser.pid"
PORT=8642

start() {
    if running; then
        echo "Already running (PID $(cat "$PIDFILE"))"
        return 1
    fi
    cd "$DIR" && nohup python3 repo_search.py > /tmp/repo-browser.log 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    if running; then
        echo "Started on http://localhost:$PORT (PID $(cat "$PIDFILE"))"
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
        echo "Config: $CONFIG"
        echo "Git root: $gitParent"
        echo "Work dir: $DIR"
        python3 -c "
import sqlite3
c = sqlite3.connect('$DB')
r = c.execute('SELECT count(*) FROM repos').fetchone()[0]
e = c.execute('SELECT count(*) FROM repo_embeddings').fetchone()[0]
t = c.execute('SELECT count(DISTINCT tag) FROM repo_tags').fetchone()[0]
print(f'  {r} repos, {e} embedded, {t} tags')
c.close()
"
    else
        echo "Not running"
    fi
}

rescan() {
    echo "Scanning repos..."
    cd "$DIR" && python3 scan_repos.py
    echo ""
    echo "Updating embeddings..."
    cd "$DIR" && python3 embed_repos.py
    echo ""
    if running; then
        echo "Server is running — refresh browser to see changes"
    else
        echo "Server not running — use: repo-browser start"
    fi
}

running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    rescan)  rescan ;;
    *)
        echo "Usage: repo-browser {start|stop|restart|status|rescan}"
        exit 1
        ;;
esac
