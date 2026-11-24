# whatsontap_at246 — Home Tap

A live guest taplist for my home bar, served by GitHub Pages:
<https://leoswag1269.github.io/whatsontap_at246/>

**Updating Notion is the only step.** The site follows within about an hour, and the
commit history of this repo doubles as the keg changeover log.

## How it works

```
Notion (Taps, Kegs, Recipes, Bottles) ──read-only──▶ scripts/sync.py
                                                          │ (only if guest-visible content changed)
                                                          ▼
                              data/taplist.json + data/bottles.json ──▶ index.html ──▶ GitHub Pages
```

- A GitHub Action (`.github/workflows/sync.yml`) runs every hour and on demand.
- It reads Notion, builds the two JSON files from an **allow-list** of fields, and commits
  only if something guests can see has changed. Cleaning logs, volume checks and keg notes
  never cause a commit.
- Each commit message says what changed, e.g. `Taplist: Keezer 2 → Elderberry Cream Ale; Picnic P3 cleared`.
- `index.html` fetches the JSON and draws the cards. Don't edit the `data/*.json` files by hand;
  the next sync would overwrite them.
- If Notion is unreachable the run fails, nothing is committed, and the site keeps showing the
  last good list.

The full spec is in [`docs/2026-10-16-home-tap-sync-prd.md`](docs/2026-10-16-home-tap-sync-prd.md).

## One-time setup

1. **Notion integration.** Create an internal integration with the **Read content** capability only.
   Share exactly four databases with it: Taps, Kegs, Recipes, Bottles (not LifeOS as a whole).
2. **GitHub secret and variables.** In the repo go to **Settings → Secrets and variables → Actions**.

   | Where | Name | Value |
   |---|---|---|
   | Secrets | `NOTION_TOKEN` | the integration token |
   | Variables | `TAPS_DB_ID` | ID of the Taps database |
   | Variables | `KEGS_DB_ID` | ID of the Kegs database |
   | Variables | `RECIPES_DB_ID` | ID of the Recipes database |
   | Variables | `BOTTLES_DB_ID` | ID of the Bottles database |

   To find a database ID, open the database as a full page in Notion and copy the 32-character
   code in the address, between the last `/` and the `?`. Paste it straight into GitHub.
   Never put the token or the IDs in a file in this repo.
3. **Notion fields.** Add a `Guest Notes` (text) field to Kegs. It is the only source of beer
   descriptions on the site. Make sure every keg on a tap has `Contents`, `BJCP Style` and `ABV %`.

## Running a sync by hand

On GitHub: **Actions → Sync taplist from Notion → Run workflow**. The first run after setup
creates `data/taplist.json` and rewrites `data/bottles.json` (the old manual snapshot has
extra bookkeeping keys), so it will commit even if the beers look the same.

On a computer (no token needed, uses made-up data and changes nothing):

```
python3 scripts/sync.py --dry-run --fixtures tests/fixtures/v2
python3 -m unittest discover tests
```

## What drives what

| On the site | Notion field |
|---|---|
| Which taps show | Taps with a `Current Keg`, in `Tap Type` then `Position` order. Cleaner/Sanitizer kegs never show |
| Tap label, Nitro tag | Taps `Tap`, `Gas` |
| Beer name, kegged month, description | Kegs `Contents`, `Kegged Date`, `Guest Notes` |
| Style, ABV | Kegs `BJCP Style` / `ABV %`, else the Recipe's |
| IBU | Recipe `IBU` |
| Bottles | Bottles rows with `Placeholder?` unchecked (and `Guest Visible` checked, once that field exists) |

Never published: Kegs `Notes`, volumes, disposition, quality, repairs, locations; Recipe sharing,
Gemini, nutrition fields; Bottles `Notes`, `Location`, `Quantity`, homebrewer names; any Notion
page URL or ID. A test checks this on every run.

## When something goes wrong

- **Red X on a run in the Actions tab** (GitHub also emails me): open the run and read the last
  step. Common causes: a database wasn't shared with the integration, a variable is missing or
  mistyped, or Notion was briefly down. Fix it and click **Run workflow**. The site is fine in the meantime.
- **Site says "Nothing's pouring"**: no keg is assigned to a tap in Notion, or `data/taplist.json`
  doesn't exist yet (run the sync once).
- **Site stopped updating and there are no runs**: GitHub turns off scheduled workflows after
  60 days with no commits to the repo, which happens when the taplist is quiet for that long.
  Open the Actions tab and click **Enable workflow**.
- **A beer shows up that shouldn't**: fix it in Notion (for example uncheck the bottle's
  visibility or clear the tap) and re-run. If a private detail ever appears, tell Claude and
  stop the workflow first.

## Repo layout

- `index.html`: the page (design and renderer, no build step)
- `data/*.json`: generated, never hand-edited
- `scripts/sync.py`: Notion → JSON, Python standard library only
- `tests/`: unit tests and made-up fixtures
- `.github/workflows/sync.yml`: hourly and manual sync
