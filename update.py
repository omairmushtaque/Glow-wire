#!/usr/bin/env python3
"""Beauty news hub updater. Python standard library only.

  python update.py                        live run (what GitHub Actions does)
  python update.py --fixtures fixtures --out preview    offline test on sample feeds

Pipeline: fetch -> health check -> dedupe -> group -> lead + "More" -> summary -> page.
If too few feeds are healthy it stops with exit code 1 and writes nothing, so the last good
site stays online.
"""
import argparse, hashlib, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
SITE_NAME = os.getenv("SITE_NAME", "Glow Wire")
TAGLINE = "Everything happening in women's beauty, grouped by story."
UA = "Mozilla/5.0 (compatible; GlowWireBot/0.1)"
WINDOW_H = int(os.getenv("WINDOW_HOURS", 72))          # stories older than this drop off
MAX_STORIES = int(os.getenv("MAX_STORIES", 80))
STALE_DAYS = 45                                         # feed with nothing newer is "stale"
MIN_HEALTHY = int(os.getenv("MIN_HEALTHY_FEEDS", 25))   # publish only if this many feeds work
MAX_NEW_SUMMARIES = int(os.getenv("MAX_NEW_SUMMARIES", 40))
SIM = 0.42                                              # headline similarity needed to group

BEAUTY = re.compile(r"\b(beauty|makeup|make-up|skincare|skin[- ]care|skin|serum|moisturi[sz]er|sunscreen|spf|lipstick|lip (?:gloss|balm|oil|liner|tint)|mascara|foundation|concealer|blush|bronzer|eyeshadow|eyeliner|fragrance|perfume|parfum|haircare|hair ?care|shampoo|conditioner|nail|nails|manicure|cosmetics?|sephora|ulta|retinol|niacinamide|hyaluronic|collagen|salon|k-beauty|dermatolog\w*|facial|exfoliat\w*|cleanser)\b", re.I)
COSMETIC = re.compile(r"\b(cosmetics?|makeup|skin|lotion|cream|shampoo|conditioner|sunscreen|deodorant|perfume|fragrance|hair|nail|soap|talc|serum|lipstick|mascara|eye ?liner)\b", re.I)
FILTERS = {"beauty": BEAUTY, "cosmetic": COSMETIC}

# tag order is also display priority
TAGS = [
    ("safety", "Safety", re.compile(r"\b(recall\w*|contaminat\w*|benzene|asbestos|banned? ingredient|safety (?:alert|warning)|warning letter|unsafe|adverse (?:event|reaction)s?)\b", re.I)),
    ("deals", "Deal", re.compile(r"(\b(sale|deals?|discount\w*|promo(?:tion)?|black friday|cyber monday|clearance|price drop|bogo|coupon)\b|\d+\s?%\s?off)", re.I)),
    ("launches", "Launch", re.compile(r"\b(launch\w*|debut\w*|unveil\w*|introduc\w*|collab\w*|limited[- ]edition|first look|arriv\w+|now available|hits? (?:shelves|stores)|new (?:collection|range|line|formula|shade|palette|fragrance|serum|product)s?|releas\w+)\b", re.I)),
    ("events", "Event", re.compile(r"\b(fashion week|expo|trade show|cosmoprof|in-cosmetics|summit|awards?|conference|festival|pop-?up|masterclass|registration opens)\b", re.I)),
    ("business", "Business", re.compile(r"\b(acquir\w*|acquisition|stake|ceo|appoints?|funding|raises? \$|ipo|earnings|quarterly|revenue|partnership|expands?|lawsuit|sues?|investor\w*|merger)\b", re.I)),
    ("trending", "Trending", re.compile(r"\b(viral|tiktok|trend\w*|obsess\w*|everyone is|dupes?)\b", re.I)),
]

