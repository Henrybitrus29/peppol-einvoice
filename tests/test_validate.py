import unittest

from einvoice.mapper import convert
from einvoice.validate import errors, is_available, validate_xml
from tests.helpers import base_invoice

SKIP = not is_available()


@unittest.skipIf(SKIP, "EN 16931 validator (saxonche) not available in this environment")
class ValidatorWrapperTests(unittest.TestCase):
    def test_clean_invoice_has_no_issues(self):
        self.assertEqual(validate_xml(convert(base_invoice())), [])

    def test_issue_str_contains_rule_id_and_message(self):
        xml = convert(base_invoice()).replace('PayableAmount currencyID="EUR">121.00<', 'PayableAmount currencyID="EUR">1.00<')
        issues = validate_xml(xml)
        self.assertTrue(issues)
        self.assertTrue(any("BR-CO-16" in str(i) for i in issues))

    def test_errors_filters_out_non_errors(self):
        xml = convert(base_invoice())
        self.assertEqual(errors(validate_xml(xml)), [])

    def test_malformed_xml_raises_rather_than_crashing_silently(self):
        from einvoice.validate import ValidatorUnavailable
        with self.assertRaises(Exception):
            validate_xml("<not well formed")

    def test_missing_mandatory_field_detected(self):
        xml = convert(base_invoice()).replace("<cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>", "")
        issues = validate_xml(xml)
        self.assertTrue(errors(issues))

    def test_is_available_true_in_this_repo(self):
        self.assertTrue(is_available())


if __name__ == "__main__":
    unittest.main()
