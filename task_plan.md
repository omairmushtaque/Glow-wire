# Task Plan: Women's Beauty News Hub (Techmeme-style MVP)

## Goal

A one-page, mobile-friendly site that groups women's beauty headlines from 40+ sources into stories, shows one lead source plus a "More:" line per story, adds a short original summary, and refreshes itself on a schedule.

## Next Step

Verify candidate feed URLs (WebFetch, parallel batches) and write `feeds.json` with 40+ sources.

## Current Phase

Phase 2

## Phases

### Phase 1: Requirements & Discovery

- [x] Topic confirmed: women's beauty products and news, worldwide, product-first (affiliate later)
- [x] Currency: show visitor's local currency, store's own price stays visible
- [x] Skills audited and installed (planning-with-files, ponytail, humanizer, 7 open-design)
- [x] Found workspace has no outside internet (see findings.md), so updater runs in GitHub Actions
- **Status:** complete

### Phase 2: Sources

- [ ] Find and verify 40+ real feed URLs (editorial, trade/industry, Reddit RSS, FDA recalls)
- [ ] Write `feeds.json` (name, url, weight, region)
- [ ] Mark which URLs could only be checked by the updater's own health check
- **Status:** in_progress

### Phase 3: Updater (`update.py`, Python stdlib only)

- [ ] Fetch all feeds in parallel, with timeouts and a User-Agent
- [ ] Health check: status, parses, has items, newest item age. Write `health.json`, skip broken feeds
- [ ] Dedupe: same URL (tracking params stripped), same title within a feed
- [ ] Group same-event headlines into stories (title similarity, 48h window)
- [ ] Pick lead source (source weight, then earliest) and build "More:" list
- [ ] Original summary via Claude API, cached, with copy-check and AI-pattern lint (humanizer rules)
- [ ] Tags (launch, deal, safety, event, trending, industry), price spotted in headline
- [ ] Daily exchange rates (Frankfurter API)
- [ ] Render `docs/index.html` and `docs/data.json`
- **Status:** pending

### Phase 4: Site (`template.html`)

- [ ] One page, no framework, no CDN, no external fonts (ponytail + minimalist-ui)
- [ ] Mobile first, filter chips, currency picker (auto-detect, manual override remembered)
- [ ] Feed thumbnails hotlinked and credited, never re-hosted
- [ ] Affiliate disclosure line, "last updated" time
- **Status:** pending

### Phase 5: Schedule

- [ ] `.github/workflows/update.yml`: cron every 30 min, run updater, publish to GitHub Pages
- [ ] `README.md` with the setup steps the user must do (repo, Pages, API key secret)
- **Status:** pending

### Phase 6: Test & Verify

- [ ] Run updater on fixture feeds (including a broken one, a duplicate, a same-event cluster)
- [ ] Check: grouping, lead/More, no copied text, lint, health report
- [ ] Look at the page at phone width (screenshot if a browser is available)
- **Status:** pending

### Phase 7: Delivery

- [ ] Send the files, plain-language summary, list of what the user must do and what was not verified live
- **Status:** pending

## Key Questions

1. Does the user have a GitHub account to host the repo? (needed for the schedule)
2. Will the user add an Anthropic API key as a repo secret? (needed for summaries; without it stories show headline only)

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Updater runs in GitHub Actions, site on GitHub Pages | Workspace has no outside internet; Actions has it, has a free cron, and needs no server |
| Python stdlib only, one `update.py` | Nothing to install, easy to read and fix (ponytail) |
| Site is pre-rendered HTML, tiny JS only for filter and currency | Works without JS, better for search engines (matters for affiliate traffic), loads fast |
| Currency from browser locale, then timezone, default USD; dropdown overrides | Free, no server. It is a good guess, not perfect |
| Show store's own price first, converted price as "≈" | Converted alone can mislead, stores price differently per country |
| Photos: hotlink the thumbnail each feed provides, credit the source, do not download or re-host | Copying and re-hosting brand or publisher photos risks copyright trouble. Affiliate product feeds can supply images later under their terms |
| Skip Google News RSS | Its terms are for personal use, risky for a monetized site |
| No `/go/` redirect yet | Needs a server or Worker. Add when the first affiliate program is approved |
| Summaries: Claude Haiku via API, only for new stories, cached | Cheap, fast. Never fall back to the publisher's blurb |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| curl to feeds returns 000 (blocked) | 1 | Network allowlist only. Design around it (Actions for live runs, fixtures for local tests) |

## Notes

- Update phase status as work progresses: `pending` to `in_progress` to `complete`.
- Later ideas (not MVP): beauty calendar, ingredient decoder, dupe radar, price per ml, email digest, `/go/` redirect.
