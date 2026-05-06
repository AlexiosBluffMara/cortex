#!/bin/bash
# baby-pi firstrun.sh -- runs once on first boot of Raspberry Pi OS.
# Place at the root of the boot partition. Raspberry Pi OS picks it up
# automatically (cloud-init style: cmdline.txt invokes /boot/firstrun.sh
# on first boot). Self-deletes after running.
#
# BEFORE FLASHING: edit the two PASTE-HERE blocks below.
#   1. TAILSCALE_AUTHKEY  -- one-shot reusable key from
#      https://login.tailscale.com/admin/settings/keys
#   2. SSH_PUBKEY         -- contents of C:\Users\soumi\.ssh\id_ed25519.pub
#                            (run ssh-keygen -t ed25519 on Windows first if missing)

set +e
exec > /var/log/firstrun.log 2>&1
set -x

#####################################################################
# >>> PASTE-HERE: Tailscale one-shot auth key (tskey-auth-...)       #
#####################################################################
TAILSCALE_AUTHKEY="tskey-auth-REPLACE-ME"

#####################################################################
# >>> PASTE-HERE: your SSH public key (single line, ssh-ed25519 ...) #
#####################################################################
SSH_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBHNtZs+gAvJ/etrsIhuMLis4ahAYxj2gNuEiVPqWyBn soumitlahiri@seratonin"

USER_NAME="soumit"
HOSTNAME_NEW="baby-pi"

# --- 1. Hostname -----------------------------------------------------
CURRENT_HOSTNAME="$(tr -d ' \t\n\r' < /etc/hostname)"
echo "$HOSTNAME_NEW" > /etc/hostname
sed -i "s/127.0.1.1.*$CURRENT_HOSTNAME/127.0.1.1\t$HOSTNAME_NEW/g" /etc/hosts
hostname "$HOSTNAME_NEW"

# --- 2. User + SSH key ----------------------------------------------
if ! id -u "$USER_NAME" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo,adm,dialout,cdrom,audio,video,plugdev,games,users,input,netdev,gpio,i2c,spi "$USER_NAME"
    # passwordless sudo so the user is reachable purely via SSH key
    echo "$USER_NAME ALL=(ALL) NOPASSWD: ALL" > "/etc/sudoers.d/010_$USER_NAME-nopasswd"
    chmod 440 "/etc/sudoers.d/010_$USER_NAME-nopasswd"
fi

install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.ssh"
echo "$SSH_PUBKEY" > "/home/$USER_NAME/.ssh/authorized_keys"
chown "$USER_NAME:$USER_NAME" "/home/$USER_NAME/.ssh/authorized_keys"
chmod 600 "/home/$USER_NAME/.ssh/authorized_keys"

# --- 3. Disable SSH password auth -----------------------------------
SSHD_CONF=/etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD_CONF"
sed -i 's/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' "$SSHD_CONF"
sed -i 's/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' "$SSHD_CONF"
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' "$SSHD_CONF"
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' "$SSHD_CONF"
systemctl enable ssh
systemctl restart ssh

# --- 4. Disable swap (8 GB RAM is plenty; saves the SD card) --------
systemctl disable --now dphys-swapfile || true
apt-get -y purge dphys-swapfile || true
swapoff -a || true

# --- 5. APT deps ----------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    curl git build-essential cmake ninja-build python3-venv jq ca-certificates

# --- 6. Tailscale ---------------------------------------------------
curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled
if [ -n "$TAILSCALE_AUTHKEY" ] && [ "$TAILSCALE_AUTHKEY" != "tskey-auth-REPLACE-ME" ]; then
    tailscale up --ssh --hostname="$HOSTNAME_NEW" --authkey="$TAILSCALE_AUTHKEY"
else
    echo "WARNING: TAILSCALE_AUTHKEY not set -- tailscale not joined." >&2
fi

# --- 6b. Cloudflared (so Pi can publish locally-bound services via tunnel) ---
# We don't auth-into-tunnel here -- just install the binary. Soumit will
# register tunnel hostnames manually if/when needed (see BABY_PI_REMOTE_ACCESS.md).
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/cloudflared.list
apt-get update || true
apt-get install -y cloudflared || true

# --- 6c. Google Cloud SDK (for any future GCP touchpoints; auth deferred) ---
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
  > /etc/apt/sources.list.d/google-cloud-sdk.list
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
  | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
apt-get update || true
apt-get install -y google-cloud-cli || true

# --- 6d. Remote-access trinity: xrdp, Pi Connect, Chrome Remote Desktop ----
# xrdp + xfce4: visual desktop reachable from Windows mstsc + Microsoft RD on
# any platform. ~250 MB.
apt-get install -y --no-install-recommends \
    xrdp xfce4 xfce4-terminal xfce4-goodies dbus-x11 \
    || true
adduser xrdp ssl-cert || true
systemctl enable --now xrdp || true

# Pi Connect: official Raspberry Pi remote (browser-based, free). Identity is
# the rpi-connect account; signin needs an interactive URL approval, so we
# just install + enable here. Soumit runs `rpi-connect signin` after first SSH.
apt-get install -y rpi-connect || true
sudo -u "$USER_NAME" rpi-connect on || true

