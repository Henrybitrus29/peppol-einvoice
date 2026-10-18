import copy
import unittest
from decimal import Decimal

from einvoice.mapper import MapConfig, MappingError, convert
from einvoice.validate import errors, is_available, validate_xml
from tests.helpers import base_invoice, party

SKIP_SCHEMATRON = not is_available()


def valid(xml: str) -> list[str]:
    return [str(i) for i in errors(validate_xml(xml))]


class MappingErrorTests(unittest.TestCase):
    """Things that must be rejected before any XML is produced."""

    def test_missing_required_fields(self):
        for field in ("invoice_number", "issue_date", "currency", "seller", "buyer", "lines"):
            inv = base_invoice()
            del inv[field]
            with self.assertRaises(MappingError, msg=field):
                convert(inv)

    def test_missing_buyer_reference_and_order_reference(self):
        inv = base_invoice()
        del inv["order_reference"]
        with self.assertRaises(MappingError):
            convert(inv)

    def test_buyer_reference_alone_is_enough(self):
        inv = base_invoice(buyer_reference="BR-1")
        del inv["order_reference"]
        convert(inv)  # should not raise

    def test_bad_date_format(self):
        inv = base_invoice(issue_date="14-03-2026")
        with self.assertRaises(MappingError):
            convert(inv)

    def test_invalid_calendar_date(self):
        inv = base_invoice(issue_date="2026-02-30")
        with self.assertRaises(MappingError):
            convert(inv)

    def test_unknown_currency_rejected_by_default(self):
        inv = base_invoice(currency="ZZZ")
        with self.assertRaises(MappingError):
            convert(inv)

    def test_currency_allowlist_can_be_widened(self):
        inv = base_invoice(currency="ZZZ")
        convert(inv, MapConfig(currency_allowlist={"ZZZ"}))

    def test_no_lines_rejected(self):
        inv = base_invoice(lines=[])
        with self.assertRaises(MappingError):
            convert(inv)

    def test_negative_or_zero_quantity_rejected(self):
        for q in (0, -1):
            inv = base_invoice()
            inv["lines"][0]["quantity"] = q
            with self.assertRaises(MappingError):
                convert(inv)

    def test_unsupported_unit_code_rejected(self):
        inv = base_invoice()
        inv["lines"][0]["unit_code"] = "BANANA"
        with self.assertRaises(MappingError):
            convert(inv)

    def test_bad_vat_category_rejected(self):
        inv = base_invoice()
        inv["lines"][0]["vat_category"] = "Q"
        with self.assertRaises(MappingError):
            convert(inv)

    def test_standard_rate_without_percentage_rejected(self):
        inv = base_invoice()
        inv["lines"][0]["vat_category"] = "S"
        inv["lines"][0]["vat_rate"] = 0
        with self.assertRaises(MappingError):
            convert(inv)

    def test_endpoint_id_without_scheme_rejected(self):
        inv = base_invoice()
        inv["seller"]["endpoint_id"] = "123456"
        with self.assertRaises(MappingError):
            convert(inv)

    def test_bad_country_code_rejected(self):
        inv = base_invoice()
        inv["seller"]["address"]["country"] = "Belgium"
        with self.assertRaises(MappingError):
            convert(inv)

    def test_bad_invoice_type_rejected(self):
        inv = base_invoice(type="quote")
        with self.assertRaises(MappingError):
            convert(inv)

    def test_charge_without_reason_rejected(self):
        inv = base_invoice(charges=[{"amount": "5.00", "vat_category": "S", "vat_rate": "21"}])
        with self.assertRaises(MappingError):
            convert(inv)

    def test_garbage_amount_rejected(self):
        inv = base_invoice()
        inv["lines"][0]["unit_price"] = "not a number"
        with self.assertRaises(MappingError):
            convert(inv)

    def test_absurdly_large_price_rejected_cleanly(self):
        # Before the magnitude guard this raised an unhandled decimal.InvalidOperation instead of MappingError.
        inv = base_invoice()
        inv["lines"][0]["unit_price"] = "99999999999999999999999999.99"
        with self.assertRaises(MappingError):
            convert(inv)

    def test_non_finite_amount_rejected(self):
        inv = base_invoice()
        for bad in ("Infinity", "-Infinity", "NaN"):
            inv["lines"][0]["unit_price"] = bad
            with self.assertRaises(MappingError, msg=bad):
                convert(inv)

    def test_non_finite_numbers_rejected(self):
        for bad in ("NaN", "Infinity", "-Infinity"):
            inv = base_invoice()
            inv["lines"][0]["unit_price"] = bad
            with self.assertRaises(MappingError, msg=bad):
                convert(inv)

    def test_excess_precision_is_rejected_not_rounded(self):
        """Reject rather than guess: a value with more decimals than the format allows is an error, never rounded."""
        cases = {
            "unit_price too precise": lambda i: i["lines"][0].update(unit_price="0.00001"),
            "quantity too precise": lambda i: i["lines"][0].update(quantity="1.00001"),
            "vat_rate too precise": lambda i: i["lines"][0].update(vat_rate="21.005"),
            "charge amount has a sub-cent part": lambda i: i.update(
                charges=[{"amount": "10.005", "reason": "Shipping", "vat_category": "S", "vat_rate": "21"}]),
            "prepaid amount has a sub-cent part": lambda i: i.update(prepaid_amount="1.005"),
        }
        for label, mutate in cases.items():
            inv = base_invoice()
            mutate(inv)
            with self.assertRaises(MappingError, msg=label) as cm:
                convert(inv)
            self.assertIn("decimal places", str(cm.exception), label)

    def test_jpy_amounts_must_be_whole_yen(self):
        inv = base_invoice(currency="JPY", prepaid_amount="0.5")
        with self.assertRaises(MappingError):
            convert(inv)


