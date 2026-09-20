// A deliberately read-only seam for the later PL-515 integration. Simulator
// distances/position hold have no calibrated mapping to this drone's RC axes.
export class TurbodroneAdapter {
  constructor(baseUrl = process.env.TURBODRONE_URL || 'http://127.0.0.1:8000') {
    const url = new URL(baseUrl);
    if (url.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || url.username || url.password || url.pathname !== '/') throw new Error('TurboDrone must be a local HTTP service.');
    this.baseUrl = url.origin;
  }
  async status() {
    try {
      const response = await fetch(`${this.baseUrl}/capabilities`, { signal: AbortSignal.timeout(1500), redirect: 'error' });
      if (!response.ok) throw new Error('Unavailable');
      const capabilities = await response.json();
      return { connected: true, flightEnabled: false, capabilities, cameraPath: `${this.baseUrl}/mjpeg`, message: 'Bridge is reachable. Physical commands remain disabled until the device is checked.' };
    } catch {
      return { connected: false, flightEnabled: false, message: 'TurboDrone is not running. Simulation is fully available.' };
    }
  }
  execute() { throw new Error('Physical flight is disabled in this simulator release.'); }
}
