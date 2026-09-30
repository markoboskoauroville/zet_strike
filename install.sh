#!/data/data/com.termux/files/usr/bin/bash
# install.sh - ZET Strike (V10), installed straight from github.com/markoboskoauroville/zet_strike
#   curl -fsSL https://raw.githubusercontent.com/markoboskoauroville/zet_strike/main/install.sh | bash
# A test branch before it reaches main:  ... | ZET_BRANCH=<branch> bash   (zet update then follows that branch)
# After install:  zet (the server, the page in Chrome)   zet now (the board in the terminal)   zet update
# Rule 42: plain exit codes, no prompts.  Rule 43: output also in ~/.zet-strike/chain.txt.
# Rule 46 & 47: Maha-style amber terminal frame.
set -e
set -o pipefail
REPO="markoboskoauroville/zet_strike"
BRANCH="${ZET_BRANCH:-main}"
APP_DIR="$HOME/.zet-strike"
BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
CHAIN="$APP_DIR/chain.txt"
mkdir -p "$APP_DIR" "$BIN_DIR"
exec > >(tee >(sed -u 's/\x1b\[[0-9;?]*[A-Za-z]//g' > "$CHAIN")) 2>&1

CB="\e[38;5;214m"; CT="\e[38;5;250m"; CH="\e[38;5;220m"; CS="\e[1;33m"; CW="\e[38;5;208m"; CR="\e[0m"
step() { printf "${CB}│${CT}  [%-2s] %-69s ${CB}│${CR}\n" "$1" "$2"; }
warn() { printf "${CB}│${CW}  %-75s${CB}│${CR}\n" "$1"; }

echo -e "${CB}┌─────────────────────────────────────────────────────────────────────────────┐"
printf "${CB}│${CH}  %-75s${CB}│\n" "MANTRA PRODUCTIONS - ZET STRIKE MONITOR (V10)"
printf "${CB}│${CT}  %-75s${CB}│\n" "from github.com/$REPO ($BRANCH)"
echo -e "├─────────────────────────────────────────────────────────────────────────────┤${CR}"

step "01" "Checking Termux packages..."
if command -v pkg >/dev/null 2>&1; then
    for p in python curl; do command -v "$p" >/dev/null 2>&1 || pkg install "$p" -y; done
    if ! command -v termux-location >/dev/null 2>&1; then
        pkg install termux-api -y || warn "termux-api not installed, zet near needs it for GPS"
    fi
fi
command -v termux-location >/dev/null 2>&1 || warn "For GPS also install the Termux:API app (F-Droid)."

step "02" "Installing Python packages..."
pip install -q flask waitress protobuf gtfs-realtime-bindings tzdata

step "03" "Downloading the app from GitHub, checking every file..."
python - "$REPO" "$APP_DIR" "$BRANCH" <<'PYEOF'
import hashlib, json, os, sys, urllib.request
repo, app, branch = sys.argv[1], sys.argv[2], sys.argv[3]
ua = {"User-Agent": "ZETStrike-installer"}
def get(u, accept=None):
    h = dict(ua)
    if accept: h["Accept"] = accept
    return urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=60).read()
try:
    ref = get("https://api.github.com/repos/%s/commits/%s" % (repo, branch), "application/vnd.github.sha").decode().strip()
    assert len(ref) == 40
except Exception:
    ref = branch
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
json.dump({"version": m.get("version"), "commit": ref, "files": m["files"], "branch": branch}, open(os.path.join(app, "VERSION.json"), "w"))
print("  V%s, commit %s, %d files verified" % (m.get("version"), ref[:7], len(data)))
PYEOF

step "04" "Checking Google Transit GTFS bindings..."
python -c "from google.transit import gtfs_realtime_pb2; print('  bindings ok')"

step "05" "Creating the command zet (and removing the old zs, zets)..."
rm -f "$BIN_DIR/zs" "$BIN_DIR/zets" "$BIN_DIR/zet" "$APP_DIR/zs.py"
printf '#!/data/data/com.termux/files/usr/bin/sh\nexec python "%s/zet.py" "$@"\n' "$APP_DIR" > "$BIN_DIR/zet"
chmod +x "$BIN_DIR/zet"

step "06" "ZET timetable: 13 MB the first time, then only when ZET changes it..."
python "$APP_DIR/zet.py" update timetable || warn "Timetable download failed, zet retries on its first run."

echo -e "${CB}├─────────────────────────────────────────────────────────────────────────────┤"
step "OK" "Installation completed."
echo -e "${CB}└─────────────────────────────────────────────────────────────────────────────┘${CR}"
echo -e "\n${CS}ZET Strike V10 is ready.${CR}"
echo -e "${CT}The server, in Chrome:  ${CH}zet${CR}   (O A U R Q)"
echo -e "${CT}What runs now:         ${CH}zet now${CR}"
echo -e "${CT}Where to catch it:     ${CH}zet near${CR}"
echo -e "${CT}Strike news:           ${CH}zet news${CR}"
echo -e "${CT}Keys from a file:      ${CH}zet keys import FILE${CR}   (or in the page, Settings)"
echo -e "${CT}Newest version:        ${CH}zet update${CR}\n"
