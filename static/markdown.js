/* Grace Ministry Hub — minimal markdown renderer.
 *
 * Loaded by the browser (window.GraceMarkdown) and by the Node test suite
 * (module.exports). All input is HTML-escaped before any markup is added,
 * so model output can never inject live HTML/script into the page.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.GraceMarkdown = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Code-block placeholders use control characters (\x00 index \x01) that
  // cannot collide with ordinary text the way the old space-delimited
  // " N " placeholders did — those matched any plain number in a sentence
  // ("arrive at 9 AM") and crashed the renderer.
  const PLACEHOLDER_RE = /\x00(\d+)\x01/g;

  function renderMarkdown(text) {
    if (typeof text !== "string") {
      console.error(
        "renderMarkdown: expected a string, got",
        typeof text,
        text
      );
      text = text == null ? "" : String(text);
    }

    // Strip any pre-existing placeholder delimiters so untrusted input
    // can't address the codeBlocks array.
    text = text.replace(/[\x00\x01]/g, "");

    const codeBlocks = [];
    text = text.replace(/```([\s\S]*?)```/g, (_, code) => {
      codeBlocks.push(code.replace(/^\w*\n/, ""));
      return "\x00" + (codeBlocks.length - 1) + "\x01";
    });

    let html = escapeHtml(text);
    html = html
      .replace(/^### (.*)$/gm, "<h3>$1</h3>")
      .replace(/^## (.*)$/gm, "<h2>$1</h2>")
      .replace(/^# (.*)$/gm, "<h1>$1</h1>")
      .replace(/^---+$/gm, "<hr>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/`([^`\n]+)`/g, "<code>$1</code>");

    // Lists: group consecutive "- " or "N. " lines.
    const lines = html.split("\n");
    const out = [];
    let listType = null;
    for (const line of lines) {
      const ul = line.match(/^\s*[-•]\s+(.*)/);
      const ol = line.match(/^\s*\d+\.\s+(.*)/);
      if (ul || ol) {
        const want = ul ? "ul" : "ol";
        if (listType !== want) {
          if (listType) out.push("</" + listType + ">");
          out.push("<" + want + ">");
          listType = want;
        }
        out.push("<li>" + (ul ? ul[1] : ol[1]) + "</li>");
      } else {
        if (listType) {
          out.push("</" + listType + ">");
          listType = null;
        }
        if (line.match(/^<h\d|^<hr/)) out.push(line);
        else if (line.trim() === "") out.push("");
        else out.push("<p>" + line + "</p>");
      }
    }
    if (listType) out.push("</" + listType + ">");
    html = out.join("\n");

    // Restore code blocks. Guarded: a placeholder index with no stored
    // block renders as nothing instead of crashing on undefined.
    html = html.replace(PLACEHOLDER_RE, (match, i) => {
      const code = codeBlocks[+i];
      if (code === undefined) {
        console.error("renderMarkdown: missing code block for placeholder", i);
        return "";
      }
      return "<pre><code>" + escapeHtml(code) + "</code></pre>";
    });
    return html;
  }

  // Never-throws wrapper for UI call sites: logs the real error and falls
  // back to escaped plain text so a rendering bug can't blank the reply or
  // masquerade as a network failure.
  function safeRenderMarkdown(text) {
    try {
      return renderMarkdown(text);
    } catch (err) {
      console.error("Markdown rendering failed; showing plain text.", err);
      const plain = text == null ? "" : String(text);
      return "<p>" + escapeHtml(plain).replace(/\n/g, "<br>") + "</p>";
    }
  }

  return { escapeHtml, renderMarkdown, safeRenderMarkdown };
});
