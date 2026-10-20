# Glow Wire (working name)

A one-page, self-updating women's beauty news hub. It reads 54 feeds, groups headlines about the same event into one story, shows one lead source plus a "More:" line, and adds a short AI-written summary. It refreshes every 30 minutes by itself.

## Put it online (about 10 minutes, free)

1. Make a free GitHub account and a new **public** repository. Upload everything in this folder.
2. Repository **Settings > Pages**: set Source to **GitHub Actions**.
3. Repository **Settings > Secrets and variables > Actions > New repository secret**. Name it `ANTHROPIC_API_KEY` and paste an API key from console.anthropic.com. Without it the site still works, but stories show the headline only (no summary).
4. Open the **Actions** tab, pick **update-site**, press **Run workflow**. After about a minute your site is live at the address shown in the run.
5. After the first run, open `health.json` on the site (`your-site/health.json`). It lists every feed as ok, stale, empty or error. Replace dead ones in `feeds.json`.

Cost: GitHub is free for this. Summaries use Claude Haiku and only run for new stories (a few cents a day).

## Beauty desk (articles)

Needs the `ANTHROPIC_API_KEY` secret. Without it no articles are written. Each run it picks a story that two or more outlets cover, has a photo, and is a launch, deal, event, business or trending story (recalls are never rewritten). It writes a short brief, then runs these checks. Failing any one means no article:

- Only facts from the source headlines and snippets. Every number must appear in the sources. Every name must appear in the sources.
- No health or medical wording unless a source says it and the sentence names who said it.
- No "I tried" or "we tested". No AI-sounding patterns. No copied runs of 6+ words.
- A second model call checks every sentence against the sources and flags anything unsupported.

Limits: 1 new article per run and 5 per day (`ARTICLES_PER_DAY`). Set `ARTICLES=off` in the workflow to stop it. Articles use only headlines and the snippet each feed gives, so they are short briefs. The photo is the outlet's own, shown from their site with a credit.

## What it checks before it publishes

- Feeds that are broken, empty or silent for 45+ days are skipped and listed in `health.json`.
- If fewer than 25 feeds work, nothing is published and the last good site stays up (the run shows as failed, and GitHub emails you).
- Duplicate links (even with tracking codes) and repeated titles are removed.
- Every summary must pass two checks: no run of 6+ words copied from the sources, and none of the common "AI-sounding" patterns (dashes, "not just X but Y", stock words). It gets one retry. If it still fails, the story shows no summary. It never falls back to the publisher's text.

## Known limits

- 10 of the 54 feed addresses were confirmed live on 20 Oct 2026. The rest come from public feed directories and standard patterns, and the first real run decides which survive. Big publishers (Condé Nast, Hearst, Dotdash) were not checkable from where this was built.
- Reddit sometimes refuses requests from cloud servers. If the six r/ feeds show errors, drop them.
- GitHub pauses scheduled runs after 60 days with no repository activity. Make any small edit now and then.
- The currency is a guess from the visitor's timezone and language. The dropdown overrides it and remembers the choice.
- Photos are loaded from the outlet's own site and credited, never copied. For affiliate product cards later, use the images in each retailer's affiliate product feed.

## Files

- `feeds.json`: the sources. Add or remove freely. `filter` keeps only beauty items from general feeds.
- `update.py`: the whole pipeline (no installs needed).
- `template.html`: the page design.
- `test_update.py`: run `python3 test_update.py`.
- `fixtures/` and `preview/`: made-up sample feeds and the page built from them.
  Rebuild the sample: `MIN_HEALTHY_FEEDS=6 python3 update.py --fixtures fixtures --out preview --now 2026-10-20T06:00:00Z`
- `task_plan.md`, `findings.md`, `progress.md`: the build plan and notes.

## Ideas for later

Beauty calendar, ingredient decoder, dupe radar, price per ml, weekly email digest, a `/go/` redirect for affiliate links.
