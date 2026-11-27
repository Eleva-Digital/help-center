#!/usr/bin/env python3
"""
Eleva Help Center sync.

Reads the public platform knowledge base (Freshdesk portal), white-labels it,
and writes static JSON into ./docs/data for GitHub Pages:

  docs/data/index.json        categories + folders + every article's title/excerpt/keywords
  docs/data/a/<id>.json       one file per article (sanitized HTML body)

Only the Python standard library is used, so it runs anywhere (GitHub Actions included).
Usage:  python3 scripts/sync.py [--limit N] [--workers 6]
"""
import argparse, concurrent.futures as cf, gzip, hashlib, html, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
SRC = CFG["source"].rstrip("/")
OUT = os.path.join(ROOT, "docs", "data")
UA = "Mozilla/5.0 (compatible; ElevaHelpSync/1.0)"

# ---------------------------------------------------------------- fetching (polite: one request at a time, paced)
import threading
_lock = threading.Lock(); _next = [0.0]
GAP = float(os.environ.get("SYNC_GAP", "0.9"))     # seconds between requests
REQS = [0]
def get(url, tries=6):
    url = urllib.parse.quote(url, safe=":/?=&%#")
    for n in range(tries):
        with _lock:
            wait = _next[0] - time.time()
            if wait > 0: time.sleep(wait)
            _next[0] = time.time() + GAP
        try:
            REQS[0] += 1
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=40) as r:
                b = r.read()
                if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
                return b.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            if e.code in (429, 503):
                ra = e.headers.get("Retry-After"); pause = int(ra) if ra and ra.isdigit() else 60 * (n + 1)
                print("  rate limited, pausing %ds" % pause, flush=True)
                with _lock: _next[0] = time.time() + pause
                continue
            if n == tries - 1: raise
        except Exception:
            if n == tries - 1: raise
        time.sleep(2 * (n + 1))

# ---------------------------------------------------------------- white label
REPL = [(re.compile(p, f), r) for p, r, f in [
    (r"https?://help\.(go)?highlevel\.com/support/solutions/articles/(\d+)[\w\-]*", r"#/a/\2", re.I),
    (r"https?://app\.(go)?highlevel\.com", "https://" + CFG["appDomain"], re.I),
    (r"https?://(www\.)?(go)?highlevel\.com[^\s\"'<]*", "https://" + CFG["siteDomain"], re.I),
    (r"\bGo ?High ?Level\b", CFG["brand"], re.I),
    (r"\bHigh ?Level\b", CFG["brand"], re.I),
    (r"\bLead ?Connector\b", CFG["brand"], re.I),
    (r"\bGHL\b", CFG["brand"], 0),
    (r"\bLC[ -](Phone|Email)\b", CFG["brand"] + r" \1", 0),
]]
def wl(s):
    for rx, rep in REPL: s = rx.sub(rep, s)
    return s

# ---------------------------------------------------------------- html sanitizer
KEEP = {"p","br","hr","h2","h3","h4","h5","strong","b","em","i","u","s","ul","ol","li","a","img","table","thead",
        "tbody","tr","td","th","blockquote","pre","code","iframe","figure","figcaption","span","div","sup","sub","kbd"}
VOID = {"br","hr","img"}
ATTRS = {"a":{"href","id"}, "img":{"src","alt","width","height"}, "iframe":{"src","allowfullscreen"},
         "td":{"colspan","rowspan"}, "th":{"colspan","rowspan"}, "h2":{"id"}, "h3":{"id"}, "h4":{"id"}}
IFRAME_OK = re.compile(r"^https://(www\.)?(youtube\.com|youtube-nocookie\.com|player\.vimeo\.com|www\.loom\.com|loom\.com|fast\.wistia\.(net|com))/", re.I)

class Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.o = []; self.skip = 0; self.stack = []
        self.text = []; self.heads = []; self._h = None; self.imgs = 0
    def handle_starttag(self, t, a):
        if t in ("script","style","noscript","form","button","svg"): self.skip += 1; return
        if self.skip or t not in KEEP: return
        d = dict(a); keep = ATTRS.get(t, set()); out = []
        if t == "img":
            src = d.get("src") or d.get("data-src") or ""
            if not src.startswith("http"): return
            d["src"] = src; self.imgs += 1; out.append(' loading="lazy"')
        if t == "iframe":
            if not IFRAME_OK.match(d.get("src","")): return
            out.append(' loading="lazy" allowfullscreen')
        if t == "a":
            href = wl(d.get("href",""))
            if href.startswith("javascript"): href = ""
            d["href"] = href
            if href.startswith("http"): out.append(' target="_blank" rel="noopener"')
        if t in ("h2","h3","h4"): self._h = []
        for k in keep:
            if k in d and d[k] is not None and k != "allowfullscreen":
                out.insert(0, ' %s="%s"' % (k, html.escape(wl(d[k]) if k == "alt" else d[k], quote=True)))
        self.o.append("<%s%s>" % (t, "".join(out)))
        if t not in VOID: self.stack.append(t)
    def handle_endtag(self, t):
        if t in ("script","style","noscript","form","button","svg"): self.skip = max(0, self.skip - 1); return
        if self.skip or t not in KEEP or t in VOID: return
        if t in self.stack:
            while self.stack:
                x = self.stack.pop(); self.o.append("</%s>" % x)
                if x == t: break
        if t in ("h2","h3","h4") and self._h is not None:
            self.heads.append(" ".join("".join(self._h).split())); self._h = None
    def handle_data(self, d):
        if self.skip: return
        d = wl(d); self.o.append(html.escape(d, quote=False)); self.text.append(d)
        if self._h is not None: self._h.append(d)
    def result(self):
        while self.stack: self.o.append("</%s>" % self.stack.pop())
        s = "".join(self.o)
        s = re.sub(r"</?span>", "", s)                                        # bare spans carry nothing
        s = re.sub(r"<div>(\s*<(?:p|h[2-4]|ul|ol|table|figure|img|iframe|blockquote|pre)\b)", r"\1", s)  # keep divs only around text
        s = re.sub(r"<div>", '<div class="blk">', s)
        s = re.sub(r"<p>(\s|&nbsp;| )*</p>", "", s)
        s = re.sub(r"<p><strong>TABLE OF CONTENTS</strong></p>", '<p class="toc-h"><strong>TABLE OF CONTENTS</strong></p>', s)
        return s

# ---------------------------------------------------------------- parsing
def between(h, start, end):
    i = h.find(start)
    if i < 0: return ""
    i = h.find(">", i) + 1
    j = h.find(end, i)
    return h[i:j if j > 0 else None]

def parse_article(aid, url, h):
    title = re.search(r'<h1[^>]*class="fw-page-title"[^>]*>(.*?)</h1>', h, re.S)
    title = wl(html.unescape(re.sub("<[^>]+>", "", title.group(1)).strip())) if title else ""
    crumbs = re.findall(r'<a[^>]+href="/support/solutions/(?:folders/)?(\d+)"[^>]*>\s*([^<]+?)\s*<', between(h, 'breadcrumb', '</nav>') or h[h.find("breadcrumb"):h.find("breadcrumb")+3000])
    cat = crumbs[0] if crumbs else ("0", "Other")
    fold = crumbs[1] if len(crumbs) > 1 else cat
    m = re.search(r"Modified on:\s*([^<]+)", h); modified = m.group(1).strip() if m else ""
    body = between(h, "fw-content--single-article", "fw-feedback")
    body = body[:body.rfind("</div>")] if "</div>" in body else body
    c = Clean(); c.feed(body); out = c.result()
    text = " ".join(" ".join(c.text).split())
    return {
        "id": aid, "t": title, "c": cat[0], "f": fold[0], "cn": wl(html.unescape(cat[1])), "fn": wl(html.unescape(fold[1])),
        "m": modified, "x": text[:220], "k": " | ".join(c.heads)[:400], "w": len(text.split()), "img": c.imgs, "html": out,
        "h": hashlib.sha1(out.encode()).hexdigest()[:12]
    }