PRICE = re.compile(r"(US\$|AU\$|CA\$|\$|£|€|AED ?|USD ?|GBP ?|EUR ?)(\d{1,4}(?:\.\d{1,2})?)(?!\d|\.\d)(?!\s?(?:million|billion|trillion|bn|m\b|k\b))|\b(\d{1,4}(?:\.\d{1,2})?) ?(AED|USD|GBP|EUR|dirhams?)\b", re.I)
CUR = {"$": "USD", "US$": "USD", "AU$": "AUD", "CA$": "CAD", "£": "GBP", "€": "EUR", "AED": "AED", "USD": "USD", "GBP": "GBP", "EUR": "EUR", "DIRHAM": "AED", "DIRHAMS": "AED"}

STOP = set("a an the and or of to in on for with at by from as is are was were be been it its this that these those how what why when who which new best top your you our their his her they we i not no vs via after before into over under about up out all more most just can will may could should would has have had do does did says say said".split())
GENERIC = set("beauty makeup skincare skin care product products launch launches launched review reviews shop get gets report week day year 2025 2026 collection brand brands".split())

# summary quality rules (from the humanizer skill): reject AI-sounding or copied text
AI_TELLS = [re.compile(p, re.I) for p in [
    r"[—–]", r"\bnot (?:just|only|merely)\b.*\bbut\b", r"\bisn['’]t (?:just|only)\b", r"[!?]", r"[*#_`]",
    r"\b(delve|tapestry|testament|pivotal|vibrant|landscape|game[- ]changer|elevat\w+|seamless\w*|unlock\w*|showcas\w+|underscor\w+|meticulous\w*|intricate|robust|must[- ]have|iconic|stands as|key role|crucial|ever-evolving|journey|realm|nestled|boasts?)\b",
    r"^\s*(overall|in conclusion|ultimately|in summary)\b", r"\b\w+ing,? (?:highlighting|underscoring|showcasing|reflecting|marking)\b",
]]

SUMMARY_SYSTEM = (
    "You write one short summary per beauty news story for a shopping-minded reader. Use only facts found in the "
    "headlines and snippets you are given. Write 1 or 2 plain sentences, 25 to 45 words in total, in your own words. "
    "Never reuse a run of four or more words from the sources, other than product or brand names. Name the brand or "
    "product and say what happened. No hype, praise, rhetorical questions, exclamation marks, emojis or dashes. Avoid "
    "phrases such as 'not just', 'game-changer', 'elevate', 'must-have', 'iconic', 'testament', 'pivotal', 'landscape', "
    "'delve', 'vibrant'. Do not invent prices, dates, numbers or claims. If the sources say little, say little. "
    "Reply with the summary text only."
)


# ---------- fetching and parsing ----------

def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
def tag(el): return el.tag.rsplit("}", 1)[-1]
def clean(s): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def fetch(url, accept="application/rss+xml, application/atom+xml, application/xml, text/xml, */*"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read(4_000_000)


def parse_date(s):
    if not s: return None
    try: d = parsedate_to_datetime(s.strip())
    except (TypeError, ValueError):
        try: d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError: return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def good_url(u):
    p = urllib.parse.urlsplit(u or "")
    return u if p.scheme in ("http", "https") and p.netloc else None


def norm_url(u):
    p = urllib.parse.urlsplit(u.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not re.match(r"(utm_|fbclid|gclid|mc_|ref$|cmpid|igshid|source$|campaign)", k, re.I)]
    return urllib.parse.urlunsplit(("https", p.netloc.lower().removeprefix("www."), p.path.rstrip("/") or "/", urllib.parse.urlencode(q), ""))


def find_image(n, kids):
    """Thumbnail the feed itself offers. We link to it, we never download or re-host it."""
    def ok(u):
        u = good_url((u or "").strip())
        if not u or re.search(r"pixel|spacer|1x1|feeds\.feedburner", u, re.I): return None
        return u.replace("http://", "https://", 1)
    for name in ("thumbnail", "content", "group"):
        for el in kids.get(name, []):
            for e in ([el] + list(el.iter())):
                t = (e.get("type") or "") + (e.get("medium") or "")
                if tag(e) in ("thumbnail", "content") and e.get("url") and (tag(e) == "thumbnail" or "image" in t or re.search(r"\.(jpe?g|png|webp)(\?|$)", e.get("url"), re.I)):
                    if ok(e.get("url")): return ok(e.get("url"))
    for el in kids.get("enclosure", []):
        if "image" in (el.get("type") or "") or re.search(r"\.(jpe?g|png|webp)(\?|$)", el.get("url") or "", re.I):
            if ok(el.get("url")): return ok(el.get("url"))
    for name in ("image", "featuredImage", "featuredimage"):
        for el in kids.get(name, []):
            u = ok(el.text) or next((ok(i.get("src")) for i in el.iter() if tag(i) == "img"), None)
            if u: return u
    raw = " ".join("".join(e.itertext()) + " ".join(str(v) for v in e.attrib.values()) for name in ("encoded", "description", "summary", "content") for e in kids.get(name, []))
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)", raw, re.I)
    return ok(m.group(1)) if m else None


