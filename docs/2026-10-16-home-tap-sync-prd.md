# PRD — Home Tap Auto-Sync (Notion → taplist site)

**Status:** DRAFT 1 — awaiting Leo's sign-off
**Owner:** Leo · **Builder:** Claude Code
**Date:** 2026-10-16
**Repo:** `leoswag1269/whatsontap_at246` (public, served by GitHub Pages)

---

## 1. Problem

The guest taplist (`index.html`) is edited by hand. Two JavaScript arrays (`TAP_LIST`, `BOTTLE_LIST`) hold the beers. The real source of truth now lives in Notion, in the Draught Hub: **Taps → Current Keg → Kegs → Recipe**. So every keg change means updating Notion *and* hand-editing HTML. In practice the site drifts out of date (the last commit is the August taplist).

There is also a small bug. The "Last updated" line shows the *viewer's* date, not the date the list changed, so it always looks fresh even when it is stale.

## 2. Goal

Updating Notion is the *only* step. The public taplist follows automatically, and the repo's commit history becomes a readable changeover log. That log is the plan agreed on 2026-09-02: Home Tap's commits double as the Hub's changeover log.

## 3. Success criteria

1. Assigning or clearing a keg on a tap in Notion shows on the live site within ~1 hour, with no manual step.
2. Each real taplist change produces exactly **one** commit, with a human-readable message, for example: `Taplist: Keezer 2 → Elderberry Cream Ale; Picnic P3 cleared`.
3. Edits in Notion that don't change what guests see produce **no** commit. This covers cleaning logs, volume checks and keg notes.
4. The page shows **no private field** under any circumstances. An automated test enforces this (§7).
5. "Last updated" reflects when the taplist content last changed.
6. The existing look of the page is preserved. This is a data plumbing change, not a redesign.
7. Leo can trigger a sync by hand from the GitHub web UI, using the "Run workflow" button.

## 4. Scope

### In (v1)
- A sync script that reads Taps, the linked Kegs, and the Recipes those kegs link to, all through the Notion API with **read-only** access.
- A generated data file `data/taplist.json`, holding whitelisted fields only, written in a stable order so diffs are clean.
- `index.html` refactored to render from `data/taplist.json` (and `data/bottles.json`), with no hardcoded beer arrays.
- A GitHub Action that runs on a schedule (hourly) plus a manual trigger, and commits only when the content changed.
- Unit tests built on fixture data. These include the privacy whitelist test and the change-detection test.
- The "Last updated" fix.
- An updated README covering how it works, how to run it by hand, and what to do when it fails.

### Out (v1)
- Any **writes** to Notion.
- Redesigning the page.
- Automating the Bottles & Cans section. There's no Notion source for it, so it stays a hand-edited `data/bottles.json` (open question Q3).
- Style-conformance visuals. These are designed for now (§6) and built in Milestone 3.
- Moving off GitHub Pages, adding a custom domain, or adding auth.

## 5. Data mapping (v1)

**Which taps appear:** Taps rows whose `Current Keg` is not empty, which matches the existing "Taplist" view. Rows are ordered by `Tap Type`, then `Position`. A keg with `Beverage Type = Cleaner/Sanitizer` is **never** shown, even if it is hooked up. For example, K09, the Star San utility keg, would be hidden while it runs through a line.

| Site field | Source | Notes |
|---|---|---|
| Tap label | Taps `Tap` (e.g. "Keezer 2") | See Q1 for the guest-facing label |
| Nitro badge | Taps `Gas = Nitro` | Shown as a tag |
| Beer name | Kegs `Contents` | Optional future `Display Name` override (Q2) |
| Style | Kegs `BJCP Style`, else Recipe `BJCP Style` | See §6 for format |
| ABV | Kegs `ABV %`, else Recipe `ABV %` | Keg value treated as measured |
| IBU | Recipe `IBU` | Recipe estimate |
| OG / FG / SRM | Recipe `OG` / `FG` / `SRM` | Stored in JSON now; displayed in Milestone 3 |
| Kegged date | Kegs `Kegged Date` | Shown as month/year ("Kegged Oct 2026") — Q4 |
| Description | **New Kegs field `Guest Notes`** (text) | See Q2 — `Notes` is private and must never be used |
| Tags | `Beverage Type` (Beer → "Homebrew"; Non-alcoholic → "NA"; Coffee/Wine as-is) | |

