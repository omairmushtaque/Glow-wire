"""Beauty desk. Writes short briefs about stories that several outlets cover, then checks them hard.

Nothing is published unless every check passes. A failed draft means no article, never a lower bar.
Needs ANTHROPIC_API_KEY (live) or fixtures/articles.json (offline test). Turn off with ARTICLES=off.
"""
import html, json, os, re, sys, urllib.error
from datetime import datetime, timedelta
from pathlib import Path

import update as u  # when update.py is the script this re-runs its top level once; harmless

MAX_PER_RUN = int(os.getenv("ARTICLES_PER_RUN", 1))
MAX_PER_DAY = int(os.getenv("ARTICLES_PER_DAY", 5))
TRIES_PER_RUN = 3          # drafts attempted per run, so one bad story cannot block the others
KEEP_DAYS = 30
MODEL = os.getenv("ARTICLE_MODEL", "claude-sonnet-5-5")
TOPIC_TAGS = {"launches", "deals", "trending", "events", "business"}   # recalls are never rewritten

HEALTH = re.compile(r"\b(cur(?:e|es|ed|ing)|heal(?:s|ed|ing)?|prevent\w*|clinically (?:proven|tested)|dermatologist[- ](?:approved|tested|recommended)|safe for|pregnan\w*|cancer|eczema|psoriasis|rosacea|dermatitis|melasma|diagnos\w*|disease|infection|allerg\w*|medical\w*|therap\w*|anti-?inflammatory|detox\w*)\b", re.I)
ATTRIB = re.compile(r"\b(says?|said|according to|reports?|reported|claims?|claimed|states?|stated|describes?|calls?)\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|I'm|I've|I'd|we|we've|we're|our|my|me)\b")   # case-sensitive on purpose
OK_CAPS = set("january february march april may june july august september october november december monday tuesday wednesday thursday friday saturday sunday ai".split())

WRITER_SYSTEM = (
    "You are the beauty desk writer for a shopping-minded news site. Write one short brief about the story, using ONLY the "
    "source headlines and snippets you are given. Return JSON only, in this shape: "
    '{"headline": "...", "dek": "...", "body": ["paragraph", "paragraph"]}. '
    "Rules: headline in sentence case, 6 to 12 words, plain and not clickbait. Dek is one sentence of at most 25 words. "
    "Body is 2 or 3 short paragraphs, 90 to 160 words in total. Name the outlet each fact comes from (for example 'Allure reports'). "
    "Say that several outlets cover the story. Attribute any claim about what a product does to the brand or the outlet "
    "('the brand says'). No medical or health claims, and avoid words such as cure, treat, heal, prevent, safe. No first person. "
    "Never say anything was tested unless a source says who tested it. Do not invent names, numbers, prices, dates or reasons. "
    "Leave out dates. If the sources say little, say little. No dashes, exclamation marks, question marks or emojis. Avoid hype "
    "words such as iconic, must-have, game-changer, elevate, stunning. Never reuse a run of five or more words from a source, "
    "except product or brand names. End on the last concrete fact, with no summary line."
)
VERIFY_SYSTEM = (
    "You are a strict fact checker. You get source headlines and snippets, then an article. List every sentence in the article "
    "that states anything not clearly stated in the sources, including numbers, names, causes, dates, comparisons, counts, and "
    "opinions written as fact. Also list any sentence that reads like a health claim without saying who made it. "
    'Reply with JSON only: {"unsupported": ["sentence", ...]}. Use an empty list if everything is supported.'
)


# ---------- checks (plain code, no model) ----------

def sentences(t): return [s for s in re.split(r"(?<=[.!?])\s+", t.strip()) if s]


