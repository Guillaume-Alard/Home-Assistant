/* Capture micro → PCM 16 kHz via AudioWorklet.
   Événements : 'chunk' (ArrayBuffer PCM16), 'level' ({value, active}), 'flushed'. */

export class Capture extends EventTarget {
  constructor() {
    super();
    this.ready = false;
    this.node = null;
    this.stream = null;
  }

  async init(audioCtx) {
    if (this.ready) return;
    // getUserMedia exige un contexte sécurisé (HTTPS) — voir le README
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    await audioCtx.audioWorklet.addModule('js/pcm-worklet.js');
    const source = audioCtx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(audioCtx, 'pcm-writer', {
      processorOptions: { targetRate: 16000 },
    });
    source.connect(this.node);
    // Sortie silencieuse : garantit que le graphe audio « tire » le worklet
    const mute = audioCtx.createGain();
    mute.gain.value = 0;
    this.node.connect(mute);
    mute.connect(audioCtx.destination);

    this.node.port.onmessage = (e) => {
      const d = e.data;
      if (d.type === 'chunk') this.dispatchEvent(new CustomEvent('chunk', { detail: d.buffer }));
      else if (d.type === 'level') this.dispatchEvent(new CustomEvent('level', { detail: d }));
      else if (d.type === 'flushed') this.dispatchEvent(new Event('flushed'));
    };
    this.ready = true;
  }

  start() { this.node && this.node.port.postMessage('start'); }
  stop() { this.node && this.node.port.postMessage('stop'); }
}
