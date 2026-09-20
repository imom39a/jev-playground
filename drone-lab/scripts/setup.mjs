import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
const manifest = process.argv[2] || 'upstream.json';
if (!['upstream.json', 'hardware-upstream.json'].includes(manifest)) throw new Error('Unknown upstream manifest.');
const upstream = JSON.parse(readFileSync(join(root, manifest), 'utf8'));
const dest = join(root, upstream.directory);
const git = (...args) => execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim();
if (!existsSync(dest)) {
  mkdirSync(join(root, '.vendor'), { recursive: true });
  git('init', dest);
  git('-C', dest, 'remote', 'add', 'origin', upstream.repository);
  git('-C', dest, 'fetch', '--depth', '1', 'origin', upstream.commit);
  git('-C', dest, 'checkout', '--detach', 'FETCH_HEAD');
}
if (git('-C', dest, 'rev-parse', 'HEAD') !== upstream.commit) throw new Error('Existing simulator has a different revision. Preserve your changes before updating it.');
if (git('-C', dest, 'status', '--porcelain')) throw new Error('Simulator checkout has local changes. Review them before setup.');
console.log(`${upstream.name} ready at ${upstream.commit.slice(0, 12)}. Its source and bundled assets are unchanged.`);