def site_map():
    """Categories (with descriptions) and their folders, from the knowledge base home page."""
    h = get(SRC + "/support/solutions") or ""
    pos = [(m.start(), m.group(1), html.unescape(m.group(2)).strip())
           for m in re.finditer(r'<h2>\s*<a[^>]+href="/support/solutions/(\d+)"[^>]*>\s*([^<]+?)\s*</a>', h)]
    cats = []
    for i, (p0, cid, name) in enumerate(pos):
        chunk = h[p0:pos[i + 1][0] if i + 1 < len(pos) else len(h)]
        d = re.search(r"</h2>\s*<p>\s*(.*?)\s*</p>", chunk, re.S)
        folders = []
        for fid, fname in re.findall(r'href="/support/solutions/folders/(\d+)"[^>]*>(.*?)</a>', chunk, re.S):
            fname = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fname)).split())
            if fname and not fname.lower().startswith("view all") and fid not in [f[0] for f in folders]: folders.append((fid, fname))
        cats.append({"id": cid, "n": name, "d": wl(html.unescape(re.sub(r"<[^>]+>", "", d.group(1)))).strip() if d else "", "folders": folders})
    return cats

def categories_order():
    return [(c["id"], c["n"]) for c in site_map()]

def folder_articles(fid):
    """(id, title, modified) for every article in a folder, following pagination."""
    out, page = [], 1
    while page < 40:
        h = get(SRC + "/support/solutions/folders/%s%s" % (fid, "" if page == 1 else "/page/%d" % page)) or ""
        rows = re.findall(r'href="/support/solutions/articles/(\d+)[^"]*"[^>]*>(.*?)</a>', h, re.S)
        for aid, inner in rows:
            t = re.search(r'line-clamp-2[^>]*>(.*?)</div>', inner, re.S)
            m = re.search(r"Modified on\s*([^<]+?)\s*<", inner)
            if t and aid not in [o[0] for o in out]:
                out.append((aid, html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip(), m.group(1).strip() if m else ""))
        if 'href="/support/solutions/folders/%s/page/%d"' % (fid, page + 1) not in h: break
        page += 1
    return out

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--rebuild", action="store_true", help="re-filter files already on disk, no downloads")
    ap.add_argument("--full", action="store_true", help="re-download every article, even unchanged ones")
    args = ap.parse_args()
    if args.rebuild: return rebuild()
    os.makedirs(os.path.join(OUT, "a"), exist_ok=True)
    t0 = time.time()
    hide = {s.lower() for s in CFG["hideCategories"]}
    ren = CFG.get("renameCategories", {})

    # 1) structure: categories -> folders -> article titles (skip hidden ones before downloading anything)
    smap = [c for c in site_map() if c["n"].lower() not in hide]
    if len(smap) < 5: sys.exit("Refusing to continue: only %d categories found (source page changed?)." % len(smap))
    cats = [(c["id"], ren.get(c["n"], c["n"])) for c in smap]
    descs = {c["id"]: c["d"] for c in smap}
    listing = []
    for c in smap:
        for fid, fname in c["folders"]:
            if any(rx.search(wl(fname)) for rx in FOLDER_RX): continue
            for aid, title, mod in folder_articles(fid):
                if any(rx.search(wl(title)) for rx in TITLE_RX): continue
                listing.append({"id": aid, "c": c["id"], "f": fid, "cn": wl(c["n"]), "fn": wl(fname), "t0": title, "lm": mod})
    seen = set(); listing = [x for x in listing if not (x["id"] in seen or seen.add(x["id"]))]
    print("structure: %d categories, %d sub-account articles listed (%d requests, %.0fs)" % (len(cats), len(listing), REQS[0], time.time() - t0), flush=True)
    if args.limit: listing = listing[:args.limit]

    # 2) download only new or changed articles
    def stored(aid):
        try: return json.load(open(os.path.join(OUT, "a", aid + ".json"), encoding="utf-8"))
        except Exception: return None
    results, failed, todo = {}, [], []
    for x in listing:
        old = stored(x["id"])
        if old and not args.full and old.get("lm") == x["lm"] and old.get("v") == PARSER_V:
            old.update({"c": x["c"], "f": x["f"], "cn": x["cn"], "fn": x["fn"]}); results[x["id"]] = derive(old)
        else: todo.append(x)
    print("unchanged %d | to download %d" % (len(results), len(todo)), flush=True)
    def work(x):
        h = get(SRC + "/support/solutions/articles/" + x["id"])
        if not h: return x, None
        a = parse_article(x["id"], "", h)
        a.update({"c": x["c"], "f": x["f"], "cn": x["cn"], "fn": x["fn"], "lm": x["lm"], "v": PARSER_V})
        return x, a
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for n, fut in enumerate(cf.as_completed([ex.submit(work, x) for x in todo]), 1):
            try:
                x, a = fut.result()
                if a and a["t"]: results[x["id"]] = a
            except Exception as e:
                failed.append(str(e)[:120])
            if n % 100 == 0: print("  %d/%d downloaded (%.0fs)" % (n, len(todo), time.time() - t0), flush=True)

    keep = {aid: a for aid, a in results.items() if not agency_only(a)}
    if len(listing) > 50 and len(keep) < 0.6 * len(listing) and not args.limit:
        sys.exit("Refusing to write: only %d of %d articles came through (source unreachable?)." % (len(keep), len(listing)))
    write_outputs(keep, cats, ren, prune=not args.limit, descs=descs)
    print("total requests %d | failed %d | %.0fs" % (REQS[0], len(failed), time.time() - t0))
    if failed: print("sample failures:", failed[:3])

