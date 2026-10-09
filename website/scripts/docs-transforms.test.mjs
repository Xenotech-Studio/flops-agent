import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dropSections } from './docs-transforms.mjs';

test('remove section, all children and its trailing reference; preserve released text', async () => {
  const source = await readFile(new URL('../../docs/CHANGELOG.md', import.meta.url), 'utf8');
  const filtered = dropSections(source, ['## [Unreleased]']);
  assert(!filtered.includes('Unreleased'));
  assert(!filtered.includes('Typed boundary contracts'));
  assert.equal(filtered.slice(filtered.indexOf('## [0.2.0]')), source.slice(source.indexOf('## [0.2.0]')).replace(/^\[Unreleased\]:.*\r?\n/gm, ''));
});
test('ignore fenced headings, stop at higher heading, support multiple selections and EOF', () => {
  const source = '# Doc\n```md\n## Hidden\n```\n## Hidden\n### Child\nsecret\n```\n# fake end\n```\n# Keep\npublic\n## Last\nprivate\n';
  assert.equal(dropSections(source, ['## Hidden', '## Last']), '# Doc\n```md\n## Hidden\n```\n# Keep\npublic\n');
});
test('fail closed on missing selector or invalid configuration', () => {
  assert.throws(() => dropSections('# Title\n', ['## Missing']), /未找到/);
  assert.throws(() => dropSections('# Title\n', '## Missing'), /数组/);
});