class ArithmeticTests(unittest.TestCase):
    """Mapper's own math, independent of the external validator."""

    def test_simple_total(self):
        inv = base_invoice()
        xml = convert(inv)
        self.assertIn("<cbc:PayableAmount currencyID=\"EUR\">121.00</cbc:PayableAmount>", xml)

    def test_decimal_precision_preserved(self):
        # The unit price keeps its own precision (33.333); only the LINE NET is rounded to the minor unit:
        # 33.333 * 3 = 99.999 -> 100.00. (Rounding the price first would give 33.33 * 3 = 99.99, a wrong amount.)
        inv = base_invoice()
        inv["lines"][0]["unit_price"] = "33.333"
        inv["lines"][0]["quantity"] = 3
        xml = convert(inv)
        self.assertIn('PriceAmount currencyID="EUR">33.333<', xml)
        self.assertIn('LineExtensionAmount currencyID="EUR">100.00<', xml)

    def test_sub_cent_unit_price_is_not_rounded(self):
        # Regression: 1000 kWh at 0.0125 EUR used to be billed as 1000 * 0.01 = 10.00 instead of 12.50.
        inv = base_invoice()
        inv["lines"][0].update(quantity="1000", unit_price="0.0125", unit_code="KWH")
        xml = convert(inv)
        self.assertIn('PriceAmount currencyID="EUR">0.0125<', xml)
        self.assertIn('LineExtensionAmount currencyID="EUR">12.50<', xml)
        # 21% of 12.50 = 2.625 -> 2.63 (half up); payable = 12.50 + 2.63
        self.assertIn('PayableAmount currencyID="EUR">15.13<', xml)

    def test_fractional_quantity_is_not_rounded(self):
        # Regression: a quantity of 0.125 used to become 0.13, turning 80.00 * 0.125 = 10.00 into 10.40.
        inv = base_invoice()
        inv["lines"][0].update(quantity="0.125", unit_price="80.00", unit_code="HUR")
        xml = convert(inv)
        self.assertIn('unitCode="HUR">0.125<', xml)
        self.assertIn('LineExtensionAmount currencyID="EUR">10.00<', xml)

    def test_whole_numbers_keep_their_usual_formatting(self):
        xml = convert(base_invoice())
        self.assertIn('>1.00</cbc:InvoicedQuantity>', xml)
        self.assertIn('PriceAmount currencyID="EUR">100.00<', xml)

    def test_trailing_zeros_do_not_count_as_extra_precision(self):
        inv = base_invoice()
        inv["lines"][0]["unit_price"] = "0.012500"
        self.assertIn('LineExtensionAmount', convert(inv))

    def test_jpy_has_no_decimals(self):
        inv = base_invoice(currency="JPY")   # default line is 21% VAT: 1000 + 210 = 1210
        inv["lines"][0]["unit_price"] = "1000"
        xml = convert(inv)
        self.assertIn('PayableAmount currencyID="JPY">1210<', xml)
        self.assertNotIn("1210.00", xml)

    def test_charge_increases_payable_and_taxable_amount(self):
        inv = base_invoice(charges=[{"amount": "10.00", "reason": "Shipping", "vat_category": "S", "vat_rate": "21"}])
        xml = convert(inv)
        # taxable base becomes 100 + 10 = 110, vat = 23.10, payable = 110 + 23.10 = 133.10
        self.assertIn('TaxableAmount currencyID="EUR">110.00<', xml)
        self.assertIn('PayableAmount currencyID="EUR">133.10<', xml)

    def test_allowance_decreases_payable_and_taxable_amount(self):
        inv = base_invoice(charges=[{"amount": "10.00", "is_charge": False, "reason": "Discount", "vat_category": "S", "vat_rate": "21"}])
        xml = convert(inv)
        self.assertIn('TaxableAmount currencyID="EUR">90.00<', xml)
        self.assertIn('PayableAmount currencyID="EUR">108.90<', xml)

    def test_prepaid_amount_reduces_payable(self):
        inv = base_invoice(prepaid_amount="50.00")
        xml = convert(inv)
        self.assertIn('PayableAmount currencyID="EUR">71.00<', xml)
        self.assertIn('PrepaidAmount currencyID="EUR">50.00<', xml)

    def test_mixed_vat_categories_each_get_own_subtotal(self):
        inv = base_invoice(lines=[
            {"name": "A", "quantity": 1, "unit_price": "100.00", "unit_code": "EA", "vat_category": "S", "vat_rate": "21"},
            {"name": "B", "quantity": 1, "unit_price": "50.00", "unit_code": "EA", "vat_category": "Z", "vat_rate": 0},
        ])
        xml = convert(inv)
        self.assertEqual(xml.count("<cac:TaxSubtotal>"), 2)


