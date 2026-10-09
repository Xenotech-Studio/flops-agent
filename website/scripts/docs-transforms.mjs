// Remove an ATX heading and its descendants, stopping at the next heading of
// equal or higher rank. Fenced examples cannot start or end a real section.
export function dropSections(markdown, selectors = []) {
  if (!Array.isArray(selectors) || selectors.some(s => typeof s !== 'string' || !/^#{1,6} \S/.test(s))) {
    throw new Error('drop_sections 必须是 Markdown 标题字符串数组');
  }
  if (!selectors.length) return markdown;
  const normalize = s => s.trim().replace(/\s+#+\s*$/, '');
  const wanted = new Set(selectors.map(normalize));
  const found = new Set(), removedLabels = new Set();
  const output = [];
  let droppedLevel = 0, fence;
  for (const line of markdown.split(/(?<=\n)/)) {
    const text = line.replace(/\r?\n$/, '');
    const marker = text.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    let heading;
    if (fence) {
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = undefined;
    } else if (marker) {
      fence = marker[1];
    } else {
      heading = text.match(/^ {0,3}(#{1,6})\s+(.+)$/);
    }
    if (heading) {
      const level = heading[1].length;
      const normalized = normalize(text);
      if (droppedLevel && level <= droppedLevel) droppedLevel = 0;
      if (wanted.has(normalized)) {
        found.add(normalized);
        if (!droppedLevel) droppedLevel = level;
        for (const label of heading[2].matchAll(/\[([^\]]+)\]/g)) removedLabels.add(label[1].toLowerCase());
      }
    }
    if (!droppedLevel) output.push(line);
  }
  for (const selector of wanted) if (!found.has(selector)) throw new Error(`未找到待剔除标题：${selector}`);
  // Changelog reference definitions often live outside their section at EOF.
  // Remove those attached to excluded headings too, including compare URLs.
  return output.filter(line => {
    const definition = line.match(/^ {0,3}\[([^\]]+)\]:/);
    return !definition || !removedLabels.has(definition[1].toLowerCase());
  }).join('');
}
