"""Shared enumerations.

These names are part of the contract between the dataset generator (ground truth),
the matching engine, the policy engine, the database and the eval harness, so they
live in one place.
"""

from enum import StrEnum


class Decision(StrEnum):
    AUTO_APPROVE = "AUTO_APPROVE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECT = "REJECT"


class DecidedBy(StrEnum):
    POLICY = "policy"
    HUMAN = "human"


class InvoiceStatus(StrEnum):
    PROCESSING = "PROCESSING"
    AUTO_APPROVED = "AUTO_APPROVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"  # approved by a human reviewer
    REJECTED = "REJECTED"  # rejected by policy or by a human reviewer
    FAILED = "FAILED"  # pipeline error; the invoice still needs a human


class POStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CheckName(StrEnum):
    """Every deterministic check from build plan section 4."""

    # 4.1 Validation
    GSTIN_FORMAT = "gstin_format"
    GSTIN_MATCHES_VENDOR = "gstin_matches_vendor"
    TAX_TYPE = "tax_type"
    ARITHMETIC = "arithmetic"
    # 4.2 Matching
    PO_EXISTS = "po_exists"
    PO_VENDOR_MATCH = "po_vendor_match"
    UNIT_PRICE = "unit_price"
    QUANTITY = "quantity"
    LINE_COVERAGE = "line_coverage"
    # 4.3 Duplicates
    EXACT_DUPLICATE = "exact_duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
