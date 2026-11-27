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


if __name__ == "__main__":
    unittest.main()
