"""Self-check for the updater: python3 test_update.py  (no network needed)."""
from datetime import datetime, timezone
from pathlib import Path
import update as u

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
SRC = [("Gloss Daily", "Marlowe Skin barrier cream review from a blogger", "Two weeks of use on dry cheeks.")]


def test_summary_rules():
    good = "A blogger tried the barrier cream from Marlowe Skin for two weeks on dry cheeks and writes up how it went."
    assert u.check_summary(good, SRC) == ""
    assert "copies" in u.check_summary("Reviewers say the Marlowe Skin barrier cream review from a blogger is thorough and calm.", SRC)
    assert u.check_summary("This cream is a game-changer for dry cheeks, and people are already buying a second jar.", SRC)
    assert u.check_summary("The cream is rich — and many people with dry cheeks seem to like it a great deal this week.", SRC)
    assert u.check_summary("It is not just a cream but a ritual that people with dry cheeks plan their evenings around.", SRC)
    assert u.check_summary("You need to try this cream on your dry cheeks right now before it sells out again!", SRC)
    assert u.check_summary("Too short.", SRC)


def test_urls_and_prices():
    assert u.norm_url("https://www.Site.com/a/?utm_source=x&id=3#top") == u.norm_url("http://site.com/a?id=3")
    assert u.price_of("Serum launches at $38") == {"amt": 38.0, "cur": "USD"}
    assert u.price_of("Mascara now £12") == {"amt": 12.0, "cur": "GBP"}
    assert u.price_of("Perfume for AED 120") == {"amt": 120.0, "cur": "AED"}
    assert u.price_of("Brand raises $1.2 billion") is None
    assert u.price_of("Everything 30% off") is None


def items(*rows):
    out = []
    for src, title in rows:
        it = {"title": title, "source": src, "weight": 1, "kind": "x", "published": NOW, "link": "https://x.example/" + title[:12], "snippet": "", "image": None}
        u.prep(it); out.append(it)
    return out


def test_grouping():
    same = items(("A", "Dune Beauty recalls SPF 50 sticks"), ("B", "Dune recall hits SPF 50 sunscreen stick"))
    assert len(u.group(same)) == 1
    diff = items(("A", "Sephora launches new serum"), ("B", "Sephora names new chief executive"))
    assert len(u.group(diff)) == 2  # sharing one brand name is not enough


def test_dedupe():
    a, b, c = items(("A", "Same title here"), ("B", "Other title"), ("A", "Same title here"))
    b["link"] = a["link"] + "?utm_source=feed"
    c["link"] = "https://x.example/different"
    out, dropped = u.dedupe([a, b, c])
    assert len(out) == 1 and dropped == 2  # tracking-link copy and same-title copy both removed


def test_parser_safety_and_formats():
    try:
        u.parse_feed(b'<!DOCTYPE x [<!ENTITY a "b">]><rss><channel/></rss>'); assert False
    except ValueError: pass
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link rel="alternate" href="https://a.example/x"/><updated>2026-10-19T10:00:00Z</updated></entry></feed>'
    assert u.parse_feed(atom)[0]["link"] == "https://a.example/x"
    bad = b'<rss><channel><item><title>x</title><link>javascript:alert(1)</link><pubDate>Mon, 19 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'
    assert u.parse_feed(bad) == []  # non-web links are dropped


def test_feed_health():
    feeds = {f["name"]: f for f in __import__("json").loads(Path("fixtures/feeds.json").read_text())}
    status = {n: u.check_feed(f, "fixtures", NOW)[0]["status"] for n, f in feeds.items()}
    assert status["Gloss Daily"] == "ok" and status["Broken Feed"] == "error" and status["Missing Feed"] == "error"
    assert status["Dusty Blog"] == "stale" and status["Empty Feed"] == "empty"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn(); print("ok  ", name)
    print("all checks passed")
