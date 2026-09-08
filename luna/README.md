# Luna

Assistante domestique de Guillaume Alard. Elle vit **dans** Home Assistant, sur
Nova. Reconstruction complète, elle remplace « Sentinelle ».

**Phases 1 à 5 livrées** : piloter la maison par écrit **et à la voix**, depuis
la carte Loggia ou n'importe quel appareil qui parle à Assist ; Luna sait à qui
elle parle, apprend les habitudes de la maison, veille sur ce qui s'y oublie et
sur son installation.

## Trois artefacts

| | Rôle | Où |
|---|---|---|
| [`addon/`](addon/) | **Le cerveau** — orchestrateur, API Claude, arbitre d'autonomie, SQLite | Conteneur HAOS sur Nova |
| [`integration/`](integration/) | **Le nerf** — enregistre les commandes `luna/*`, relaie, expose `binary_sensor.luna_en_ligne` | Processus Python de Home Assistant |
| [`card/`](card/) | **Le visage** — `luna-card.js`, un seul fichier, aucun build | Navigateur, dans Loggia |

La voix n'ajoute pas un second cerveau : elle ajoute **une deuxième porte**.
Luna est un agent de conversation Home Assistant, et ne porte ni transcription
ni synthèse — Whisper et Piper restent les add-ons officiels, dans le pipeline
Assist (§7 : « voie principale : pipeline Assist de HA »).

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

## Deux portes, un cerveau

```
 carte Loggia ──┐                            ┌── Whisper (add-on officiel)
                ├─► intégration ─► add-on ◄──┤
 Assist ────────┘     (le nerf)   (le cerveau)└── Piper  (add-on officiel)
   ▲                                    │
   └── app Companion, iPad mural,        └─► Home Assistant
       satellite ESP32 plus tard
```

Le micro de la carte passe par le pipeline Assist en transcription seule, puis
le texte repart par le même chemin qu'une phrase tapée. Un tour de parole traité
par un satellite rejoint le fil de la carte restée ouverte : à l'écrit et à
l'oral, c'est la même conversation.

## Ce que Luna sait faire, et ce qu'elle refuse

Elle lit l'état de la maison, allume et éteint les lumières et les
interrupteurs, active une scène. Elle **propose** un réglage de thermostat —
c'est Guillaume qui valide, et la décision est journalisée, acceptée ou refusée.

Elle sait à qui elle parle : par la session Home Assistant quand il y en a une,
par la voix sur un appareil partagé. Ça change **quoi** elle répond et **comment**
elle le dit. Ça ne change jamais ce qu'elle a le droit de faire : le niveau
d'une action vient du registre, et valider une proposition demande toujours la
session Home Assistant, pas une ressemblance de voix.

Elle surveille ce qui s'oublie — un ouvrant resté ouvert la nuit, une lumière
allumée après le coucher — à partir de capteurs que Guillaume écrit dans Home
Assistant. Elle n'apporte que la pertinence : elle se tait pendant les heures de
silence, ne répète pas une alerte avant quatre heures, et retient un « ne plus
me le dire » pendant trente jours. Si une enceinte lui est déclarée, elle le dit
à voix haute — par l'arbitre, comme tout le reste, et avec les droits d'un
invité quand elle agit d'elle-même.

Elle ne commande ni les ouvrants, ni les serrures, ni l'alarme, et n'envoie rien
vers l'extérieur : niveau 5, hors périmètre v1. Le refus est structurel — ces
services ne sont pas déclarés à Claude, qui ne peut donc pas les demander, et
l'arbitre les refuserait de toute façon.

## Documents

