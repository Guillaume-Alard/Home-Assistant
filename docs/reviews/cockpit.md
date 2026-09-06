# Revue interne — Refonte UI « Cockpit »

Refonte de l'interface web d'après une maquette Claude Design fournie par
Guillaume. On passe de l'ancien HUD (orbe ambiante + fenêtres flottantes) à un
**cockpit fixe à 3 colonnes**, avec accent cyan assorti à l'orbe WebGL
« Noyau Synaptique » (conservée telle quelle, sans décor autour).

## Portée

- **Coque** : en-tête (onglets Cockpit/Paramètres, liaisons live, outils
  veille/propositions/santé/historique) + deux vues (`#view-cockpit`,
  `#view-settings`).
- **Cockpit** : Conversation (gauche) · Orbe/Voix (centre : orbe nue,
  transcript, boutons Parler/Interrompre, chips d'action) · Tâches de fond +
  Aperçu (droite : onglets Aperçu Vite / Code = diff réel).
- **Paramètres** : Connexions (réel + feuille de route « bientôt »), Voix &
  réveil, Moteur & mémoire, Confidentialité (garde-fous + protocoles).
- **Tiroirs** : santé, historique, propositions — restylés, ouverts depuis
  l'en-tête.

## Fichiers

- `ui/index.html`, `ui/css/main.css`, `ui/js/app.js` : réécrits pour le cockpit
  (mêmes protocoles WS et fonctions de rendu). `ui/js/windows.js` supprimé.
- `ui/js/chat.js` : ajout de `lastAssistantText()` (transcript sous l'orbe).
- `core/app/main.py` : `hello` expose `engine` (modèle/effort/tokens/STT/TTS/
  réveil/fuseau) et `config` (capacités booléennes) — affichage seul.
- `core/app/config.py` : champs d'affichage `whisper_model` / `piper_voice`.
- `docker-compose.yml` : `WHISPER_MODEL` / `PIPER_VOICE` passés à `sentinel-core`.

## Invariants de sécurité (inchangés)

- « Propose → tu approuves » : aucune action directe.
- Actions **sensibles** approuvées **uniquement dans l'UI** — jamais à la voix ;
  la page Paramètres l'affiche comme **verrouillé**, sans interrupteur de
  contournement.
- Atelier de dev isolé (ni Nova, ni socket Docker, ni jetons).
- Fonctionnement LAN, aucune donnée exposée en plus par cette refonte
  (`engine`/`config` = métadonnées non sensibles ; jamais la clé ni les jetons).

## Points de vigilance vérifiés

- **Zone morte temporelle** (cause du bug « hors ligne » précédent) : tout l'état
  (`st`, `dev`) est déclaré avant usage ; `ws.connect()` reste en dernier.
- Toutes les références DOM de `app.js` existent dans `index.html` (vérifié).
- Syntaxe JS (ES modules) et Python valides ; **125 tests** passent, dont
  `test_ws` étendu au contrat `engine`/`config`.

## Différé

- Barre d'onglets mobile (Voix/Chat/Tâches) : à traiter avec l'accès iPhone/PWA
  (le mobile actuel empile et défile, fonctionnel).
- Identité « Luna » + voix féminine : tâche dédiée.
