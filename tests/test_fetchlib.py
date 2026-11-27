import hashlib, http.server, sys, tempfile, threading, unittest, zipfile, io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from me4bench import fetchlib as fl  # noqa: E402

DATA = b"x" * 3_000_000


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        rng = self.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        body = DATA[start:]
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(DATA)-1}/{len(DATA)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class T(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/f"

    def tearDown(self):
        self.srv.shutdown()

    def test_download_and_resume_and_checksum(self):
        d = Path(tempfile.mkdtemp())
        (d / "f.part").write_bytes(DATA[:1_000_000])
        sha = hashlib.sha256(DATA).hexdigest()
        self.assertEqual(fl.download(self.url, d / "f", expect_sha=sha, quiet=True), sha)
        self.assertEqual((d / "f").read_bytes(), DATA)
        with self.assertRaises(fl.FetchError):
            fl.download(self.url, d / "g", expect_sha="0" * 64, quiet=True)

    def test_zip_slip(self):
        d = Path(tempfile.mkdtemp())
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("top/a.txt", "a")
            z.writestr("top/../../evil.txt", "e")
        (d / "z.zip").write_bytes(b.getvalue())
        n = fl.safe_extract_zip(d / "z.zip", d / "out")
        self.assertEqual(n, 1)
        self.assertFalse((d / "evil.txt").exists())

    def test_progress_counts_bytes_single_and_parallel(self):
        d = Path(tempfile.mkdtemp())
        logs = []
        with fl.Progress("one", log=logs.append, interval=0.05) as pg:
            fl.download(self.url, d / "a", progress=pg)
        self.assertEqual(pg.bytes, len(DATA))
        self.assertEqual(pg.total_bytes, len(DATA))
        self.assertIn("100%", pg.line())
        from concurrent.futures import ThreadPoolExecutor
        (d / "b.part").write_bytes(DATA[:1_000_000])   # resumed: only the rest is counted
        with fl.Progress("many", total_items=3, log=logs.append, interval=0.05) as pg:
            def one(n):
                fl.download(self.url, d / n, progress=pg)
                pg.item_done()
            with ThreadPoolExecutor(3) as ex:
                list(ex.map(one, ["b", "c", "e"]))
        self.assertEqual(pg.done, 3)
        self.assertEqual(pg.bytes, 3 * len(DATA) - 1_000_000)
        self.assertIn("3/3 (100%)", pg.line())
        self.assertTrue(logs)   # final line is logged when not a terminal

    def test_mmlb_parallel_fetch_with_fallback(self):
        from me4bench.datasets import base, mmlb
        import json as _j
        root = Path(tempfile.mkdtemp())
        names = [f"d{i}.pdf" for i in range(9)]
        seen = []

        def fake_download(url, dest, expect_sha=None, validate=None, progress=None, **k):
            seen.append(url)
            if url.endswith("samples.json"):
                body = _j.dumps([{"doc_id": n} for n in names]).encode()
            elif "media." in url and url.endswith("d3.pdf"):
                raise fl.FetchError("media 404")
            else:
                body = b"%PDF-1.4 x"
            if progress:
                progress.add(len(body))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            return hashlib.sha256(body).hexdigest()

        orig = fl.download
        fl.download = fake_download
        try:
            mmlb.fetch(base.Ctx(root=root, log=lambda *a: None))
        finally:
            fl.download = orig
        self.assertTrue((root / "mmlb" / "_done").exists())
        self.assertEqual(sorted(p.name for p in (root / "mmlb" / "documents").iterdir()), names)
        self.assertTrue(any("raw." in u and u.endswith("d3.pdf") for u in seen))


if __name__ == "__main__":
    unittest.main()
