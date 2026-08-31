/* Visualiseur : anneau de barres radiales autour du bouton micro.
   Réagit au niveau audio (micro ou voix de Sentinel) et à l'état courant. */

const COLORS = {
  idle: '#2c4a63',
  listening: '#4fd8eb',
  transcribing: '#ffb454',
  thinking: '#ffb454',
  speaking: '#4fd8eb',
  offline: '#22303f',
};

export class Viz {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.level = 0;
    this.target = 0;
    this.mode = 'idle';
    this.phase = 0;

    const resize = () => {
      const dpr = devicePixelRatio || 1;
      const r = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(r.width * dpr));
      canvas.height = Math.max(1, Math.round(r.height * dpr));
    };
    new ResizeObserver(resize).observe(canvas);
    resize();
    requestAnimationFrame(() => this._frame());
  }

  setLevel(v) { this.target = Math.max(this.target, Math.min(1, v * 5)); }
  setMode(m) { this.mode = COLORS[m] ? m : 'idle'; }

  _frame() {
    const { ctx, canvas } = this;
    const w = canvas.width, h = canvas.height;
    const dpr = devicePixelRatio || 1;
    ctx.clearRect(0, 0, w, h);

    this.level += (this.target - this.level) * 0.25;
    this.target *= 0.82; // retombée naturelle

    const waiting = this.mode === 'thinking' || this.mode === 'transcribing';
    const active = this.mode === 'listening' || this.mode === 'speaking';
    this.phase += waiting ? 0.085 : 0.03 + this.level * 0.2;

    const cx = w / 2, cy = h / 2;
    const inner = (Math.min(w, h) / 2) * 0.58;
    const color = COLORS[this.mode];
    const bars = 56;

    ctx.lineCap = 'round';
    ctx.lineWidth = 2 * dpr;
    ctx.strokeStyle = color;

    for (let i = 0; i < bars; i++) {
      const angle = (i / bars) * Math.PI * 2 - Math.PI / 2;
      const wob = Math.sin(this.phase * 2 + i * 0.7) * 0.5 + 0.5;
      let len;
      if (waiting) {
        len = 3 + 5 * wob;
        ctx.globalAlpha = 0.2 + 0.8 * ((Math.sin(this.phase * 3 - i * (Math.PI * 2 / bars) * 3) + 1) / 2);
      } else if (active) {
        len = 2.5 + this.level * 30 * (0.35 + 0.65 * wob);
        ctx.globalAlpha = 0.85;
      } else {
        len = 2 + 2.4 * (Math.sin(this.phase + i * 0.35) * 0.5 + 0.5);
        ctx.globalAlpha = this.mode === 'offline' ? 0.35 : 0.55;
      }
      const r2 = inner + len * dpr;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
      ctx.lineTo(cx + Math.cos(angle) * r2, cy + Math.sin(angle) * r2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    requestAnimationFrame(() => this._frame());
  }
}
