/* Fil de conversation : rendu des messages (dont streaming) et mini-Markdown.
   Aucune bibliothèque : le HTML est toujours échappé avant transformation. */

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

export function renderMarkdown(md) {
  // 1) Blocs de code mis de côté (avant tout le reste)
  const codeBlocks = [];
  // Le tag de langage n'existe que suivi d'un saut de ligne (```bash\n…) ;
  // sinon (fence « en ligne ») tout le contenu est du code
  let src = md.replace(/```(?:\w*\n)?([\s\S]*?)```/g, (m, code) => {
    codeBlocks.push(code.replace(/\n$/, ''));
    // Entouré de sauts de ligne : le marqueur est toujours seul sur sa ligne,
    // même quand la clôture ``` partage sa ligne avec du texte
    return `\n\u0000B${codeBlocks.length - 1}\u0000\n`;
  });

  // 2) Échappement HTML puis styles en ligne
  src = escapeHtml(src);
  src = src.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  src = src.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  src = src.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s.,;:!?)]|$)/gm, '$1<em>$2</em>');
  src = src.replace(
    /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
  );

  // 3) Assemblage par blocs : paragraphes, listes, titres, code
  const out = [];
  let list = null;
  let para = [];
  const closePara = () => { if (para.length) { out.push(`<p>${para.join('<br>')}</p>`); para = []; } };
  const closeList = () => { if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; } };

  for (const line of src.split('\n')) {
    const t = line.trim();
    const code = t.match(/^\u0000B(\d+)\u0000$/);
    if (code) {
      closePara(); closeList();
      out.push(`<pre><code>${escapeHtml(codeBlocks[+code[1]])}</code></pre>`);
      continue;
    }
    if (!t) { closePara(); closeList(); continue; }
    const li = t.match(/^[-*+]\s+(.*)/);
    if (li) { closePara(); (list ||= []).push(`<li>${li[1]}</li>`); continue; }
    const h = t.match(/^#{1,6}\s+(.*)/);
    if (h) { closePara(); closeList(); out.push(`<strong class="h">${h[1]}</strong>`); continue; }
    closeList();
    para.push(t);
  }
  closePara(); closeList();
  return out.join('');
}

const timeFr = (iso) => {
  try {
    return new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
};

export class Thread {
  constructor(el) {
    this.el = el;
    this.known = new Set(); // ids déjà affichés
    this.streamEl = null;
    this.streamText = '';
  }

  clear() {
    this.el.textContent = '';
    this.known.clear();
    this.streamEl = null;
    this.streamText = '';
  }

  addMessage(m) {
    if (!m || this.known.has(m.id)) return;
    this.known.add(m.id);
    const el = this._bubble(m.role, m.source, m.created_at);
    el.querySelector('.body').innerHTML = renderMarkdown(m.content);
    this._append(el);
  }

  startStream(id) {
    this.endStream(null, null, false); // sécurité : un seul flux à la fois
    this.streamText = '';
    this.streamEl = this._bubble('assistant', 'text', new Date().toISOString());
    this.streamEl.classList.add('streaming');
    this.streamEl.dataset.streamId = id;
    this._append(this.streamEl);
  }

  addDelta(id, text) {
    if (!this.streamEl) this.startStream(id);
    this.streamText += text;
    this.streamEl.querySelector('.body').textContent = this.streamText;
    this._stick();
  }

  endStream(id, message, cancelled) {
    const el = this.streamEl;
    if (!el) return;
    this.streamEl = null;
    el.classList.remove('streaming');
    if (message) {
      this.known.add(message.id);
      el.querySelector('.body').innerHTML = renderMarkdown(message.content);
      if (cancelled) {
        const note = document.createElement('div');
        note.className = 'interrupted';
        note.textContent = '— interrompu —';
        el.appendChild(note);
        el.classList.add('cancelled');
      }
    } else {
      el.remove(); // rien produit (erreur ou interruption immédiate)
    }
    this._stick();
    this.streamText = '';
  }

  notice(text) { this._line(text, 'line'); }
  error(text) { this._line(text, 'line error'); }

  _line(text, className) {
    const el = document.createElement('div');
    el.className = className;
    el.textContent = text;
    this._append(el);
  }

  _bubble(role, source, createdAt) {
    const el = document.createElement('article');
    el.className = `msg ${role === 'user' ? 'user' : 'assistant'}`;
    const meta = document.createElement('div');
    meta.className = 'meta';
    const who = role === 'user' ? (source === 'voice' ? 'Toi · voix' : 'Toi') : 'Sentinel';
    const at = createdAt ? ` · ${timeFr(createdAt)}` : '';
    meta.textContent = who + at;
    const body = document.createElement('div');
    body.className = 'body';
    el.appendChild(meta);
    el.appendChild(body);
    return el;
  }

  _append(el) {
    const stick = this._nearBottom();
    this.el.appendChild(el);
    if (stick) this.el.scrollTop = this.el.scrollHeight;
  }

  _stick() {
    if (this._nearBottom()) this.el.scrollTop = this.el.scrollHeight;
  }

  _nearBottom() {
    return this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight < 120;
  }
}
