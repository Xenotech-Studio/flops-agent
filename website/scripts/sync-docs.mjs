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
  const header = { canonical: href, section: 'framework', title, group: entry.group, status: '本次构建读取的本地 clone 内容', source, overview: entry.slug === 'overview' };
  const publicMarkdown = '---\n' + Object.entries(header).map(([k,v]) => `${k}: ${JSON.stringify(v)}`).join('\n') + '\n---\n\n' + markdown;
  return { slug: entry.slug, title, group: entry.group, order, href, source, markdown, publicMarkdown, html, headings, summary, overview: header.overview };
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
await writeFile(join(website, 'src/content/docs/_meta.json'), JSON.stringify({ pages }, null, 2) + '\n');
await writeFile(join(website, 'public/docs-index.json'), JSON.stringify(pages.map(p => ({ slug: p.slug, title: p.title, summary: p.summary, section: 'framework', section_title: '框架文档', section_url: '/docs', section_order: 0, kind: 'use', status: '本次构建读取的本地 clone 内容', canonical_url: p.href, markdown_url: `/docs-source/${p.slug}.md`, source: p.source, group: p.group, overview: p.overview, headings: p.headings })), null, 2) + '\n');
console.log(`已从本地文档目录同步 ${pages.length} 篇；仅包含显式发布清单。`);