def parse_feed(data):
    if re.search(rb"<!ENTITY", data): raise ValueError("entity declarations are not allowed")
    root = ET.fromstring(data)
    items = []
    for n in root.iter():
        if tag(n) not in ("item", "entry"): continue
        kids = {}
        for c in n: kids.setdefault(tag(c), []).append(c)
        get = lambda k: clean("".join(kids[k][0].itertext())) if kids.get(k) else ""
        link = ""
        for c in kids.get("link", []):
            if c.get("href") and c.get("rel") in (None, "alternate"): link = c.get("href")
            elif (c.text or "").strip(): link = c.text.strip()
        if not good_url(link): link = get("guid") if good_url(get("guid")) else ""
        date = next((parse_date(get(k)) for k in ("pubDate", "published", "updated", "date") if get(k)), None)
        snippet = next((get(k) for k in ("description", "summary", "encoded", "content") if get(k)), "")
        title = get("title")
        if title and good_url(link) and date:
            items.append({"title": title, "link": link.strip(), "published": date, "snippet": snippet[:600], "image": find_image(n, kids)})
    return items


def check_feed(feed, fixtures, now):
    """Fetch one feed and grade it. Returns (health record, items)."""
    h = {"name": feed["name"], "kind": feed.get("kind"), "status": "error", "items": 0}
    urls = feed["url"] if isinstance(feed["url"], list) else [feed["url"]]
    items = []
    for u in urls:
        t0 = time.time()
        try:
            if fixtures:
                f = Path(fixtures) / (slug(feed["name"]) + ".xml")
                if not f.exists(): raise urllib.error.HTTPError(u, 404, "Not Found", None, None)
                code, data = 200, f.read_bytes()
            else:
                code, data = fetch(u)
            items = parse_feed(data)
            h.update(url=u, http=code, ms=int((time.time() - t0) * 1000), items=len(items)); h.pop("error", None)
            break
        except urllib.error.HTTPError as e: h.update(url=u, http=e.code, error=f"HTTP {e.code}")
        except (ET.ParseError, ValueError) as e: h.update(url=u, error=f"not valid XML ({str(e)[:60]})")
        except (urllib.error.URLError, OSError) as e: h.update(url=u, error=str(getattr(e, "reason", e))[:80])
    if "error" in h: return h, []
    if not items: h["status"] = "empty"; return h, []
    newest = max(i["published"] for i in items)
    h["newest"] = newest.isoformat(); h["age_days"] = round((now - newest).total_seconds() / 86400, 1)
    h["status"] = "stale" if h["age_days"] > STALE_DAYS else "ok"
    if h["status"] != "ok": return h, []
    flt = FILTERS.get(feed.get("filter"))
    if flt: items = [i for i in items if flt.search(i["title"] + " " + i["snippet"])]
    h["kept"] = len(items)
    for i in items: i.update(source=feed["name"], weight=feed.get("weight", 1), kind=feed.get("kind", ""))
    return h, items


# ---------- dedupe and grouping ----------

def dedupe(items):
    seen_url, seen_title, out = set(), set(), []
    for it in sorted(items, key=lambda i: (-i["weight"], i["published"])):  # best source keeps its copy
        k, t = norm_url(it["link"]), (it["source"], re.sub(r"\W+", " ", it["title"].lower()).strip())
        if k in seen_url or t in seen_title: continue
        seen_url.add(k); seen_title.add(t); out.append(it)
    return out, len(items) - len(out)


