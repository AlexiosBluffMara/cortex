baby-pi unattended SD card boot -- instructions
================================================

1. Run Raspberry Pi Imager and write Raspberry Pi OS (64-bit Lite) to the SD
   card on drive F:. When Imager finishes, do NOT eject the SD card.

2. Open Windows Explorer. The boot partition will reappear as a new drive
   letter (typically D:, E:, or G:) labeled "bootfs" or "boot".

3. Copy D:\cortex\scripts\baby-pi-boot\firstrun.sh to the ROOT of that boot
   partition. End path on the card should look like: <DriveLetter>:\firstrun.sh

4. Open firstrun.sh in Notepad (or VS Code) and edit two lines:
   a. TAILSCALE_AUTHKEY="tskey-auth-REPLACE-ME"
      Replace with a one-shot reusable auth key from
      https://login.tailscale.com/admin/settings/keys
      (Generate auth key -> Reusable: yes, Ephemeral: no, Pre-approved: yes)
   b. SSH_PUBKEY="ssh-ed25519 AAAAREPLACEMEREPLACEME soumit@seratonin"
      Replace with the contents of your Windows public key. Get it via:
        type C:\Users\soumi\.ssh\id_ed25519.pub
      If that file does not exist, first run:
        ssh-keygen -t ed25519 -C "soumit@seratonin"
      then re-run the type command.

5. Save the file. Eject the SD card from Windows safely.

6. Plug the SD card into the Raspberry Pi 5. Connect micro-HDMI to the 4K
   monitor, USB keyboard, then USB-C power LAST.

7. First boot takes ~3 min. You will see the auto-config running on screen
   (hostname change, apt install, Tailscale join, BitNet schedule). The Pi
   reboots itself when firstrun.sh finishes.

8. From this Windows desktop, SSH in over Tailscale:
        tailscale ssh soumit@baby-pi
   Or from PowerShell explicitly:
        & "C:\Program Files\Tailscale\tailscale.exe" ssh soumit@baby-pi

9. The first interactive login triggers the BitNet build (~20-40 min). Watch
   it run, then start the server:
        cd ~/BitNet && . .venv/bin/activate
        ./build/bin/llama-server -m models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf --host 0.0.0.0 --port 8000

Logs from the unattended firstrun live at /var/log/firstrun.log on the Pi.
