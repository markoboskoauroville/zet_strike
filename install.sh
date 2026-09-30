#!/data/data/com.termux/files/usr/bin/bash
# install.sh - ZET Strike (V6), installed straight from github.com/markoboskoauroville/z_strike
#   curl -fsSL https://raw.githubusercontent.com/markoboskoauroville/z_strike/main/install.sh | bash
# After install:  zs (terminal)   zets (map in Chrome)   zs update (newest version from GitHub)
# Rule 42: plain exit codes, no prompts.  Rule 43: output also in ~/.zet-strike/chain.txt.
# Rule 46 & 47: Maha-style amber terminal frame.
set -e
set -o pipefail
REPO="markoboskoauroville/z_strike"
APP_DIR="$HOME/.zet-strike"
BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
CHAIN="$APP_DIR/chain.txt"
mkdir -p "$APP_DIR" "$BIN_DIR"
exec > >(tee >(sed -u 's/\x1b\[[0-9;?]*[A-Za-z]//g' > "$CHAIN")) 2>&1

CB="\e[38;5;214m"; CT="\e[38;5;250m"; CH="\e[38;5;220m"; CS="\e[1;33m"; CW="\e[38;5;208m"; CR="\e[0m"
step() { printf "${CB}│${CT}  [%-2s] %-69s ${CB}│${CR}\n" "$1" "$2"; }
warn() { printf "${CB}│${CW}  %-75s${CB}│${CR}\n" "$1"; }

echo -e "${CB}┌─────────────────────────────────────────────────────────────────────────────┐"
printf "${CB}│${CH}  %-75s${CB}│\n" "MANTRA PRODUCTIONS - ZET STRIKE MONITOR (V6)"
printf "${CB}│${CT}  %-75s${CB}│\n" "from github.com/$REPO"
echo -e "├─────────────────────────────────────────────────────────────────────────────┤${CR}"

step "01" "Checking Termux packages..."
if command -v pkg >/dev/null 2>&1; then
    for p in python curl; do command -v "$p" >/dev/null 2>&1 || pkg install "$p" -y; done
    if ! command -v termux-location >/dev/null 2>&1; then
        pkg install termux-api -y || warn "termux-api not installed, zs near needs it for GPS"
    fi
fi
command -v termux-location >/dev/null 2>&1 || warn "For GPS also install the Termux:API app (F-Droid)."

step "02" "Installing Python packages..."
pip install -q flask protobuf gtfs-realtime-bindings tzdata

step "03" "Downloading the app from GitHub, checking every file..."
python - "$REPO" "$APP_DIR" <<'PYEOF'
import hashlib, json, os, sys, urllib.request
repo, app = sys.argv[1], sys.argv[2]
ua = {"User-Agent": "ZETStrike-installer"}
def get(u, accept=None):
    h = dict(ua)
    if accept: h["Accept"] = accept
    return urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=60).read()
try:
    ref = get("https://api.github.com/repos/%s/commits/main" % repo, "application/vnd.github.sha").decode().strip()
    assert len(ref) == 40
except Exception:
    ref = "main"
raw = "https://raw.githubusercontent.com/%s/%s/app/" % (repo, ref)
m = json.loads(get(raw + "MANIFEST.json"))
data = {}
for name, want in m["files"].items():
    assert "/" not in name and not name.startswith(".")
    b = get(raw + name)
    if hashlib.sha256(b).hexdigest() != want:
        sys.exit("  %s failed its checksum, nothing installed" % name)
    data[name] = b
for name, b in data.items():
    tmp = os.path.join(app, "." + name + ".new")
    open(tmp, "wb").write(b)
    os.replace(tmp, os.path.join(app, name))
json.dump({"version": m.get("version"), "commit": ref, "files": m["files"]}, open(os.path.join(app, "VERSION.json"), "w"))
print("  V%s, commit %s, %d files verified" % (m.get("version"), ref[:7], len(data)))
PYEOF

step "04" "Checking Google Transit GTFS bindings..."
python -c "from google.transit import gtfs_realtime_pb2; print('  bindings ok')"

step "05" "Creating commands zs and zets..."
rm -f "$BIN_DIR/zs" "$BIN_DIR/zets"
printf '#!/data/data/com.termux/files/usr/bin/sh\nexec python "%s/zs.py" "$@"\n' "$APP_DIR" > "$BIN_DIR/zs"
printf '#!/data/data/com.termux/files/usr/bin/sh\nexec python "%s/app.py" "$@"\n' "$APP_DIR" > "$BIN_DIR/zets"
chmod +x "$BIN_DIR/zs" "$BIN_DIR/zets"

step "06" "Downloading the ZET timetable (about 13 MB)..."
python "$APP_DIR/zs.py" update timetable || warn "Timetable download failed, zs retries on its first run."

echo -e "${CB}├─────────────────────────────────────────────────────────────────────────────┤"
step "OK" "Installation completed."
echo -e "${CB}└─────────────────────────────────────────────────────────────────────────────┘${CR}"
echo -e "\n${CS}ZET Strike V6 is ready.${CR}"
echo -e "${CT}What runs now:         ${CH}zs${CR}"
echo -e "${CT}Where to catch it:     ${CH}zs near${CR}"
echo -e "${CT}Strike news:           ${CH}zs news${CR}"
echo -e "${CT}Map in Chrome:         ${CH}zets${CR}"
echo -e "${CT}Newest version:        ${CH}zs update${CR}\n"
