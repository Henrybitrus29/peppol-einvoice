"""Tests for `python -m einvoice convert`, run as a real subprocess so the actual exit code a CI pipeline or a
caller's shell script would see is what's being checked -- not just the return value of a Python function."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from einvoice.validate import is_available
from tests.helpers import base_invoice

ROOT = Path(__file__).resolve().parent.parent
SKIP_SCHEMATRON = not is_available()


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "einvoice", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)


def write_invoice(tmp_path: Path, invoice: dict) -> str:
    p = tmp_path / "invoice.json"
    p.write_text(json.dumps(invoice), encoding="utf-8")
    return str(p)


class ConvertExitCodeTests(unittest.TestCase):
    def test_valid_invoice_exits_zero(self):
        r = run_cli("convert", "samples/simple_invoice.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<Invoice", r.stdout)

    def test_mapping_failure_exits_nonzero(self):
        r = run_cli("convert", "samples/bad_vat_category.json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("REJECTED", r.stderr)
        self.assertEqual(r.stdout, "")   # nothing that looks like output XML on a rejection

    def test_missing_file_exits_nonzero(self):
        r = run_cli("convert", "samples/does_not_exist.json")
        self.assertNotEqual(r.returncode, 0)

    @unittest.skipIf(SKIP_SCHEMATRON, "EN 16931 validator not available")
    def test_official_validation_failure_exits_nonzero(self):
        """The regression this test exists for: convert used to print the validation errors but still exit 0,
        so a CI step or a calling script would see a document that fails the real EU rules as a success."""
        r = run_cli("convert", "samples/bad_en16931_reverse_charge_rate.json")
        self.assertNotEqual(r.returncode, 0, "a document that fails EN 16931 validation must not exit 0")
        self.assertIn("BR-AE", r.stderr)

    @unittest.skipIf(SKIP_SCHEMATRON, "EN 16931 validator not available")
    def test_official_validation_pass_exits_zero(self):
        r = run_cli("convert", "samples/reverse_charge.json")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_skip_validate_flag_ignores_official_rules(self):
        r = run_cli("convert", "samples/bad_en16931_reverse_charge_rate.json" if not SKIP_SCHEMATRON else "samples/simple_invoice.json",
                    "--skip-validate")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_price_too_large_is_a_clean_mapping_rejection_not_a_crash(self):
        r = run_cli("convert", "samples/bad_price_too_large.json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("REJECTED", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("too large", r.stderr)

    def test_out_file_is_written_only_when_exit_is_zero_path(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "out.xml")
            r = run_cli("convert", "samples/simple_invoice.json", "--out", out)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(Path(out).exists())
            self.assertIn("<Invoice", Path(out).read_text())

    @unittest.skipIf(SKIP_SCHEMATRON, "EN 16931 validator not available")
    def test_out_file_is_still_written_when_validation_fails(self):
        """convert's job is to show you the document AND tell you it's wrong -- failing validation should not
        swallow the --out write, only the exit code changes."""
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "out.xml")
            r = run_cli("convert", "samples/bad_en16931_reverse_charge_rate.json", "--out", out)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(Path(out).exists())


if __name__ == "__main__":
    unittest.main()
