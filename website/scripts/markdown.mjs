// Deliberately small renderer for this repository's Markdown dialect. Raw HTML is
// escaped, URLs are allowlisted, and heading IDs come from this same parse.
const escape = (s) => s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function renderMarkdown(source) {
  const references = new Map();
  // Articles 1–2 use escaped backticks even around code fences. Interpret
  // that legacy notation for display only; all Markdown exports stay untouched.
  const lines = source.replace(/\\`/g, '`').replace(/\r\n?/g, '\n').split('\n').filter(line => {
    const ref = line.match(/^\[([^\]]+)\]:\s*(\S+)\s*$/);
    if (ref) { references.set(ref[1].toLowerCase(), ref[2]); return false; }
    return true;
  });
  function link(label, url) {
    return /^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(url)
      ? `<a href="${escape(url)}">${inline(label)}</a>` : inline(label);
  }
  function inline(text) {
    const pattern = /\\([\\`*_[\]{}()#+.!|>-])|`([^`]+)`|\[([^\]]+)\]\(([^\s)]+)\)|\[([^\]]+)\]|\*\*([^*]+)\*\*|\*([^*]+)\*/g;
    let result = '', last = 0;
    for (const match of text.matchAll(pattern)) {
      result += escape(text.slice(last, match.index));
      if (match[1]) result += escape(match[1]);
      else if (match[2]) result += `<code>${escape(match[2])}</code>`;
      else if (match[3]) result += link(match[3], match[4]);
      else if (match[5]) result += references.has(match[5].toLowerCase()) ? link(match[5], references.get(match[5].toLowerCase())) : escape(match[0]);
      else if (match[6]) result += `<strong>${inline(match[6])}</strong>`;
      else result += `<em>${inline(match[7])}</em>`;
      last = match.index + match[0].length;
    }
    return result + escape(text.slice(last));
  }
  const headings = [], ids = new Map();
  let html = '', summary = '';
  const special = line => /^(#{1,6}\s| {4}|```|~~~|[-*+]\s|\d+\.\s|>\s)/.test(line);
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^(`{3,}|~{3,})(.*)$/);
    if (fence || /^ {4}/.test(line)) {
      const code = [];
      if (fence) {
        i++;
        while (i < lines.length && !lines[i].startsWith(fence[1])) code.push(lines[i++]);
        i++;
      } else {
        while (i < lines.length && (/^ {4}/.test(lines[i]) || !lines[i].trim())) code.push(lines[i++].replace(/^ {4}/, ''));
        while (code.at(-1) === '') code.pop();
      }
      html += `<pre tabindex="0" aria-label="Code block; scroll horizontally"><code>${escape(code.join('\n'))}</code></pre>`;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length, text = heading[2];
      const base = 'docs-' + text.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s+/g, '-');
      const count = ids.get(base) || 0; ids.set(base, count + 1);
      const id = base + (count ? `-${count}` : '');
      if (level === 2 || level === 3) headings.push({ level, text: text.replace(/[`*\[\]]/g, ''), id });
      html += `<h${level} id="${id}">${inline(text)}</h${level}>`; i++; continue;
    }
    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || '')) {
      const cells = row => row.trim().replace(/^\||\|$/g, '').split('|').map(s => s.trim());
      html += '<div class="docs-table" tabindex="0" role="region" aria-label="Table; scroll horizontally"><table><thead><tr>' + cells(line).map(c => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>'; i += 2;
      while (i < lines.length && lines[i].includes('|')) html += '<tr>' + cells(lines[i++]).map(c => `<td>${inline(c)}</td>`).join('') + '</tr>';
      html += '</tbody></table></div>'; continue;
    }
    const list = line.match(/^(?:([-*+])|\d+\.)\s+(.*)$/);
    if (list) {
      const tag = list[1] ? 'ul' : 'ol'; html += tag === 'ol' ? `<ol start="${Number(line.match(/^\d+/)?.[0] ?? 1)}">` : '<ul>';
      while (i < lines.length && /^(?:[-*+]|\d+\.)\s/.test(lines[i])) {
        let item = lines[i++].replace(/^(?:[-*+]|\d+\.)\s+/, '');
        while (i < lines.length && /^ {1,3}\S/.test(lines[i])) item += ' ' + lines[i++].trim();
        html += `<li>${inline(item)}</li>`;
      }
      html += `</${tag}>`; continue;
    }
    if (/^>\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^>/.test(lines[i])) quote.push(lines[i++].replace(/^>\s?/, ''));
      html += `<blockquote>${inline(quote.join(' '))}</blockquote>`; continue;
    }
    let paragraph = lines[i++];
    while (i < lines.length && lines[i].trim() && !special(lines[i])) paragraph += '\n' + lines[i++];
    if (!summary) summary = paragraph.replace(/\s+/g, ' ').slice(0, 200);
    html += `<p>${inline(paragraph)}</p>`;
  }
  return { html, headings, summary };
}
