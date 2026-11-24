"""Render synthetic invoices to PDF with reportlab, in three deliberately different layouts.

The layouts differ in structure (grid table / minimal rows / monospaced text bill),
labels ("Invoice No." / "Invoice #" / "Bill No."), date formats and number formats
(Indian grouping / Western grouping / no grouping) so an extractor cannot rely on a
single template.

``rasterise()`` turns a digital PDF into a "scanned" image-only PDF: rendered at 150 dpi,
greyscale, slightly rotated and with seeded noise. It contains no text layer.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from world import STATES, InvoiceSpec, Party

PAGE_W, PAGE_H = A4
MARGIN = 40


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_indian(x: Decimal) -> str:
    """1234567.5 -> '12,34,567.50' (lakh/crore grouping)."""
    sign = "-" if x < 0 else ""
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}{whole}.{frac}"


def fmt_western(x: Decimal) -> str:
    return f"{x:,.2f}"


def fmt_plain(x: Decimal) -> str:
    return f"{x:.2f}"


def fmt_qty(q: Decimal) -> str:
    return f"{q.normalize():f}"


_ONES = (
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _words_below_1000(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def amount_in_words(x: Decimal) -> str:
    """Indian-system words, e.g. 'Rupees One Lakh Twenty Thousand and Fifty Paise Only'."""
    rupees = int(x)
    paise = int((x - rupees) * 100)
    parts = []
    for unit, size in (("Crore", 10_000_000), ("Lakh", 100_000), ("Thousand", 1000)):
        if rupees >= size:
            parts.append(f"{_words_below_1000(rupees // size)} {unit}")
            rupees %= size
    if rupees:
        parts.append(_words_below_1000(rupees))
    text = "Rupees " + (" ".join(parts) or "Zero")
    if paise:
        text += f" and {_words_below_1000(paise)} Paise"
    return text + " Only"


def _fit(c: Canvas, text: str, font: str, size: float, width: float) -> str:
    if c.stringWidth(text, font, size) <= width:
        return text
    while text and c.stringWidth(text + "...", font, size) > width:
        text = text[:-1]
    return text + "..."


def _tax_rows(inv: InvoiceSpec) -> list[tuple[str, Decimal]]:
    if inv.intra_state_billing:
        return [("CGST", inv.cgst), ("SGST", inv.sgst)]
    return [("IGST", inv.igst)]


def _draw_injection(c: Canvas, inv: InvoiceSpec, x: float, y: float, font: str) -> None:
    if not inv.injection_text:
        return
    if inv.injection_hidden:
        # White, 1pt text: invisible to a human, present in the PDF text layer.
        c.saveState()
        c.setFillColor(colors.white)
        c.setFont(font, 1)
        c.drawString(x, y, inv.injection_text)
        c.restoreState()
    else:
        c.saveState()
        c.setFont(font, 7)
        c.setFillColor(colors.HexColor("#555555"))
        c.drawString(x, y, _fit(c, "Note: " + inv.injection_text, font, 7, PAGE_W - 2 * MARGIN))
        c.restoreState()


# ---------------------------------------------------------------------------
# Layout A: classic GST tax invoice with a ruled table
# ---------------------------------------------------------------------------
def _layout_a(c: Canvas, inv: InvoiceSpec, buyer: Party) -> None:
    v = inv.vendor.party
    navy = colors.HexColor("#1F3A5F")
    c.setFillColor(navy)
    c.rect(0, PAGE_H - 95, PAGE_W, 95, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(MARGIN, PAGE_H - 40, v.name)
    c.setFont("Helvetica", 8.5)
    c.drawString(MARGIN, PAGE_H - 56, v.address)
    c.drawString(MARGIN, PAGE_H - 69, f"GSTIN: {inv.printed_gstin}")
    c.drawString(MARGIN, PAGE_H - 82, f"State: {STATES[v.state_code]} ({v.state_code})")

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 120, "TAX INVOICE")

    top = PAGE_H - 140
    c.setLineWidth(0.6)
    c.rect(MARGIN, top - 78, (PAGE_W - 2 * MARGIN) / 2 - 5, 78)
    c.rect(PAGE_W / 2 + 5, top - 78, (PAGE_W - 2 * MARGIN) / 2 - 5, 78)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN + 6, top - 14, "Bill To")
    c.setFont("Helvetica", 8.5)
    c.drawString(MARGIN + 6, top - 28, buyer.name)
    c.drawString(MARGIN + 6, top - 41, _fit(c, buyer.address, "Helvetica", 8.5, 240))
    c.drawString(MARGIN + 6, top - 54, f"GSTIN: {buyer.gstin}")
    meta = [
        ("Invoice No.", inv.invoice_number),
        ("Invoice Date", inv.invoice_date.strftime("%d-%m-%Y")),
        ("PO No.", inv.printed_po_number or "-"),
        ("Place of Supply", f"{STATES[buyer.state_code]} ({buyer.state_code})"),
    ]
    for k, (label, value) in enumerate(meta):
        y = top - 16 - k * 15
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(PAGE_W / 2 + 12, y, f"{label}:")
        c.setFont("Helvetica", 8.5)
        c.drawString(PAGE_W / 2 + 95, y, value)

    cols = [
        MARGIN,
        MARGIN + 22,
        MARGIN + 85,
        MARGIN + 290,
        MARGIN + 330,
        MARGIN + 380,
        MARGIN + 445,
        PAGE_W - MARGIN,
    ]
    heads = ["#", "Item Code", "Description", "GST %", "Qty", "Rate", "Amount"]
    y = top - 105
    c.setFillColor(colors.HexColor("#E8EEF5"))
    c.rect(MARGIN, y - 5, PAGE_W - 2 * MARGIN, 18, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    for k, h in enumerate(heads):
        c.drawString(cols[k] + 4, y + 1, h)
    row_h = 18
    c.setFont("Helvetica", 8.5)
    for n, ln in enumerate(inv.lines, start=1):
        y -= row_h
        c.drawString(cols[0] + 4, y + 1, str(n))
        c.drawString(cols[1] + 4, y + 1, ln.sku)
        c.drawString(cols[2] + 4, y + 1, _fit(c, ln.description, "Helvetica", 8.5, 198))
        c.drawRightString(cols[4] - 6, y + 1, f"{fmt_qty(ln.tax_rate)}%")
        c.drawRightString(cols[5] - 6, y + 1, fmt_qty(ln.qty))
        c.drawRightString(cols[6] - 6, y + 1, fmt_indian(ln.unit_price))
        c.drawRightString(cols[7] - 6, y + 1, fmt_indian(ln.line_total))
    table_top, table_bottom = top - 105 + 13, y - 6
    c.rect(MARGIN, table_bottom, PAGE_W - 2 * MARGIN, table_top - table_bottom)
    for x in cols[1:-1]:
        c.line(x, table_bottom, x, table_top)

    y = table_bottom - 20
    rows = [("Taxable Value", inv.subtotal), *_tax_rows(inv)]
    for label, amount in rows:
        c.setFont("Helvetica", 9)
        c.drawString(PAGE_W - MARGIN - 200, y, label)
        c.drawRightString(PAGE_W - MARGIN - 6, y, fmt_indian(amount))
        y -= 15
    y -= 8
    c.setFillColor(navy)
    c.rect(PAGE_W - MARGIN - 206, y - 6, 206, 20, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(PAGE_W - MARGIN - 200, y, "Grand Total (INR)")
    c.drawRightString(PAGE_W - MARGIN - 6, y, fmt_indian(inv.grand_total))
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(MARGIN, y - 30, f"Amount in words: {amount_in_words(inv.grand_total)}")

    c.setFont("Helvetica", 8)
    c.drawString(
        MARGIN, 90, "Declaration: We declare that this invoice shows the actual price of the"
    )
    c.drawString(MARGIN, 79, "goods described and that all particulars are true and correct.")
    c.setFont("Helvetica-Bold", 8.5)
    c.drawRightString(PAGE_W - MARGIN, 90, f"For {v.name}")
    c.setFont("Helvetica", 8)
    c.drawRightString(PAGE_W - MARGIN, 55, "Authorised Signatory")
    _draw_injection(c, inv, MARGIN, 120, "Helvetica")


# ---------------------------------------------------------------------------
# Layout B: modern minimal, Western number grouping, no grid
# ---------------------------------------------------------------------------
def _layout_b(c: Canvas, inv: InvoiceSpec, buyer: Party) -> None:
    v = inv.vendor.party
    grey = colors.HexColor("#6B7280")
    accent = colors.HexColor("#0F766E")
    c.setFont("Helvetica-Bold", 15)
    c.drawString(MARGIN, PAGE_H - 55, v.name)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(grey)
    c.drawString(MARGIN, PAGE_H - 70, v.address)
    c.drawString(MARGIN, PAGE_H - 82, f"GST No. {inv.printed_gstin}")

    c.setFont("Helvetica-Bold", 26)
    c.setFillColor(colors.HexColor("#D1D5DB"))
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - 60, "INVOICE")
    meta = [
        ("Invoice #", inv.invoice_number),
        ("Issued", inv.invoice_date.strftime("%d %b %Y")),
        ("Purchase Order", inv.printed_po_number or "Not quoted"),
    ]
    for k, (label, value) in enumerate(meta):
        y = PAGE_H - 85 - k * 14
        c.setFont("Helvetica", 8.5)
        c.setFillColor(grey)
        c.drawRightString(PAGE_W - MARGIN - 110, y, label)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(PAGE_W - MARGIN, y, value)

    y = PAGE_H - 150
    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN, y, "BILLED TO")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    c.drawString(MARGIN, y - 14, buyer.name)
    c.setFillColor(grey)
    c.setFont("Helvetica", 8.5)
    c.drawString(MARGIN, y - 27, buyer.address)
    c.drawString(MARGIN, y - 39, f"GST No. {buyer.gstin}")

    y -= 80
    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 8)
    xs = {
        "item": MARGIN,
        "sku": MARGIN + 260,
        "qty": MARGIN + 370,
        "price": MARGIN + 445,
        "total": PAGE_W - MARGIN,
    }
    c.drawString(xs["item"], y, "ITEM")
    c.drawString(xs["sku"], y, "SKU")
    c.drawRightString(xs["qty"], y, "QTY")
    c.drawRightString(xs["price"], y, "UNIT PRICE")
    c.drawRightString(xs["total"], y, "TOTAL")
    c.setStrokeColor(accent)
    c.setLineWidth(1.2)
    c.line(MARGIN, y - 6, PAGE_W - MARGIN, y - 6)
    c.setStrokeColor(colors.HexColor("#E5E7EB"))
    c.setLineWidth(0.5)
    c.setFillColor(colors.black)
    for ln in inv.lines:
        y -= 24
        c.setFont("Helvetica", 9)
        c.drawString(xs["item"], y, _fit(c, ln.description, "Helvetica", 9, 250))
        c.setFillColor(grey)
        c.drawString(xs["sku"], y, ln.sku)
        c.setFillColor(colors.black)
        c.drawRightString(xs["qty"], y, fmt_qty(ln.qty))
        c.drawRightString(xs["price"], y, fmt_western(ln.unit_price))
        c.drawRightString(xs["total"], y, fmt_western(ln.line_total))
        c.line(MARGIN, y - 9, PAGE_W - MARGIN, y - 9)

    y -= 40
    for label, amount in [("Subtotal", inv.subtotal), *_tax_rows(inv)]:
        c.setFont("Helvetica", 9)
        c.setFillColor(grey)
        c.drawRightString(PAGE_W - MARGIN - 110, y, label)
        c.setFillColor(colors.black)
        c.drawRightString(PAGE_W - MARGIN, y, fmt_western(amount))
        y -= 16
    y -= 8
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(accent)
    c.drawRightString(PAGE_W - MARGIN - 110, y, "Total Due (INR)")
    c.drawRightString(PAGE_W - MARGIN, y, fmt_western(inv.grand_total))

    c.setFillColor(grey)
    c.setFont("Helvetica", 8)
    c.drawString(MARGIN, 70, "Payment terms: Net 30 days from invoice date.")
    c.drawString(MARGIN, 58, "Please quote the invoice number with your remittance.")
    _draw_injection(c, inv, MARGIN, 100, "Helvetica")


# ---------------------------------------------------------------------------
# Layout C: monospaced "bill" printout, plain numbers, "Rs." prefix
# ---------------------------------------------------------------------------
def _layout_c(c: Canvas, inv: InvoiceSpec, buyer: Party) -> None:
    v = inv.vendor.party
    font, bold = "Courier", "Courier-Bold"
    rule = "-" * 86
    y = PAGE_H - 50
    c.setFont(bold, 13)
    c.drawCentredString(PAGE_W / 2, y, v.name.upper())
    c.setFont(font, 8.5)
    c.drawCentredString(PAGE_W / 2, y - 14, v.address)
    c.drawCentredString(PAGE_W / 2, y - 26, f"GSTIN/UIN : {inv.printed_gstin}")
    c.setFont(font, 8)
    c.drawString(MARGIN, y - 40, rule)
    c.setFont(bold, 11)
    c.drawCentredString(PAGE_W / 2, y - 54, "BILL / TAX INVOICE")
    c.setFont(font, 8)
    c.drawString(MARGIN, y - 64, rule)

    y -= 82
    c.setFont(font, 9)
    left = [
        f"Bill No.      : {inv.invoice_number}",
        f"Dated         : {inv.invoice_date.isoformat()}",
        f"Order Ref (PO): {inv.printed_po_number or 'NIL'}",
    ]
    right = [
        f"Buyer : {buyer.name}",
        f"GSTIN : {buyer.gstin}",
        f"State : {STATES[buyer.state_code]}",
    ]
    for k in range(3):
        c.drawString(MARGIN, y - k * 13, left[k])
        c.drawString(PAGE_W / 2 + 10, y - k * 13, right[k])

    y -= 52
    c.setFont(font, 8)
    c.drawString(MARGIN, y + 10, rule)
    c.setFont(bold, 8.5)
    header = f"{'CODE':<10}{'DESCRIPTION':<38}{'QTY':>8}{'RATE':>12}{'AMOUNT':>14}"
    c.drawString(MARGIN, y, header)
    c.setFont(font, 8)
    c.drawString(MARGIN, y - 10, rule)
    c.setFont(font, 8.5)
    for ln in inv.lines:
        y -= 22
        desc = ln.description if len(ln.description) <= 36 else ln.description[:35] + "."
        row = (
            f"{ln.sku:<10}{desc:<38}{fmt_qty(ln.qty):>8}"
            f"{fmt_plain(ln.unit_price):>12}{fmt_plain(ln.line_total):>14}"
        )
        c.drawString(MARGIN, y, row)
    y -= 14
    c.setFont(font, 8)
    c.drawString(MARGIN, y, rule)

    y -= 18
    c.setFont(font, 9)
    rows = [("Sub Total", inv.subtotal)] + [(f"Add: {n}", a) for n, a in _tax_rows(inv)]
    for label, amount in rows:
        c.drawString(PAGE_W / 2 + 10, y, f"{label:<18}Rs.{fmt_plain(amount):>14}")
        y -= 14
    c.setFont(bold, 10)
    c.drawString(PAGE_W / 2 + 10, y - 4, f"{'NET PAYABLE':<18}Rs.{fmt_plain(inv.grand_total):>14}")
    c.setFont(font, 8)
    c.drawString(MARGIN, y - 30, amount_in_words(inv.grand_total))
    c.drawString(MARGIN, 80, "E.& O.E.  Subject to local jurisdiction.")
    c.drawRightString(PAGE_W - MARGIN, 80, "Receiver's Signature        Authorised Signatory")
    _draw_injection(c, inv, MARGIN, 110, font)


LAYOUT_RENDERERS = {"A": _layout_a, "B": _layout_b, "C": _layout_c}


def render_invoice(inv: InvoiceSpec, buyer: Party, path: Path) -> None:
    """Write a single-page digital PDF. ``invariant=1`` makes the bytes reproducible."""
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(f"Invoice {inv.invoice_number}")
    c.setAuthor(inv.vendor.party.name)
    c.setCreator("TriMatch synthetic dataset")
    LAYOUT_RENDERERS[inv.layout](c, inv, buyer)
    c.showPage()
    c.save()


def rasterise(src: Path, dst: Path, rng: random.Random, dpi: int = 150) -> None:
    """Simulate a scan: render to greyscale, rotate slightly, add seeded noise, save image PDF."""
    doc = pdfium.PdfDocument(str(src))
    try:
        img = doc[0].render(scale=dpi / 72).to_pil().convert("L")
    finally:
        doc.close()
    angle = rng.uniform(0.6, 1.8) * rng.choice((-1, 1))
    img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    noise = Image.frombytes("L", img.size, rng.randbytes(img.size[0] * img.size[1]))
    img = Image.blend(img, noise, alpha=0.10)
    # Slight greying of the paper, like a cheap office scanner.
    img = img.point(lambda p: int(p * 0.93 + 8))
    # Pillow stamps the current time into the PDF unless told otherwise; pin it so the
    # output is byte-for-byte reproducible.
    fixed = dt.datetime(2024, 11, 24, 12, 0, 0, tzinfo=dt.UTC).utctimetuple()
    img.save(
        dst,
        "PDF",
        resolution=float(dpi),
        quality=70,
        creationDate=fixed,
        modDate=fixed,
        producer="TriMatch synthetic dataset",
    )
