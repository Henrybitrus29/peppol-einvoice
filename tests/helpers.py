def party(name, country, vat_prefix=None):
    return {"name": name, "vat_id": f"{vat_prefix or country}{abs(hash(name)) % 10**9:09d}",
            "address": {"line1": "1 Example St", "city": "City", "postal_code": "1000", "country": country}}


def base_invoice(**overrides):
    inv = {
        "invoice_number": "INV-0001",
        "issue_date": "2026-03-14",
        "currency": "EUR",
        "order_reference": "PO-100",
        "seller": party("Seller Co", "BE"),
        "buyer": party("Buyer Co", "BE"),
        "lines": [{"name": "Service", "quantity": 1, "unit_price": "100.00", "unit_code": "EA",
                   "vat_category": "S", "vat_rate": "21"}],
    }
    inv.update(overrides)
    return inv