PARSER_V = 2
def derive(a):
    """Excerpt + headings for search, rebuilt from stored HTML."""
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", a.get("html", ""))).split())
    a["x"] = text[:220]
    a["k"] = " | ".join(" ".join(html.unescape(re.sub("<[^>]+>", "", h)).split()) for h in re.findall(r"<h[23][^>]*>(.*?)</h[23]>", a.get("html", ""), re.S))[:400]
    return a

FOLDER_RX = [re.compile(p, re.I) for p in CFG.get("hideFolderPatterns", [])]
TITLE_RX = [re.compile(p, re.I) for p in CFG.get("hideTitlePatterns", [])]
def agency_only(a):
    """Clients only: keep sub-account features and operations; drop agency-level and non-feature content."""
    if any(rx.search(a.get("fn", "")) for rx in FOLDER_RX): return True
    if any(rx.search(a.get("t", "")) for rx in TITLE_RX): return True
    return False

def rebuild():
    """Re-apply filters and rebuild index.json from the article files already on disk (no downloads)."""
    hide = {s.lower() for s in CFG["hideCategories"]}
    ren = CFG.get("renameCategories", {})
    smap = [c for c in site_map() if c["n"].lower() not in hide]
    cats = [(c["id"], ren.get(c["n"], c["n"])) for c in smap]
    allowed = {cid for cid, _ in cats}
    keep = {}
    for fn in os.listdir(os.path.join(OUT, "a")):
        if not fn.endswith(".json"): continue
        a = json.load(open(os.path.join(OUT, "a", fn), encoding="utf-8"))
        derive(a)
        if a["c"] in allowed and a["t"] and not agency_only(a): keep[a["id"]] = a
    write_outputs(keep, cats, ren, prune=True, descs={c["id"]: c["d"] for c in smap})

