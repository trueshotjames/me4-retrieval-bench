import io, json, os, sys, tempfile, unittest, zipfile
import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from me4bench import sheetconv as sc  # noqa: E402
from me4bench.datasets import mimotable  # noqa: E402

M = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def xlsx(sheets, merges=None) -> bytes:
    """Minimal .xlsx: sheets = {name: sheetData xml}. Styles: 1 date, 2 time, 3 percent (0.0%)."""
    merges = merges or {}
    wb = (f'<workbook {M} {R}><sheets>' + "".join(
        f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(sheets)) + "</sheets></workbook>")
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(
        f'<Relationship Id="rId{i + 1}" Type="x" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
        + "</Relationships>")
    styles = (f'<styleSheet {M}><numFmts><numFmt numFmtId="164" formatCode="yyyy/m/d;@"/>'
              '<numFmt numFmtId="165" formatCode="0.0%"/></numFmts><cellXfs>'
              '<xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="20"/><xf numFmtId="165"/></cellXfs></styleSheet>')
    sst = f'<sst {M}><si><t>shared</t></si><si><r><t>ri</t></r><r><t>ch</t></r></si></sst>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/sharedStrings.xml", sst)
        for i, (n, data) in enumerate(sheets.items()):
            mc = "".join(f'<mergeCell ref="{m}"/>' for m in merges.get(n, []))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", f'<worksheet {M}><sheetData>{data}</sheetData>'
                       + (f"<mergeCells>{mc}</mergeCells>" if mc else "") + "</worksheet>")
    return buf.getvalue()


ROW1 = ('<row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c><c r="B1" t="s"><v>0</v></c>'
        '<c r="C1" t="s"><v>1</v></c><c r="D1" t="inlineStr"><is><t>a, "b"</t></is></c></row>')
ROW2 = ('<row r="2"><c r="A2"><v>3</v></c><c r="B2"><v>2.5</v></c><c r="C2" s="1"><v>43831</v></c>'
        '<c r="D2" s="2"><v>0.5</v></c><c r="E2" s="3"><v>0.125</v></c><c r="F2" t="b"><v>1</v></c>'
        '<c r="G2" t="e"><v>#DIV/0!</v></c><c r="H2" t="n"></c></row>')


class TestRead(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.xlsx").write_bytes(xlsx({"Data": ROW1 + ROW2, "Second": '<row r="3"><c r="B3"><v>7</v></c></row>'},
                                               {"Data": ["A1:A2"]}))
        self.sheets = sc.read_xlsx(self.tmp / "a.xlsx")

    def test_cells(self):
        s = self.sheets[0]
        self.assertEqual([x["name"] for x in self.sheets], ["Data", "Second"])
        self.assertEqual((s["rows"], s["cols"]), (2, 7))   # H2 is empty
        c = s["cells"]
        self.assertEqual(c[(1, 2)], ("s", "shared"))
        self.assertEqual(c[(1, 3)], ("s", "rich"))
        self.assertEqual(c[(2, 3)], ("d", dt.datetime(2020, 1, 1)))
        self.assertEqual(c[(2, 4)], ("t", dt.time(12, 0)))
        self.assertEqual(sc.display(c[(2, 5)]), "12.5%")
        self.assertEqual(sc.display(c[(2, 6)]), "TRUE")
        self.assertEqual(s["merges"], [(1, 1, 2, 1)])
        self.assertEqual(self.sheets[1]["cells"], {(3, 2): ("n", "7")})

    def test_delimited(self):
        out = sc.to_delimited(self.sheets[0]).decode("utf-8").splitlines()
        self.assertEqual(out[0], 'Name,shared,rich,"a, ""b"""')
        self.assertEqual(out[1], "3,2.5,2020-01-01,12:00,12.5%,TRUE,#DIV/0!")
        self.assertEqual(sc.to_delimited(self.sheets[0], "\t").decode().splitlines()[1].split("\t")[2], "2020-01-01")

    def test_ods(self):
        b = sc.to_ods(self.sheets)
        z = zipfile.ZipFile(io.BytesIO(b))
        first = z.infolist()[0]
        self.assertEqual((first.filename, first.compress_type), ("mimetype", zipfile.ZIP_STORED))
        root = ET.fromstring(z.read("content.xml"))   # well-formed
        t = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
        o = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
        tables = root.iter(t + "table")
        self.assertEqual([x.get(t + "name") for x in tables], ["Data", "Second"])
        cells = list(root.iter(t + "table-cell"))
        top = [c for c in cells if c.get(t + "number-rows-spanned")]
        self.assertEqual(top[0].get(t + "number-rows-spanned"), "2")
        self.assertEqual(len(list(root.iter(t + "covered-table-cell"))), 1)
        self.assertIn("2020-01-01", [c.get(o + "date-value") for c in cells])
        self.assertIn("PT12H00M00S", [c.get(o + "time-value") for c in cells])
        self.assertEqual(b, sc.to_ods(self.sheets))   # reproducible bytes

    def test_xls_ok(self):
        self.assertTrue(sc.xls_ok(self.sheets))
        self.assertFalse(sc.xls_ok([{"name": "x" * 32, "rows": 1, "cols": 1}]))


