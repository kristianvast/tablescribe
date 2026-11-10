// Small safe Markdown renderer: all text is HTML-escaped before any markup is added.

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

const PH = '\u0000';

function inline(src) {
  const codes = [];
  let s = String(src).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(`<code>${esc(c)}</code>`);
    return `${PH}${codes.length - 1}${PH}`;
  });
  s = esc(s);
  const links = [];
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, text, url) => {
    links.push(`<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`);
    return `${PH}L${links.length - 1}${PH}`;
  });
  s = s
    .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, '$1<strong>$2</strong>')
    .replace(/\*(?=\S)([^*]*?\S)\*/g, '<em>$1</em>')
    .replace(/(^|[^\w])_(?=\S)([^_]*?\S)_(?!\w)/g, '$1<em>$2</em>')
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>');
  s = s.replace(new RegExp(`${PH}L(\\d+)${PH}`, 'g'), (_, i) => links[+i]);
  return s.replace(new RegExp(`${PH}(\\d+)${PH}`, 'g'), (_, i) => codes[+i]);
}

const RE = {
  fence: /^\s*(```|~~~)\s*([\w+-]*)\s*$/,
  heading: /^(#{1,6})\s+(.*?)\s*#*\s*$/,
  hr: /^\s*([-*_])(\s*\1){2,}\s*$/,
  quote: /^\s*>\s?(.*)$/,
  ul: /^(\s*)[-*+]\s+(.*)$/,
  ol: /^(\s*)(\d+)[.)]\s+(.*)$/,
  tableSep: /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$|^\s*\|\s*:?-+:?\s*\|\s*$/,
  task: /^\[([ xX])\]\s+(.*)$/,
};

const isListLine = (l) => RE.ul.test(l) || RE.ol.test(l);
const indentOf = (l) => l.match(/^\s*/)[0].replace(/\t/g, '    ').length;

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

function renderTable(lines) {
  const head = splitRow(lines[0]);
  const aligns = splitRow(lines[1]).map((c) => {
    const l = c.startsWith(':');
    const r = c.endsWith(':');
    return l && r ? 'center' : r ? 'right' : l ? 'left' : '';
  });
  const cell = (tag, c, i) => `<${tag}${aligns[i] ? ` style="text-align:${aligns[i]}"` : ''}>${inline(c)}</${tag}>`;
  const body = lines.slice(2).map((l) => `<tr>${splitRow(l).map((c, i) => cell('td', c, i)).join('')}</tr>`);
  return `<table><thead><tr>${head.map((c, i) => cell('th', c, i)).join('')}</tr></thead>`
    + `<tbody>${body.join('')}</tbody></table>`;
}

function renderList(lines) {
  const base = indentOf(lines[0]);
  const ordered = !RE.ul.test(lines[0]) && RE.ol.test(lines[0]);
  const items = [];
  for (const line of lines) {
    const m = RE.ul.exec(line) || RE.ol.exec(line);
    if (m && indentOf(line) <= base) items.push({ text: m[m.length - 1], children: [] });
    else if (items.length) items[items.length - 1].children.push(line);
  }
  const start = ordered ? Number(RE.ol.exec(lines[0])[2]) : 1;
  const lis = items.map(({ text, children }) => {
    const nested = children.length ? renderMarkdown(dedent(children)) : '';
    const t = RE.task.exec(text);
    if (t) {
      const checked = t[1].toLowerCase() === 'x' ? ' checked' : '';
      return `<li class="task"><input type="checkbox" disabled${checked}> ${inline(t[2])}${nested}</li>`;
    }
    return `<li>${inline(text)}${nested}</li>`;
  });
  const tag = ordered ? 'ol' : 'ul';
  const startAttr = ordered && start !== 1 ? ` start="${start}"` : '';
  const cls = items.some((i) => RE.task.test(i.text)) ? ' class="tasks"' : '';
  return `<${tag}${startAttr}${cls}>${lis.join('')}</${tag}>`;
}

function dedent(lines) {
  const min = Math.min(...lines.filter((l) => l.trim()).map(indentOf));
  return lines.map((l) => l.replace(/\t/g, '    ').slice(Number.isFinite(min) ? min : 0)).join('\n');
}

export function renderMarkdown(md) {
  const lines = String(md ?? '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const fence = RE.fence.exec(line);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      const lang = fence[2] ? ` class="lang-${esc(fence[2])}"` : '';
      out.push(`<pre><code${lang}>${esc(body.join('\n'))}</code></pre>`);
      continue;
    }
    const h = RE.heading.exec(line);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (RE.hr.test(line)) { out.push('<hr>'); i++; continue; }
    if (RE.quote.test(line)) {
      const body = [];
      while (i < lines.length && RE.quote.test(lines[i])) body.push(RE.quote.exec(lines[i++])[1]);
      out.push(`<blockquote>${renderMarkdown(body.join('\n'))}</blockquote>`);
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && RE.tableSep.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const rows = [line, lines[i + 1]];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(lines[i++]);
      out.push(renderTable(rows));
      continue;
    }
    if (isListLine(line)) {
      const block = [line];
      const base = indentOf(line);
      i++;
      while (i < lines.length) {
        const l = lines[i];
        if (!l.trim()) {
          const next = lines[i + 1];
          if (next !== undefined && (isListLine(next) || indentOf(next) > base) && next.trim()) { i++; continue; }
          break;
        }
        if (isListLine(l) || indentOf(l) > base) { block.push(l); i++; continue; }
        break;
      }
      out.push(renderList(block));
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !RE.fence.test(lines[i]) && !RE.heading.test(lines[i])
      && !RE.hr.test(lines[i]) && !RE.quote.test(lines[i]) && !isListLine(lines[i])) {
      para.push(lines[i++].trim());
    }
    if (!para.length) { para.push(lines[i++].trim()); }
    out.push(`<p>${inline(para.join('\n')).replace(/\n/g, '<br>')}</p>`);
  }
  return out.join('\n');
}

const TS = /(\d{1,2}:)?\d{1,2}:\d{2}/;
const TS_GROUP = new RegExp(`\\[(${TS.source}(?:\\s*[,;-]\\s*${TS.source})*)\\]`, 'g');

function toSeconds(ts) {
  return ts.split(':').map(Number).reduce((acc, n) => acc * 60 + n, 0);
}

/** Turn [mm:ss] / [h:mm:ss] (and [mm:ss, mm:ss]) in text nodes into <button class="ts" data-t="seconds">. */
export function linkifyTimestamps(html) {
  const parts = String(html ?? '').split(/(<[^>]*>)/);
  let skip = 0;
  return parts.map((part) => {
    if (part.startsWith('<')) {
      if (/^<(code|pre|a|button)\b/i.test(part)) skip++;
      else if (/^<\/(code|pre|a|button)>/i.test(part)) skip = Math.max(0, skip - 1);
      return part;
    }
    if (skip) return part;
    return part.replace(TS_GROUP, (whole, inner) => inner.replace(new RegExp(TS.source, 'g'), (ts) =>
      `<button type="button" class="ts ts-link" data-t="${toSeconds(ts)}">[${ts}]</button>`));
  }).join('');
}
