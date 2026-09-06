/* Fenêtres flottantes déplaçables (HUD). Vanilla, sans dépendance.
   - glissement par la barre de titre (pointer events, capture propre)
   - passage au premier plan au clic (z-index croissant)
   - position/taille mémorisées par fenêtre (localStorage)
   - sur petit écran : la fenêtre devient une feuille plein écran (pas de glissement)
   Les boutons de la barre de titre (réduire/fermer) ne déclenchent pas le glissement. */

const MOBILE = '(max-width: 760px)';
let zTop = 40;

export function bringToFront(win) {
  win.style.zIndex = String(++zTop);
}

const isSheet = () => window.matchMedia(MOBILE).matches;

export function makeDraggable(win, key) {
  const head = win.querySelector('.win-head');
  if (!head) return;
  const KEY = `sentinel.win.${key}`;

  // Restaurer la position (seulement en mode fenêtre, pas en feuille plein écran)
  if (!isSheet()) {
    try {
      const s = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (s && Number.isFinite(s.x)) {
        Object.assign(win.style, {
          left: `${s.x}px`, top: `${s.y}px`, right: 'auto', bottom: 'auto',
        });
        if (s.w) win.style.width = `${s.w}px`;
        if (s.h) win.style.height = `${s.h}px`;
      }
    } catch { /* stockage indisponible */ }
  }

  const save = () => {
    if (isSheet()) return;
    try {
      const r = win.getBoundingClientRect();
      localStorage.setItem(KEY, JSON.stringify({
        x: Math.round(r.left), y: Math.round(r.top),
        w: Math.round(r.width), h: Math.round(r.height),
      }));
    } catch { /* ignore */ }
  };

  win.addEventListener('pointerdown', () => bringToFront(win), true);

  let dragging = false;
  let ox = 0;
  let oy = 0;

  head.addEventListener('pointerdown', (e) => {
    if (isSheet() || e.button !== 0 || e.target.closest('button')) return;
    const r = win.getBoundingClientRect();
    ox = e.clientX - r.left;
    oy = e.clientY - r.top;
    Object.assign(win.style, {
      left: `${r.left}px`, top: `${r.top}px`, right: 'auto', bottom: 'auto',
    });
    dragging = true;
    head.setPointerCapture(e.pointerId);
    head.classList.add('dragging');
  });

  head.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const maxX = window.innerWidth - win.offsetWidth - 6;
    const maxY = window.innerHeight - 44; // garde la barre de titre attrapable
    win.style.left = `${Math.max(6, Math.min(e.clientX - ox, maxX))}px`;
    win.style.top = `${Math.max(6, Math.min(e.clientY - oy, maxY))}px`;
  });

  const end = (e) => {
    if (!dragging) return;
    dragging = false;
    head.classList.remove('dragging');
    try { head.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
    save();
  };
  head.addEventListener('pointerup', end);
  head.addEventListener('pointercancel', end);

  // ── Redimensionnement (poignée maison, en bas à droite) ────────────────
  // Poignée dédiée plutôt que `resize: both` du navigateur : la poignée native
  // se retrouve sous l'iframe (Aperçu) et n'est plus attrapable ; celle-ci est
  // au-dessus et capture le pointeur, donc elle marche partout.
  const grip = document.createElement('div');
  grip.className = 'win-resize';
  grip.setAttribute('aria-hidden', 'true');
  win.appendChild(grip);

  let rez = false;
  let sx = 0;
  let sy = 0;
  let sw = 0;
  let sh = 0;
  grip.addEventListener('pointerdown', (e) => {
    if (isSheet() || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const r = win.getBoundingClientRect();
    Object.assign(win.style, {
      left: `${r.left}px`, top: `${r.top}px`, right: 'auto', bottom: 'auto',
    });
    sx = e.clientX; sy = e.clientY; sw = r.width; sh = r.height;
    rez = true;
    grip.setPointerCapture(e.pointerId);
  });
  grip.addEventListener('pointermove', (e) => {
    if (!rez) return;
    const r = win.getBoundingClientRect();
    const w = Math.max(280, Math.min(sw + (e.clientX - sx), window.innerWidth - r.left - 6));
    const h = Math.max(160, Math.min(sh + (e.clientY - sy), window.innerHeight - r.top - 6));
    win.style.width = `${w}px`;
    win.style.height = `${h}px`;
  });
  const endRez = (e) => {
    if (!rez) return;
    rez = false;
    try { grip.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
    save();
  };
  grip.addEventListener('pointerup', endRez);
  grip.addEventListener('pointercancel', endRez);
}
