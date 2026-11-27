import gzip, http.server, json, sys, tempfile, threading, unittest, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from me4bench.datasets import base, frames  # noqa: E402
from me4bench import wikitext  # noqa: E402

CALLS = []
PAGES = {"Alan Turing": "'''Alan Turing''' was a [[mathematician]].\n== Life ==\nBorn {{birth date|1912|6|23}}.",
         "Eiffel Tower": "The '''tower''' is {{convert|330|m|ft}} tall.\n{| class=wikitable\n! A !! B\n|-\n| 1 || 2\n|}"}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        CALLS.append(q)
        if len(CALLS) == 1:                       # first call: maxlag, must be retried
            body, extra = {"error": {"code": "maxlag", "info": "lagged"}}, {"Retry-After": "1"}
        else:
            extra = {}
            titles = q["titles"].split("|")
            norm, redir, pages = [], [], []
            for t in titles:
                f = t
                if f[0].islower():
                    norm.append({"from": f, "to": f[0].upper() + f[1:]})
                    f = f[0].upper() + f[1:]
                if f == "Turing":
                    redir.append({"from": f, "to": "Alan Turing"})
                    f = "Alan Turing"
                if f in PAGES:
                    pages.append({"title": f, "revisions": [{"revid": 7, "timestamp": "2026-01-01T00:00:00Z",
                                                             "slots": {"main": {"content": PAGES[f]}}}]})
                else:
                    pages.append({"title": f, "missing": True})
            # split the reply in two (continuation), like a large real batch
            if "rvcontinue" not in q:
                body = {"continue": {"rvcontinue": "x", "continue": "||"},
                        "query": {"normalized": norm, "redirects": redir, "pages": pages[:1]}}
            else:
                body = {"query": {"pages": pages[1:]}}
        raw = gzip.compress(json.dumps(body).encode())
        self.send_response(200)
        self.send_header("Content-Encoding", "gzip")
        for k, v in extra.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Ctx(base.Ctx):
    def get(self, ds, url, dest, validate=None, progress=None):
        return dest


class T(unittest.TestCase):
    def test_batched_fetch(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = frames.WIKI_API, frames.GAP
        frames.WIKI_API, frames.GAP = f"http://127.0.0.1:{srv.server_port}/w/api.php", 0.0
        try:
            root = Path(tempfile.mkdtemp())
            (root / "frames").mkdir()
            links = ["https://en.wikipedia.org/wiki/Turing", "https://en.wikipedia.org/wiki/eiffel_Tower",
                     "https://en.wikipedia.org/wiki/Nope"]
            hdr = "Prompt\tAnswer\treasoning_types\t" + "\t".join(f"wikipedia_link_{i}" for i in range(1, 4))
            (root / "frames" / "test.tsv").write_text(hdr + "\nQ?\tA\tNumerical reasoning\t" + "\t".join(links) + "\n",
                                                      encoding="utf-8")
            ctx = Ctx(root=root, log=lambda *a: None)
            frames.fetch(ctx)        # 1 of 3 missing > 2%: raises after writing the rest
        except Exception as e:  # noqa: BLE001
            self.assertIn("Too many", str(e))
        finally:
            frames.WIKI_API, frames.GAP = old
            srv.shutdown()
        self.assertEqual(len([c for c in CALLS if "titles" in c and c["titles"].count("|") == 2]), 3)  # retry + 2 parts
        self.assertEqual(CALLS[-1]["maxlag"], "5")
        files = {p.name: p for p in (root / "frames" / "articles").iterdir()}
        self.assertEqual(len(files), 2)
        failed = json.loads((root / "frames" / "_failed.json").read_text())
        self.assertEqual([f[0] for f in failed], [links[2]])
        revs = json.loads((root / "frames" / "_revisions.json").read_text())
        self.assertEqual(revs[links[0]], 7)
        md = next((p for n, p in files.items() if n.startswith("Turing") and n.endswith(".md")), None)
        if md:
            t = md.read_text(encoding="utf-8")
            self.assertIn("**Alan Turing** was a mathematician", t)
            self.assertIn("June 23, 1912", t)

    def test_wikitext_basics(self):
        md = wikitext.to_markdown(PAGES["Eiffel Tower"] + "<ref>{{cite web|url=x}}</ref>\n[[Category:X]]\n"
                                  "== References ==\n{{reflist}}\n")
        self.assertIn("330 m tall", md)
        self.assertIn("| A | B |", md)
        self.assertIn("| 1 | 2 |", md)
        self.assertNotIn("Category", md)
        self.assertNotIn("References", md)
        self.assertNotIn("cite", md)
        md = wikitext.to_markdown("{{Infobox person\n| name = X\n| image = a.jpg\n| born = [[Paris|City]]\n}}\nText")
        self.assertIn("- born: City", md)
        self.assertNotIn("a.jpg", md)


if __name__ == "__main__":
    unittest.main()
