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
}
