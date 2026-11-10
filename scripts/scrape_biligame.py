"""Scrape Princess Connect Re:Dive character wikitext from biligame wiki (resumable)."""
import json, os, sys, time, urllib.parse, urllib.request

API = "https://wiki.biligame.com/pcr/api.php"
UA = "Mozilla/5.0 (PCR-TeamBuilder data research)"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "pcr_team", "data", "raw_wikitext.json")


def api(params):
    params = dict(params, format="json", formatversion="2")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa
            last = e
            time.sleep(3 + attempt * 3)
    raise RuntimeError(f"failed {url}: {last}")


def main():
    pages = {}
    if os.path.exists(OUT):
        pages = json.load(open(OUT, encoding="utf-8"))
        print("resume with", len(pages))
    data = api({"action": "query", "list": "categorymembers",
                "cmtitle": "Category:角色", "cmlimit": "500"})
    titles = [m["title"] for m in data["query"]["categorymembers"]]
    todo = [t for t in titles if t not in pages]
    print("titles:", len(titles), "todo:", len(todo))
    for i in range(0, len(todo), 20):
        batch = todo[i:i + 20]
        d = api({"action": "query", "prop": "revisions", "rvprop": "content",
                 "rvslots": "main", "titles": "|".join(batch)})
        if "query" not in d:
            print("no query in response", str(d)[:300], file=sys.stderr)
            continue
        for p in d["query"]["pages"]:
            if "revisions" not in p:
                print("missing", p["title"], file=sys.stderr)
                continue
            pages[p["title"]] = p["revisions"][0]["slots"]["main"]["content"]
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(pages, f, ensure_ascii=False)
        print(f"{min(i+20, len(todo))}/{len(todo)} (total {len(pages)})", flush=True)
        time.sleep(1.0)
    print("saved", len(pages), "->", OUT)


if __name__ == "__main__":
    main()
