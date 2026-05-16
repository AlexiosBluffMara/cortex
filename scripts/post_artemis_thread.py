"""Create a Discord thread in bot-test-4 and post the Artemis gallery into it,
via the bot's REST API (does NOT depend on Mercury's gateway on_message).

Reads D:/cortex/data/artemis_db.sqlite, creates a public thread, posts:
  - a topic intro
  - one message per scanned media item with its 4-tier Gemma narration
  - audio/text items noted as catalogued-but-TRIBE-unsupported
"""
import json
import re
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path

ENV = (Path.home() / ".mercury" / ".env").read_text(encoding="utf-8")
TOKEN = re.search(r"^DISCORD_BOT_TOKEN=(\S+)", ENV, re.M).group(1).strip('"').strip("'")
CHAN = "1501660119350644808"  # bot-test-4
H = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json",
     "User-Agent": "artemis-thread/1.0"}
DB = r"D:/cortex/data/artemis_db.sqlite"


def api(method, path, body=None):
    url = f"https://discord.com/api/v10{path}"
    data = json.dumps(body).encode() if body is not None else None
    for _ in range(5):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=data, headers=H, method=method),
                timeout=15) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                ra = 5
                try:
                    ra = json.loads(e.read()).get("retry_after", 5)
                except Exception:
                    pass
                print(f"  429; sleep {ra}s")
                time.sleep(ra + 1)
                continue
            print(f"  HTTP {e.code} {method} {path}: {e.read()[:200]}")
            return None
    return None


def chunk(s, n=1900):
    s = s or ""
    return [s[i:i + n] for i in range(0, len(s), n)] or [""]


# 1. Anchor message + thread
anchor = api("POST", f"/channels/{CHAN}/messages", {
    "content": "**🌙 Artemis Gallery** — a one-topic multimodal brain-response set "
               "(NASA Artemis / lunar exploration). Public-domain NASA media run "
               "through Cortex (TRIBE v2 + Gemma 4, 4 audience tiers)."})
if not anchor:
    print("anchor post failed — aborting"); raise SystemExit(1)
thread = api("POST", f"/channels/{CHAN}/messages/{anchor['id']}/threads",
             {"name": "Artemis Gallery — multimodal brain-response", "auto_archive_duration": 1440})
if not thread:
    print("thread create failed — aborting"); raise SystemExit(1)
TID = thread["id"]
print(f"thread created id={TID}")


def post(content):
    for c in chunk(content):
        api("POST", f"/channels/{TID}/messages", {"content": c})
        time.sleep(0.7)


# 2. Items
con = sqlite3.connect(DB)
rows = con.execute(
    "select filename,modality,status,tier,seconds_elapsed,"
    "narr_student,narr_patient,narr_clinician,narr_ml_scientist from scans"
).fetchall()
con.close()

n_ok = 0
for (fn, mod, st, tier, secs, ns, np_, ncl, nml) in rows:
    icon = {"video": "🎬", "image": "🖼️", "audio": "🔊", "text": "📄"}.get(mod, "📦")
    bad_narr = (not ns) or ("Ollama error" in (ns or "")) or ("unreadable" in (ns or "").lower())
    if st == "complete" and not bad_narr:
        n_ok += 1
        post(f"{icon} **{fn}**  ·  `{mod}`  ·  tier {tier}  ·  "
             f"{f'{secs:.1f}s' if isinstance(secs,(int,float)) else 'n/a'}\n"
             f"__Student__: {ns.strip()}")
        post(f"__Patient__: {(np_ or '').strip()}")
        post(f"__Clinician__: {(ncl or '').strip()}")
        post(f"__ML scientist__: {(nml or '').strip()}")
    else:
        # TRIBE v2 is trimodal (text→Llama-3.2, audio→Wav2Vec-BERT,
        # video→V-JEPA2); all four modalities ARE valid brain-response
        # inputs. A non-complete status here is a genuine per-item failure,
        # never an "unsupported modality".
        post(f"{icon} **{fn}**  ·  `{mod}`  — catalogued in the topic DB, "
             f"not brain-narrated (status={st}).")
    time.sleep(0.5)

post(f"— end of gallery · {n_ok} brain-narrated item(s) · "
     f"DB: `D:/cortex/data/artemis_db.sqlite` · topic: NASA Artemis / lunar exploration")
print(f"DONE — posted {n_ok} narrated items into thread {TID}")
print(f"thread URL: https://discord.com/channels/1164231468378239017/{TID}")
