# Brief de revue externe — Phase 5A (le mot d'éveil dans le navigateur)

> Pour un avis extérieur. Contexte : [PLAN.md](../PLAN.md), [ARCHITECTURE.md](../ARCHITECTURE.md),
> briefs précédents : [phase-1](phase-1.md) … [phase-4](phase-4.md).

## Ce qui a été construit

La première moitié de la Phase 5 : **« Hey Jarvis » sans matériel**, entièrement
dans l'onglet. (La 5B — satellites ESP32 / agent Assist — dépend du matériel de
Guillaume et viendra ensuite.)

- **Conteneur `sentinel-openwakeword`** (`rhasspy/wyoming-openwakeword`, réseau
  interne) préchargeant `hey_jarvis` ; `config/wakewords/` monté pour un modèle
  `.tflite` personnalisé (« sentinel »).
- **Client Wyoming de détection** (`voice/wyoming.py` : `WakeWordDetector`,
  `WakeStream`) : session à flux continu (≠ requête/réponse de whisper/piper).
  Le task lecteur invoque le callback à la détection puis **se termine seul** et
  ferme sa connexion ; `close()` (arrêt externe) ne l'annule pas s'il est le task
  courant — sinon on couperait l'envoi du callback.
- **Routage WS** : hors capture, les trames binaires nourrissent la veille ;
  `wake_start`/`wake_stop`, réponses `wake`/`wake_error`, `hello` porte
  `wake_available` + `wake_word`. La capture d'un tour reste prioritaire.
- **UI** : bouton ◉ (préférence par appareil, `localStorage`), streaming continu
  au repos, **carillon WebAudio** à la détection puis bascule en écoute, ré-armement
  après chaque tour, pause quand l'onglet est caché, gestion du verrou autoplay
  (ré-essai au premier geste) et des erreurs (backoff 8 s).
- Tests : 119 (7 nouveaux — détecteur unitaire, service injoignable, session
  coupée signalée, veille → détection → tour vocal de bout en bout, non
  configuré ; faux openWakeWord).

## Points de doute — avis sollicité

1. **Micro toujours ouvert au repos** quand la veille est active : nécessaire pour
   openWakeWord, mais c'est un flux permanent core→openwakeword. Acceptable en
   LAN mono-foyer ? Faut-il un plafond de durée / une mise en veille après inactivité ?
2. **Auto-annulation évitée par `asyncio.current_task() is not self._reader`** dans
   `close()` : correct, ou trop malin ? (Le bug qu'il corrige — détection perdue
   car le callback était annulé en plein envoi — a été vu puis fixé, cf. §revue.)
3. **Une session de veille par appareil** : plusieurs onglets = plusieurs flux vers
   openWakeWord (modèle chargé une fois, mais N connexions). Suffisant, ou mutualiser ?
4. **Carillon + bascule sans confirmation** : un faux positif ouvre une écoute de
   quelques secondes (rien n'est exécuté sans la suite). Seuil openWakeWord à exposer ?

## Addendum — revue interne du 06/09 (3 constats, tous corrigés)

1. **Session figée en silence (le plus grave)** : si openWakeWord coupe la
   connexion sans détection (redémarrage du conteneur → EOF propre), le lecteur
   sortait en posant `_closed` mais `client.wake` pointait encore la session
   morte ; `send()` se taisait alors (court-circuit `_closed`), donc aucun
   `wake_error`, aucune reprise — la veille se figeait → propriété `closed`
   exposée, `_on_audio_chunk` la teste et émet `wake_error` (l'UI ré-arme).
2. UI : `armOnGesture` n'écoutait que `pointerdown` alors que son commentaire
   promettait « clic OU touche » → un utilisateur au clavier ne pouvait pas
   ré-armer la veille → écoute aussi `keydown`.
3. UI : `tryEnsureCapture` recopiait la création d'`audioCtx`/`player`
   d'`ensureAudio` → extraction dans `makeAudio()`, plus de duplication.

119 tests après correctifs. Les points de doute restent ouverts.

## Comment tester

```bash
cd core && pip install -r requirements-dev.txt && pytest -q   # 119 tests
```

Réel : `git pull && docker compose up -d --build`, recharger l'UI (vidage du cache),
cliquer ◉, dire « Hey Jarvis » → carillon → enchaîner « allume le salon ».

## Hors périmètre

Phase 5B : satellites ESP32-S3 / Voice PE et exposition de Sentinel comme agent
conversationnel d'Assist (whisper/piper déjà en Wyoming, réutilisables tels quels).
