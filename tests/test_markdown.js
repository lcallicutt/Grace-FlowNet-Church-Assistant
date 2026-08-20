/* Regression tests for static/markdown.js — run with: node tests/test_markdown.js */
"use strict";

const { escapeHtml, renderMarkdown, safeRenderMarkdown } = require("../static/markdown.js");

let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    passed++;
    console.log("  ✓ " + name);
  } catch (err) {
    failed++;
    console.error("  ✗ " + name + "\n      " + err.message);
  }
}

function assertIncludes(haystack, needle, label) {
  if (!haystack.includes(needle)) {
    throw new Error((label || "expected output to include") + " " + JSON.stringify(needle) + "\n      got: " + JSON.stringify(haystack));
  }
}

function assertExcludes(haystack, needle, label) {
  if (haystack.includes(needle)) {
    throw new Error((label || "expected output NOT to include") + " " + JSON.stringify(needle) + "\n      got: " + JSON.stringify(haystack));
  }
}

console.log("renderMarkdown");

check("plain text becomes a paragraph", () => {
  assertIncludes(renderMarkdown("Hello there."), "<p>Hello there.</p>");
});

check("ordinary numbers in text survive (the original crash)", () => {
  // This exact shape crashed the old renderer: a standalone number matched
  // the space-delimited code-block placeholder regex.
  const out = renderMarkdown("We need 5 greeters and 12 ushers by 8 sharp");
  assertIncludes(out, "need 5 greeters");
  assertIncludes(out, "12 ushers");
  assertIncludes(out, "by 8 sharp");
  assertExcludes(out, "<pre>", "plain numbers must not become code blocks:");
});

check("times like 9:00 AM render intact", () => {
  const out = renderMarkdown("Service at 9:00 AM and 11:00 AM. Greeters arrive at 8 AM.");
  assertIncludes(out, "9:00 AM");
  assertIncludes(out, "11:00 AM");
  assertIncludes(out, "at 8 AM");
  assertExcludes(out, "<pre>");
});

check("headings render at all three levels", () => {
  const out = renderMarkdown("# One\n## Two\n### Three");
  assertIncludes(out, "<h1>One</h1>");
  assertIncludes(out, "<h2>Two</h2>");
  assertIncludes(out, "<h3>Three</h3>");
});

check("bold text renders", () => {
  assertIncludes(renderMarkdown("a **bold move** here"), "<strong>bold move</strong>");
});

check("italic text renders", () => {
  assertIncludes(renderMarkdown("an *emphasized* word"), "<em>emphasized</em>");
});

check("numbered lists render as <ol>", () => {
  const out = renderMarkdown("1. Welcome\n2. Worship\n3. Sermon");
  assertIncludes(out, "<ol>");
  assertIncludes(out, "<li>Welcome</li>");
  assertIncludes(out, "<li>Sermon</li>");
  assertIncludes(out, "</ol>");
});

check("bullet lists render as <ul>", () => {
  const out = renderMarkdown("- Greeters\n- Ushers\n- Nursery");
  assertIncludes(out, "<ul>");
  assertIncludes(out, "<li>Greeters</li>");
  assertIncludes(out, "</ul>");
});

check("horizontal rules render", () => {
  assertIncludes(renderMarkdown("above\n---\nbelow"), "<hr>");
});

check("code blocks render with contents preserved", () => {
  const out = renderMarkdown("Before\n```\nSunday, March 9\n9:00 AM Service\n```\nAfter");
  assertIncludes(out, "<pre><code>");
  assertIncludes(out, "Sunday, March 9");
  assertIncludes(out, "9:00 AM Service");
  assertIncludes(out, "<p>Before</p>");
  assertIncludes(out, "<p>After</p>");
});

check("code block language tags are stripped", () => {
  const out = renderMarkdown("```python\nx = 1\n```");
  assertIncludes(out, "x = 1");
  assertExcludes(out, "python");
});

check("multiple code blocks restore to the right places", () => {
  const out = renderMarkdown("```\nfirst\n```\nmiddle with 7 items\n```\nsecond\n```");
  const firstIdx = out.indexOf("first");
  const secondIdx = out.indexOf("second");
  if (!(firstIdx >= 0 && secondIdx > firstIdx)) {
    throw new Error("blocks out of order: " + JSON.stringify(out));
  }
  assertIncludes(out, "7 items");
});

check("inline code renders and escapes", () => {
  assertIncludes(renderMarkdown("use `x < y` here"), "<code>x &lt; y</code>");
});

console.log("\nXSS escaping");

check("script tags are escaped, never live", () => {
  const out = renderMarkdown('<script>alert(1)</script>');
  assertIncludes(out, "&lt;script&gt;");
  assertExcludes(out, "<script>");
});

check("HTML inside code blocks is escaped", () => {
  const out = renderMarkdown('```\n<img src=x onerror=alert(1)>\n```');
  assertIncludes(out, "&lt;img");
  assertExcludes(out, "<img");
});

check("HTML in headings and lists is escaped", () => {
  const out = renderMarkdown("# <b>hi</b>\n- <i>there</i>");
  assertExcludes(out, "<b>");
  assertExcludes(out, "<i>");
});

check("escapeHtml handles non-strings", () => {
  assertIncludes(escapeHtml(42), "42");
});

console.log("\ntype validation and safe fallbacks");

check("null input does not throw", () => {
  const out = renderMarkdown(null);
  if (typeof out !== "string") throw new Error("expected string output");
});

check("number input is coerced", () => {
  assertIncludes(renderMarkdown(42), "42");
});

check("undefined input does not throw", () => {
  renderMarkdown(undefined);
});

check("input containing placeholder control chars cannot address code blocks", () => {
  const out = renderMarkdown("evil \x005\x01 attempt");
  assertExcludes(out, "undefined");
  assertExcludes(out, "<pre>");
});

check("safeRenderMarkdown never throws and preserves escaping", () => {
  const out = safeRenderMarkdown('<script>x</script> at 9 AM');
  assertIncludes(out, "&lt;script&gt;");
  assertExcludes(out, "<script>");
  safeRenderMarkdown(null);
  safeRenderMarkdown({ unexpected: "object" });
});

console.log("\n" + passed + " passed, " + failed + " failed");
process.exit(failed ? 1 : 0);
