import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';

test('credential loader accepts dotenv spacing and never executes file contents', () => {
  const dir = mkdtempSync(join(tmpdir(), 'flight-loader-test-'));
  try {
    mkdirSync(join(dir, 'scripts'));
    copyFileSync(new URL('../../scripts/load-jev-env.sh', import.meta.url), join(dir, 'scripts/load-jev-env.sh'));
    writeFileSync(join(dir, '.env'), "JEV_API_KEY = 'fake-test-key'\nopenouterkey = 'fake router key'\nUNTRUSTED=$(touch sentinel)\n");
    const output = execFileSync('bash', ['--noprofile', '--norc', '-c', 'source scripts/load-jev-env.sh; [[ "$TYPESAFE_API_KEY" == fake-test-key && "$openouterkey" == "fake router key" ]] && printf parsed'], { cwd: dir, encoding: 'utf8', env: { PATH: process.env.PATH, HOME: process.env.HOME } });
    assert.equal(output, 'parsed'); assert.equal(existsSync(join(dir, 'sentinel')), false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
