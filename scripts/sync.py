#!/usr/bin/env python3
"""Notion -> JSON sync for the Home Tap guest taplist.

Reads the Taps, Kegs, Recipes and Bottles databases (read-only) and writes
data/taplist.json and data/bottles.json. See docs/2026-10-16-home-tap-sync-prd.md.

Python 3 standard library only.

Privacy: output records are CONSTRUCTED from the named fields in PRD sections
5 and 5b. Nothing is copied wholesale from a Notion row, so a new Notion
property can never reach the output by accident.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Pinned. 2022-06-28 queries by database ID (the IDs stored as Actions
# variables) and Notion states older versions stay supported. Newer versions
# query "data sources" by a different ID.
NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

TAPLIST_FILE = "taplist.json"
BOTTLES_FILE = "bottles.json"
HASH_EXCLUDED_KEYS = ("updated",)

BEVERAGE_TAGS = {
    "beer": "Homebrew",
    "non-alcoholic": "NA",
    "coffee": "Coffee",
    "wine": "Wine",
}
# Source values meaning "Leo brewed it". Anything else non-"homebrew" is commercial.
OWN_SOURCES = {"brewed here", "leo's own", "leos own", "own", "mine"}

BJCP_CODE = r"(?:C\d{1,2}[A-Z]?|X\d{1,2}|\d{1,2}[A-Z])"
LEADING_CODE_RE = re.compile(r"^\s*(" + BJCP_CODE + r")\b[\s.:\-–—]*(.*)$", re.S)
BASE_CODE_RE = re.compile(r"base:?\s*(" + BJCP_CODE + r")\b", re.I)


class SyncError(Exception):
    """Raised for any failure that must abort the run without writing."""


# ---------------------------------------------------------------- Notion I/O

def _notion_query(db_id, token, label):
    """Return all rows of one database. Read-only: only /query is ever called."""
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            "%s/databases/%s/query" % (NOTION_API, db_id),
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": "Bearer " + token,
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        data = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(min(int(e.headers.get("Retry-After", "2")), 30))
                    continue
                # Never include the URL (contains the DB ID) or the body.
                raise SyncError("Notion returned HTTP %d for the %s database" % (e.code, label))
            except (urllib.error.URLError, OSError, ValueError):
                raise SyncError("Could not reach Notion for the %s database" % label)
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")


def fetch_live(env):
    token = env.get("NOTION_TOKEN")
    ids = {k: env.get(k) for k in ("TAPS_DB_ID", "KEGS_DB_ID", "RECIPES_DB_ID", "BOTTLES_DB_ID")}
    missing = [k for k, v in dict(ids, NOTION_TOKEN=token).items() if not v]
    if missing:
        raise SyncError("Missing environment variables: " + ", ".join(sorted(missing)))
    return {
        "taps": _notion_query(ids["TAPS_DB_ID"], token, "Taps"),
        "kegs": _notion_query(ids["KEGS_DB_ID"], token, "Kegs"),
        "recipes": _notion_query(ids["RECIPES_DB_ID"], token, "Recipes"),
        "bottles": _notion_query(ids["BOTTLES_DB_ID"], token, "Bottles"),
    }


def fetch_fixtures(directory):
    out = {}
    for name in ("taps", "kegs", "recipes", "bottles"):
        try:
            with open(os.path.join(directory, name + ".json"), encoding="utf-8") as f:
                out[name] = json.load(f)["results"]
        except (OSError, ValueError, KeyError) as e:
            raise SyncError("Bad fixture %s.json: %s" % (name, e))
    return out


# ------------------------------------------------------- property readers

def _plain(rich):
    text = "".join(part.get("plain_text", "") for part in rich or []).strip()
    return text or None


def prop_value(prop):
    """Notion property -> plain Python value. Empty -> None."""
    if not isinstance(prop, dict):
        return None
    t = prop.get("type")
    v = prop.get(t) if t else None
    if v is None:
        return None
    if t in ("title", "rich_text"):
        return _plain(v)
    if t in ("select", "status"):
        return v.get("name") or None
    if t == "multi_select":
        return [o["name"] for o in v if o.get("name")] or None
    if t == "date":
        return v.get("start")
    if t == "relation":
        return [r["id"] for r in v if r.get("id")] or None
    if t == "formula":
        return prop_value({"type": v.get("type"), v.get("type"): v.get(v.get("type"))})
    if t == "rollup":
        rt = v.get("type")
        if rt == "array":
            vals = [prop_value(item) for item in v.get("array", [])]
            vals = [x for x in vals if x is not None]
            return vals or None
        return prop_value({"type": rt, rt: v.get(rt)})
    if t in ("number", "checkbox", "url", "email", "phone_number", "string"):
        return v if v != "" else None
    return None


def field(row, name):
    return prop_value((row.get("properties") or {}).get(name))


def text(value):
    """Collapse a scalar-or-list value to a single string (or None)."""
    if isinstance(value, list):
        value = next((x for x in value if x not in (None, "")), None)
    if value is None:
        return None
    return str(value).strip() or None


def number(value):
    if isinstance(value, list):
        value = next((x for x in value if isinstance(x, (int, float))), None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else value


def split_style(raw):
    """'21A American IPA' -> ('21A', 'American IPA'); no code -> (None, raw)."""
    raw = text(raw)
    if not raw:
        return None, None
    m = LEADING_CODE_RE.match(raw)
    if m:
        return m.group(1).upper(), (m.group(2).strip() or None)
    return None, raw


def put(d, key, value):
    if value is not None and value != [] and value != "":
        d[key] = value
    return d


# ------------------------------------------------------------------ taplist

def _stat(value, source):
    return None if value is None else {"value": value, "source": source}


def _is_cleaner(beverage_type):
    b = (beverage_type or "").lower()
    return "cleaner" in b or "sanitizer" in b


def build_taplist(taps, kegs, recipes):
    kegs_by_id = {k["id"]: k for k in kegs}
    recipes_by_id = {r["id"]: r for r in recipes}
    entries = []
    for tap in taps:
        keg_ids = field(tap, "Current Keg")
        if not keg_ids:
            continue
        keg = kegs_by_id.get(keg_ids[0])
        if keg is None:
            continue
        btype = text(field(keg, "Beverage Type"))
        if _is_cleaner(btype):
            continue
        recipe_ids = field(keg, "Recipe")
        recipe = recipes_by_id.get(recipe_ids[0], {}) if recipe_ids else {}

        label = text(field(tap, "Tap"))
        style_raw = text(field(keg, "BJCP Style")) or text(field(recipe, "BJCP Style"))
        code, style_name = split_style(style_raw)
        base = BASE_CODE_RE.search(style_raw or "")
        keg_abv = number(field(keg, "ABV %"))
        keg_og = number(field(keg, "OG (measured)"))
        keg_fg = number(field(keg, "FG (measured)"))
        kegged = text(field(keg, "Kegged Date"))

        tags = []
        if text(field(tap, "Gas")) and text(field(tap, "Gas")).lower() == "nitro":
            tags.append("Nitro")
        if btype and btype.lower() in BEVERAGE_TAGS:
            tags.append(BEVERAGE_TAGS[btype.lower()])

        rec = {}
        put(rec, "tap", label)
        put(rec, "name", text(field(keg, "Contents")))
        put(rec, "style", style_name)
        put(rec, "bjcp", code)
        put(rec, "bjcp_base", base.group(1).upper() if base else None)
        put(rec, "abv", _stat(keg_abv, "measured") or _stat(number(field(recipe, "ABV %")), "recipe"))
        put(rec, "ibu", _stat(number(field(recipe, "IBU")), "recipe"))
        put(rec, "og", _stat(keg_og, "measured") or _stat(number(field(recipe, "OG")), "recipe"))
        put(rec, "fg", _stat(keg_fg, "measured") or _stat(number(field(recipe, "FG")), "recipe"))
        put(rec, "srm", _stat(number(field(recipe, "SRM")), "recipe"))
        put(rec, "kegged", kegged[:7] if kegged else None)
        put(rec, "description", text(field(keg, "Guest Notes")))
        put(rec, "tags", tags)
        if "name" not in rec:
            continue
        sort_key = (
            text(field(tap, "Tap Type")) or "",
            number(field(tap, "Position")) if number(field(tap, "Position")) is not None else 10 ** 9,
            label or "",
        )
        entries.append((sort_key, rec))
    entries.sort(key=lambda e: e[0])
    return {"taps": [rec for _, rec in entries]}


# ------------------------------------------------------------------ bottles

def _bottle_style(raw):
    raw = text(raw)
    if not raw:
        return None, None
    low = raw.lower()
    if low == "unknown":
        return None, None
    if low.startswith("n/a"):
        return None, "Non-beer"
    if low.startswith("specialty/other"):
        return None, "Specialty beer"
    return split_style(raw)


def build_bottles(rows):
    guest_gate = any("Guest Visible" in (r.get("properties") or {}) for r in rows)
    homebrew, commercial = [], []
    for row in rows:
        if field(row, "Placeholder?"):
            continue
        if guest_gate and not field(row, "Guest Visible"):
            continue
        name = text(field(row, "Name"))
        if not name:
            continue
        source = (text(field(row, "Source")) or "").lower()
        is_own = source in OWN_SOURCES
        is_homebrew = source == "homebrew" or is_own
        code, style = _bottle_style(field(row, "Style"))
        size = text(field(row, "Size"))
        country = text(field(row, "Country"))

        tags = []
        if source == "homebrew":
            tags.append("Homebrew")
        if is_own:
            tags.append("Brewed here")
        if code and code.startswith("C1"):
            tags.append("Cider")
        if not is_homebrew and country and country.lower() not in ("usa", "us", "united states"):
            tags.append("Import")

        rec = {}
        put(rec, "name", name)
        if not is_homebrew:  # homebrewers are private individuals
            put(rec, "brewery", text(field(row, "Brewery / Brewer")))
        put(rec, "style", style)
        put(rec, "bjcp", code)
        put(rec, "abv", number(field(row, "ABV %")))
        put(rec, "size", None if (size or "").lower() == "unknown" else size)
        put(rec, "container", text(field(row, "Container")))
        if not is_homebrew:
            put(rec, "country", country)
        put(rec, "tags", tags)
        (homebrew if is_homebrew else commercial).append(rec)
    key = lambda r: r["name"].lower()
    return {"bottles": sorted(homebrew, key=key) + sorted(commercial, key=key)}


# ----------------------------------------------- change detection / writing

def content_hash(obj):
    stripped = {k: v for k, v in obj.items() if k not in HASH_EXCLUDED_KEYS}
    canon = json.dumps(stripped, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_if_changed(path, content, today, dry_run=False):
    """Write content (stamped with `updated`) only if guest-visible content changed.

    Returns (changed, previous_content_or_None).
    """
    old = load_json(path)
    if old is not None and content_hash(old) == content_hash(content):
        return False, old
    if not dry_run:
        stamped = dict({"updated": today}, **content)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stamped, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    return True, old


# ------------------------------------------------------------ commit message

def _tap_summary(rec):
    return rec.get("name")


def taplist_changes(old, new):
    old_taps = {t["tap"]: t for t in (old or {}).get("taps", []) if "tap" in t}
    parts = []
    seen = set()
    for t in new.get("taps", []):
        label = t.get("tap") or t["name"]
        seen.add(label)
        before = old_taps.get(label)
        if before is None or before.get("name") != t.get("name"):
            parts.append("%s → %s" % (label, t["name"]))
        elif before != t:
            parts.append("%s details updated" % label)
    for label in old_taps:
        if label not in seen:
            parts.append("%s cleared" % label)
    return parts


def bottle_changes(old, new):
    old_by = {b["name"]: b for b in (old or {}).get("bottles", [])}
    new_by = {b["name"]: b for b in new.get("bottles", [])}
    added = [n for n in new_by if n not in old_by]
    removed = [n for n in old_by if n not in new_by]
    edited = [n for n in new_by if n in old_by and new_by[n] != old_by[n]]
    bits = []
    if added:
        bits.append("+%d" % len(added))
    if removed:
        bits.append("−%d" % len(removed))
    if edited:
        bits.append("~%d edited" % len(edited))
    return bits


def commit_message(old_taplist, new_taplist, old_bottles, new_bottles):
    """Human-readable one-liner, or '' if neither file changed."""
    pieces = []
    tap_parts = taplist_changes(old_taplist, new_taplist) if new_taplist is not None else []
    if tap_parts:
        pieces.append("Taplist: " + "; ".join(tap_parts))
    bottle_parts = bottle_changes(old_bottles, new_bottles) if new_bottles is not None else []
    if bottle_parts:
        pieces.append("Bottles: " + ", ".join(bottle_parts))
    return " | ".join(pieces)


# ---------------------------------------------------------------------- CLI

def run(data, out_dir, today, dry_run=False):
    """Build both files from fetched rows. Returns (changed_any, message)."""
    taplist = build_taplist(data["taps"], data["kegs"], data["recipes"])
    bottles = build_bottles(data["bottles"])
    tap_path = os.path.join(out_dir, TAPLIST_FILE)
    bottle_path = os.path.join(out_dir, BOTTLES_FILE)
    tap_changed, old_taps = write_if_changed(tap_path, taplist, today, dry_run)
    bottle_changed, old_bottles = write_if_changed(bottle_path, bottles, today, dry_run)
    message = commit_message(
        old_taps, taplist if tap_changed else None,
        old_bottles, bottles if bottle_changed else None,
    )
    if (tap_changed or bottle_changed) and not message:
        message = "Sync: guest-visible details updated"
    return (tap_changed or bottle_changed), message


def main(argv=None, env=None):
    p = argparse.ArgumentParser(description="Sync the Home Tap taplist from Notion.")
    p.add_argument("--out", default="data", help="output directory (default: data)")
    p.add_argument("--fixtures", help="read Notion-shaped JSON from this directory instead of calling Notion")
    p.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    p.add_argument("--message-file", help="write the commit message here when something changed")
    args = p.parse_args(argv)
    env = os.environ if env is None else env
    try:
        data = fetch_fixtures(args.fixtures) if args.fixtures else fetch_live(env)
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        changed, message = run(data, args.out, today, args.dry_run)
    except SyncError as e:
        print("sync failed: %s" % e, file=sys.stderr)
        return 1
    if not changed:
        print("No guest-visible changes; nothing written.")
        return 0
    print(("Would write: " if args.dry_run else "Updated: ") + message)
    if args.message_file and not args.dry_run:
        with open(args.message_file, "w", encoding="utf-8") as f:
            f.write(message + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
