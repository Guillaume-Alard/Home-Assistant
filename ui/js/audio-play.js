/* Lecture de la voix de Sentinel : chunks PCM 16 bits mono planifiés bout à
   bout dans le contexte Web Audio (le navigateur rééchantillonne tout seul). */

export class Player extends EventTarget {
  constructor(audioCtx) {
    super();
    this.ctx = audioCtx;
    this.analyser = audioCtx.createAnalyser();
    this.analyser.fftSize = 256;
    this.gain = audioCtx.createGain();
    this.gain.connect(this.analyser);
    this.analyser.connect(audioCtx.destination);
    this.timeData = new Uint8Array(this.analyser.frequencyBinCount);
    this.sources = new Set();
    this.cursor = 0;
    this.rate = 22050;
    this.playing = false;
    this.endTimer = null;
  }

  begin(rate) {
    this.rate = rate || 22050;
    this.cursor = this.ctx.currentTime + 0.08;
    this.playing = true;
    clearTimeout(this.endTimer);
  }

  push(arrayBuffer) {
    if (!this.playing || arrayBuffer.byteLength < 2) return;
    const usable = arrayBuffer.byteLength - (arrayBuffer.byteLength % 2);
    const pcm = new Int16Array(arrayBuffer, 0, usable / 2);
    const f32 = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768;

    const buffer = this.ctx.createBuffer(1, f32.length, this.rate);
    buffer.copyToChannel(f32, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.gain);
    const at = Math.max(this.cursor, this.ctx.currentTime + 0.02);
    src.start(at);
    this.cursor = at + buffer.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  end() {
    // speak_end reçu : « terminé » quand tout l'audio planifié a été joué
    const waitMs = Math.max(0, (this.cursor - this.ctx.currentTime) * 1000) + 100;
    clearTimeout(this.endTimer);
    this.endTimer = setTimeout(() => {
      this.playing = false;
      this.dispatchEvent(new Event('ended'));
    }, waitMs);
  }

  stop() {
    clearTimeout(this.endTimer);
    for (const src of this.sources) { try { src.stop(); } catch { /* déjà fini */ } }
    this.sources.clear();
    this.playing = false;
    this.cursor = 0;
    this.dispatchEvent(new Event('ended'));
  }

  level() {
    if (!this.playing) return 0;
    this.analyser.getByteTimeDomainData(this.timeData);
    let sum = 0;
    for (let i = 0; i < this.timeData.length; i++) {
      const v = (this.timeData[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / this.timeData.length);
  }
}
