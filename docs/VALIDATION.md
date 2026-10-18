# Validation: what is checked, by what, and what is NOT verified

## Layer 1: the mapper's own checks (`einvoice/mapper.py`), always on
Required fields, date formats, currency allowlist, VAT category codes, unit codes, positive quantities, a
buyer reference or order reference (Peppol BIS3 requires one), and a reason on every document-level charge or
allowance. These reject obviously wrong input before any XML is built.

## Layer 2: the official EN 16931 Schematron rules, always on by default
Run via `einvoice/validate.py`, using the real CEN/TC 434 rule set (`vendor/en16931/EN16931-UBL-validation.xslt`,
version 1.3.16), executed through **SaxonC-HE** via the `saxonche` Python package. This is the same engine and the
same rule set the Java reference tool (`validator_cli.jar` + Saxon) uses — but it runs natively in Python with no
JVM, and **the rule file is bundled in this repo**, so there's no package-registry dependency at validation time
(this is what made it possible to get this layer fully working, unlike the HAPI FHIR validator in the sibling
`fhir-hl7-bridge` project, which needs to fetch its definitions from a package server I didn't have access to).

**85 automated tests, including 16 that run real invoices (standard rate, zero rate, reverse charge, exempt,
mixed categories, credit notes, JPY's zero-decimal currency, document-level charges and allowances, prepaid
amounts, decimal quantities) through these exact official rules** — not a reimplementation, not a mock. One test
(`test_detects_broken_invoice`) deliberately corrupts a valid invoice and confirms the rules catch it, so a
regression that silently weakened validation would be caught too.

**A class of bug the official rules cannot catch.** The Schematron checks that an invoice is internally
consistent, not that it matches what the seller meant to charge. An earlier version rounded the unit price and
quantity to 2 decimals before multiplying, so 1000 kWh at 0.0125 EUR was invoiced as 10.00 instead of 12.50 and the
result still passed every rule. The mapper now keeps price and quantity exact, rejects excess precision, and
`tests/test_mapper.py` pins both cases (`test_sub_cent_unit_price_is_not_rounded`,
`test_fractional_quantity_is_not_rounded`).

Run it yourself:
```bash
python -m unittest tests.test_mapper tests.test_validate -v
```

## What this project does NOT verify
- **A specific country's extension rules.** Several countries (Germany's XRechnung, France's Factur-X/2026 rules,
  Belgium's domestic profile) add their own Schematron rules on top of EN 16931's. This project validates against
  the **base EN 16931 + Peppol BIS Billing 3.0** rule set only. A country-specific deployment needs that country's
  own validation artifacts too (CEN/TC 434 and most national authorities publish them the same way EN16931's are
  published here).
- **Delivery over a real Peppol network.** `PeppolSandboxSink` has only been tested against a local mock HTTP
  server (`tests/test_pipeline.py`), never against a real Peppol Access Point or test network sandbox. See
  `docs/SETUP.md` for how to get a free sandbox account and try it for real — that step is still open.
- **A real Peppol SMP/directory lookup**, i.e. resolving a recipient's Peppol ID to their actual Access Point.
  This project assumes you already have a destination (a sandbox provider's send endpoint).
- **CII (Cross Industry Invoice) format.** Only UBL is implemented; EN 16931 also permits CII, and CEN publishes a
  separate CII validation artifact this project does not use.
