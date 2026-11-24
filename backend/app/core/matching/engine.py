"""Runs every check for one invoice and returns the results. Pure: no I/O of any kind."""

from __future__ import annotations

from app.core.matching import duplicates, matching, validation
from app.core.matching.models import CheckResult, MatchConfig, MatchInputs


def run_three_way_match(inputs: MatchInputs, config: MatchConfig) -> list[CheckResult]:
    """Evaluate section 4 for one invoice.

    Checks that need a trustworthy PO (line coverage, price, quantity) are only run when the
    PO exists and belongs to the invoicing vendor: matching an invoice against someone else's
    PO would produce misleading failures. The policy engine treats a check that was not run
    as "not passed", so skipping can only ever make the outcome stricter.
    """
    inv = inputs.invoice
    vendor = inputs.vendor
    vendor_id = vendor.id if vendor else None

    supplier_state = vendor.state_code if vendor else None
    if supplier_state is None and validation.gstin_format(inv.vendor_gstin).passed:
        supplier_state = (inv.vendor_gstin or "").strip()[:2]

    results: list[CheckResult] = [
        validation.gstin_format(inv.vendor_gstin),
        validation.gstin_matches_vendor(inv.vendor_gstin, vendor),
        validation.tax_type(supplier_state, inputs.buyer_state_code, inv.cgst, inv.sgst, inv.igst),
        validation.arithmetic(inv, config),
        matching.po_exists(inputs.po, inv.po_number),
    ]

    if inputs.po is not None:
        owner = matching.po_vendor_match(inputs.po, vendor_id)
        results.append(owner)
        if owner.passed:
            mapped = matching.map_lines(inv.lines, inputs.po)
            results.append(matching.line_coverage(mapped))
            results.append(matching.unit_price(mapped, config))
            results.append(matching.quantity(mapped, inputs.received, inputs.already_invoiced))

    results.append(duplicates.exact_duplicate(vendor_id, inv.invoice_number, inputs.prior_invoices))
    results.append(
        duplicates.possible_duplicate(
            vendor_id,
            inv.grand_total,
            inv.invoice_date,
            inputs.prior_invoices,
            config.duplicate_window_days,
        )
    )
    return results
