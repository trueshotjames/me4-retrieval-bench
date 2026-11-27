import io
import unittest
import xml.dom.minidom as minidom
import zipfile

from me4bench import convert as c

MD = ("# Title\n\nSource: u\n\n## Part\n\nHello **bold** and *it* a & b < c.\n\n- one\n- two\n\n"
      "| A | B\\|x |\n|---|---|\n| 1 | **2** |\n\n1. first\n")


class T(unittest.TestCase):
    def test_choice_is_fixed_and_share_is_close(self):
        titles = [f"Article {i}" for i in range(5000)]
        a = [c.choose_format(t, .3) for t in titles]
        self.assertEqual(a, [c.choose_format(t, .3) for t in titles])
        mixed = [x for x in a if x != "md"]
        self.assertAlmostEqual(len(mixed) / len(a), .3, delta=.03)
        for f in c.FORMATS:
            self.assertGreater(mixed.count(f), len(mixed) * .2)
        self.assertNotIn("pdf", set(a))
        self.assertNotIn("rtf", set(a))
        self.assertEqual({c.choose_format(t, 0) for t in titles}, {"md"})

    def test_all_formats_are_valid(self):
        for f in c.FORMATS:
            data = c.convert(MD, f, "T")
            self.assertTrue(data)
            if f in ("docx", "odt"):
                z = zipfile.ZipFile(io.BytesIO(data))
                self.assertIsNone(z.testzip())
                minidom.parseString(z.read("word/document.xml" if f == "docx" else "content.xml"))
        self.assertIn(b"a &amp; b", c.convert(MD, "html"))
        self.assertIn(b"Hello bold and it", c.convert(MD, "txt"))

    def test_odt_mimetype_first_and_stored(self):
        z = zipfile.ZipFile(io.BytesIO(c.convert(MD, "odt")))
        self.assertEqual(z.infolist()[0].filename, "mimetype")
        self.assertEqual(z.infolist()[0].compress_type, zipfile.ZIP_STORED)

    def test_reproducible_bytes(self):
        self.assertEqual(c.convert(MD, "docx"), c.convert(MD, "docx"))

    def test_control_chars_do_not_break_xml(self):
        z = zipfile.ZipFile(io.BytesIO(c.convert("x \x07 y", "docx")))
        minidom.parseString(z.read("word/document.xml"))

    def test_off_switch(self):
        import os
        from me4bench.datasets import frames
        os.environ["ME4_FRAMES_MIXED_SHARE"] = "0"
        try:
            self.assertTrue(all(frames.article_file(f"https://en.wikipedia.org/wiki/A{i}").endswith(".md")
                                for i in range(200)))
        finally:
            del os.environ["ME4_FRAMES_MIXED_SHARE"]


if __name__ == "__main__":
    unittest.main()