| | |
|---|---|
| [`docs/P1-HYPOTHESES.md`](docs/P1-HYPOTHESES.md) | Les sept contradictions du cahier des charges et leurs arbitrages, les trente hypothèses, et ce qui reste ouvert |
| [`docs/P1-CONTRATS.md`](docs/P1-CONTRATS.md) | Couches, contrats WebSocket, échelle d'autonomie, schéma SQLite, écarts constatés, état de la recette |
| [`docs/P0-HTTPS.md`](docs/P0-HTTPS.md) | Le HTTPS local, sans nom de domaine : DuckDNS et deux add-ons officiels |
| [`docs/P2-VOIX.md`](docs/P2-VOIX.md) | La phase voix : cinq décisions, contrats, écarts constatés, état de la recette |
| [`docs/P3-IDENTITE.md`](docs/P3-IDENTITE.md) | L'identité : sept décisions, la formule de fusion, les contrats, les écarts |
| [`docs/P4-HABITUDES-VEILLE.md`](docs/P4-HABITUDES-VEILLE.md) | Habitudes et veille : huit décisions, la décroissance des faits, les écarts constatés, la liste de ce que P4 ne fait pas |
| [`docs/P4-CAPTEURS.md`](docs/P4-CAPTEURS.md) | **À coller dans `configuration.yaml`** — les capteurs sans lesquels la veille n'a rien à regarder |
| [`docs/P5-GARDIENNE.md`](docs/P5-GARDIENNE.md) | La gardienne de l'installation, définie par ses refus : huit décisions, treize hypothèses, les écarts constatés |
| [`docs/VOIX-CUSTOM.md`](docs/VOIX-CUSTOM.md) | *Side-quest* — entraîner une voix Piper sur Orion, et ce que ça demande vraiment |

## Installer sur Nova

```bash
./ops/deploy.sh root@nova.local
```

Puis les quatre étapes manuelles que le script rappelle : installer l'add-on,
redémarrer Home Assistant, ajouter l'intégration, déclarer la ressource
Lovelace.

## Vérifier

```bash
cd addon        && pytest -q && lint-imports    # 365 tests, 4 contrats de couches
cd integration  && pytest -q                    # 45 tests, vraie instance HA
cd card         && pytest -q                    # 68 tests, vrai Chromium
```

478 tests, aucun appel réseau réel : le client Home Assistant tourne contre un
faux serveur WebSocket, le client Claude contre des réponses enregistrées, et la
carte contre un faux `hass` qui rejoue le contrat §4 — mais avec un **vrai**
`AudioWorklet` et un micro synthétique de Chromium, donc le chemin de capture
est réellement exercé. **La CI ne consomme jamais de crédit.**

## Où en est le projet

**P1 à P5 sont livrées. Il reste P6, la reconnaissance faciale.**

P5 part d'un fait vérifié dans le code de Home Assistant : **Luna est déjà
administratrice**. L'add-on passe par le Supervisor, dont l'utilisateur vit dans
`GROUP_ID_ADMIN` — réécrire le dashboard ou désactiver une intégration lui est
ouvert aujourd'hui, sans rien changer. Ça n'avait jamais eu d'importance : P1 à
P4 n'appellent que `call_service` sur des lumières.

P5 est donc la première phase où « ce que Home Assistant l'empêche de faire » et
« ce qu'elle refuse de faire » cessent d'être la même chose. Elle se définit par
ses refus, et ils tiennent par des tests statiques plutôt que par de la
vigilance : aucun fichier n'a le droit de nommer une commande d'écriture, et le
manifeste ne monte que `share:ro`. Le premier échec de ce test a été une
docstring que je venais d'écrire pour dire que Luna n'écrivait pas.

Elle regarde quatre choses — entités indisponibles, intégrations en erreur,
automatisations qui plantent, cartes de Loggia qui pointent dans le vide — et
n'en fait qu'une ligne quand c'est un hub entier qui est tombé. Le seul
correctif qu'elle sache appliquer, recharger une intégration, est en niveau 4 :
proposition, validation administrateur. Tout le reste est une phrase, et c'est
très bien : « ton hub Zigbee est tombé mardi à 14 h 12, douze entités avec lui »
vaut mieux qu'un bouton qui redémarre quelque chose au hasard.

Le modèle de langage n'entre qu'à la fin, pour mettre en français un constat
déjà établi, et **il n'est jamais nécessaire** : sans lui, l'alerte sort quand
même. C'est aussi la première fois que Luna fait lire à un modèle des chaînes
qu'elle n'a pas écrites — noms d'appareils, messages d'exception, titres de
cartes. Ce qu'il rend est du texte, et rien que du texte.

P4 est la phase pour laquelle §1 a été écrit : une fois que Luna observe la
maison et prend la parole, tout devient tentant. Le document tient donc une
liste de ce que P4 **ne fait pas**, aussi longue que celle de ce qu'elle fait —
et elle est tenue.

La décision structurante : **la détection reste dans Home Assistant**, sous
forme de capteurs déclaratifs. Luna ne calcule jamais « fenêtre ouverte et nuit
et alarme non armée » — elle regarde le verdict, et n'apporte que ce que Home
Assistant ne sait pas faire : ne pas harceler, choisir le moment, se souvenir
d'un « ne plus me le dire ».

