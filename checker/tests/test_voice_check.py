#!/usr/bin/env python3
"""Tests for checker/voice_check.py.

    python3 -m unittest discover -s checker/tests

Every fail code and every STE code has a fixture that must fire. Every false
alarm I found while tuning the checker on real replies has a fixture that must
stay silent, so a later tweak cannot bring it back. The CLI is run as a
subprocess to check the JSON keys and exit codes the mod relies on.
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHECKER))
import voice_check as vc  # noqa: E402

CLI = CHECKER / "voice_check.py"


def chat(text):
    return vc.run(text, "chat")


def codes(res, key="fails"):
    return [x["code"] for x in res[key]]


def ste_codes(res, kind):
    return [i["code"] for i in res["ste"][kind]["issues"]]


# A reply in the claude.ai house style: the answer first, plain sentences,
# one question at most. Contractions, an em dash, money and a date are all fine.
CLEAN = """Yes, the invoice went out this morning and Dana already replied.

She paid the $380 on 1 Oct, so nothing is left to chase. I've knocked out the
two fixes she asked for \u2014 the vendor list and the late-fee line \u2014 and both
show up in her app now.

The next thing on her side is the Acme Supply invoice, which goes in on her next
run. Want me to check her log for it tomorrow?"""


class TestCleanReply(unittest.TestCase):
    def test_clean_reply_passes(self):
        res = chat(CLEAN)
        self.assertTrue(res["ok"], res["fails"])
        self.assertEqual(res["notes"], [])
        self.assertEqual(res["ste"]["prose"]["score"], 1.0)
        self.assertEqual(res["ste"]["prose"]["issues"], [])

    def test_em_dash_is_fine_in_chat(self):
        self.assertTrue(chat("It works \u2014 both bills are in QuickBooks now.")["ok"])

    def test_urls_money_dates_are_fine(self):
        res = chat("Here's the link: https://preview-7e84e030.example.app and the brief at "
                   "brief-claims-ba99189c.example.app. She paid $2,500 on 9/28 at 8:15 AM.")
        self.assertTrue(res["ok"], res["fails"])


class TestChatFails(unittest.TestCase):
    def test_path_absolute(self):
        res = chat("I saved it to /opt/project/artifacts/plan.html for you.")
        self.assertIn("PATH", codes(res))

    def test_path_home(self):
        self.assertIn("PATH", codes(chat("The script lives in ~/.config/scripts now.")))

    def test_path_relative_with_extension(self):
        res = chat("I logged it in clients/acme/notes.md and moved on.")
        self.assertIn("PATH", codes(res))
        self.assertEqual(res["fails"][0]["excerpt"], "clients/acme/notes.md")

    def test_path_markdown_link_to_local_file(self):
        self.assertIn("PATH", codes(chat("The plan is [here](artifacts/plan.html).")))

    def test_web_and_mail_links_are_not_paths(self):
        res = chat("The plan is [here](https://example.com/plan) and you can [email her](mailto:dana@example.com).")
        self.assertNotIn("PATH", codes(res))

    def test_bare_filename_is_not_a_path(self):
        self.assertTrue(chat("Attach Print Station guide.pdf from your Desktop folder.")["ok"])

    def test_path_in_blockquoted_bug_report_is_not_flagged(self):
        # Former false alarm: a quoted error message in a bug report for a developer.
        res = chat('Here is the note for the developer:\n\n> The helper stops with "undeclared pack '
                   'file: tools/__pycache__/build_pack.pyc" on a fresh download.')
        self.assertNotIn("PATH", codes(res))

    def test_hash_commit(self):
        res = chat("It's live on main 56a8083, so the site has the new card.")
        self.assertIn("HASH", codes(res))

    def test_hash_uuid_and_tool_id(self):
        res = chat("Session 77d2c669-e790-4bf4-8c69-32170f325335 ran toolu_01AbCdEf for it.")
        self.assertEqual(codes(res).count("HASH"), 2)

    def test_command_fence(self):
        res = chat("Run this:\n\n```bash\npython3 scripts/build_home.py\n```\n")
        self.assertIn("COMMAND", codes(res))

    def test_command_dollar_line(self):
        self.assertIn("COMMAND", codes(chat("Type this in the terminal:\n$ open -a Chrome\n")))

    def test_money_line_is_not_a_command(self):
        self.assertTrue(chat("The total:\n$ 2,500 setup plus $300 a month.")["ok"])

    def test_opener(self):
        for opener in ("Certainly! Here it is.", "Absolutely, done.", "Great question. It works.",
                       "Sure thing, I'll do it.", "I'd be happy to help with that."):
            self.assertIn("OPENER", codes(chat(opener)), opener)

    def test_banned_eat_filler_ramp(self):
        self.assertIn("BANNED", codes(chat("That step eats an hour every Monday.")))
        self.assertIn("BANNED", codes(chat("We can leverage her rules for the next client.")))
        self.assertIn("BANNED", codes(chat("New hires need a long ramp before they stop asking.")))

    def test_banned_skips_quoted_mentions(self):
        # Former false alarm: copy under discussion, in quotes.
        res = chat('The close is now just "I\'d love to talk!" and I cut "eats time" from line two.')
        self.assertNotIn("BANNED", codes(res))

    def test_quietly_is_not_banned_in_chat(self):
        self.assertTrue(chat("The job ran quietly in the background overnight.")["ok"])

    def test_great_and_treat_are_not_eat(self):
        self.assertTrue(chat("Great work on the treatment plan, it reads well.")["ok"])


class TestDrafts(unittest.TestCase):
    def test_fenced_email_draft_with_em_dash_fails(self):
        reply = ("Here's the draft:\n\n```\nHey Dana,\n\nThe update is ready \u2014 just open the app "
                 "once and click Close so it installs, and leave the Acme Supply invoice where it is.\n\n"
                 "Thanks!\nSam\n```\n\nWant me to put it in your Gmail?")
        res = chat(reply)
        self.assertIn("DRAFT", codes(res))
        self.assertIn("em dash", res["fails"][0]["msg"])

    def test_fenced_draft_filler_fails(self):
        reply = ("```email\nHi Priya, I wanted to reach out about the time-off system and the 2027 records "
                 "we built for the office this week.\n```")
        self.assertIn("DRAFT", codes(chat(reply)))

    def test_short_or_code_fence_is_not_a_draft(self):
        self.assertTrue(chat("```\n{\"a\": 1, \"b\": 2}\n```\n")["ok"])

    def test_unfenced_draft_between_rules(self):
        reply = ("Here it is again.\n\n---\n\nHey Dana,\n\nThe fix is in \u2014 open the app once.\n\n"
                 "Thanks!\nSam\n\n---\n\nWant me to send it?")
        self.assertIn("DRAFT", codes(chat(reply)))

    def test_unfenced_draft_in_blockquote(self):
        reply = ("The outreach draft:\n\n> **Subject:** admin work\n>\n> Hey Morgan,\n>\n"
                 "> If your staff could use a breather, I'd love to talk with you.\n")
        res = chat(reply)
        self.assertIn("DRAFT", codes(res))
        self.assertNotIn("BANNED", codes(res))


class TestSTEWordCount(unittest.TestCase):
    def test_rule_8_counting(self):
        self.assertEqual(vc.ste_words("The partial-day bug is fixed."), 5)
        self.assertEqual(vc.ste_words("Send it to Riverside Family Dental today."), 5)
        self.assertEqual(vc.ste_words("Dana saved 30 minutes (on a busy Monday) today."), 5)
        self.assertEqual(vc.ste_words('She said "it works fine now" twice.'), 4)
        self.assertEqual(vc.ste_words("Click **Save and check the connection** now."), 3)

    def test_long_status_sentence(self):
        # A real-shaped status line: 27 words by rule 8, so it fails LONG.
        s = ("Ana, Ben, Chris, and Dee were all hitting the same root cause: the system "
             "was mishandling the time format and falling back to a default 9am start.")
        self.assertGreater(vc.ste_words(s), 25)


class TestSTECodes(unittest.TestCase):
    def test_long_prose(self):
        s = ("The permission guard is now blocking anything tied to the personal Gmail send, including "
             "a read of the send log, so I won't keep pushing on it today.")
        self.assertIn("LONG", ste_codes(chat(s), "prose"))

    def test_long_step(self):
        res = vc.run("Go back to the left menu of the page's admin view, the one with the posts, the "
                     "photos and the analytics tab.", "steps")
        self.assertIn("LONG", codes(res))

    def test_passive(self):
        self.assertIn("PASSIVE", ste_codes(chat("The send was refused by the permission guard."), "prose"))
        self.assertIn("PASSIVE", ste_codes(chat("Every clip is checked for size and length."), "prose"))
        self.assertIn("PASSIVE", ste_codes(chat("Captions update before any caption is drawn."), "prose"))

    def test_passive_states_are_not_flagged(self):
        # Former false alarms: present-tense status words.
        for s in ("Everything is saved and pushed.", "The guide in your folder is untouched.",
                  "Wait until I confirm version 34 is published.", "Nothing is booked for today.",
                  "It's fixed and the call is done.", "The test itself was broken."):
            self.assertNotIn("PASSIVE", ste_codes(chat(s), "prose"), s)

    def test_active_perfect_is_not_passive(self):
        self.assertNotIn("PASSIVE", ste_codes(chat("She's sent the email to Priya."), "prose"))

    def test_semicolon(self):
        self.assertIn("SEMICOLON", ste_codes(chat("The app skips a $0 bill; Dana enters it by hand."), "prose"))

    def test_multi(self):
        res = vc.run("Open Print Station and click Close.", "steps")
        self.assertIn("MULTI", codes(res))
        self.assertIn("MULTI", codes(vc.run("Test it on a copy of her file, then publish it.", "steps")))

    def test_multi_ignores_button_names_and_noun_lists(self):
        # Former false alarm: the button is called "Save and check the connection".
        self.assertTrue(vc.run("Click **Save and check the connection**.", "steps")["ok"])
        self.assertTrue(vc.run("Attach the guide and the invoice.", "steps")["ok"])

    def test_condition(self):
        res = vc.run("Click Replace if it asks.", "steps")
        self.assertIn("CONDITION", codes(res))
        self.assertTrue(vc.run("If it asks, click Replace.", "steps")["ok"])

    def test_condition_skips_whether_and_loops(self):
        # Former false alarms: "if" meaning whether, "until" ending a loop.
        self.assertTrue(vc.run("Ask her if she enters that one.", "steps")["ok"])
        self.assertTrue(vc.run("Wait until the menu appears.", "steps")["ok"])
        self.assertTrue(vc.run("Click Close once.", "steps")["ok"])
        self.assertTrue(vc.run("Send it after the call.", "steps")["ok"])

    def test_soft_notes(self):
        self.assertIn("ING", codes(chat("Refresh the page and the four should be gone, leaving only Acme."), "notes"))
        self.assertIn("THISVERB", codes(chat("This is the best idea in the pack."), "notes"))
        self.assertIn("LATIN", codes(chat("Do the two questions (vendors etc.) first."), "notes"))

    def test_ing_skips_gerund_lists(self):
        res = chat("**Prevention:** checking coverage before visits, and later, helping the desk pick a plan.")
        self.assertNotIn("ING", codes(res, "notes"))

    def test_contractions_are_never_flagged(self):
        res = chat("I'm on it. We'll send it today and it's fine, they're ready.")
        self.assertTrue(res["ok"])
        self.assertEqual(res["ste"]["prose"]["issues"], [])


class TestChatNotes(unittest.TestCase):
    def test_format_header_in_short_reply(self):
        self.assertIn("FORMAT", codes(chat("## Status\n\nThe invoice went out today."), "notes"))

    def test_format_bullet_wall(self):
        reply = ("- The invoice went out to Dana this morning with the guide.\n"
                 "- Priya has the time-off note and the two approvals waiting.\n"
                 "- The site is live with the new card and the founders section.\n")
        self.assertIn("FORMAT", codes(chat(reply), "notes"))

    def test_format_allows_parallel_lists(self):
        # Former false alarms: a lead sentence plus a list, steps, short data rows.
        led = ("Yes, everything is logged and saved. You're good to close the session.\n\n"
               "- **Client notes:** the full story of the time-off work and what's open.\n"
               "- **Decision log:** the build isn't billed separately this month.\n"
               "- **Activity log:** a one-line summary of today's work.\n")
        rows = "Check the table:\n- Office Depot $179.41, paid\n- Amazon $103.68, paid\n- Staples $293.05\n"
        steps = "1. Click **Open the Invoices folder**.\n2. Open **Filing Rules.txt**.\n3. Save it.\n"
        for reply in (led, rows, steps):
            self.assertNotIn("FORMAT", codes(chat(reply), "notes"), reply)

    def test_questions(self):
        res = chat("Want me to send it now? And should I copy Priya?")
        self.assertIn("QUESTIONS", codes(res, "notes"))
        self.assertEqual(res["stats"]["questions"], 2)

    def test_questions_skip_quoted_and_prepared(self):
        reply = ('The email ends on "Are some insurers slow to pay you?" now.\n\n'
                 "**Questions to bring to the call:**\n- What does she use today?\n"
                 "- How many claims are over 60 days?\n- Would she co-design it?\n\n"
                 "**Is Dana an admin?** No, going by her answer.\n\nWant me to start?")
        self.assertNotIn("QUESTIONS", codes(chat(reply), "notes"))

    def test_para6_note_in_chat(self):
        para = " ".join(f"Sentence number {i} is here." for i in range(1, 8))
        res = chat(para)
        self.assertIn("PARA6", codes(res, "notes"))
        self.assertTrue(res["ok"])


GUIDE_HTML = """<!doctype html><html><head><title>Guide</title><style>.x{color:red}</style></head>
<body><nav>Home Steps</nav><h1>Print Station</h1>
<p>Print Station reads the bills in your Invoices folder and puts them into QuickBooks.</p>
<ol>
  <li><p>Double-click Print Station on your Desktop.</p></li>
  <li>Click <b>Read the Invoices folder</b>.</li>
  <li>Click Go if the table looks right.</li>