**Explicitly forbidden (never read into output, test-enforced):**
- From Kegs: `Notes`, `Volume Left`, `Disposition`, `Quality`, `Needs / Repair`, `Location`, cleaning fields, queue fields.
- From Recipes: `Share With`, `Gemini URL`/`Gemini ID`, `Creator`, `Source URL`, `Flags`, all nutrition fields, and the page body.
- From any database: Notion page URLs or IDs.

## 6. Designing for Milestone 3 now: "how true to style"

You flagged this up front, and it changes v1. Retrofitting it later would mean reworking the data shape. So v1 makes three structural choices, even though nothing visual ships yet.

**6.1 Stats carry their provenance.** Every stat in `taplist.json` is stored as `{ "value": 1.012, "source": "measured" | "recipe" }`. That's the honest way to compare against style ranges. A recipe target in range only says the *plan* was to style. Measured OG/FG/ABV say what the *beer* actually is. IBU and SRM are almost never measured at home, so they will stay labeled as estimates.

**6.2 Measured gravity needs a home in Notion.** Kegs has `ABV %` today but no measured OG/FG. Recommendation: add `OG (measured)` and `FG (measured)` number fields to **Kegs**, the per-batch record, not Recipes. The renderer prefers measured values and falls back to recipe values.

**6.3 Style is matched by BJCP code, not free text.** `BJCP Style` is currently free text (e.g. "Fruit Beer - Elderberry, Base: Cream Ale"). Free-text matching will be wrong often enough to be embarrassing. Convention: start the field with the code, e.g. `21A American IPA` or `29A Fruit Beer (base: 1C Cream Ale)`. The sync extracts the code; for specialty styles it compares against the **base** style's ranges and says so. If a style has no code, the beer simply shows no gauge. It never guesses.

**6.4 Your style guidelines file.** `styles/style-guidelines.md`, written by you, gets parsed into `data/styles.json`. To make parsing deterministic, each style uses this exact shape:

```markdown
## 21A American IPA
- OG: 1.056-1.070
- FG: 1.008-1.014
- ABV: 5.5-7.5
- IBU: 40-70
- SRM: 6-14
- Notes: (optional, your own words)
```

Only the numbers are needed. Keep any guideline prose in your own words, since the repo is public.

**6.5 Visual (Milestone 3).** One small horizontal bar per stat. The bar shows the style's range as a band and the beer's value as a marker. Measured values get a solid marker; recipe estimates get a hollow one. A one-line summary sits beneath, e.g. "4 of 5 in style · IBU above range".

**Recommendation:** no single "% true to style" score. It implies precision the inputs don't have, and a hoppy beer 5 IBU over range isn't "80% wrong."

## 7. Architecture

```
Notion (Taps, Kegs, Recipes) ──read-only API──▶ scripts/sync.py
        ▲                                             │ writes (only if content changed)
        │                                             ▼
   you edit here                             data/taplist.json ──▶ index.html (fetches JSON) ──▶ GitHub Pages
                                                      │
                                   GitHub Action (hourly + manual) commits with a changeover message
```