# Chrome Remote Desktop: Google-account-keyed remote, the "Googley" path.
# Headless setup needs an interactive URL too; we just stage the package.
curl -fsSL https://dl.google.com/linux/direct/chrome-remote-desktop_current_arm64.deb \
  -o /tmp/crd.deb
apt-get install -y /tmp/crd.deb || true
rm -f /tmp/crd.deb

# Sunshine deps (for game-streaming-grade remote via Moonlight clients).
# Build itself is ~10 min; defer to first SSH session via a sentinel script.
apt-get install -y --no-install-recommends \
    libssl-dev libavdevice-dev libboost-locale-dev libboost-log-dev \
    libboost-program-options-dev libpulse-dev libopus-dev libxtst-dev \
    libx11-dev libxrandr-dev libxfixes-dev libxcb-shm0-dev libxcb-xfixes0-dev \
    libxcb-shape0-dev libxcb-randr0-dev libxcb-image0-dev libwayland-dev \
    libdrm-dev libcap-dev libcurl4-openssl-dev \
    || true

cat > "/home/$USER_NAME/install-sunshine.sh" <<'SUNSH'
#!/bin/bash
# Run this once after first SSH login to build Sunshine for low-latency
# Moonlight streaming. ~10 min on RPi 5.
set -e
cd "$HOME"
[ -d sunshine ] || git clone --recursive https://github.com/LizardByte/Sunshine.git ~/sunshine
cd ~/sunshine
mkdir -p build && cd build
cmake -DSUNSHINE_ENABLE_X11=ON -DSUNSHINE_ENABLE_WAYLAND=ON \
      -DSUNSHINE_ENABLE_DRM=ON -DSUNSHINE_ENABLE_VAAPI=ON \
      -G Ninja ..
ninja
sudo ninja install
systemctl --user enable --now sunshine
echo "Sunshine installed. Web UI: https://baby-pi:47990"
SUNSH
chown "$USER_NAME:$USER_NAME" "/home/$USER_NAME/install-sunshine.sh"
chmod 755 "/home/$USER_NAME/install-sunshine.sh"

# UFW: lock incoming to LAN + Tailscale CGNAT only (skip if ufw not desired)
apt-get install -y ufw || true
ufw default deny incoming || true
ufw default allow outgoing || true
ufw allow from 192.168.0.0/16 || true     # LAN
ufw allow from 100.64.0.0/10 || true       # Tailscale CGNAT
ufw --force enable || true

# --- 7. /etc/motd ---------------------------------------------------
cat > /etc/motd <<'EOF'

  baby-pi -- Ascended Base ternary tier
  Red Team Kitchen / Alexios Bluff Mara LLC
  BitNet b1.58 2B-4T live demo node. Be gentle with the SD card.

EOF

# --- 8. Schedule BitNet build on next interactive login -------------
BUILD_HOOK="/home/$USER_NAME/.bitnet-pending-build"
cat > "$BUILD_HOOK" <<'EOF'
#!/bin/bash
# Auto-runs from ~/.bash_profile once. Builds BitNet llama-server.
set -e
SENTINEL="$HOME/.bitnet-build-done"
[ -f "$SENTINEL" ] && exit 0
echo "[baby-pi] First-login BitNet build starting (~20-40 min)..."
cd "$HOME"
[ -d BitNet ] || git clone --recursive https://github.com/microsoft/BitNet.git
cd BitNet
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python setup_env.py -md models/BitNet-b1.58-2B-4T -q i2_s
touch "$SENTINEL"
echo "[baby-pi] BitNet build complete. Run with: ./build/bin/llama-server -m models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf --host 0.0.0.0 --port 8000"
EOF
chown "$USER_NAME:$USER_NAME" "$BUILD_HOOK"
chmod 755 "$BUILD_HOOK"

PROFILE="/home/$USER_NAME/.bash_profile"
touch "$PROFILE"
chown "$USER_NAME:$USER_NAME" "$PROFILE"
if ! grep -q "bitnet-pending-build" "$PROFILE"; then
    cat >> "$PROFILE" <<'EOF'

# baby-pi one-shot BitNet build (auto-removes itself after success)
if [ -x "$HOME/.bitnet-pending-build" ] && [ ! -f "$HOME/.bitnet-build-done" ]; then
    "$HOME/.bitnet-pending-build" || true
fi
EOF
fi

# --- 9. Self-delete -------------------------------------------------
rm -f /boot/firstrun.sh /boot/firmware/firstrun.sh 2>/dev/null || true

# Strip any cmdline.txt hook that ran us, so we don't re-run.
for CMDLINE in /boot/cmdline.txt /boot/firmware/cmdline.txt; do
    if [ -f "$CMDLINE" ]; then
        sed -i 's| systemd.run.*firstrun.sh||g' "$CMDLINE"
        sed -i 's| systemd.run_success_action=reboot||g' "$CMDLINE"
        sed -i 's| systemd.unit=kernel-command-line.target||g' "$CMDLINE"
    fi
done

echo "[baby-pi] firstrun.sh complete. Rebooting."
sync
sleep 2
reboot