</ol>
<div class="path"><div class="step"><span class="num">STEP 04</span><h4>Close it</h4>
<p>Click <b>Close</b> and wait for the window to go away.</p></div></div>
<ul><li class="ck-item" data-id="s1"><label><input type="checkbox" id="s1"><span class="ck-text">Check the log.</span></label><p class="ck-detail">The log is a list of every file it moved.</p></li></ul>
</body></html>"""


class TestCompactGuides(unittest.TestCase):
    """Guides are compact (a two-action fix may share a line), checklists stay
    strict. The compact guide read better in both blind comparisons."""

    TWO = "1. Close Invoice Log in Excel, then click Try again.\n2. Open File Explorer.\n"
    THREE = "1. Close Invoice Log in Excel, then click Try again, then click Close.\n"

    def test_guide_allows_one_paired_action(self):
        res = vc.run(self.TWO, "guide")
        self.assertTrue(res["ok"])
        self.assertEqual(ste_codes(res, "steps"), [])

    def test_guide_refuses_three_actions(self):
        res = vc.run(self.THREE, "guide")
        self.assertFalse(res["ok"])
        self.assertEqual(codes(res), ["MULTI"])

    def test_checklist_stays_strict(self):
        res = vc.run("Close Invoice Log in Excel, then click Try again.", "steps")
        self.assertFalse(res["ok"])
        self.assertEqual(codes(res), ["MULTI"])


class TestHTMLAndSurfaces(unittest.TestCase):
    def test_html_guide_steps(self):
        res = vc.run(GUIDE_HTML, "guide", html=True)
        steps = res["ste"]["steps"]
        self.assertEqual(steps["total"], 5)  # 3 <ol> items, 1 kit step, 1 checkbox
        # "Click Close and wait ..." is a two-action fix: fine in a compact guide.
        self.assertEqual(sorted(i["code"] for i in steps["issues"]), ["CONDITION"])
        self.assertFalse(res["ok"])
        self.assertEqual(sorted(codes(res)), ["CONDITION"])
        # The checkbox detail and the intro are prose, not steps.
        self.assertEqual(res["ste"]["prose"]["total"], 2)

    def test_guide_para6_fails(self):
        para = " ".join(f"Sentence number {i} is here." for i in range(1, 8))
        self.assertIn("PARA6", codes(vc.run(para, "guide")))

    def test_guide_runs_copy_check_artifact_profile(self):
        self.assertIn("DASH", codes(vc.run("It works \u2014 really.", "guide")))

    def test_email_surface_uses_copy_check_email_fails(self):
        res = vc.run("Hey Dana,\n\nIt works \u2014 and it eats nothing.\n\nThanks!\nSam", "email")
        self.assertIn("DASH", codes(res))
        self.assertIn("BANNED", codes(res))

    def test_email_steps_report_only(self):
        res = vc.run("Hey Dana,\n\n1. Open the app and click Close.\n\nThanks!\nSam", "email")
        self.assertTrue(res["ok"], res["fails"])
        self.assertIn("MULTI", ste_codes(res, "steps"))

    def test_social_and_artifact_report_only(self):
        for surface in ("social", "artifact"):
            res = vc.run("The send was refused by the guard; nothing went out.", surface)
            self.assertTrue(res["ok"], surface)
            self.assertIn("SEMICOLON", ste_codes(res, "prose"))

    def test_steps_surface_one_per_line(self):
        res = vc.run("Open the app.\nClick Close.\nWait for the window to close.\n", "steps")
        self.assertTrue(res["ok"])
        self.assertEqual(res["ste"]["steps"]["total"], 3)
        self.assertEqual(res["ste"]["steps"]["score"], 1.0)

    def test_chat_steps_are_reported_not_failed(self):
        res = chat("Two things for Dana:\n\n1. Open Print Station and click Close.\n2. Move the PDF out.\n")
        self.assertTrue(res["ok"])
        self.assertIn("MULTI", ste_codes(res, "steps"))
        self.assertEqual(res["ste"]["steps"]["total"], 2)

    def test_product_names_are_not_orders(self):
        res = chat("- Print Station is installed on her PC.\n- Select Optical orders go in as card expenses.\n")
        self.assertEqual(res["ste"]["steps"]["total"], 0)

    def test_empty_scores_are_one(self):
        res = vc.run("", "chat")
        self.assertEqual(res["ste"]["prose"]["score"], 1.0)
        self.assertEqual(res["ste"]["steps"]["score"], 1.0)


class TestCLI(unittest.TestCase):
    KEYS = {"surface", "ok", "fails", "notes", "verify", "ste", "stats"}

    def run_cli(self, args, stdin=None):
        return subprocess.run([sys.executable, str(CLI), *args], input=stdin,
                              capture_output=True, text=True, timeout=30)

    def test_json_shape_and_exit_codes(self):
        ok = self.run_cli(["-", "--surface", "chat", "--json"], CLEAN)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        data = json.loads(ok.stdout)
        self.assertEqual(set(data), self.KEYS)
        self.assertEqual(set(data["ste"]), {"prose", "steps"})
        for kind in ("prose", "steps"):
            self.assertEqual(set(data["ste"][kind]), {"total", "pass", "score", "target", "issues"})
        self.assertEqual(data["ste"]["prose"]["target"], 0.8)
        self.assertEqual(data["ste"]["steps"]["target"], 1.0)
        self.assertEqual(set(data["stats"]), {"words", "sentences", "bullets", "headers", "questions"})

        bad = self.run_cli(["-", "--surface", "chat", "--json"], "Certainly! See clients/acme/notes.md.")
        self.assertEqual(bad.returncode, 1)
        data = json.loads(bad.stdout)
        self.assertFalse(data["ok"])
        for f in data["fails"]:
            self.assertEqual(set(f), {"code", "msg", "excerpt"})
        self.assertEqual({f["code"] for f in data["fails"]}, {"OPENER", "PATH"})

    def test_html_file_detected_by_extension(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "guide.html"
            p.write_text(GUIDE_HTML, encoding="utf-8")
            out = self.run_cli([str(p), "--surface", "guide", "--json"])
            self.assertEqual(json.loads(out.stdout)["ste"]["steps"]["total"], 5)
            self.assertEqual(out.returncode, 1)

    def test_readable_output(self):
        out = self.run_cli(["-", "--surface", "chat"], "Certainly! It's done.")
        self.assertIn("FAIL   OPENER", out.stdout)
        self.assertTrue(out.stdout.startswith("voice_check"))

    def test_fast_on_a_1500_word_reply(self):
        reply = (CLEAN + "\n\n") * 20
        self.assertGreater(len(reply.split()), 1500)
        t = time.time()
        out = self.run_cli(["-", "--surface", "chat", "--json"], reply)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertLess(time.time() - t, 1.0)


if __name__ == "__main__":
    unittest.main()