def stem(w): return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w  # recalls = recall


def prep(it):
    words = re.findall(r"[A-Za-z0-9]+", it["title"])
    it["tok"] = {stem(w.lower()) for w in words if w.lower() not in STOP and w.lower() not in GENERIC and len(w) > 1}
    it["ent"] = {stem(w.lower()) for w in words if w[0].isupper()} & it["tok"]


def sim(a, b):
    shared = a["tok"] & b["tok"]
    if len(shared) < 2 or not (shared & (a["ent"] | b["ent"])): return 0
    return len(shared) / len(a["tok"] | b["tok"])


def group(items):
    clusters = []
    for it in sorted(items, key=lambda i: i["published"]):
        best, score = None, 0
        for c in clusters:
            s = max(sim(it, m) for m in c)
            if s >= SIM and s > score: best, score = c, s
        if best: best.append(it)
        else: clusters.append([it])
    return clusters


def price_of(text):
    for m in PRICE.finditer(text):
        sym, amt, amt2, word = m.group(1), m.group(2), m.group(3), m.group(4)
        cur = CUR.get((sym or word).strip().upper().replace(" ", "")) or CUR.get((sym or word).strip())
        if cur: return {"amt": float(amt or amt2), "cur": cur}
    return None


def build_story(c, now):
    by_src = {}
    for m in sorted(c, key=lambda m: m["published"]): by_src.setdefault(m["source"], m)  # one link per outlet
    members = list(by_src.values())
    lead = max(members, key=lambda m: (m["weight"], -m["published"].timestamp()))
    more = sorted((m for m in members if m is not lead), key=lambda m: (-m["weight"], m["source"]))
    pic = next((m for m in [lead] + more if m["image"]), None)
    tags = [t for t, _, rx in TAGS if rx.search(lead["title"])]  # lead headline only, other outlets add noise
    if "safety" not in tags and any(TAGS[0][2].search(m["title"]) for m in members): tags.insert(0, "safety")  # a recall in any outlet counts
    if any(m["kind"] == "community" for m in members) and "trending" not in tags: tags.append("trending")
    newest = max(m["published"] for m in members)
    age_h = max(0, (now - newest).total_seconds() / 3600)
    score = (1 + 0.8 * (len(members) - 1) + 0.2 * lead["weight"]) * 0.5 ** (age_h / 18)
    return {
        "id": hashlib.sha1(norm_url(lead["link"]).encode()).hexdigest()[:10],
        "title": lead["title"], "url": lead["link"], "source": lead["source"],
        "published": lead["published"].isoformat(), "updated": newest.isoformat(),
        "more": [{"source": m["source"], "url": m["link"]} for m in more],
        "image": {"url": pic["image"], "credit": pic["source"]} if pic else None,
        "tags": tags, "price": price_of(lead["title"]), "score": round(score, 4), "summary": None,
        "_inputs": [(m["source"], m["title"], m["snippet"]) for m in sorted(members, key=lambda m: -m["weight"])[:5]],
    }


# ---------- summaries ----------

def words(s): return re.findall(r"[a-z0-9']+", s.lower())
def shingles(ws, n=6): return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def check_summary(text, inputs):
    """Return a problem string, or '' if the summary may be published."""
    n = len(words(text))
    if not 12 <= n <= 55: return f"length {n} words"
    if "\n" in text: return "more than one paragraph"
    for rx in AI_TELLS:
        m = rx.search(text)
        if m: return f"AI-sounding pattern: {m.group(0)!r}"
    s = shingles(words(text))
    for src, title, snip in inputs:
        if s & (shingles(words(title)) | shingles(words(snip))): return f"copies a run of 6+ words from {src}"
    return ""