ART_LINK = re.compile(r'href="https?://(?:help\.(?:go)?highlevel\.com|help\.leadconnectorhq\.com|gohighlevelassist\.freshdesk\.com)(?:/[a-z]{2}(?:-[A-Z]{2})?)?/support/solutions/articles/(\d+)[^"]*"', re.I)
OTHER_LINK = re.compile(r'<a href="https?://[^"/]*(?:highlevel|leadconnector|marketplace\.eleva\.com|freshdesk)[^"]*"[^>]*>(.*?)</a>', re.I | re.S)
API_HOSTS = re.compile(r'https?://(?:services|backend|api|link|rest)\.(?:leadconnectorhq|msgsndr)\.com', re.I)
def fix_links(h):
    """Point article links inside our help center; unlink other platform pages (keep real API endpoints)."""
    h = ART_LINK.sub(r'href="#/a/\1"', h)
    h = re.sub(r'href="#/a/(\d+)[^"\d][^"]*"', r'href="#/a/\1"', h)
    def unlink(m):
        return m.group(0) if API_HOSTS.search(m.group(0)) else m.group(1)
    h = OTHER_LINK.sub(unlink, h)
    h = re.sub(r'(?i)marketplace\.eleva\.com/docs/ghl/', "", h)
    h = re.sub(r'href="#/a/(\d+)"\s+target="_blank"\s+rel="noopener"', r'href="#/a/\1"', h)
    # tidy empty spacing left by the source editor
    h = re.sub(r"<(p|div)( class=\"blk\")?>(\s|&nbsp;| |<br>)*</\1>", "", h)
    h = re.sub(r"(<br>\s*){2,}", "<br>", h)
    h = re.sub(r"(<(?:p|li|div[^>]*)>)\s*<br>", r"\1", h)
    h = re.sub(r"<br>\s*(</(?:p|li|div)>)", r"\1", h)
    # heading anchors (id="..." / href="#...") carry the old brand name too
    h = re.sub(r'((?:id|href)="#?)([^"/]*)"', lambda m: m.group(1) + re.sub(r"(?i)go-?high-?level|high-?level|lead-?connector|\bGHL\b", CFG["brand"], m.group(2)) + '"', h)
    return h

def write_outputs(keep, cats, ren, prune, descs=None):
    t0 = time.time()
    dead = re.compile(r'<a [^>]*?href="#/a/(\d+)"[^>]*>(.*?)</a>', re.S)
    for a in keep.values():
        a["html"] = dead.sub(lambda m: m.group(0) if m.group(1) in keep else m.group(2), fix_links(a.get("html", "")))

    # folders per category
    folders = {}
    for a in keep.values():
        folders.setdefault(a["c"], {}).setdefault(a["f"], {"id": a["f"], "n": a["fn"], "count": 0})["count"] += 1
    index = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "count": len(keep),
        "cats": [{"id": cid, "n": n, "d": "" if re.search(r"(?i)agenc|\$\d|plan\b|pricing", (descs or {}).get(cid, "")) else (descs or {}).get(cid, ""), "count": sum(f["count"] for f in folders.get(cid, {}).values()),
                  "folders": sorted(folders.get(cid, {}).values(), key=lambda f: f["n"].lower())} for cid, n in cats if cid in folders],
        "arts": [[a["id"], a["t"], a["c"], a["f"], a["x"], a["k"], a["m"]] for a in sorted(keep.values(), key=lambda a: a["t"].lower())],
        "popular": [x for x in CFG.get("popular", []) if x in keep],
    }
    for a in index["cats"]: a["n"] = ren.get(a["n"], a["n"])

    # write files (only rewrite when content changed -> small git diffs)
    changed = 0
    for aid, a in keep.items():
        p = os.path.join(OUT, "a", aid + ".json")
        data = {k: a.get(k, "") for k in ("id", "t", "c", "f", "cn", "fn", "m", "lm", "v", "w", "html")}
        data["cn"] = ren.get(data["cn"], data["cn"])
        s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        try:
            if open(p, encoding="utf-8").read() == s: continue
        except FileNotFoundError: pass
        open(p, "w", encoding="utf-8").write(s); changed += 1
    removed = 0
    if prune:
        for fn in os.listdir(os.path.join(OUT, "a")):
            if fn.endswith(".json") and fn[:-5] not in keep: os.remove(os.path.join(OUT, "a", fn)); removed += 1
    json.dump(index, open(os.path.join(OUT, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("kept %d articles in %d categories | changed %d | removed %d | %.1fs"
          % (len(keep), len(index["cats"]), changed, removed, time.time() - t0))

if __name__ == "__main__":
    main()