def check_article(a, inputs):
    """Return a problem string, or '' if the article may be published."""
    head, dek, paras = a.get("headline", ""), a.get("dek", ""), a.get("body", [])
    fields = [head, dek] + paras
    n = len(u.words(" ".join(paras)))
    if not 60 <= n <= 220: return f"body length {n} words"
    if not 3 <= len(head.split()) <= 16 or len(dek.split()) > 35: return "headline or dek length"
    text = " ".join(fields)
    for rx in u.AI_TELLS:
        m = rx.search(text)
        if m: return f"AI-sounding pattern: {m.group(0)!r}"
    if FIRST_PERSON.search(text): return "first person wording"
    grams = u.shingles(u.words(text))
    for src, title, snip in inputs:
        if grams & (u.shingles(u.words(title)) | u.shingles(u.words(snip))): return f"copies a run of 6+ words from {src}"
    src_text = " ".join(f"{s}. {t}. {sn}" for s, t, sn in inputs)
    src_l, src_n = src_text.lower(), src_text.replace(",", "")
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        num = m.group(0).rstrip(".,").replace(",", "")
        if num not in src_n: return f"number {num} not found in sources"
    for f in fields:
        for sent in sentences(f):
            for w in re.findall(r"[A-Za-z][A-Za-z'&-]+", sent)[1:]:
                if w[0].isupper() and w.lower() not in src_l and w.lower() not in OK_CAPS: return f"name {w!r} not found in sources"
            for m in HEALTH.finditer(sent):
                if m.group(0).lower() not in src_l: return f"health wording not in sources: {m.group(0)!r}"
                if not ATTRIB.search(sent): return f"health wording without attribution: {m.group(0)!r}"
    return ""


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try: return json.loads(m.group(0)) if m else None
    except ValueError: return None


def shape_problem(d):
    ok = isinstance(d, dict) and isinstance(d.get("headline"), str) and isinstance(d.get("dek"), str) \
        and isinstance(d.get("body"), list) and 1 <= len(d["body"]) <= 4 and all(isinstance(p, str) for p in d["body"])
    return "" if ok else "reply was not the expected JSON"


# ---------- writing ----------

def write_one(s, fixture):
    inputs = s["_inputs"]
    if fixture is not None:   # offline test: a canned draft, but the same checks
        d = fixture.get(s["url"])
        problem = shape_problem(d) or check_article(d, inputs) if d else "no canned draft"
        if problem: print(f"  no article for {s['url']}: {problem}", file=sys.stderr)
        return None if problem else d
    prompt = "Story sources:\n" + "\n".join(f"- [{src}] {t}. Snippet: {sn[:500]}" for src, t, sn in inputs)
    problem = ""
    for _ in range(2):   # one retry that tells the writer what went wrong
        try:
            d = parse_json(u.ask_model(prompt + (f"\n\nYour last draft was rejected: {problem}. Write it again." if problem else ""), WRITER_SYSTEM, MODEL, 800))
            problem = shape_problem(d) or check_article(d, inputs)
            if not problem:   # second, independent model call checks every sentence against the sources
                body = f"SOURCES:\n{prompt}\n\nARTICLE:\n{d['headline']}\n{d['dek']}\n" + "\n".join(d["body"])
                v = parse_json(u.ask_model(body, VERIFY_SYSTEM, MODEL, 500))
                bad = v.get("unsupported") if isinstance(v, dict) else None
                if bad is None: problem = "the fact checker gave no usable answer"
                elif bad: problem = "the fact checker flagged: " + "; ".join(map(str, bad))[:300]
                else: return d
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            print(f"  article call failed: {e}", file=sys.stderr); return None
    print(f"  no article for {s['url']}: {problem}", file=sys.stderr)
    return None   # never publish a draft that failed a check


def make_new(stories, store, now, fixture):
    done = {a["story_id"] for a in store["articles"]} | set(store["failed"])
    today = sum(a["made"][:10] == now.date().isoformat() for a in store["articles"])
    room = min(MAX_PER_RUN, MAX_PER_DAY - today)
    todo = [s for s in stories if s["id"] not in done and s["more"] and s["image"] and TOPIC_TAGS & set(s["tags"])]
    made = tries = 0
    for s in todo:
        if made >= room or tries >= TRIES_PER_RUN: break
        tries += 1
        d = write_one(s, fixture)
        if not d: store["failed"][s["id"]] = now.isoformat(); continue
        store["articles"].append({
            "story_id": s["id"], "slug": f"{u.slug(d['headline'])[:50]}-{s['id'][:4]}", "headline": d["headline"].strip(), "dek": d["dek"].strip(),
            "body": [p.strip() for p in d["body"]], "image": s["image"],
            "sources": [{"source": s["source"], "url": s["url"]}] + s["more"], "made": now.isoformat()})
        made += 1
    return made