class TestPlan(unittest.TestCase):
    names = [f"f{i}.xlsx" for i in range(60)]

    def test_split(self):
        flat = set(self.names[::2])
        p = sc.plan(self.names, flat, 0.3)
        self.assertEqual(len(p), 18)
        n = {k: sum(1 for v in p.values() if v == k) for k in ("ods", "csv", "tsv", "xls")}
        self.assertEqual((n["csv"] + n["tsv"], n["xls"], n["ods"]), (6, 6, 6))
        self.assertTrue(all(k in flat for k, v in p.items() if v in ("csv", "tsv")))
        self.assertEqual(p, sc.plan(list(reversed(self.names)), flat, 0.3))   # order does not matter

    def test_limits(self):
        self.assertEqual(sc.plan(self.names, set(), 0), {})
        p = sc.plan(self.names, set(), 0.3, have_xls=False)
        self.assertEqual(set(p.values()), {"ods"})
        p = sc.plan(self.names, set(self.names), 0.3, xls_fit=set())
        self.assertNotIn("xls", p.values())


class TestMimotableMix(unittest.TestCase):
    def test_mix_and_load(self):
        root = Path(tempfile.mkdtemp())
        eng = root / "mimotable" / "english"
        (eng / "spreadsheet").mkdir(parents=True)
        qs = []
        for i in range(10):
            (eng / "spreadsheet" / f"t{i}.xlsx").write_bytes(xlsx({"S": ROW1 + ROW2}))
            tt = "single file, single sheet, simple header"
            q = "In which worksheet is it?" if i == 0 else f"What is in t{i}?"
            qs.append({"spreadsheet_list": [f"./spreadsheet/t{i}.xlsx"], "table_type": tt, "question": q, "answer": "1"})
        (eng / "problems.json").write_text("\n".join(json.dumps(q) for q in qs))
        with mock.patch.object(sc, "find_soffice", return_value=None):
            fmap = mimotable.mix(eng, 0.3, log=lambda s: None)
        self.assertEqual(len(fmap), 3)
        self.assertNotIn(".xls", {Path(v).suffix for v in fmap.values()})
        self.assertNotIn(fmap.get("t0.xlsx", "t0.xlsx")[-4:], (".csv", ".tsv"))   # names a sheet
        self.assertEqual(len(list((eng / "spreadsheet").iterdir())), 10)
        rows = mimotable.load(root)
        for r in rows:
            for g in r["gold_docs"]:
                self.assertTrue((root / "mimotable" / g).exists(), g)
        self.assertEqual(sorted(t for r in rows for t in r["meta"]["file_types"]).count("xlsx"), 7)

    def test_off(self):
        with mock.patch.dict(os.environ, {"ME4_MIMOTABLE_MIXED_SHARE": "0"}):
            self.assertEqual(mimotable.mixed_share(), 0.0)


if __name__ == "__main__":
    unittest.main()
