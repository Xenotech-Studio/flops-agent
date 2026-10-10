import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { isAbsolute, join, posix } from 'node:path';
import { renderMarkdown } from './markdown.mjs';
import { dropSections } from './docs-transforms.mjs';
const website = fileURLToPath(new URL('../', import.meta.url));
const override = process.env.FLOPS_AGENT_DOCS_DIR;
if (override !== undefined && !isAbsolute(override)) throw new Error('FLOPS_AGENT_DOCS_DIR 必须是绝对路径');
const sourceDir = override ?? fileURLToPath(new URL('../../docs/', import.meta.url));
const sources = JSON.parse(await readFile(new URL('./docs-sources.json', import.meta.url), 'utf8'));
const definitions = JSON.parse(await readFile(new URL('./docs-sections.json', import.meta.url), 'utf8'));
if (!Array.isArray(definitions)) throw new Error('区域配置必须是数组');
const sectionMap = new Map();
for (const section of definitions) {
  if (!section || !/^[a-z][a-z0-9-]*$/.test(section.id) || sectionMap.has(section.id) || typeof section.title !== 'string' || !section.title.trim() || !Number.isSafeInteger(section.order) || !['use', 'contract'].includes(section.kind) || (section.navigation !== undefined && typeof section.navigation !== 'boolean')) throw new Error('无效或重复的区域配置');
  sectionMap.set(section.id, section);
}
for (const entry of sources) {
  if (!sectionMap.has(entry.section)) throw new Error(`${entry.slug}: 未知 section ${entry.section}`);
  if (entry.overview !== undefined && typeof entry.overview !== 'boolean') throw new Error(`${entry.slug}: overview 必须为布尔值`);
}
const sections = definitions.map(section => {
  const entries = sources.filter(entry => entry.section === section.id);
  const overviews = entries.filter(entry => entry.overview);
  if (!entries.length || overviews.length > 1) throw new Error(`${section.id}: 区域不能为空，且最多只能有一个 overview`);
  const landing = overviews[0] ?? entries[0];
  return { ...section, href: landing.slug === 'overview' ? '/docs' : `/docs/${landing.slug}` };
}).sort((a, b) => a.order - b.order);
const github = 'https://github.com/Xenotech-Studio/flops-agent/';
const forbidden = /(?:^|\/)(?:TODO|DESIGN_NOTES_FROM_DOCS)\.md(?:$|[#?])/i;
const routes = new Map(sources.map(s => [s.file, s.slug === 'overview' ? '/docs' : `/docs/${s.slug}`]));
function rewrite(markdown) {
  return markdown.replace(/(\]\()([^\s)]+)(\))/g, (_, start, href, end) => {
    if (/^(?:[a-z]+:|\/|#)/i.test(href)) return start + href + end;
    if (forbidden.test(href)) throw new Error(`禁止发布的文档链接：${href}`);
    const [file, hash] = href.split('#');
    const normalized = posix.normalize(file);
    const target = routes.get(normalized);
    return start + (target ? target + (hash ? `#${hash}` : '') : github + (file.endsWith('/') ? 'tree/' : 'blob/') + 'master/' + posix.normalize('docs/' + file) + (hash ? `#${hash}` : '')) + end;
  });
}
// Read every required local source before touching generated output. Explicit allowlist only.
const pages = await Promise.all(sources.map(async (entry, order) => {
  if (forbidden.test(entry.file) || !/^[\w-]+\.md$/.test(entry.file) || !/^[\w-]+$/.test(entry.slug)) throw new Error('非法文档清单');
  let markdown;
  try { markdown = await readFile(join(sourceDir, entry.file), 'utf8'); }
  catch (error) { throw new Error(`缺少必需文档 ${entry.file}: ${error.message}`); }
  if (entry.slug === 'overview') markdown = await readFile(new URL('./docs-overview.md', import.meta.url), 'utf8');
  markdown = dropSections(markdown, entry.drop_sections);
  if (entry.publication_note) {
    if (typeof entry.publication_note !== 'string' || /[\r\n]/.test(entry.publication_note)) throw new Error('publication_note 必须是单行文本');
    markdown = markdown.replace(/^(#\s+.+)$/m, `$1\n\n${entry.publication_note}`);
  }
  const h1 = markdown.match(/^#\s+(.+)$/m)?.[1];
  if (!h1) throw new Error(`${entry.file} 缺少 H1`);
  markdown = rewrite(markdown);
  const title = entry.slug === 'api_surface' ? 'API 契约' : h1;
  const href = routes.get(entry.file);
  const source = entry.slug === 'overview' ? 'website/scripts/docs-overview.md (based on docs/README.md)' : `docs/${entry.file}`;
  const { html, headings, summary } = renderMarkdown(markdown);
  const section = sections.find(section => section.id === entry.section);
  const header = { canonical: href, section: section.id, title, group: entry.group, status: '本次构建读取的本地 clone 内容', source, overview: entry.overview === true };
  const publicMarkdown = '---\n' + Object.entries(header).map(([k,v]) => `${k}: ${JSON.stringify(v)}`).join('\n') + '\n---\n\n' + markdown;
  return { slug: entry.slug, section: section.id, section_title: section.title, section_url: section.href, section_order: section.order, kind: section.kind, title, group: entry.group, order, href, source, markdown, publicMarkdown, html, headings, summary, overview: header.overview };
}));
if (new Set(pages.map(p => p.slug)).size !== pages.length) throw new Error('重复 slug');
for (const dir of ['src/content/docs', 'public/docs-source']) {
  await rm(join(website, dir), { recursive: true, force: true });
  await mkdir(join(website, dir), { recursive: true });
}
for (const page of pages) {
  await writeFile(join(website, 'src/content/docs', page.slug + '.md'), page.markdown);
  await writeFile(join(website, 'public/docs-source', page.slug + '.md'), page.publicMarkdown);
}
await writeFile(join(website, 'src/content/docs/_meta.json'), JSON.stringify({ sections, pages }, null, 2) + '\n');
await writeFile(join(website, 'public/docs-index.json'), JSON.stringify(pages.map(p => ({ slug: p.slug, title: p.title, summary: p.summary, section: p.section, section_title: p.section_title, section_url: p.section_url, section_order: p.section_order, kind: p.kind, status: '本次构建读取的本地 clone 内容', canonical_url: p.href, markdown_url: `/docs-source/${p.slug}.md`, source: p.source, group: p.group, overview: p.overview, headings: p.headings })), null, 2) + '\n');
console.log(`已从本地文档目录同步 ${pages.length} 篇；仅包含显式发布清单。`);
