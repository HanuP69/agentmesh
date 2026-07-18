import React, { useMemo } from "react";

/**
 * Lightweight Markdown → HTML renderer (no npm deps).
 * Handles: h1-h4, bold, italic, bullet/numbered lists, code blocks, inline code, hr.
 */
function parseMarkdown(md) {
  if (!md) return "";
  const lines = md.split("\n");
  let html = "";
  let inCodeBlock = false;
  let inList = false;      // "ul" | "ol" | false
  let listType = null;

  const closeList = () => {
    if (inList) {
      html += `</${listType}>`;
      inList = false;
      listType = null;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];

    // Fenced code blocks
    if (line.trim().startsWith("```")) {
      if (inCodeBlock) {
        html += "</code></pre>";
        inCodeBlock = false;
      } else {
        closeList();
        inCodeBlock = true;
        html += "<pre><code>";
      }
      continue;
    }
    if (inCodeBlock) {
      html += escapeHtml(line) + "\n";
      continue;
    }

    // Horizontal rule
    if (/^(\*{3,}|-{3,}|_{3,})\s*$/.test(line.trim())) {
      closeList();
      html += "<hr/>";
      continue;
    }

    // Headers
    const headerMatch = line.match(/^(#{1,4})\s+(.*)/);
    if (headerMatch) {
      closeList();
      const level = headerMatch[1].length;
      html += `<h${level}>${inlineFormat(headerMatch[2])}</h${level}>`;
      continue;
    }

    // Unordered list items
    const ulMatch = line.match(/^(\s*)[*\-+]\s+(.*)/);
    if (ulMatch) {
      if (listType !== "ul") {
        closeList();
        html += "<ul>";
        inList = true;
        listType = "ul";
      }
      html += `<li>${inlineFormat(ulMatch[2])}</li>`;
      continue;
    }

    // Ordered list items
    const olMatch = line.match(/^(\s*)\d+\.\s+(.*)/);
    if (olMatch) {
      if (listType !== "ol") {
        closeList();
        html += "<ol>";
        inList = true;
        listType = "ol";
      }
      html += `<li>${inlineFormat(olMatch[2])}</li>`;
      continue;
    }

    // Non-list line → close any open list
    closeList();

    // Empty line
    if (line.trim() === "") {
      continue;
    }

    // Regular paragraph
    html += `<p>${inlineFormat(line)}</p>`;
  }

  closeList();
  if (inCodeBlock) html += "</code></pre>";
  return html;
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inlineFormat(text) {
  // Order matters: bold before italic
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, '<code class="inline-code">$1</code>')
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="md-inline-image" />');
}

export default function MarkdownRenderer({ content }) {
  const html = useMemo(() => parseMarkdown(content), [content]);
  return <div className="md-content" dangerouslySetInnerHTML={{ __html: html }} />;
}
