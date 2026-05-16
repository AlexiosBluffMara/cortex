r"""Continuous separate A/V capture.

OBS records the muxed multitrack master (reliable capture). Every ROTATE_S
this watcher cleanly rotates the recording (stop -> start) to finalize a
chunk, then demuxes that chunk into SEPARATE video + per-stem audio files
(separate_av.separate) and deletes the redundant muxed chunk after an
ffprobe-verified PASS. The live STREAM is a different OBS output and is
never touched by record stop/start (verified).
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

from obsws_python import ReqClient
from separate_av import separate

ROTATE_S = 300  # 5-min chunks
cfg = json.loads((Path(os.environ["APPDATA"]) /
                  "obs-studio/plugin_config/obs-websocket/config.json").read_text())


def c() -> ReqClient:
    return ReqClient(host="localhost", port=cfg["server_port"],
                     password=cfg["server_password"], timeout=15)


def main() -> None:
    cl = c()
    if not cl.get_record_status().output_active:
        cl.start_record()
        print("recording (re)started", flush=True)
    while True:
        time.sleep(ROTATE_S)
        try:
            cl = c()
            if not cl.get_record_status().output_active:
                cl.start_record(); time.sleep(2); continue
            r = cl.stop_record()
            chunk = Path(r.output_path)
            time.sleep(2)
            cl.start_record()  # resume immediately (stream unaffected)
            if chunk.exists():
                print(f"[{time.strftime('%H:%M:%S')}] rotate -> {chunk.name}", flush=True)
                ok = separate(chunk)
                if ok:
                    sz = chunk.stat().st_size
                    chunk.unlink()  # redundant muxed master removed; content lives in separated/
                    print(f"  separated + removed muxed chunk ({sz/1e9:.2f} GB reclaimed)", flush=True)
                else:
                    print("  SEPARATION FAILED - keeping muxed chunk for recovery", flush=True)
        except Exception as e:
            print(f"[watch] error: {e}", flush=True)
            try:
                if not c().get_record_status().output_active:
                    c().start_record()
            except Exception:
                pass


if __name__ == "__main__":
    main()
