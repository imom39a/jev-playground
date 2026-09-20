import { readdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
for (const dir of ['server', 'shared', 'public', 'scripts', 'test']) {
  for (const name of readdirSync(dir)) if (/\.(mjs|js)$/.test(name)) execFileSync(process.execPath, ['--check', `${dir}/${name}`], { stdio: 'inherit' });
}
console.log('JavaScript syntax checked.');
