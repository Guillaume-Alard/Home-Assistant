# Les scénarios & routines de Luna (Phase 8)

Une **routine** est une séquence d'actions **nommée et réutilisable** — « Bonne
nuit » = fermer les volets + éteindre le salon. Luna te les **propose** (depuis
tes habitudes ou la conversation) ; **c'est toi qui les actives** ; ensuite tu
les déclenches d'un mot.

## Le principe : proposer, activer, déclencher

1. **Luna propose** une routine — soit parce qu'elle a repéré une **habitude**
   (les mêmes actions, à la même heure, sur plusieurs jours), soit parce que tu
   le lui demandes (« fais-en une routine »). Elle arrive **proposée**, pas
   active.
2. **Tu l'actives** dans **Paramètres › Routines** (revue humaine — tant qu'elle
   n'est pas active, elle **ne se déclenche pas**). Tu peux la renommer.
3. **Tu la déclenches** quand tu veux : « **lance Bonne nuit** », ou le bouton
   **Lancer** du cockpit.

## Ce qu'une routine ne peut pas faire — le garde-fou

Une routine se déclenche d'un mot, y compris par une personne **reconnue mais non
propriétaire**. Elle ne doit donc jamais pouvoir ouvrir un accès. **Une routine
ne contient QUE des actions courantes** : allumer / éteindre, volets, scènes,
consigne de chauffage, notification. **Jamais** de déverrouillage, de désarmement
d'alarme, ni de service arbitraire — c'est **refusé à la création** et
**verrouillé par des tests** (`core/tests/test_invariant.py`, `routines/safety.py`).

Si tu veux qu'une routine verrouille la porte le soir, ça reste un geste **manuel
et à l'interface** — comme toute action sensible.

## Comment Luna apprend une habitude

Le veilleur relit le **journal des actions déjà exécutées** et y cherche des
**scénarios récurrents** : un même groupe d'actions (fermer tel volet, éteindre
telle lumière…) qui revient **à la même heure**, sur **au moins 3 jours** (réglable)
dans les 3 dernières semaines. Quand un motif est assez régulier, elle te propose
d'en faire une routine — une seule fois (pas de harcèlement ; si tu l'écartes,
elle n'insiste pas).

L'exécution passe **toujours par le moteur d'actions** (jamais Nova en direct) :
chaque étape est une action courante, journalisée. Une étape qui échoue n'arrête
pas la routine ; le résultat est résumé.

## Utilisation

- **Depuis la conversation** : « **Luna, quand je dis “cinéma”, baisse les volets
  du salon et tamise la lumière — fais-en une routine.** » → elle la **propose**.
- **Activer / gérer** : **Paramètres › Routines** — *Activer* / *Rejeter* une
  proposition ; *Lancer* / *Renommer* / *Supprimer* une routine active.
- **Déclencher** : « **lance cinéma** » (voix ou écrit), ou le bouton **Lancer**.

## Sous le capot

- `core/app/routines/safety.py` : la **liste blanche** `ROUTINE_SAFE_ACTIONS` et
  la validation des étapes (refuse tout ce qui est sensible), + la signature
  d'anti-doublon.
- `core/app/routines/analyzer.py` : `find_candidates` — journal → scénarios
  récurrents (fonction pure, testable).
- `core/app/routines/runner.py` : exécute une routine étape par étape **via le
  moteur** (`run_direct`).
- `core/app/routines/service.py` : proposer / activer / rejeter / déclencher +
  la boucle de détection d'habitudes (une passe par heure).
- `core/app/store/db.py` : table `routines` (`steps` JSON, `status`
  proposed/active/rejected).
- `core/app/brain/toolbox.py` : `proposer_routine` (owner) /
  `lancer_routine` (personne reconnue) / `lister_routines`.
- `core/app/main.py` : WebSocket `routines` / `routine_approve` /
  `routine_reject` / `routine_run` / `routine_rename` / `routine_delete`.
- `ui/` : Paramètres › Routines (proposées + actives).

## Réglages

`.env` : `SENTINEL_ROUTINES` (on/off), `SENTINEL_ROUTINE_MIN_DAYS` (défaut 3),
`SENTINEL_ROUTINE_LOOKBACK` (défaut 21 jours). Nécessite **Nova connectée**
(`HA_URL`).

## Limites & évolutions

- Détection d'habitudes basée sur les **actions déjà exécutées** (allumer /
  éteindre / volets / scènes). Pistes : routines **déclenchées** par une heure ou
  un événement (pas seulement à la demande), édition fine des étapes dans le
  cockpit, routines composées de protocoles. La règle d'or ne bougera pas :
  **Luna propose, tu actives, aucune action sensible dans une routine.**
