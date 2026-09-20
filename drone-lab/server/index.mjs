import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { decide, describeImage, modelConfig } from './models.mjs';
import { validateState } from '../shared/contract.mjs';
import { TurbodroneAdapter } from './turbodrone.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const vendor = resolve(root, '.vendor/drone-commander');
const upstream = JSON.parse(readFileSync(resolve(root, 'upstream.json'), 'utf8'));
const port = Number(process.env.DRONE_LAB_PORT || 4180);
const baseOrigin = `http://127.0.0.1:${port}`;
const allowedHosts = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
if (!existsSync(resolve(vendor, 'index.html'))) throw new Error('Run npm run setup to install the pinned simulator.');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.jpg': 'image/jpeg', '.png': 'image/png', '.svg': 'image/svg+xml', '.mp3': 'audio/mpeg', '.stl': 'application/octet-stream', '.obj': 'text/plain', '.mtl': 'text/plain' };
const send = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)); };
let modelRequests = 0;
async function body(req) {
  let bytes = 0; const chunks = [];
  for await (const chunk of req) { bytes += chunk.length; if (bytes > 1000000) throw new Error('Request too large.'); chunks.push(chunk); }
  return JSON.parse(Buffer.concat(chunks).toString() || '{}');
}
const server = createServer(async (req, res) => {
  res.setHeader('x-content-type-options', 'nosniff');
  res.setHeader('referrer-policy', 'no-referrer');
  res.setHeader('x-frame-options', 'SAMEORIGIN');
  // The stock Blockly editor executes its own generated JS. No model-generated
  // code is passed to it. External connections are confined to the backend.
  res.setHeader('Content-Security-Policy', "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'self'");
  if (!allowedHosts.has(req.headers.host)) return send(res, 403, { error: 'Local host required.' });
  const pathname = new URL(req.url, baseOrigin).pathname;
  try {
    if (pathname === '/api/config' && req.method === 'GET') {
      const config = modelConfig();
      return send(res, 200, { jev: Boolean(config.jevKey), llm: Boolean(config.routerKey), jevModel: config.jevModel, llmModel: config.llmModel, visionModel: config.visionModel, physicalFlightEnabled: false, upstream });
    }
    if (pathname === '/api/hardware' && req.method === 'GET') return send(res, 200, await new TurbodroneAdapter().status());
    if (pathname.startsWith('/api/')) {
      if (req.method !== 'POST' || !['/api/decide', '/api/observe'].includes(pathname)) return send(res, 404, { error: 'No such endpoint. Physical flight is not exposed.' });
      if (req.headers.origin !== `http://${req.headers.host}` || req.headers['x-flight-lab'] !== '1' || !req.headers['content-type']?.startsWith('application/json')) return send(res, 403, { error: 'Use the local Flight Lab page.' });
      if (modelRequests >= 2) return send(res, 429, { error: 'A model request is already running. Try again shortly.' });
      modelRequests++;
      try {
        const payload = await body(req);
        if (pathname === '/api/observe') return send(res, 200, await describeImage(payload.image));
        if (typeof payload.request !== 'string' || !payload.request.trim() || payload.request.length > 1500) throw new Error('Enter a command under 1,500 characters.');
        const state = validateState(payload.state);
        return send(res, 200, await decide(payload.request, state, payload.provider));
      } finally { modelRequests--; }
    }
    if (!['GET', 'HEAD'].includes(req.method)) return send(res, 405, { error: 'Method not allowed.' });
    let dir, relative;
    if (pathname.startsWith('/sim/')) { dir = vendor; relative = decodeURIComponent(pathname.slice(5)) || 'index.html'; }
    else if (pathname.startsWith('/shared/')) { dir = resolve(root, 'shared'); relative = decodeURIComponent(pathname.slice(8)); }
    else { dir = resolve(root, 'public'); relative = decodeURIComponent(pathname.slice(1)) || 'index.html'; }
    const path = resolve(dir, relative);
    if (!path.startsWith(dir + sep) || relative.split(/[\\/]/).some(p => p.startsWith('.'))) return send(res, 403, { error: 'Path not allowed.' });
    if (!(await stat(path)).isFile()) return send(res, 404, { error: 'File not found.' });
    let content = await readFile(path);
    if (pathname === '/sim/' || pathname === '/sim/index.html') {
      // Keep the upstream checkout pristine. Extend its page only while serving.
      content = Buffer.from(content.toString().replace('</body>', '<script type="module" src="/sim-bridge.mjs"></script></body>'));
    }
    res.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream', 'cache-control': 'no-cache' });
    res.end(req.method === 'HEAD' ? undefined : content);
  } catch (error) {
    send(res, error.code === 'ENOENT' ? 404 : 400, { error: error.code === 'ENOENT' ? 'File not found.' : error.message });
  }
});
server.listen(port, '127.0.0.1', () => {
  const c = modelConfig();
  console.log(`Jev Flight Lab: ${baseOrigin}`);
  console.log(`Simulator: Drone Commander ${upstream.commit.slice(0, 12)} | Jev: ${c.jevKey ? 'configured' : 'offline'} | OpenRouter: ${c.routerKey ? 'configured' : 'offline'}`);
  console.log('Physical flight endpoints: disabled.');
});
server.on('error', error => { console.error(error.message); process.exitCode = 1; });
