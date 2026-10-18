![CI](https://github.com/Henrybitrus29/peppol-einvoice/actions/workflows/ci.yml/badge.svg)
# ERP invoice to UBL/EN16931 (Peppol e-invoicing)

Converts a plain ERP-style invoice (parties, lines, VAT, totals as a dict/JSON) into a UBL 2.1 Invoice or
CreditNote conformant to **EN 16931** and **Peppol BIS Billing 3.0**, validated against the **real, official EU
Schematron rules** before anything is sent. Built as a second portfolio piece for interoperability work — the
same shape as the sibling `fhir-hl7-bridge` project, applied to the EU's mandatory B2B e-invoicing rollout
(Belgium since Jan 2026, Poland and France through 2026, Germany/UAE/Philippines from Jan 2027).
**Synthetic invoice data only.**

```
ERP invoice (dict/JSON) -> map -> validate (official EN 16931 rules) -> deliver (file or Peppol sandbox)
                                                |
                                          dead-letter store (every failure, with the reason)
```

## What's here
| Path | What it is |
|---|---|
| `einvoice/mapper.py` | invoice dict -> UBL 2.1 XML (Invoice or CreditNote), all money math in `Decimal` |
| `einvoice/validate.py` | runs the real CEN/TC 434 EN 16931 Schematron rules via Saxon (`saxonche`, no JVM) |
| `einvoice/pipeline.py` | map -> validate -> deliver, with a dead-letter entry for every failure |
| `einvoice/sinks.py` | write to files, or POST to a Peppol test-network sandbox, with retries |
| `einvoice/cli.py` | `convert`, `send`, `replay` |
| `vendor/en16931/` | the official EN 16931 UBL validation XSLT (EUPL-licensed, from CEN/TC 434) and one CEN example |
| `samples/` | 5 valid sample invoices (standard, reverse charge, credit note, charges/allowances, mixed VAT), 4 invalid |
| `docs/DESIGN.md`, `docs/VALIDATION.md`, `docs/SETUP.md` | design rationale, exactly what is and isn't verified, how to run it |

## Try it
```bash
pip install -r requirements.txt
python -m einvoice convert samples/simple_invoice.json --out /tmp/invoice.xml
python -m unittest discover -s tests -t .
```

## Status: what I've verified myself, and what I haven't
- **85 automated tests**, including **16 that validate real generated invoices against the actual official EU
  EN 16931 rules** (not a reimplementation) — standard/zero/reverse-charge/exempt VAT, mixed categories, credit
  notes, a zero-decimal currency (JPY), document-level charges and allowances, prepaid amounts, decimal
  quantities. One test deliberately breaks a valid invoice and confirms the rules catch it. The CLI's exit codes
  are tested as a real subprocess, not just as a Python function call, so they reflect what a CI pipeline sees.
- **Two real bugs were found and fixed by this exact process** while building it (not hypothetical — see
  `docs/VALIDATION.md` and `docs/DESIGN.md`): document-level charges need a stated reason and must be folded into
  the right VAT category's taxable amount, not just added to the total; and zero-rated/standard-rated categories
  must NOT carry a VAT exemption reason while every other category must.
- **A later review caught two more bugs, now fixed:** the mapper used to round unit price and quantity to the
  currency's minor unit before multiplying, which silently lost money on sub-cent pricing (0.0125 EUR/kWh) and
  fractional quantities (0.125 hours); and `replay()` could crash on a mapping failure because it assumed every
  dead-lettered entry was XML. See `docs/VALIDATION.md` and `docs/DESIGN.md` for what changed.
- **Two further bugs, also now fixed:** an absurdly large price (far beyond any real invoice) raised an unhandled
  `decimal.InvalidOperation` instead of a clean rejection — a real risk for a form a visitor can type into; and
  `convert` printed EN 16931 validation errors but still exited `0`, so a CI step or calling script would see a
  document that fails the official rules as a success. Both are covered by new tests, and the CI workflow now
  checks the deliberately-invalid samples separately from the valid ones, instead of one loop that could never
  have passed with both kinds mixed together under `set -e`.
- **`PeppolSandboxSink` has been tested only against a local mock HTTP server, never a real Peppol test network.**
  `docs/SETUP.md` explains how to get a free sandbox account (an application process, not instant) and try it
  for real — that step is still open.
- **Never tested against a real ERP export, a real ERP's data quirks, or any non-synthetic invoice data.**

## Not implemented
CII format (only UBL), country-specific extension rules (Germany's XRechnung, France's rules, etc. — see
`docs/VALIDATION.md`), Peppol SMP/directory lookups, and running an actual Peppol Access Point (this is the
integration layer that feeds one).