# ---------- pages ----------

EXTRA_CSS = """
.art h1{font-size:30px;margin:24px 0 8px}.art .dek{color:var(--mute);font-size:18px;margin:0 0 20px}
.art figure{margin:0 0 24px}.art figure img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:12px;border:1px solid var(--line);background:var(--gray)}
.art figcaption{font-size:12px;color:var(--mute);margin-top:6px}.art p{margin:0 0 16px}
.srcs{border-top:1px solid var(--line);margin-top:32px;padding-top:16px;font-size:14px}.srcs ul{padding-left:20px;margin:8px 0}
.note{font-size:13px;color:var(--mute);max-width:62ch}.back{font-size:14px;color:var(--mute)}
"""


def page(a, css, site, sample):
    e = html.escape
    credit = a["image"]["credit"]
    curl = next((x["url"] for x in a["sources"] if x["source"] == credit), a["sources"][0]["url"])
    srcs = "".join(f'<li><a href="{e(x["url"])}" rel="noopener">{e(x["source"])}</a></li>' for x in a["sources"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(a["headline"])} | {e(site)}</title><meta name="description" content="{e(a["dek"])}">
{'<meta name="robots" content="noindex">' if sample else ''}<link rel="icon" href="data:,"><style>{css}{EXTRA_CSS}</style></head>
<body><div class="wrap art"><header class="top" style="padding-bottom:0"><a class="back" href="../">&larr; {e(site)}</a></header>
<article><h1>{e(a["headline"])}</h1><p class="dek">{e(a["dek"])}</p>
<figure><img src="{e(a["image"]["url"])}" alt="" loading="eager" decoding="async" referrerpolicy="no-referrer" onerror="this.parentNode.remove()">
<figcaption>Photo from <a href="{e(curl)}" rel="noopener">{e(credit)}</a>, shown from their site.</figcaption></figure>
{"".join(f"<p>{e(p)}</p>" for p in a["body"])}
<div class="srcs"><strong>Sources</strong><ul>{srcs}</ul>
<p class="note">Written by an AI model from the sources above and checked by software against them. It can still be wrong, so read the originals before you buy. {e(u.human_time(a["made"]))}.</p>
<p class="note">Some links on this site may become affiliate links. When they do, we will say so next to the link.</p></div>
</article></div></body></html>"""


def cards(arts):
    if not arts: return ""
    e = html.escape
    c = "".join(f'<a class="brief" href="a/{e(a["slug"])}.html"><img src="{e(a["image"]["url"])}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" '
                f'onerror="this.remove()"><div><b>{e(a["headline"])}</b><span>{e(a["dek"])}</span></div></a>' for a in arts[:4])
    return f'<section class="briefs wrap" aria-label="Briefs"><h3>Briefs</h3><div class="brief-grid">{c}</div></section>'


# ---------- entry point ----------

def run(stories, out, now, fixtures, template_css):
    f = out / "articles.json"
    try: store = json.loads(f.read_text()) if f.exists() else {}
    except ValueError: store = {}
    store.setdefault("articles", []); store.setdefault("failed", {})
    made = 0
    if os.getenv("ARTICLES", "on") != "off":
        fx = None
        if fixtures is not None:
            p = fixtures / "articles.json"
            fx = json.loads(p.read_text()) if p.exists() else {}
        if fx is not None or os.getenv("ANTHROPIC_API_KEY"): made = make_new(stories, store, now, fx)
    old = (now - timedelta(days=KEEP_DAYS)).isoformat()
    store["articles"] = sorted((a for a in store["articles"] if a["made"] > old), key=lambda a: a["made"], reverse=True)[:150]
    store["failed"] = {k: v for k, v in store["failed"].items() if v > (now - timedelta(days=3)).isoformat()}
    f.write_text(json.dumps(store, indent=1))
    (out / "a").mkdir(exist_ok=True)
    for a in store["articles"]: (out / "a" / f"{a['slug']}.html").write_text(page(a, template_css, u.SITE_NAME, bool(fixtures)))
    print(f"articles: {made} new, {len(store['articles'])} on site, {len(store['failed'])} failed recently")
    return cards(store["articles"])