- **Language:** Python 3, **standard library only**. No dependencies to install or update.
- **Secrets:** the Notion token is stored as GitHub secret `NOTION_TOKEN`. The database IDs are stored as GitHub repository *variables*, not in code. The token never appears in code, logs, fixtures or commits.
- **Notion integration:** an internal integration with **Read content** capability only. It is shared with exactly three databases: Taps, Kegs, Recipes.
- **Change detection:** the script hashes the guest-visible content, with the timestamp excluded. If the hash matches the committed file, it writes nothing. That makes rule 3 of §3 hold.
- **Commit messages:** generated from the diff, listing which taps changed and what changed on each.
- **Failure behavior:** if Notion is unreachable or returns errors, the script exits non-zero and changes nothing. The live site keeps showing the last good list. A failed run emails Leo by default through GitHub's own notifications.
- **Tests (`tests/`):**
  - (a) mapping from fixture API responses
  - (b) **privacy test:** fixtures seed every forbidden field with a sentinel string such as `PRIVATE_SENTINEL`, and the test asserts it appears nowhere in the output
  - (c) cleaner kegs are excluded
  - (d) empty taplist renders the empty state
  - (e) no-op runs produce no write
  - (f) commit-message generation
- **Development without the real token:** Claude Code builds and tests against hand-made fixtures that match the real schemas. The first live run happens through the manual "Run workflow" trigger after merge.

## 8. Milestones

| # | Milestone | Done when |
|---|---|---|
| 1 | Sync script + JSON + tests (fixtures only) | `python -m unittest` passes, including the privacy test; PR opened |
| 2 | Page reads JSON + Action wired + README | Local preview screenshot from fixture data matches today's look; PR merged; first manual live run succeeds |
| 3 | Style conformance | Your `style-guidelines.md` exists, measured OG/FG fields are added in Notion, and the gauges render for at least one beer |

You only need to act at the start of Milestone 2 (secrets and variables) and Milestone 3 (the .md file and Notion fields).

## 9. Your setup tasks (one time, ~30 min)

1. Create a Notion internal integration with the Read content capability only, and copy its token.
2. Share **Taps**, **Kegs**, and **Recipes** with the integration. Do not share LifeOS as a whole.
3. In GitHub, go to repo **Settings → Secrets and variables → Actions**. Add secret `NOTION_TOKEN`, then add variables `TAPS_DB_ID`, `KEGS_DB_ID`, `RECIPES_DB_ID`. Claude will give you the exact values.
4. In Notion, add `Guest Notes` (text) to Kegs. Optionally add `OG (measured)` / `FG (measured)` now as well.
5. Finish keg labeling, then make sure every keg on a tap has `Contents`, `BJCP Style` and `ABV %` filled in.

## 10. Open questions (my recommendation in brackets)

- **Q1 — Tap labels for guests:** show the internal names ("Keezer 2", "Picnic P3") or a simplified number? [Show the Notion `Tap` name as is. Renaming happens in Notion, which keeps one source of truth.]
- **Q2 — Description source:** add a `Guest Notes` field to Kegs, or skip descriptions? [Add it. The current site's descriptions are its best feature, and `Notes` is full of private details like dump candidates and volumes.] Add a `Display Name` override too? [Not yet. `Contents` reads fine.]
- **Q3 — Bottles & Cans:** keep it as a hand-edited `data/bottles.json`, or remove the section? [Keep it, hand-edited. It's rarely changed.]
- **Q4 — Show kegged date / beer age to guests?** [Yes, as month and year only.]
- **Q5 — Picnic taps:** they're portable. Should they show on the house taplist when in use? [Yes; they're only listed when a keg is assigned.]
- **Q6 — Sync frequency:** hourly is plenty, and GitHub's scheduler can lag a few minutes. [Hourly plus the manual button.]
- **Q7 — Milestone 3 style labels:** BJCP 2021 codes as the canonical key? [Yes. Your Cicerone work and BeerSmith both speak BJCP.]

## 11. Risks

- **Public repo, private Notion.** This is mitigated by the field whitelist (an allow-list, not a block-list), the sentinel privacy test, and the read-only integration scoped to three databases.
- **Notion API changes.** The script pins an API version header; Claude Code verifies the current version against Notion's docs at build time.
- **Data quality.** Empty or messy fields render as gaps, not errors. The README will list which Notion fields drive what.
