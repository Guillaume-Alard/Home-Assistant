/* Connexion WebSocket au serveur Sentinel, avec reconnexion automatique.
   Événements : 'open', 'close', 'event' (JSON du serveur), 'audio' (ArrayBuffer). */

export class WSClient extends EventTarget {
  constructor() {
    super();
    this.ws = null;
    this.alive = false;
    this.retry = 0;
    this.pingTimer = null;
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      this.retry = 0;
      this.alive = true;
      this.pingTimer = setInterval(() => this.sendJSON({ type: 'ping' }), 20000);
      this.dispatchEvent(new Event('open'));
    };

    ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }
        this.dispatchEvent(new CustomEvent('event', { detail: msg }));
      } else {
        this.dispatchEvent(new CustomEvent('audio', { detail: e.data }));
      }
    };

    ws.onclose = () => {
      this.alive = false;
      clearInterval(this.pingTimer);
      this.dispatchEvent(new Event('close'));
      const delay = Math.min(10000, 800 * Math.pow(1.7, this.retry++));
      setTimeout(() => this.connect(), delay);
    };

    ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
    this.ws = ws;
  }

  sendJSON(obj) {
    if (this.alive) { try { this.ws.send(JSON.stringify(obj)); } catch { /* ignore */ } }
  }

  sendBytes(buffer) {
    if (this.alive) { try { this.ws.send(buffer); } catch { /* ignore */ } }
  }
}
