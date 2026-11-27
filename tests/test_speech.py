"""Tests for me4bench.speech (plain unittest)."""
from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from me4bench import slots  # noqa: E402
from me4bench.speech import (CLARIFY_REPLY, int_to_words, normalise,  # noqa: E402
                             ordinal_words, year_words)

CASES = [
    # years
    ("In 1601 the", "In sixteen oh one the"),
    ("In 2023, sales", "In twenty twenty-three, sales"),
    ("2005", "two thousand five"),
    ("2000", "two thousand"),
    ("1900", "nineteen hundred"),
    ("since 2010", "since twenty ten"),
    ("in May 2023", "in May twenty twenty-three"),
    ("1990s", "nineteen nineties"),
    ("the 80s", "the eighties"),
    ("1990-1995", "nineteen ninety to nineteen ninety-five"),
    # integers
    ("There were 1,234,567 people", "There were one million two hundred thirty-four "
     "thousand five hundred sixty-seven people"),
    ("1601 people", "one thousand six hundred one people"),
    ("42", "forty-two"),
    ("0", "zero"),
    ("007", "zero zero seven"),
    ("pages 5-10", "pages five to ten"),
    # decimals, percents
    ("3.5", "three point five"),
    ("0.25", "zero point two five"),
    ("Version 3.5.1", "Version three point five point one"),
    ("12.5% of", "twelve point five percent of"),
    ("45 %", "forty-five percent"),
    # currency
    ("$5.2 million", "five point two million dollars"),
    ("$1", "one dollar"),
    ("$3.25", "three dollars and twenty-five cents"),
    ("$0.50", "fifty cents"),
    ("$1,200", "one thousand two hundred dollars"),
    ("€40", "forty euros"),
    ("£2bn", "two billion pounds"),
    # dates
    ("2023-05-03", "May third, twenty twenty-three"),
    ("May 3, 2023", "May third, twenty twenty-three"),
    ("May 3rd, 2023", "May third, twenty twenty-three"),
    ("on 3 May 2023", "on May third, twenty twenty-three"),
    ("Jan. 21, 1999", "January twenty-first, nineteen ninety-nine"),
    ("by March 1", "by March first"),
    # ordinals
    ("the 21st century", "the twenty-first century"),
    ("3rd", "third"),
    ("the 100th", "the one hundredth"),
    ("12th", "twelfth"),
    # symbols
    ("a & b", "a and b"),
    ("and/or", "and or"),
    ("1/2 of 3/4", "one half of three quarters"),
    ("x = 4", "x equals four"),
    ("at 5:30 pm", "at five thirty p m"),
    ("10km", "ten kilometres"),
    ("Tabular, 2–3 articles", "Tabular, two to three articles"),
    ("item #3", "item number three"),
    # codes, files, urls
    ("ABC-4", "A B C dash four"),
    ("GPT-4 and COVID-19", "G P T dash four and COVID dash nineteen"),
    ("Q3 revenue", "Q three revenue"),
    ("report.pdf", "report dot P D F"),
    ("config_loader.py", "config underscore loader dot P Y"),
    ("Open settings.yaml", "Open settings dot yaml"),
    ("https://www.example.com/path?q=1.", "example dot com."),
    ("see bbc.co.uk", "see bbc dot co dot uk"),
    ("mail a@b.com", "mail a at b dot com"),
]


class TestNormalise(unittest.TestCase):
    def test_cases(self):
        for src, want in CASES:
            with self.subTest(src=src):
                self.assertEqual(normalise(src), want)

    def test_no_digits_left(self):
        for src, _ in CASES:
            self.assertIsNone(re.search(r"\d", normalise(src)), src)

    def test_idempotent(self):
        for src, _ in CASES:
            once = normalise(src)
            self.assertEqual(normalise(once), once, src)

    def test_all_stratum_labels_speakable(self):
        for ds, strata in slots.SLOTS.items():
            for st in strata:
                self.assertIsNone(re.search(r"\d", normalise(st)), st)

    def test_empty(self):
        self.assertEqual(normalise(""), "")
        self.assertEqual(normalise(None), "")

    def test_plain_text_unchanged(self):
        s = "Who wrote the novel that the film was based on?"
        self.assertEqual(normalise(s), s)

    def test_question_sentence(self):
        q = "What was the revenue of ACME in 2019, in $ millions, per the 10-K?"
        out = normalise(q)
        self.assertIn("twenty nineteen", out)
        self.assertNotRegex(out, r"\d")


class TestWords(unittest.TestCase):
    def test_int_to_words(self):
        self.assertEqual(int_to_words(15), "fifteen")
        self.assertEqual(int_to_words(101), "one hundred one")
        self.assertEqual(int_to_words(1000000), "one million")
        self.assertEqual(int_to_words(2_500_000_000), "two billion five hundred million")
        self.assertEqual(int_to_words(-7), "minus seven")

    def test_ordinals(self):
        self.assertEqual(ordinal_words(1), "first")
        self.assertEqual(ordinal_words(2), "second")
        self.assertEqual(ordinal_words(20), "twentieth")
        self.assertEqual(ordinal_words(33), "thirty-third")
        self.assertEqual(ordinal_words(1000), "one thousandth")

    def test_years(self):
        self.assertEqual(year_words(1601), "sixteen oh one")
        self.assertEqual(year_words(2023), "twenty twenty-three")
        self.assertEqual(year_words(1066), "ten sixty-six")
        self.assertEqual(year_words(2009), "two thousand nine")
        self.assertEqual(year_words(1800), "eighteen hundred")

    def test_clarify_reply(self):
        self.assertEqual(CLARIFY_REPLY, "No extra details. Answer from the files, or say not found.")


if __name__ == "__main__":
    unittest.main()