class DeterminismTests(unittest.TestCase):
    def test_same_input_same_output(self):
        inv = base_invoice()
        self.assertEqual(convert(copy.deepcopy(inv)), convert(copy.deepcopy(inv)))

    def test_credit_note_uses_credit_note_root(self):
        inv = base_invoice(type="credit_note")
        xml = convert(inv)
        self.assertTrue(xml.strip().startswith("<?xml") and "<CreditNote " in xml)
        self.assertIn("<cac:CreditNoteLine>", xml)
        self.assertIn("<cbc:CreditedQuantity", xml)


@unittest.skipIf(SKIP_SCHEMATRON, "EN 16931 validator (saxonche) not available in this environment")
class OfficialValidatorTests(unittest.TestCase):
    """Every one of these invoices is run through the real CEN/TC 434 EN 16931 Schematron rules (via Saxon).
    A pass here means the official EU reference validator accepts the output, not just this project's own opinion."""

    def check(self, invoice, cfg=None):
        xml = convert(invoice, cfg)
        errs = valid(xml)
        self.assertEqual(errs, [], f"\n{xml}")

    def test_simple_invoice(self):
        self.check(base_invoice())

    def test_sub_cent_price_and_fractional_quantity(self):
        """The two rounding regressions, accepted by the official rules: utility-style pricing and 0.125 hours."""
        inv = base_invoice(lines=[
            {"name": "Electricity", "quantity": "1000", "unit_price": "0.0125", "unit_code": "KWH",
             "vat_category": "S", "vat_rate": "21"},
            {"name": "Consulting", "quantity": "0.125", "unit_price": "80.00", "unit_code": "HUR",
             "vat_category": "S", "vat_rate": "21"},
        ])
        self.check(inv)

    def test_credit_note(self):
        self.check(base_invoice(type="credit_note"))

    def test_jpy_zero_decimal_currency(self):
        inv = base_invoice(currency="JPY")
        inv["lines"][0]["unit_price"] = "1000"
        self.check(inv)

    def test_reverse_charge(self):
        inv = base_invoice(seller=party("Seller RC", "DE"), buyer=party("Buyer RC", "FR"))
        inv["lines"][0]["vat_category"] = "AE"
        inv["lines"][0]["vat_rate"] = 0
        self.check(inv)

    def test_zero_rated(self):
        inv = base_invoice()
        inv["lines"][0]["vat_category"] = "Z"
        inv["lines"][0]["vat_rate"] = 0
        self.check(inv)

    def test_exempt(self):
        inv = base_invoice()
        inv["lines"][0]["vat_category"] = "E"
        inv["lines"][0]["vat_rate"] = 0
        self.check(inv)

    def test_mixed_vat_categories(self):
        self.check(base_invoice(lines=[
            {"name": "A", "quantity": 1, "unit_price": "100.00", "unit_code": "EA", "vat_category": "S", "vat_rate": "21"},
            {"name": "B", "quantity": 1, "unit_price": "50.00", "unit_code": "EA", "vat_category": "Z", "vat_rate": 0},
        ]))

    def test_document_level_charge(self):
        self.check(base_invoice(charges=[{"amount": "10.00", "reason": "Shipping", "vat_category": "S", "vat_rate": "21"}]))

    def test_document_level_allowance(self):
        self.check(base_invoice(charges=[{"amount": "10.00", "is_charge": False, "reason": "Discount",
                                          "vat_category": "S", "vat_rate": "21"}]))

    def test_prepaid_amount(self):
        self.check(base_invoice(prepaid_amount="50.00"))

    def test_decimal_quantity(self):
        inv = base_invoice()
        inv["lines"][0]["quantity"] = "7.25"
        inv["lines"][0]["unit_price"] = "85.00"
        inv["lines"][0]["unit_code"] = "HUR"
        self.check(inv)

    def test_multiple_lines_same_category(self):
        self.check(base_invoice(lines=[
            {"name": "A", "quantity": 2, "unit_price": "10.00", "unit_code": "EA", "vat_category": "S", "vat_rate": "21"},
            {"name": "B", "quantity": 3, "unit_price": "7.50", "unit_code": "EA", "vat_category": "S", "vat_rate": "21"},
        ]))

    def test_endpoint_ids_and_legal_registration(self):
        seller = party("Seller EP", "BE")
        seller["endpoint_id"], seller["endpoint_scheme"] = "0123456789", "9925"
        seller["legal_registration_id"] = {"id": "0123456789", "scheme": "0208"}
        self.check(base_invoice(seller=seller))

    def test_payment_means_and_terms(self):
        self.check(base_invoice(payment_means={"code": "58", "iban": "BE68539007547034", "reference": "INV-0001"},
                                payment_terms="Net 30"))

    def test_detects_broken_invoice(self):
        """Sanity check the other direction: a deliberately wrong document must be rejected, not silently waved
        through, proving these tests would actually catch a regression."""
        inv = base_invoice()
        xml = convert(inv)
        broken = xml.replace('PayableAmount currencyID="EUR">121.00<', 'PayableAmount currencyID="EUR">999.00<')
        self.assertNotEqual(xml, broken)
        self.assertTrue(valid(broken), "a wrong payable amount must fail validation")


if __name__ == "__main__":
    unittest.main()
