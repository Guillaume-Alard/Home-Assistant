/* AudioWorklet : convertit le micro (Float32, fréquence du contexte, souvent
   48 kHz) en PCM 16 bits mono 16 kHz, par interpolation linéaire.
   Publie aussi le niveau (RMS) pour le visualiseur et la détection de silence. */

class PcmWriter extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const target = (options.processorOptions && options.processorOptions.targetRate) || 16000;
    this.ratio = sampleRate / target; // `sampleRate` est global dans un worklet
    this.readPos = 0;
    this.pending = new Float32Array(0);
    this.out = new Int16Array(2048); // ~128 ms à 16 kHz
    this.outLen = 0;
    this.active = false;
    this.frame = 0;
    this.port.onmessage = (e) => {
      if (e.data === 'start') {
        this.active = true;
        this.pending = new Float32Array(0);
        this.readPos = 0;
        this.outLen = 0;
      } else if (e.data === 'stop') {
        this.active = false;
        this.flush();
      }
    };
  }

  flush() {
    if (this.outLen > 0) {
      const buf = this.out.slice(0, this.outLen);
      this.port.postMessage({ type: 'chunk', buffer: buf.buffer }, [buf.buffer]);
      this.outLen = 0;
    }
    this.port.postMessage({ type: 'flushed' });
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    // Niveau sonore (1 message sur 4 ≈ toutes les ~11 ms)
    if ((this.frame++ & 3) === 0) {
      let sum = 0;
      for (let i = 0; i < channel.length; i++) sum += channel[i] * channel[i];
      this.port.postMessage({
        type: 'level',
        value: Math.sqrt(sum / channel.length),
        active: this.active,
      });
    }

    if (!this.active) return true;

    const data = new Float32Array(this.pending.length + channel.length);
    data.set(this.pending);
    data.set(channel, this.pending.length);

    let pos = this.readPos;
    while (pos + 1 < data.length) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const sample = data[i] * (1 - frac) + data[i + 1] * frac;
      this.out[this.outLen++] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
      if (this.outLen === this.out.length) {
        const buf = this.out.slice(0);
        this.port.postMessage({ type: 'chunk', buffer: buf.buffer }, [buf.buffer]);
        this.outLen = 0;
      }
      pos += this.ratio;
    }
    const consumed = Math.floor(pos);
    this.pending = data.slice(consumed);
    this.readPos = pos - consumed;
    return true;
  }
}

registerProcessor('pcm-writer', PcmWriter);
