#!/bin/bash
# Cortex Edge first-boot — Tailscale + AdGuard Home, nothing else.
# Runs once via systemd.run hook in cmdline.txt; self-disables.

set +e
exec >> /var/log/firstrun.log 2>&1
echo "=== firstrun $(date) ==="

# ------------------------------------------------------------------
# 1. Hostname + SSH key
# ------------------------------------------------------------------
CURRENT_HOSTNAME=$(tr -d '\0' < /etc/hostname | tr -d '\n')
echo baby-pi > /etc/hostname
sed -i "s/127.0.1.1.*$CURRENT_HOSTNAME/127.0.1.1\tbaby-pi/g" /etc/hosts

SSH_DIR=/home/soumitlahiri/.ssh
mkdir -p "$SSH_DIR"
echo "__SSH_PUBKEY__" > "$SSH_DIR/authorized_keys"
chmod 700 "$SSH_DIR"
chmod 600 "$SSH_DIR/authorized_keys"
chown -R soumitlahiri:soumitlahiri /home/soumitlahiri
systemctl enable --now ssh

# ------------------------------------------------------------------
# 2. Base packages
# ------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl wget jq ca-certificates

# ------------------------------------------------------------------
# 3. Tailscale (do not auto-up — user runs `tailscale up` once)
# ------------------------------------------------------------------
curl -fsSL https://tailscale.com/install.sh | sh
# Enable IP forwarding for subnet router / exit-node future use
cat >/etc/sysctl.d/99-tailscale.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sysctl -p /etc/sysctl.d/99-tailscale.conf

# ------------------------------------------------------------------
# 4. AdGuard Home — official installer, then drop our config
# ------------------------------------------------------------------
# Free port 53 first (systemd-resolved will fight us)
mkdir -p /etc/systemd/resolved.conf.d
cat >/etc/systemd/resolved.conf.d/cortex-edge.conf <<'EOF'
[Resolve]
DNSStubListener=no
EOF
systemctl restart systemd-resolved 2>/dev/null || true
rm -f /etc/resolv.conf
echo 'nameserver 1.1.1.1' > /etc/resolv.conf
echo 'nameserver 8.8.8.8' >> /etc/resolv.conf

# Install
curl -s -S -L https://raw.githubusercontent.com/AdguardTeam/AdGuardHome/master/scripts/install.sh \
    | sh -s -- -v
systemctl stop AdGuardHome 2>/dev/null || true

# Drop our pre-baked config (placed by flash-baby-pi-v2.ps1 at /boot/firmware/AdGuardHome.yaml)
if [[ -f /boot/firmware/AdGuardHome.yaml ]]; then
    cp /boot/firmware/AdGuardHome.yaml /opt/AdGuardHome/AdGuardHome.yaml
    chown root:root /opt/AdGuardHome/AdGuardHome.yaml
    chmod 644 /opt/AdGuardHome/AdGuardHome.yaml
    rm -f /boot/firmware/AdGuardHome.yaml
elif [[ -f /boot/AdGuardHome.yaml ]]; then
    cp /boot/AdGuardHome.yaml /opt/AdGuardHome/AdGuardHome.yaml
    chown root:root /opt/AdGuardHome/AdGuardHome.yaml
    chmod 644 /opt/AdGuardHome/AdGuardHome.yaml
    rm -f /boot/AdGuardHome.yaml
fi

systemctl enable AdGuardHome
systemctl start AdGuardHome

# ------------------------------------------------------------------
# 4b. Drop the kiosk setup script and run it (X server + Chromium kiosk)
# ------------------------------------------------------------------
if [[ -f /boot/firmware/kiosk-setup.sh ]]; then
    cp /boot/firmware/kiosk-setup.sh /usr/local/sbin/kiosk-setup.sh
    chmod +x /usr/local/sbin/kiosk-setup.sh
    rm -f /boot/firmware/kiosk-setup.sh
    nohup /usr/local/sbin/kiosk-setup.sh >/var/log/kiosk-setup.log 2>&1 &
elif [[ -f /boot/kiosk-setup.sh ]]; then
    cp /boot/kiosk-setup.sh /usr/local/sbin/kiosk-setup.sh
    chmod +x /usr/local/sbin/kiosk-setup.sh
    rm -f /boot/kiosk-setup.sh
    nohup /usr/local/sbin/kiosk-setup.sh >/var/log/kiosk-setup.log 2>&1 &
fi

# ------------------------------------------------------------------
# 5. Disable cloud-init / runonce so this never fires again
# ------------------------------------------------------------------
rm -f /boot/firmware/firstrun.sh /boot/firstrun.sh
sed -i 's| systemd.run.*||' /boot/firmware/cmdline.txt 2>/dev/null
sed -i 's| systemd.run.*||' /boot/cmdline.txt 2>/dev/null

echo "=== firstrun done $(date) ==="
echo ""
echo "Next steps for the user:"
echo "  ssh soumitlahiri@$(hostname).local"
echo "  sudo tailscale up --ssh --advertise-routes=192.168.0.0/24 --advertise-exit-node"
echo "  Browse to http://$(hostname).local/   (admin: soumit / ChangeMeNow!)"
echo "  TP-Link router 192.168.0.1 -> LAN -> DHCP -> Primary DNS = $(hostname -I | awk '{print $1}')"
exit 0
