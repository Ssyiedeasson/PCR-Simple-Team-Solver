# -*- coding: utf-8 -*-
"""Download unit icons for playable characters (concurrent, manifest-driven).

Source: KKeyfun/pcr-lookup-assets. Files: unit_icon_unit_<角色ID><variant>.webp.
"""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "pcr_team", "data")
ICON_DIR = os.path.join(DATA, "icons")
UA = "Mozilla/5.0 (PCR-TeamBuilder icons)"
CDNS = [
    "https://cdn.jsdelivr.net/gh/KKeyfun/pcr-lookup-assets@main/unitIcons/{name}",
    "https://raw.githubusercontent.com/KKeyfun/pcr-lookup-assets/main/unitIcons/{name}",
]


def fetch(name: str) -> bytes:
    last = None
    for tpl in CDNS:
        try:
            req = urllib.request.Request(tpl.format(name=name), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            if data[:4] == b"RIFF" or data[:3] == b"\xff\xd8\xff":
                return data
            last = "bad data"
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(str(last))


def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    chars = json.load(open(os.path.join(DATA, "characters.json"), encoding="utf-8"))
    manifest = set(json.load(open(os.path.join(DATA, "icon_files.json"), encoding="utf-8")))
    jobs, index, missing = [], [], []
    for c in chars:
        if not c.get("playable") or not c.get("id"):
            continue
        cid = str(c["id"])
        path = os.path.join(ICON_DIR, cid + ".webp")
        if os.path.exists(path):
            index.append({"name": c["name"], "id": c["id"], "file": cid + ".webp"})
            continue
        for suffix in ("61", "31", "11"):
            name = f"unit_icon_unit_{cid}{suffix}.webp"
            if name in manifest:
                jobs.append((c["name"], c["id"], name, path))
                break
        else:
            missing.append(c["name"])

    def work(job):
        name, cid, remote, path = job
        try:
            data = fetch(remote)
            with open(path, "wb") as f:
                f.write(data)
            return {"name": name, "id": cid, "file": cid + ".webp"}
        except Exception:  # noqa: BLE001
            return None

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(work, jobs):
            done += 1
            if res:
                index.append(res)
            if done % 40 == 0:
                print(" ", done, "/", len(jobs), flush=True)
    index.sort(key=lambda x: x["id"])
    json.dump(index, open(os.path.join(DATA, "icon_index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("icons", len(index), "missing", len(missing), missing[:12])


if __name__ == "__main__":
    main()
