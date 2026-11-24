import assert from "node:assert/strict";
import { test } from "node:test";

import { basicAuthHeader } from "./auth.ts";
import { describeError } from "./errors.ts";
import { checkLabel, confidenceLevel, formatMoney, formatQty, statusLabel } from "./format.ts";

test("money uses Indian digit grouping and always shows paise", () => {
  assert.equal(formatMoney("1234567.5"), "₹12,34,567.50");
  assert.equal(formatMoney("0"), "₹0.00");
  assert.equal(formatMoney("999.999"), "₹1,000.00");
});

test("missing or junk money renders as a dash instead of NaN", () => {
  for (const v of [null, undefined, "", "abc", "NaN"]) assert.equal(formatMoney(v as string), "—");
});

test("quantities drop trailing zeros", () => {
  assert.equal(formatQty("10.000"), "10");
  assert.equal(formatQty("2.5"), "2.5");
  assert.equal(formatQty(null), "—");
});

test("confidence is flagged below the policy minimum", () => {
  assert.equal(confidenceLevel(1), "high");
  assert.equal(confidenceLevel(0.9), "high"); // exactly the minimum passes, like the policy
  assert.equal(confidenceLevel(0.8999), "medium");
  assert.equal(confidenceLevel(0.59), "low");
  assert.equal(confidenceLevel(0.5, 0.4), "high"); // the minimum is configurable
});

test("labels", () => {
  assert.equal(statusLabel("NEEDS_REVIEW"), "Needs Review");
  assert.equal(checkLabel("unit_price"), "Unit prices match PO");
  assert.equal(checkLabel("something_new"), "something_new");
});

test("basic auth header is correct and handles non-ASCII", () => {
  assert.equal(basicAuthHeader("reviewer", "secret"), "Basic " + btoa("reviewer:secret"));
  const header = basicAuthHeader("réviewer", "pässwörd:€");
  const decoded = Buffer.from(header.slice(6), "base64").toString("utf-8");
  assert.equal(decoded, "réviewer:pässwörd:€");
});

test("API errors become readable sentences", () => {
  assert.equal(describeError({ detail: "Invoice not found." }, 404), "Invoice not found.");
  assert.equal(
    describeError({ detail: [{ loc: ["body", "comment"], msg: "Value error, a comment is required" }] }, 422),
    "comment: Value error, a comment is required",
  );
  assert.equal(describeError(null, 413), "That file is too large.");
  assert.equal(describeError("<html>", 502), "The server had a problem. Please try again.");
  assert.equal(describeError({}, 418), "Request failed (418).");
});
