"""Tests for scripts/sync.py. Run: python -m unittest discover tests

All data comes from tests/fixtures (hand-made, Notion API response shape).
No network access and no real token or database IDs are used.
"""
import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sync  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")

TAP_KEYS = {"tap", "name", "style", "bjcp", "bjcp_base", "abv", "ibu", "og", "fg", "srm",
            "kegged", "description", "tags"}
BOTTLE_KEYS = {"name", "brewery", "style", "bjcp", "abv", "size", "container", "country", "tags"}
STAT_KEYS = {"value", "source"}


def load(version):
    return sync.fetch_fixtures(os.path.join(FIXTURES, version))


def strings(obj):
    """Every string in a JSON structure, keys included."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)
    elif isinstance(obj, str):
        yield obj


def built(version="v1"):
    d = load(version)
    return sync.build_taplist(d["taps"], d["kegs"], d["recipes"]), sync.build_bottles(d["bottles"])


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.taplist, self.bottles = built()
        self.taps = {t["tap"]: t for t in self.taplist["taps"]}

    def test_tap_order_is_through_wall_then_beermeister_then_picnic(self):
        self.assertEqual([t["tap"] for t in self.taplist["taps"]],
                         ["Keezer 1", "Keezer 2", "Keezer 3", "Beermeister 1", "Picnic P1", "Picnic P3"])

    def test_keg_abv_is_measured_and_recipe_stats_are_recipe(self):
        t = self.taps["Keezer 1"]
        self.assertEqual(t["abv"], {"value": 5.8, "source": "measured"})
        self.assertEqual(t["ibu"], {"value": 40, "source": "recipe"})
        self.assertEqual(t["og"], {"value": 1.058, "source": "recipe"})

    def test_abv_falls_back_to_recipe(self):
        self.assertEqual(self.taps["Keezer 2"]["abv"], {"value": 5, "source": "recipe"})

    def test_style_prefers_keg_then_recipe_and_splits_code(self):
        self.assertEqual((self.taps["Keezer 1"]["bjcp"], self.taps["Keezer 1"]["style"]),
                         ("21A", "American IPA"))  # from recipe
        e = self.taps["Keezer 2"]  # from keg, specialty with base style
        self.assertEqual((e["bjcp"], e["bjcp_base"]), ("29A", "1C"))

    def test_style_without_code_has_no_code(self):
        t = self.taps["Picnic P3"]
        self.assertEqual(t["style"], "NA Seltzer")
        self.assertNotIn("bjcp", t)

    def test_kegged_is_month_precision(self):
        self.assertEqual(self.taps["Keezer 1"]["kegged"], "2026-09")

    def test_description_comes_only_from_guest_notes(self):
        self.assertEqual(self.taps["Keezer 1"]["description"],
                         "An All-Azacca IPA with tropical hop character.")
        self.assertNotIn("description", self.taps["Keezer 3"])  # keg has Notes but no Guest Notes

    def test_tags(self):
        self.assertEqual(self.taps["Keezer 3"]["tags"], ["Nitro", "Homebrew"])
        self.assertEqual(self.taps["Picnic P3"]["tags"], ["NA"])
        self.assertEqual(self.taps["Picnic P1"]["tags"], ["Coffee"])
        self.assertEqual(self.taps["Beermeister 1"]["tags"], ["Wine"])

    def test_bottle_groups_and_order(self):
        names = [b["name"] for b in self.bottles["bottles"]]
        self.assertEqual(names, ["Apple Cider", "Black Barleywine", "Super Saison",
                                 "Dernière Volonté", "Fruition", "Hard Seltzer",
                                 "Mystery Bottle", "Odd Brew"])

    def test_homebrew_bottles_have_no_brewery_or_country(self):
        for b in self.bottles["bottles"][:3]:
            self.assertNotIn("brewery", b)
            self.assertNotIn("country", b)

    def test_bottle_tags(self):
        by = {b["name"]: b for b in self.bottles["bottles"]}
        self.assertEqual(by["Apple Cider"]["tags"], ["Homebrew", "Cider"])
        self.assertEqual(by["Super Saison"]["tags"], ["Brewed here"])
        self.assertNotIn("brewery", by["Super Saison"])
        self.assertEqual(by["Dernière Volonté"]["tags"], ["Import"])
        self.assertEqual(by["Odd Brew"]["tags"], ["Import"])
        self.assertNotIn("tags", by["Fruition"])  # USA commercial

    def test_bottle_style_special_cases(self):
        by = {b["name"]: b for b in self.bottles["bottles"]}
        self.assertEqual((by["Fruition"]["bjcp"], by["Fruition"]["style"]), ("X5", "New Zealand Pilsner"))
        self.assertEqual(by["Hard Seltzer"]["style"], "Non-beer")
        self.assertEqual(by["Odd Brew"]["style"], "Specialty beer")
        self.assertNotIn("style", by["Mystery Bottle"])
        self.assertNotIn("bjcp", by["Mystery Bottle"])

    def test_unknown_size_and_empty_abv_omitted(self):
        by = {b["name"]: b for b in self.bottles["bottles"]}
        self.assertNotIn("size", by["Mystery Bottle"])
        self.assertNotIn("abv", by["Mystery Bottle"])
        self.assertEqual(by["Dernière Volonté"]["abv"], 7)

    def test_guest_visible_gate_applies_only_when_property_exists(self):
        rows = load("v1")["bottles"]
        self.assertEqual(len(sync.build_bottles(rows)["bottles"]), 8)  # no gate: all non-placeholder
        gated = copy.deepcopy(rows)
        for i, r in enumerate(gated):
            r["properties"]["Guest Visible"] = {"type": "checkbox", "checkbox": i == 0}
        out = sync.build_bottles(gated)["bottles"]
        self.assertEqual([b["name"] for b in out], ["Black Barleywine"])


class ExclusionTests(unittest.TestCase):
    def test_cleaner_keg_and_empty_tap_hidden(self):
        taplist, _ = built()
        labels = [t["tap"] for t in taplist["taps"]]
        self.assertNotIn("Keezer 4", labels)  # Star San cleaner keg, hooked up
        self.assertNotIn("Keezer 5", labels)  # no keg
        self.assertNotIn("Star San", [t["name"] for t in taplist["taps"]])

    def test_placeholder_bottles_hidden(self):
        _, bottles = built()
        self.assertNotIn("Placeholder Stout", [b["name"] for b in bottles["bottles"]])

    def test_empty_taplist(self):
        d = load("v1")
        for t in d["taps"]:
            t["properties"]["Current Keg"] = {"type": "relation", "relation": []}
        self.assertEqual(sync.build_taplist(d["taps"], d["kegs"], d["recipes"]), {"taps": []})


class PrivacyTests(unittest.TestCase):
    def test_fixtures_actually_seed_the_sentinels(self):
        # Guards against a vacuous privacy test.
        raw = ""
        for v in ("v1", "v2"):
            for n in ("taps", "kegs", "recipes", "bottles"):
                with open(os.path.join(FIXTURES, v, n + ".json"), encoding="utf-8") as f:
                    raw += f.read()
        for name in ("keg_notes", "disposition", "quality", "repair", "keg_location", "share_with",
                     "gemini_url", "gemini_id", "creator", "source_url", "flags", "bottle_notes",
                     "bottle_location", "homebrewer", "tap_notes"):
            self.assertIn("PRIVATE_SENTINEL_" + name, raw)
        self.assertIn("Volume Left (gal)", raw)
        self.assertIn("987.65", raw)    # Volume Left
        self.assertIn("424243", raw)    # Quantity
        self.assertIn("notion.so", raw)

    def test_no_private_data_in_output(self):
        for version in ("v1", "v2"):
            taplist, bottles = built(version)
            blob = json.dumps([taplist, bottles], ensure_ascii=False)
            for forbidden in ("PRIVATE_SENTINEL", "987.65", "424242", "424243", "notion.so",
                              "gemini", "example.com", "Leo", "tap-", "keg-", "recipe-", "bottle-"):
                self.assertNotIn(forbidden, blob, version)
            for s in strings([taplist, bottles]):
                self.assertNotRegex(s, r"https?://")

    def test_only_allow_listed_keys_reach_output(self):
        for version in ("v1", "v2"):
            taplist, bottles = built(version)
            self.assertEqual(set(taplist), {"taps"})
            self.assertEqual(set(bottles), {"bottles"})
            for t in taplist["taps"]:
                self.assertLessEqual(set(t), TAP_KEYS)
                for k in ("abv", "ibu", "og", "fg", "srm"):
                    if k in t:
                        self.assertEqual(set(t[k]), STAT_KEYS)
                        self.assertIn(t[k]["source"], ("measured", "recipe"))
            for b in bottles["bottles"]:
                self.assertLessEqual(set(b), BOTTLE_KEYS)

    def test_written_files_contain_no_sentinels(self):
        with tempfile.TemporaryDirectory() as out:
            sync.run(load("v1"), out, "2026-10-29")
            for name in (sync.TAPLIST_FILE, sync.BOTTLES_FILE):
                with open(os.path.join(out, name), encoding="utf-8") as f:
                    body = f.read()
                self.assertNotIn("PRIVATE_SENTINEL", body)
                self.assertNotIn("notion.so", body)


class ChangeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def snapshot(self):
        res = {}
        for n in (sync.TAPLIST_FILE, sync.BOTTLES_FILE):
            p = os.path.join(self.out, n)
            with open(p, "rb") as f:
                res[n] = (f.read(), os.stat(p).st_mtime_ns)
        return res

    def test_first_run_writes_both_files(self):
        changed, msg = sync.run(load("v1"), self.out, "2026-10-29")
        self.assertTrue(changed)
        self.assertTrue(msg)
        with open(os.path.join(self.out, sync.TAPLIST_FILE)) as f:
            self.assertEqual(json.load(f)["updated"], "2026-10-29")

    def test_noop_run_writes_nothing(self):
        sync.run(load("v1"), self.out, "2026-10-29")
        before = self.snapshot()
        changed, msg = sync.run(load("v1"), self.out, "2026-11-30")  # later date, same content
        self.assertFalse(changed)
        self.assertEqual(msg, "")
        self.assertEqual(self.snapshot(), before)  # bytes and mtime untouched, `updated` not bumped

    def test_private_only_edits_in_notion_cause_no_write(self):
        sync.run(load("v1"), self.out, "2026-10-29")
        before = self.snapshot()
        d = load("v1")
        d["kegs"][0]["properties"]["Notes"] = sync_text("dump candidate, 2 gal left")
        d["kegs"][0]["properties"]["Volume Left"] = {"type": "number", "number": 1.5}
        d["recipes"][0]["properties"]["Gemini ID"] = sync_text("changed")
        d["bottles"][0]["properties"]["Quantity"] = {"type": "number", "number": 3}
        d["taps"][0]["properties"]["Notes"] = sync_text("cleaned line")
        changed, _ = sync.run(d, self.out, "2026-11-30")
        self.assertFalse(changed)
        self.assertEqual(self.snapshot(), before)

    def test_real_change_writes_and_bumps_updated(self):
        sync.run(load("v1"), self.out, "2026-10-29")
        changed, msg = sync.run(load("v2"), self.out, "2026-11-02")
        self.assertTrue(changed)
        with open(os.path.join(self.out, sync.TAPLIST_FILE)) as f:
            data = json.load(f)
        self.assertEqual(data["updated"], "2026-11-02")
        self.assertEqual(data["taps"][1]["name"], "Hazy Pale Ale")

    def test_only_changed_file_is_rewritten(self):
        sync.run(load("v1"), self.out, "2026-10-29")
        d = load("v1")
        d["bottles"][0]["properties"]["ABV %"] = {"type": "number", "number": 8.8}
        before = self.snapshot()
        changed, msg = sync.run(d, self.out, "2026-11-02")
        self.assertTrue(changed)
        after = self.snapshot()
        self.assertEqual(after[sync.TAPLIST_FILE], before[sync.TAPLIST_FILE])
        self.assertNotEqual(after[sync.BOTTLES_FILE][0], before[sync.BOTTLES_FILE][0])
        self.assertTrue(msg.startswith("Bottles:"))

    def test_dry_run_writes_nothing(self):
        changed, msg = sync.run(load("v1"), self.out, "2026-10-29", dry_run=True)
        self.assertTrue(changed)
        self.assertEqual(os.listdir(self.out), [])

    def test_hash_ignores_updated_only(self):
        a = {"updated": "x", "taps": [{"name": "A"}]}
        self.assertEqual(sync.content_hash(a), sync.content_hash({"updated": "y", "taps": [{"name": "A"}]}))
        self.assertNotEqual(sync.content_hash(a), sync.content_hash({"taps": [{"name": "B"}]}))


def sync_text(s):
    return {"type": "rich_text", "rich_text": [{"plain_text": s}]}


class CommitMessageTests(unittest.TestCase):
    def test_changeover_message(self):
        old, _ = built("v1")
        new, _ = built("v2")
        self.assertEqual(sync.commit_message(old, new, None, None),
                         "Taplist: Keezer 2 → Hazy Pale Ale; Picnic P3 cleared")

    def test_new_tap_and_details_update(self):
        old = {"taps": [{"tap": "Keezer 1", "name": "A", "abv": 5}]}
        new = {"taps": [{"tap": "Keezer 1", "name": "A", "abv": 6}, {"tap": "Keezer 2", "name": "B"}]}
        self.assertEqual(sync.commit_message(old, new, None, None),
                         "Taplist: Keezer 1 details updated; Keezer 2 → B")

    def test_bottle_counts(self):
        _, old = built("v1")
        _, new = built("v2")
        self.assertEqual(sync.commit_message(None, None, old, new), "Bottles: +1, −1")

    def test_both_sections(self):
        t1, b1 = built("v1")
        t2, b2 = built("v2")
        msg = sync.commit_message(t1, t2, b1, b2)
        self.assertEqual(msg, "Taplist: Keezer 2 → Hazy Pale Ale; Picnic P3 cleared | Bottles: +1, −1")

    def test_unchanged_sections_omitted(self):
        self.assertEqual(sync.commit_message(None, None, None, None), "")


class FailureAndSafetyTests(unittest.TestCase):
    ENV = {"NOTION_TOKEN": "secret_TESTTOKEN", "TAPS_DB_ID": "dbid-taps", "KEGS_DB_ID": "dbid-kegs",
           "RECIPES_DB_ID": "dbid-recipes", "BOTTLES_DB_ID": "dbid-bottles"}

    def run_main(self, args, env):
        err, out = io.StringIO(), io.StringIO()
        with mock.patch("sys.stderr", err), mock.patch("sys.stdout", out):
            code = sync.main(args, env)
        return code, out.getvalue(), err.getvalue()

    def test_missing_env_fails_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as out:
            code, _, err = self.run_main(["--out", out], {})
            self.assertEqual(code, 1)
            self.assertEqual(os.listdir(out), [])
            self.assertIn("NOTION_TOKEN", err)

    def test_notion_error_fails_without_writing_or_leaking(self):
        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b"{}"))
        with tempfile.TemporaryDirectory() as out:
            existing = os.path.join(out, sync.TAPLIST_FILE)
            with open(existing, "w") as f:
                f.write('{"taps": []}\n')
            with mock.patch("urllib.request.urlopen", boom):
                code, so, err = self.run_main(["--out", out], self.ENV)
            self.assertEqual(code, 1)
            self.assertEqual(os.listdir(out), [sync.TAPLIST_FILE])
            with open(existing) as f:
                self.assertEqual(f.read(), '{"taps": []}\n')
            for leak in ("TESTTOKEN", "dbid-"):
                self.assertNotIn(leak, so + err)

    def test_network_error_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as out:
            with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
                code, _, err = self.run_main(["--out", out], self.ENV)
            self.assertEqual(code, 1)
            self.assertEqual(os.listdir(out), [])

    def test_pagination_and_headers(self):
        calls = []

        class Resp:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *a): return json.dumps(self.payload).encode()

        pages = [{"results": [{"id": "a"}], "has_more": True, "next_cursor": "c1"},
                 {"results": [{"id": "b"}], "has_more": False}]

        def fake(req, timeout=None):
            calls.append(req)
            return Resp(pages[len(calls) - 1])

        with mock.patch("urllib.request.urlopen", fake), mock.patch("json.load", lambda r: r.payload):
            rows = sync._notion_query("dbid", "tok", "Taps")
        self.assertEqual([r["id"] for r in rows], ["a", "b"])
        self.assertEqual(json.loads(calls[1].data)["start_cursor"], "c1")
        self.assertEqual(calls[0].get_header("Notion-version"), sync.NOTION_VERSION)
        self.assertEqual({c.get_method() for c in calls}, {"POST"})

    def test_notion_is_only_ever_queried(self):
        with open(os.path.join(ROOT, "scripts", "sync.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(re.findall(r"/databases/%s/(\w+)", src), ["query"])
        for bad in ('"PATCH"', '"PUT"', '"DELETE"', "/pages", "/blocks", "/comments"):
            self.assertNotIn(bad, src)
        self.assertEqual(len(re.findall(r"urllib\.request\.Request\(", src)), 1)

    def test_no_secrets_or_real_ids_in_repo_files(self):
        pat = re.compile(r"(secret_[A-Za-z0-9]{20,}|ntn_[A-Za-z0-9]{20,}|[0-9a-f]{32}|"
                         r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for fn in files:
                if fn.endswith((".json", ".py", ".md", ".yml", ".html")):
                    with open(os.path.join(base, fn), encoding="utf-8") as f:
                        body = f.read()
                    # A pinned action (`uses: owner/repo@<40-hex commit SHA>`) is public, not an ID.
                    body = re.sub(r"uses:\s*\S+@[0-9a-f]{40}", "uses: PINNED", body)
                    self.assertIsNone(pat.search(body), os.path.join(base, fn))


class PageAndWorkflowTests(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_page_renders_from_json_with_no_hardcoded_beers(self):
        html = self.read("index.html")
        self.assertNotIn("TAP_LIST", html)
        self.assertNotIn("BOTTLE_LIST", html)
        self.assertIn("data/taplist.json", html)
        self.assertIn("data/bottles.json", html)

    def test_page_escapes_data_and_does_not_use_viewer_date(self):
        html = self.read("index.html")
        self.assertIn("function esc(", html)
        self.assertNotIn("new Date()", html)

    def test_workflow_schedule_permissions_and_secret_handling(self):
        wf = self.read(".github", "workflows", "sync.yml")
        self.assertRegex(wf, r"(?m)^\s*schedule:")
        self.assertRegex(wf, r"(?m)^\s*workflow_dispatch:")
        self.assertRegex(wf, r"(?m)^permissions:\n  contents: write\n(?!  )")
        self.assertEqual(re.findall(r"\$\{\{\s*([^}]*?)\s*\}\}", wf),
                         ["secrets.NOTION_TOKEN", "vars.TAPS_DB_ID", "vars.KEGS_DB_ID",
                          "vars.RECIPES_DB_ID", "vars.BOTTLES_DB_ID"])
        for uses in re.findall(r"uses:\s*(\S+)", wf):
            self.assertRegex(uses, r"@[0-9a-f]{40}$")  # pinned to a full commit SHA
        self.assertIn("git commit -F", wf)  # message never interpolated into the shell


if __name__ == "__main__":
    unittest.main()
