# Design notes

## Scope
One ERP-style invoice (a plain dict: parties, lines, VAT, totals) to a UBL 2.1 Invoice or CreditNote, conformant to
EN 16931 and Peppol BIS Billing 3.0, validated against the real rules before anything is sent. Chosen for the same
reason as the sibling FHIR project: mandatory B2B e-invoicing is a hard legal deadline across the EU (and beyond)
through 2026-2027, and the technical problem — bridge a system's native data into a standard, validated, delivered
format — is structurally the same shape as healthcare's HL7-to-FHIR problem.

## Why these decisions
- **Reject rather than guess.** A missing seller VAT scheme, an invalid unit code, a charge with no stated reason —
  all raise `MappingError` before any XML is produced. An invoice is a legal and financial document; a silently
  wrong one (a misrounded total, a missing VAT breakdown) is worse than a rejected one a human has to fix.
- **The mapper computes totals itself** from the line items and charges given, rather than trusting totals the
  caller supplies. Arithmetic is done in `Decimal`. **Inputs are never silently rounded.** A unit price or quantity may
  carry up to 4 decimals (0.0125 EUR per kWh is a real tariff) and keeps that precision in the XML; a value with more
  decimals than allowed is rejected with an error naming the field. Only the *computed* amounts (line net, VAT per
  category, totals) are rounded, half-up, to the currency's minor unit (2 places, 0 for JPY), which is what the
  small-tolerance rounding rules in EN 16931's business rules (BR-CO-*) expect. Charge, allowance and prepaid amounts
  must already be in the currency's minor unit.
- **Document-level charges and allowances are folded into the correct VAT category's taxable amount**, not just
  added to the grand total — this is what EN 16931's BR-S-08 and sibling rules actually check, and getting it
  wrong was one of two real bugs the official validator caught while building this (see `docs/VALIDATION.md`).
- **The Z (zero-rated) and S (standard-rated) categories must NOT carry a VAT exemption reason; every other
  category MUST.** This is backwards from what seems intuitive (zero-rated feels like it needs an explanation) and
  was the other real bug the official rules caught.
- **Every failure is dead-lettered** with the stage and reason (map, validate, or deliver), and nothing is ever
  sent to a sink without first passing the official EN 16931 rules (see `Pipeline.process`), unless
  `skip_schematron` is explicitly set for local iteration.
- **`PeppolSandboxSink` is provider-agnostic on purpose.** Every sandbox provider's API shape differs; this project
  does not pick one, it takes a URL and headers from whichever sandbox you've set up (see `docs/SETUP.md`).

## What's deliberately out of scope
Country-specific extension rules, CII format, real Peppol SMP/directory lookups, and actually running an Access
Point (this project is the integration layer that feeds one, not an Access Point itself). See `docs/VALIDATION.md`
for exactly what each of these would require.