Deux sources de faits, et une seule entre en vigueur toute seule. Les
**observateurs** mesurent — l'heure de coucher, les séquences d'une même pièce —
et ce qu'ils produisent est actif d'emblée. Le **modèle**, une fois par nuit,
relit les échanges récents et **propose** ; ce qu'il croit comprendre attend
dans une file de relecture, et s'efface tout seul au bout de quatorze jours si
personne ne l'ouvre.

Un fait n'est jamais supprimé, il se périme : `confiance = maturité × fraîcheur`,
avec une demi-vie par catégorie — trente jours pour une heure de coucher, un an
pour « Clara est allergique aux chats ». Sous le seuil, il existe toujours ; il
cesse simplement d'être proposé. C'est la différence entre oublier et se taire.

Sur l'identité, le recadrage qui a rendu la phase faisable : le signal le plus
fort n'est pas biométrique, c'est l'utilisateur Home Assistant authentifié, et
il est livré depuis P1. Sur un téléphone, Luna sait déjà à qui elle parle. La
voix ne sert que là où ce signal est muet — **l'iPad partagé du couloir**.

### Ce qui reste à faire sur Nova, et que le code ne peut pas faire

- **Installer les add-ons faster-whisper et piper**, et créer un pipeline Assist
  dont Luna est l'agent de conversation.
- **Mesurer la latence de transcription** : §7 annonce 1 à 3 s sur le N95, et
  aucun test ne peut le vérifier d'ici.
- **Déposer un modèle ONNX d'empreinte de locuteur** dans `/share`, renseigner
  l'option `modele_voix`, déclarer les entités `device_tracker` de chacun, puis
  inscrire les voix depuis le badge de la carte. Sans modèle, Luna converse et
  pilote la maison comme avant — seule la reconnaissance reste éteinte, et elle
  le dit.

- **P0, le HTTPS local.** Sans lui, `getUserMedia` reste refusé sur le réseau de
  la maison et la voix n'a pas de micro. Pas de domaine en propre : la procédure
  passe par un sous-domaine **DuckDNS** gratuit et deux add-ons officiels, en
  sept étapes chiffrées dans [`docs/P0-HTTPS.md`](docs/P0-HTTPS.md).
  La première, vider l'URL interne dans l'app Companion, débloque le micro en
  deux minutes en attendant le reste.
- **Vérifier que le cache de prompt prend**
  (`cache_read_input_tokens > 0` au second échange). C'est le levier de coût
  numéro un, et le seul point de la recette qu'aucun test ne peut couvrir sans
  dépenser des crédits.
- **Choisir une voix française** dans l'add-on Piper. C'est une question
  d'oreille : `rhasspy.github.io/piper-samples`. En remplacer une par une voix
  entraînée sur mesure est une side-quest à part —
  [`docs/VOIX-CUSTOM.md`](docs/VOIX-CUSTOM.md).
- **Coller les capteurs de veille** dans `configuration.yaml`, puis déclarer les
  règles correspondantes dans les options de l'add-on
  ([`docs/P4-CAPTEURS.md`](docs/P4-CAPTEURS.md)). Sans eux, la veille démarre et
  annonce dans son journal qu'elle n'a rien à surveiller.
- **Regarder ce que la gardienne trouve la première semaine**, et remplir
  `gardienne.ignorer` avec les appareils hors ligne par choix. Le seuil de
  30 minutes se règle aussi : trop court et tu verras des hoquets Zigbee, trop
  long et une panne du matin se signale l'après-midi.
- **Laisser passer une vingtaine de soirées.** Le rappel de coucher ne part que
  si Luna a vraiment observé une habitude : trois soirs pour qu'elle s'y
  autorise, une vingtaine pour qu'elle en soit sûre. C'est le point 8 de la
  recette de P4, et le seul que la vraie maison peut juger.

## Licence et parenté

Trois principes — contrats de couches vérifiés en CI, mémoire à faits atomiques,
échelle d'autonomie graduée — sont **inspirés** de jarvis-OS (Grominet95).
Inspiration uniquement : jarvis-OS est sous AGPL-3.0, aucun fichier n'en est
repris. Les idées architecturales ne sont pas couvertes par le droit d'auteur ;
les fichiers, si.
