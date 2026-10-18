import json
import logging
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from einvoice.pipeline import Pipeline
from einvoice.sinks import DeadLetter, FileSink, PeppolSandboxSink
from einvoice.validate import is_available
from tests.helpers import base_invoice

SKIP_SCHEMATRON = not is_available()


def base_invoice_xml() -> str:
    from einvoice.mapper import convert
    return convert(base_invoice())


class RecordingHandler(BaseHTTPRequestHandler):
    log = None
    script = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        status = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        RecordingHandler.log.append((status, body.decode("utf-8"), dict(self.headers)))
        self.send_response(status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def start_server(script):
    RecordingHandler.log = []
    RecordingHandler.script = list(script)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/send"


class FileSinkTests(unittest.TestCase):
    def test_writes_a_file_per_invoice(self):
        with tempfile.TemporaryDirectory() as d:
            sink = FileSink(d)
            r1 = sink.send("<Invoice/>", "INV-1")
            r2 = sink.send("<Invoice/>", "INV-2")
            self.assertTrue(r1.ok and r2.ok)
            self.assertIn("INV-1", r1.detail)
            import os
            self.assertEqual(len(os.listdir(d)), 2)


class PeppolSandboxSinkTests(unittest.TestCase):
    def test_success_and_headers_sent(self):
        srv, url = start_server([202])
        try:
            res = PeppolSandboxSink(url, headers={"X-Api-Key": "secret"}).send("<Invoice/>", "INV-1")
            self.assertTrue(res.ok)
            self.assertEqual(RecordingHandler.log[0][2].get("X-Api-Key"), "secret")
        finally:
            srv.shutdown()

    def test_retries_on_500_then_succeeds(self):
        srv, url = start_server([500, 500, 200])
        try:
            res = PeppolSandboxSink(url, attempts=3, backoff=0.01).send("<Invoice/>")
            self.assertTrue(res.ok)
            self.assertEqual(len(RecordingHandler.log), 3)
        finally:
            srv.shutdown()

    def test_400_is_not_retried(self):
        srv, url = start_server([400])
        try:
            res = PeppolSandboxSink(url, attempts=5, backoff=0.01).send("<Invoice/>")
            self.assertFalse(res.ok)
            self.assertFalse(res.retryable)
            self.assertEqual(len(RecordingHandler.log), 1)
        finally:
            srv.shutdown()

    def test_network_error_is_retryable(self):
        res = PeppolSandboxSink("http://127.0.0.1:1", attempts=2, backoff=0.01, timeout=1).send("<Invoice/>")
        self.assertFalse(res.ok)
        self.assertTrue(res.retryable)


class DeadLetterTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            dl = DeadLetter(d)
            stem = dl.put("<Invoice/>", "validate", "bad total", ["error: BR-CO-16"])
            self.assertEqual(dl.items(), [stem])
            self.assertEqual(dl.read(stem), "<Invoice/>")
            dl.remove(stem)
            self.assertEqual(dl.items(), [])

    def test_files_are_owner_only(self):
        import os
        import stat
        with tempfile.TemporaryDirectory() as d:
            stem = DeadLetter(d).put("x", "map", "bad")
            mode = stat.S_IMODE(os.stat(os.path.join(d, stem + ".src")).st_mode)
            self.assertEqual(mode, 0o600)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_mapping_failure_dead_lettered(self):
        bad = base_invoice()
        del bad["seller"]
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            outcome = Pipeline(FileSink(out), DeadLetter(dead)).process(bad)
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.stage, "map")
            self.assertEqual(len(DeadLetter(dead).items()), 1)

    @unittest.skipIf(SKIP_SCHEMATRON, "EN 16931 validator not available")
    def test_good_invoice_validated_and_delivered(self):
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            outcome = Pipeline(FileSink(out), DeadLetter(dead)).process(base_invoice())
            self.assertTrue(outcome.ok)
            import os
            self.assertEqual(len(os.listdir(out)), 1)
            self.assertEqual(DeadLetter(dead).items(), [])

    @unittest.skipIf(SKIP_SCHEMATRON, "EN 16931 validator not available")
    def test_validation_failure_is_dead_lettered_not_sent(self):
        inv = base_invoice()
        inv["lines"][0]["vat_category"] = "AE"   # reverse charge without an exemption reason text would normally
        inv["lines"][0]["vat_rate"] = "21"        # fail BR-AE-* rules since AE must be taxed at 0%, not 21%
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            outcome = Pipeline(FileSink(out), DeadLetter(dead)).process(inv)
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.stage, "validate")
            import os
            self.assertEqual(os.listdir(out), [])
            self.assertEqual(len(DeadLetter(dead).items()), 1)

    def test_skip_schematron_flag_bypasses_official_validation(self):
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            outcome = Pipeline(FileSink(out), DeadLetter(dead), skip_schematron=True).process(base_invoice())
            self.assertTrue(outcome.ok)

    @unittest.skipIf(SKIP_SCHEMATRON, "EN 16931 validator not available")
    def test_sink_failure_is_dead_lettered(self):
        srv, url = start_server([500])
        try:
            with tempfile.TemporaryDirectory() as dead:
                outcome = Pipeline(PeppolSandboxSink(url, attempts=1), DeadLetter(dead)).process(base_invoice())
                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.stage, "deliver")
                self.assertEqual(len(DeadLetter(dead).items()), 1)
        finally:
            srv.shutdown()

    @unittest.skipIf(SKIP_SCHEMATRON, "EN 16931 validator not available")
    def test_replay_processes_and_clears_dead_letters(self):
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            pipe = Pipeline(FileSink(out), DeadLetter(dead))
            # simulate an earlier failure by dead-lettering a VALID xml directly (as if delivery had failed then)
            xml = Pipeline(FileSink(out), skip_schematron=True).process(base_invoice()).xml
            pipe.dead.put(xml, "deliver", "simulated earlier outage")
            result = pipe.replay()
            self.assertEqual(result["replayed_ok"], 1)
            self.assertEqual(DeadLetter(dead).items(), [])

    # --- replay of mapping failures: the stored source is invoice JSON, not XML (regression) -------------------
    # These use skip_schematron=True so they test the replay logic itself and run without the EN 16931 validator.

    def assertDelivered(self, out_dir, count):
        """Every file the sink wrote is a real UBL invoice document (replay must never ship raw JSON as XML)."""
        import os
        files = sorted(os.listdir(out_dir))
        self.assertEqual(len(files), count)
        for name in files:
            with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                body = f.read()
            self.assertTrue(body.startswith("<?xml"), f"{name} is not XML: {body[:60]!r}")
            self.assertIn("<cbc:ID>INV-0001</cbc:ID>", body)

    def test_replay_reruns_a_mapping_failure_once_the_input_is_fixed(self):
        import os
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            bad = base_invoice()
            del bad["seller"]
            pipe = Pipeline(FileSink(out), DeadLetter(dead), skip_schematron=True)
            self.assertEqual(pipe.process(bad).stage, "map")
            self.assertEqual(len(pipe.dead.items()), 1)
            # the person fixes the input in place (the .src file holds the invoice JSON)
            stem = pipe.dead.items()[0]
            with open(os.path.join(dead, stem + ".src"), "w", encoding="utf-8") as f:
                json.dump(base_invoice(), f)
            self.assertEqual(pipe.replay(), {"replayed_ok": 1, "still_failing": 0})
            self.assertEqual(pipe.dead.items(), [])
            self.assertEqual(len(os.listdir(out)), 1)
            self.assertDelivered(out, count=1)      # real UBL XML, not the raw JSON that was stored

    def test_replay_keeps_an_entry_that_still_fails_without_duplicating_it(self):
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            bad = base_invoice()
            del bad["seller"]
            pipe = Pipeline(FileSink(out), DeadLetter(dead), skip_schematron=True)
            pipe.process(bad)
            self.assertEqual(pipe.replay(), {"replayed_ok": 0, "still_failing": 1})
            self.assertEqual(len(pipe.dead.items()), 1)

    def test_replay_handles_json_and_xml_entries_in_the_same_store(self):
        import os
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            pipe = Pipeline(FileSink(out), DeadLetter(dead), skip_schematron=True)
            xml = pipe.process(base_invoice()).xml
            pipe.dead.put(xml, "deliver", "simulated outage")
            pipe.dead.put(json.dumps(base_invoice()), "map", "simulated old mapping bug")
            before = len(os.listdir(out))
            self.assertEqual(pipe.replay(), {"replayed_ok": 2, "still_failing": 0})
            self.assertEqual(len(os.listdir(out)), before + 2)
            self.assertDelivered(out, count=before + 2)

    def test_replay_without_a_sidecar_decides_by_content(self):
        import os
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
            pipe = Pipeline(FileSink(out), DeadLetter(dead), skip_schematron=True)
            stem = pipe.dead.put(json.dumps(base_invoice()), "map", "x")
            os.remove(os.path.join(dead, stem + ".json"))           # lose the sidecar
            self.assertEqual(pipe.dead.stage(stem), "")
            self.assertEqual(pipe.replay()["replayed_ok"], 1)
            self.assertDelivered(out, count=1)

    def test_replay_survives_garbage_and_a_raising_sink(self):
        class ExplodingSink:
            def send(self, xml, invoice_id=""):
                raise RuntimeError("sink is down")

        with tempfile.TemporaryDirectory() as dead:
            pipe = Pipeline(ExplodingSink(), DeadLetter(dead), skip_schematron=True)
            pipe.dead.put("this is not json {", "map", "garbage")
            pipe.dead.put("[1, 2, 3]", "map", "json but not an invoice")
            pipe.dead.put(base_invoice_xml(), "deliver", "outage")
            self.assertEqual(pipe.replay(), {"replayed_ok": 0, "still_failing": 3})
            self.assertEqual(len(pipe.dead.items()), 3)

    def test_no_invoice_data_in_logs(self):
        import io
        logging.disable(logging.NOTSET)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        log = logging.getLogger("einvoice")
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        try:
            with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as dead:
                Pipeline(FileSink(out), DeadLetter(dead), skip_schematron=True).process(base_invoice())
        finally:
            log.removeHandler(handler)
            logging.disable(logging.CRITICAL)
        self.assertNotIn("Seller Co", stream.getvalue())
        self.assertNotIn("Buyer Co", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
