#!/bin/bash
# Cortex Edge — kiosk add-on
# Runs after firstrun.sh has installed Tailscale + AdGuard Home.
# Sets up Chromium in fullscreen kiosk on the dual Targus HDMI outputs,
# pointed at Seratonin's dashboard.

set +e
exec >> /var/log/kiosk-setup.log 2>&1
echo "=== kiosk-setup $(date) ==="

DASHBOARD_URL_LEFT="${DASHBOARD_URL_LEFT:-http://seratonin:9090/mercury}"
DASHBOARD_URL_RIGHT="${DASHBOARD_URL_RIGHT:-http://seratonin:9090/cortex}"

export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------
# 1. X server, window manager, browser
# ---------------------------------------------------------------------
apt-get update
apt-get install -y \
    xserver-xorg xinit x11-xserver-utils \
    openbox \
    chromium-browser \
    fonts-roboto fonts-inter \
    unclutter \
    plymouth plymouth-themes

# ---------------------------------------------------------------------
# 2. DisplayLink driver (in case Targus dock is DL-based, not native HDMI)
# ---------------------------------------------------------------------
# Detect Synaptics DisplayLink on USB; if present, install evdi + driver.
if lsusb | grep -qi "DisplayLink\|17e9"; then
    echo "DisplayLink dock detected — installing evdi + driver"
    apt-get install -y dkms libdrm-dev libevdi0 || true
    # Synaptics' install script (avoids out-of-tree headaches):
    wget -qO /tmp/dl.zip "https://www.synaptics.com/sites/default/files/exe_files/2024-04/displaylink-driver-6.1.0-15.zip" || true
    if [[ -f /tmp/dl.zip ]]; then
        cd /tmp && unzip -q dl.zip && chmod +x displaylink-driver-*.run
        ./displaylink-driver-*.run --accept || echo "DL install failed; native HDMI will still work"
        cd -
    fi
else
    echo "No DisplayLink — using Pi 5 native dual micro-HDMI (preferred)"
fi

# ---------------------------------------------------------------------
# 3. Auto-login + start X for the kiosk user
# ---------------------------------------------------------------------
KIOSK_USER=soumitlahiri
KIOSK_HOME="/home/$KIOSK_USER"

# raspi-config style auto-login to console (avoids gdm/lightdm bloat)
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $KIOSK_USER --noclear %I \$TERM
EOF

# Start X on tty1 login
cat > "$KIOSK_HOME/.bash_profile" <<'EOF'
# Auto-start X on tty1 only
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec startx -- -nocursor
fi
EOF
chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.bash_profile"

# ---------------------------------------------------------------------
# 4. .xinitrc — openbox + xrandr dual-display + dual Chromium
# ---------------------------------------------------------------------
cat > "$KIOSK_HOME/.xinitrc" <<'EOF'
#!/bin/sh
# Hide cursor after 1s of inactivity
unclutter -idle 1 -root &

# Detect outputs and arrange side-by-side. Native Pi 5 = HDMI-1, HDMI-2.
# DisplayLink names will be DVI-I-1, DVI-I-2 etc.
OUTPUTS=$(xrandr --listmonitors | awk 'NR>1 {print $4}')
PRIMARY=$(echo "$OUTPUTS" | head -1)
SECONDARY=$(echo "$OUTPUTS" | sed -n 2p)

if [ -n "$PRIMARY" ] && [ -n "$SECONDARY" ]; then
    xrandr --output "$PRIMARY"  --auto --primary --pos 0x0
    xrandr --output "$SECONDARY" --auto --right-of "$PRIMARY"
fi

# No screen blanking
xset s off
xset s noblank
xset -dpms

# Window manager (super lean)
openbox-session &
sleep 1

# Get geometry of each monitor
LEFT_GEOM=$(xrandr | awk -v out="$PRIMARY" '$1==out && /\bconnected\b/ {print $0}' | grep -oP '\d+x\d+\+\d+\+\d+' | head -1)
RIGHT_GEOM=$(xrandr | awk -v out="$SECONDARY" '$1==out && /\bconnected\b/ {print $0}' | grep -oP '\d+x\d+\+\d+\+\d+' | head -1)

LW=$(echo "$LEFT_GEOM"  | cut -dx -f1)
LH=$(echo "$LEFT_GEOM"  | cut -d+ -f1 | cut -dx -f2)
LX=$(echo "$LEFT_GEOM"  | cut -d+ -f2)
LY=$(echo "$LEFT_GEOM"  | cut -d+ -f3)

RW=$(echo "$RIGHT_GEOM" | cut -dx -f1)
RH=$(echo "$RIGHT_GEOM" | cut -d+ -f1 | cut -dx -f2)
RX=$(echo "$RIGHT_GEOM" | cut -d+ -f2)
RY=$(echo "$RIGHT_GEOM" | cut -d+ -f3)

# Two Chromium instances, separate user-data-dirs to allow side-by-side
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-translate \
    --disable-features=TranslateUI \
    --no-first-run \
    --user-data-dir=/tmp/chrome-left \
    --window-position=${LX},${LY} \
    --window-size=${LW},${LH} \
    "__DASHBOARD_LEFT__" &

if [ -n "$SECONDARY" ]; then
    sleep 2
    chromium-browser \
        --kiosk \
        --noerrdialogs \
        --disable-infobars \
        --disable-translate \
        --disable-features=TranslateUI \
        --no-first-run \
        --user-data-dir=/tmp/chrome-right \
        --window-position=${RX},${RY} \
        --window-size=${RW},${RH} \
        "__DASHBOARD_RIGHT__" &
fi

# Keep X running
wait
EOF

# Substitute the URLs (avoids quoting hell in heredoc)
sed -i "s|__DASHBOARD_LEFT__|$DASHBOARD_URL_LEFT|" "$KIOSK_HOME/.xinitrc"
sed -i "s|__DASHBOARD_RIGHT__|$DASHBOARD_URL_RIGHT|" "$KIOSK_HOME/.xinitrc"
chmod +x "$KIOSK_HOME/.xinitrc"
chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_HOME/.xinitrc"

# ---------------------------------------------------------------------
# 5. Boot splash — cardinal red on black with "ASCENDED BASE" text
# ---------------------------------------------------------------------
plymouth-set-default-theme spinner 2>/dev/null || true

# ---------------------------------------------------------------------
# 6. Done — reboot recommended for clean kiosk start
# ---------------------------------------------------------------------
systemctl set-default multi-user.target  # don't pull in graphical.target deps
systemctl enable getty@tty1

echo "=== kiosk-setup done $(date) ==="
echo "Reboot the Pi to enter kiosk mode."
echo "Left monitor:  $DASHBOARD_URL_LEFT"
echo "Right monitor: $DASHBOARD_URL_RIGHT"
