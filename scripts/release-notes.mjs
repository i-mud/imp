export function normalizeGeneratedReleaseNotes(notes, version) {
  if (typeof notes !== 'string') {
    throw new TypeError('release notes must be a string');
  }

  const lines = notes.trim().replace(/\r\n/g, '\n').split('\n');

  if (lines.length > 0 && /^#{1,6}\s/.test(lines[0]) && lines[0].includes(version)) {
    lines.shift();
  }

  while (lines.length > 0 && lines[0].trim() === '') {
    lines.shift();
  }

  return lines.join('\n').trim();
}

export function canonicalizeReleaseNotes(notes) {
  return notes
    .replace(/\r\n/g, '\n')
    .trim()
    .split('\n')
    .map((line) => line.replace(/^(\s*)[*+]\s+/, '$1- '))
    .join('\n')
    .replace(/\s+/g, ' ');
}

export function extractChangelogReleaseNotes(changelog, version) {
  const escapedVersion = version.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const section = new RegExp(
    `^## \\[${escapedVersion}\\][^\\r\\n]*\\r?\\n(?<body>[\\s\\S]*?)(?=^## \\[)`,
    'm',
  ).exec(changelog);

  if (!section?.groups?.body) {
    throw new Error(`CHANGELOG.md: could not locate release section ${version}`);
  }

  return section.groups.body.trim();
}