def ask_model(prompt, system=SUMMARY_SYSTEM, model=None, max_tokens=200):
    body = json.dumps({"model": model or os.getenv("SUMMARY_MODEL", "claude-haiku-5-5"), "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)["content"][0]["text"].strip()


def write_summary(story, fixture_summaries):
    inputs = story["_inputs"]
    if fixture_summaries is not None:
        text = fixture_summaries.get(story["url"], "")
        return text if text and not check_summary(text, inputs) else None
    if not os.getenv("ANTHROPIC_API_KEY"): return None
    prompt = "Story headlines from different outlets:\n" + "\n".join(f"- [{s}] {t}. Snippet: {sn[:300]}" for s, t, sn in inputs)
    problem = ""
    for _ in range(2):  # one retry, telling the model what went wrong
        try: text = ask_model(prompt + (f"\n\nYour last attempt was rejected: {problem}. Write it again." if problem else ""))
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            print(f"  summary call failed: {e}", file=sys.stderr); return None
        problem = check_summary(text, inputs)
        if not problem: return text
    print(f"  no summary for {story['url']}: {problem}", file=sys.stderr)
    return None  # never fall back to the publisher's own text


# ---------- exchange rates ----------

PEGGED = {"USD": 1, "AED": 3.6725, "SAR": 3.75, "QAR": 3.64, "BHD": 0.376, "OMR": 0.3845}  # officially pegged, safe fallback


def get_rates(out, now):
    cache = out / "rates.json"
    try:
        _, data = fetch("https://open.er-api.com/v6/latest/USD", "application/json")
        d = json.loads(data)
        if d.get("result") == "success":
            rates = {"base": "USD", "date": d["time_last_update_utc"][5:16], "rates": d["rates"]}
            cache.write_text(json.dumps(rates)); return rates
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        print(f"exchange rates fetch failed: {e}", file=sys.stderr)
    if cache.exists(): return json.loads(cache.read_text())
    return {"base": "USD", "date": "fixed pegs only", "rates": PEGGED}


# ---------- page ----------

def human_time(iso): return datetime.fromisoformat(iso).strftime("%b %d, %H:%M UTC").replace(" 0", " ")


def story_html(s):
    e = html.escape
    img = ""
    if s["image"]:
        img = (f'<a class="thumb" href="{e(s["url"])}" tabindex="-1" aria-hidden="true"><img src="{e(s["image"]["url"])}" alt="" width="96" height="96" '
               f'loading="lazy" decoding="async" referrerpolicy="no-referrer" title="Photo: {e(s["image"]["credit"])}" onerror="this.parentNode.remove()"></a>')
    labels = {k: lab for k, lab, _ in TAGS}
    chips = "".join(f'<span class="tag t-{k}">{labels[k]}</span>' for k in s["tags"][:2])
    price = ""
    if s["price"]:
        p = s["price"]; sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(p["cur"], p["cur"] + " ")
        amt = f'{p["amt"]:g}'
        price = f'<span class="price" data-amt="{p["amt"]}" data-cur="{p["cur"]}">{sym}{amt}</span>'
    more = ""
    if s["more"]:
        more = '<p class="more">More: ' + ", ".join(f'<a href="{e(m["url"])}" rel="noopener">{e(m["source"])}</a>' for m in s["more"]) + "</p>"
    summ = f'<p class="sum">{e(s["summary"])}</p>' if s["summary"] else ""
    return (f'<article class="story" data-tags="{" ".join(s["tags"])}"><div class="body">'
            f'<h2><a href="{e(s["url"])}" rel="noopener">{e(s["title"])}</a> <span class="src">{e(s["source"])}</span></h2>{summ}'
            f'<p class="meta">{chips}{price}<time datetime="{e(s["published"])}">{human_time(s["published"])}</time></p>{more}</div>{img}</article>')


def render(stories, rates, now, n_sources, sample, briefs=""):
    labels = [(k, lab) for k, lab, _ in TAGS if any(k in s["tags"] for s in stories)]
    chips = '<button class="chip on" data-f="all">All</button>' + "".join(f'<button class="chip" data-f="{k}">{lab}</button>' for k, lab in labels)
    banner = '<p class="sample">Sample preview. These stories and outlets are made up so you can see the layout. The live site fills with real stories after its first scheduled update.</p>' if sample else ""
    robots = '<meta name="robots" content="noindex">' if sample else ""
    page = (ROOT / "template.html").read_text()
    for k, v in {"SITE_NAME": html.escape(SITE_NAME), "TAGLINE": html.escape(TAGLINE), "CHIPS": chips, "STORIES": "\n".join(story_html(s) for s in stories),
                 "RATES_JSON": json.dumps(rates).replace("</", "<\\/"), "UPDATED_ISO": now.isoformat(), "UPDATED_HUMAN": human_time(now.isoformat()),
                 "SOURCES": str(n_sources), "COUNT": str(len(stories)), "FX_DATE": html.escape(str(rates["date"])), "BANNER": banner, "ROBOTS": robots, "BRIEFS": briefs}.items():
        page = page.replace("{{" + k + "}}", v)
    return page


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures"); ap.add_argument("--out", default="docs"); ap.add_argument("--now")
    a = ap.parse_args()
    out = ROOT / a.out; out.mkdir(exist_ok=True)
    now = datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now else datetime.now(timezone.utc)
    feeds = json.loads((Path(a.fixtures) / "feeds.json" if a.fixtures else ROOT / "feeds.json").read_text())

    with ThreadPoolExecutor(12) as ex: results = list(ex.map(lambda f: check_feed(f, a.fixtures, now), feeds))
    health = [h for h, _ in results]
    ok = sum(h["status"] == "ok" for h in health)
    print(f"{'feed':28} {'status':8} items  note")
    for h in health: print(f"{h['name'][:28]:28} {h['status']:8} {h['items']:5}  {h.get('error') or (str(h.get('age_days')) + 'd old' if 'age_days' in h else '')}")
    print(f"\nhealthy feeds: {ok} of {len(feeds)} (need {MIN_HEALTHY})")
    if ok < MIN_HEALTHY:
        print("Not publishing: too many feeds are broken. The last good site stays online.", file=sys.stderr); return 1

    cutoff = now - timedelta(hours=WINDOW_H)
    items = [i for _, its in results for i in its if cutoff <= i["published"] <= now + timedelta(hours=1)]
    items, dropped = dedupe(items)
    for i in items: prep(i)
    stories = sorted((build_story(c, now) for c in group(items)), key=lambda s: -s["score"])[:MAX_STORIES]
    print(f"{len(items)} unique headlines ({dropped} duplicates removed) -> {len(stories)} stories")

    cache_f = out / "summaries.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    fixture_sums = json.loads((Path(a.fixtures) / "summaries.json").read_text()) if a.fixtures and (Path(a.fixtures) / "summaries.json").exists() else ({} if a.fixtures else None)
    new = 0
    for s in stories:
        if s["id"] in cache: s["summary"] = cache[s["id"]]["text"]; continue
        if new >= MAX_NEW_SUMMARIES: continue
        text = write_summary(s, fixture_sums); new += 1
        if text: s["summary"] = text; cache[s["id"]] = {"text": text, "made": now.isoformat()}
    cache = {k: v for k, v in cache.items() if datetime.fromisoformat(v["made"]) > now - timedelta(days=14)}
    cache_f.write_text(json.dumps(cache, indent=1))
    print(f"summaries: {sum(bool(s['summary']) for s in stories)} of {len(stories)} stories ({new} new)")

    import articles  # beauty desk; needs the stories' source text, so it runs before "_inputs" is dropped
    css = re.search(r"<style>(.*?)</style>", (ROOT / "template.html").read_text(), re.S).group(1)
    briefs = articles.run(stories, out, now, Path(a.fixtures) if a.fixtures else None, css)
    for s in stories: s.pop("_inputs")
    rates = get_rates(out, now)
    (out / "index.html").write_text(render(stories, rates, now, ok, bool(a.fixtures), briefs))
    (out / "data.json").write_text(json.dumps({"updated": now.isoformat(), "stories": stories}, indent=1))
    (out / "health.json").write_text(json.dumps({"updated": now.isoformat(), "healthy": ok, "total": len(feeds), "feeds": health}, indent=1))
    print(f"wrote {out}/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
