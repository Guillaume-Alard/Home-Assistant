# Luna

Assistante domestique de Guillaume Alard. Elle vit **dans** Home Assistant, sur
Nova. Reconstruction complète, elle remplace « Sentinelle ».

**Phase 1 livrée** : piloter la maison par écrit depuis la carte Loggia.

## Trois artefacts

| | Rôle | Où |
|---|---|---|
| [`addon/`](addon/) | **Le cerveau** — orchestrateur, API Claude, arbitre d'autonomie, SQLite | Conteneur HAOS sur Nova |
| [`integration/`](integration/) | **Le nerf** — enregistre les commandes `luna/*`, relaie, expose `binary_sensor.luna_en_ligne` | Processus Python de Home Assistant |
| [`card/`](card/) | **Le visage** — `luna-card.js`, un seul fichier, aucun build | Navigateur, dans Loggia |

Le cahier des charges n'en prévoyait que deux. Le troisième, l'intégration, est
structurellement nécessaire : une carte Lovelace ne peut parler qu'à Home
Assistant, et seul du code tournant *dans* Home Assistant peut y enregistrer une
commande WebSocket. Voir [`docs/P1-HYPOTHESES.md`](docs/P1-HYPOTHESES.md), A1.

## Le chemin d'un message

```
 ①  « allume le salon », tapé dans la carte
 ②  carte → hass.connection.subscribeMessage({type:"luna/chat", …})
 ③  Home Assistant authentifie. connection.user est connu.
 ④  intégration → add-on          (WS interne, secret partagé)
 ⑤  add-on → Claude               (streaming, outils, cache de prompt)
 ⑥  Claude demande commander_lumiere(salon, on)
 ⑦  L'ARBITRE résout light.turn_on → niveau 2 → autorisé pour ce profil
 ⑧  add-on → Home Assistant       (call_service, via le Supervisor)
 ⑨  add-on → intégration → carte  : delta, delta, tool, done
```

Les points ⑦ et ⑧ sont l'invariant de sécurité (§9.2 du cahier des charges) :
**aucun chemin de code n'atteint ⑧ sans passer par ⑦**, et un test statique
échoue si un fichier autre que l'arbitre mentionne seulement l'écriture.

## Ce que Luna sait faire, et ce qu'elle refuse

Elle lit l'état de la maison, allume et éteint les lumières et les
interrupteurs, active une scène. Elle **propose** un réglage de thermostat —
c'est Guillaume qui valide, et la décision est journalisée, acceptée ou refusée.

Elle ne commande ni les ouvrants, ni les serrures, ni l'alarme, et n'envoie rien
vers l'extérieur : niveau 5, hors périmètre v1. Le refus est structurel — ces
services ne sont pas déclarés à Claude, qui ne peut donc pas les demander, et
l'arbitre les refuserait de toute façon.

## Documents

| | |
|---|---|
| [`docs/P1-HYPOTHESES.md`](docs/P1-HYPOTHESES.md) | Les sept contradictions du cahier des charges et leurs arbitrages, les trente hypothèses, et ce qui reste ouvert |
| [`docs/P1-CONTRATS.md`](docs/P1-CONTRATS.md) | Couches, contrats WebSocket, échelle d'autonomie, schéma SQLite, écarts constatés, état de la recette |
| [`docs/P0-HTTPS.md`](docs/P0-HTTPS.md) | Le prérequis bloquant §4, à régler avant la voix |

## Installer sur Nova

```bash
./ops/deploy.sh root@nova.local
```

Puis les quatre étapes manuelles que le script rappelle : installer l'add-on,
redémarrer Home Assistant, ajouter l'intégration, déclarer la ressource
Lovelace.

## Vérifier

```bash
cd addon        && pytest -q && lint-imports    # 151 tests, 4 contrats de couches
cd integration  && pytest -q                    # 17 tests, vraie instance HA
cd card         && pytest -q                    # 23 tests, vrai Chromium
```

191 tests, aucun appel réseau réel : le client Home Assistant tourne contre un
faux serveur WebSocket, le client Claude contre des réponses enregistrées, la
carte contre un faux `hass` qui rejoue le contrat §4. **La CI ne consomme jamais
de crédit.**

## Ce qui reste avant P2

- Confirmer que Nova tourne bien Home Assistant OS (hypothèse H1).
- Choisir le nom de domaine et l'hébergeur DNS, pour le HTTPS local. Sans lui,
  `getUserMedia` reste refusé sur le réseau de la maison et la voix n'a pas de
  micro — voir [`docs/P0-HTTPS.md`](docs/P0-HTTPS.md).
- Vérifier sur Nova que le cache de prompt prend vraiment
  (`cache_read_input_tokens > 0` au second échange) : c'est le levier de coût
  numéro un, et le seul point de la recette qu'aucun test ne peut couvrir sans
  dépenser des crédits.

## Licence et parenté

Trois principes — contrats de couches vérifiés en CI, mémoire à faits atomiques,
échelle d'autonomie graduée — sont **inspirés** de jarvis-OS (Grominet95).
Inspiration uniquement : jarvis-OS est sous AGPL-3.0, aucun fichier n'en est
repris. Les idées architecturales ne sont pas couvertes par le droit d'auteur ;
les fichiers, si.
