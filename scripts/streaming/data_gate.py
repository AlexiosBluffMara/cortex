r"""Compliance gate for OBS multitrack recordings -> AI-training corpus.

OBS RTK profile records 4 audio stems into each .mp4:
  a:0 = T1 full stream mix      (presentation only, NOT training)
  a:1 = T2 own mic              (Soumit's voice  -> UNRESTRICTED)
  a:2 = T3 desktop / game       (Soumit's machine -> UNRESTRICTED)
  a:3 = T4 Discord (VAC Line 1) (THIRD-PARTY VOICES -> consent-gated)

Split rule (Illinois 720 ILCS 5/14 eavesdropping + 740 ILCS 14 BIPA):
  * video + a:1 + a:2  -> D:\rtk-data\own\         free for RL / voice / video training
  * a:3 (Discord)      -> D:\rtk-data\quarantine\  NEVER enters training unless a
                          consent_manifest.json entry releases that session.
A session is only promoted to D:\rtk-data\consented\ when every speaker in that
Craig recording has an opt-in record (written release + retention term on file).
"""
from __future__ import annotations
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"D:\rtk-data")
OWN, QUAR, CONS = ROOT / "own", ROOT / "quarantine", ROOT / "consented"
MANIFEST = ROOT / "consent_manifest.json"
REC_DIR = Path(r"C:\Users\soumi\Videos\OBS")
for d in (OWN, QUAR, CONS):
    d.mkdir(parents=True, exist_ok=True)
if not MANIFEST.exists():
    MANIFEST.write_text(json.dumps({"sessions": {}}, indent=2))


def _ff(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                   check=True)


def gate(src: Path) -> None:
    sid = src.stem  # OBS names files by timestamp -> stable session id
    man = json.loads(MANIFEST.read_text())
    sess = man["sessions"].setdefault(sid, {
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "source": str(src), "discord_consent": "PENDING",
        "speakers_opted_in": [], "retention_until": None,
    })

    own = OWN / f"{sid}.mp4"
    # -map_metadata -1 strips embedded tags (privacy: no leaked identifiers)
    _ff(["-i", str(src), "-map", "0:v", "-map", "0:a:1", "-map", "0:a:2",
         "-map_metadata", "-1", "-c", "copy", str(own)])
    print(f"[own/unrestricted] {own}")

    if sess["discord_consent"] == "RELEASED" and sess["speakers_opted_in"]:
        dst = CONS / f"{sid}_discord.m4a"
        tag = "consented"
    else:
        dst = QUAR / f"{sid}_discord.m4a"
        tag = "quarantine"
    try:
        _ff(["-i", str(src), "-map", "0:a:3", "-map_metadata", "-1",
             "-c", "copy", str(dst)])
        print(f"[{tag}] {dst}  (Discord track — {sess['discord_consent']})")
    except subprocess.CalledProcessError:
        print("[info] no Discord track in this recording (a:3 absent)")

    MANIFEST.write_text(json.dumps(man, indent=2))
    print(f"manifest: {MANIFEST}")


def cloud_sync(remote: str = "gdrive:rtk-data") -> None:
    """Push OWN + CONSENTED to cloud. QUARANTINE is categorically excluded —
    a minor's / non-consented biometric audio is never cloud-replicated."""
    import shutil as _sh
    if not _sh.which("rclone"):
        print("[cloud] rclone not found — skipping (local copy intact)")
        return
    remotes = subprocess.run(["rclone", "listremotes"],
                             capture_output=True, text=True).stdout
    base = remote.split(":")[0] + ":"
    if base not in remotes:
        print(f"[cloud] remote '{base}' not configured — skipping "
              f"(have: {remotes.split() or 'none'})")
        return
    for tier in (OWN, CONS):
        subprocess.run(["rclone", "copy", str(tier),
                        f"{remote}/{tier.name}", "--progress"], check=False)
        print(f"[cloud] synced {tier.name} -> {remote}/{tier.name}")
    print(f"[cloud] QUARANTINE deliberately NOT synced "
          f"({QUAR} stays local-only per BIPA/minor policy)")


if __name__ == "__main__":
    targets = ([Path(sys.argv[1])] if len(sys.argv) > 1
               else sorted(REC_DIR.glob("*.mp4")))
    if not targets:
        print("no recordings found")
    for t in targets:
        print(f"\n=== {t.name} ===")
        gate(t)
    if "--cloud" in sys.argv:
        cloud_sync()
