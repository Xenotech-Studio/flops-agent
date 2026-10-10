import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { isAbsolute, join, posix } from 'node:path';
import { renderMarkdown } from './markdown.mjs';
import { dropSections } from './docs-transforms.mjs';
const website = fileURLToPath(new URL('../', import.meta.url));
const override = process.env.FLOPS_AGENT_DOCS_DIR;
if (override !== undefined && !isAbsolute(override)) throw new Error('FLOPS_AGENT_DOCS_DIR must be an absolute path');
const sourceDir = override ?? fileURLToPath(new URL('../../docs/', import.meta.url));
const sources = JSON.parse(await readFile(new URL('./docs-sources.json', import.meta.url), 'utf8'));
const definitions = JSON.parse(await readFile(new URL('./docs-sections.json', import.meta.url), 'utf8'));
if (!Array.isArray(definitions)) throw new Error('Section configuration must be an array');
const sectionMap = new Map();
for (const section of definitions) {
  if (!section || !/^[a-z][a-z0-9-]*$/.test(section.id) || sectionMap.has(section.id) || typeof section.title !== 'string' || !section.title.trim() || !Number.isSafeInteger(section.order) || !['use', 'contract'].includes(section.kind) || (section.navigation !== undefined && typeof section.navigation !== 'boolean')) throw new Error('Invalid or duplicate section configuration');
  sectionMap.set(section.id, section);
}
for (const entry of sources) {
  if (!sectionMap.has(entry.section)) throw new Error(`${entry.slug}: Unknown section ${entry.section}`);
  if (entry.overview !== undefined && typeof entry.overview !== 'boolean') throw new Error(`${entry.slug}: overview must be a boolean`);
}
const sections = definitions.map(section => {
  const entries = sources.filter(entry => entry.section === section.id);
  const overviews = entries.filter(entry => entry.overview);
  if (!entries.length || overviews.length > 1) throw new Error(`${section.id}: A section must contain pages and at most one overview`);
  const landing = overviews[0] ?? entries[0];
  return { ...section, href: landing.slug === 'overview' ? '/docs' : `/docs/${landing.slug}` };
}).sort((a, b) => a.order - b.order);
const github = 'https://github.com/Xenotech-Studio/flops-agent/';
const forbidden = /(?:^|\/)(?:TODO|DESIGN_NOTES_FROM_DOCS|IA)\.md(?:$|[#?])/i;
const routes = new Map(sources.map(s => [s.file, s.slug === 'overview' ? '/docs' : `/docs/${s.slug}`]));
function rewrite(markdown) {
  return markdown.replace(/(\]\()([^\s)]+)(\))/g, (_, start, href, end) => {
    if (/^(?:[a-z]+:|\/|#)/i.test(href)) return start + href + end;
    if (forbidden.test(href)) throw new Error(`Disallowed documentation link: ${href}`);
    const [file, hash] = href.split('#');
    const normalized = posix.normalize(file);
    const target = routes.get(normalized);
    return start + (target ? target + (hash ? `#${hash}` : '') : github + (file.endsWith('/') ? 'tree/' : 'blob/') + 'master/' + posix.normalize('docs/' + file) + (hash ? `#${hash}` : '')) + end;
  });
}
// Read every required local source before touching generated output. Explicit allowlist only.
const pages = await Promise.all(sources.map(async (entry, order) => {
  if (forbidden.test(entry.file) || !/^[\w-]+\.md$/.test(entry.file) || !/^[\w-]+$/.test(entry.slug)) throw new Error('Invalid documentation manifest');
  let markdown;
  try { markdown = await readFile(join(sourceDir, entry.file), 'utf8'); }
  catch (error) { throw new Error(`Missing required document ${entry.file}: ${error.message}`); }
  markdown = dropSections(markdown, entry.drop_sections);
  if (entry.publication_note) {
    if (typeof entry.publication_note !== 'string' || /[\r\n]/.test(entry.publication_note)) throw new Error('publication_note must be a single line');
    markdown = markdown.replace(/^(#\s+.+)$/m, `$1\n\n${entry.publication_note}`);
  }
  const h1 = markdown.match(/^#\s+(.+)$/m)?.[1];
  if (!h1) throw new Error(`${entry.file} Missing H1`);
  markdown = rewrite(markdown);
  const title = h1;
  const href = routes.get(entry.file);
  const source = `docs/${entry.file}`;
  let { html, headings, summary } = renderMarkdown(markdown);
  // Preserve published heading URLs when a tutorial is rewritten or split.
  // Aliases resolve to an existing semantic section; moved topics link onward.
  const aliases = entry.heading_aliases ?? {};
  if (!aliases || Array.isArray(aliases) || typeof aliases !== 'object') throw new Error(`${entry.slug}: Invalid heading_aliases`);
  for (const [id, target] of Object.entries(aliases)) {
    const heading = headings.find(h => h.text === target);
    if (!/^[\p{L}\p{N}-]+$/u.test(id) || !heading || headings.some(h => h.id === id)) throw new Error(`${entry.slug}: Invalid or conflicting heading alias ${id}`);
    html = html.replace(`<h${heading.level} id="${heading.id}">`, `<span id="${id}" class="docs-heading-alias" aria-hidden="true"></span><h${heading.level} id="${heading.id}">`);
  }
  const lang = entry.lang ?? 'en';
  if (!['zh-CN', 'en'].includes(lang)) throw new Error(`${entry.slug}: Invalid lang`);
  const section = sections.find(section => section.id === entry.section);
  const header = { canonical: href, section: section.id, title, group: entry.group, status: 'Content from the local clone used for this build', source, overview: entry.overview === true };
  const publicMarkdown = '---\n' + Object.entries(header).map(([k,v]) => `${k}: ${JSON.stringify(v)}`).join('\n') + '\n---\n\n' + markdown;
  return { slug: entry.slug, section: section.id, section_title: section.title, section_url: section.href, section_order: section.order, kind: section.kind, title, group: entry.group, order, href, source, lang, markdown, publicMarkdown, html, headings, heading_aliases: aliases, summary, overview: header.overview };
}));
if (new Set(pages.map(p => p.slug)).size !== pages.length) throw new Error('Duplicate slug');
for (const dir of ['src/content/docs', 'public/docs-source']) {
  await rm(join(website, dir), { recursive: true, force: true });
  await mkdir(join(website, dir), { recursive: true });
}
for (const page of pages) {
  await writeFile(join(website, 'src/content/docs', page.slug + '.md'), page.markdown);
  await writeFile(join(website, 'public/docs-source', page.slug + '.md'), page.publicMarkdown);
}
await writeFile(join(website, 'src/content/docs/_meta.json'), JSON.stringify({ sections, pages }, null, 2) + '\n');
await writeFile(join(website, 'public/docs-index.json'), JSON.stringify(pages.map(p => ({ slug: p.slug, title: p.title, summary: p.summary, section: p.section, section_title: p.section_title, section_url: p.section_url, section_order: p.section_order, kind: p.kind, status: 'Content from the local clone used for this build', canonical_url: p.href, markdown_url: `/docs-source/${p.slug}.md`, source: p.source, group: p.group, overview: p.overview, headings: p.headings, heading_aliases: p.heading_aliases })), null, 2) + '\n');
console.log(`Synced ${pages.length} pages from local docs; explicit publication manifest only.`);
