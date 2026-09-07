# La musique multi-pièces (Phase 9)

Luna pilote la musique de la maison — **par la voix, par l'écrit, ou depuis le
cockpit**. Elle passe par les lecteurs **`media_player`** de Nova (Spotify, Sonos,
Chromecast, enceintes connectées… peu importe l'intégration) et leurs services
standard. **Aucun compte ni jeton en plus** : tout reste sur le LAN, via Nova.

## Ce qu'elle sait faire

- **Lecture** : lecture, pause, morceau suivant / précédent, stop.
- **Volume** : régler (0–100 %), monter, baisser, couper / rétablir le son.
- **Source** : choisir une source parmi celles du lecteur (Spotify, radio…).
- **Multi-pièces** : « **envoie-le aussi dans la cuisine** » regroupe les pièces
  (là où l'intégration le permet — Sonos, Spotify Connect…).
- **Jouer un contenu** : « **mets du jazz** » via un **préréglage** (voir plus bas)
  ou une source.

## À la voix / à l'écrit

- « **Luna, mets de la musique dans le salon** » · « **pause** » · « **monte le
  son** » · « **mets le volume à 30 dans la chambre** »
- « **passe à la chanson suivante** » · « **mets la radio dans la cuisine** »
- « **envoie la musique du salon aussi dans la cuisine** »
- « **qu'est-ce qui joue ?** » → Luna liste les lecteurs actifs, titres et volumes.

C'est de la **domotique courante** : réservé aux **personnes reconnues** (toi ou
la maisonnée) ; un invité non reconnu ne pilote pas la musique. Rien de sensible
ici — pas de proposition à approuver, ça se fait directement.

## Depuis le cockpit — la tuile ♫ Musique

Le bouton **♫** ouvre un tiroir qui liste les lecteurs **actifs**, avec pour
chacun : le **titre / artiste**, les boutons **⏮ ▶/⏸ ⏭**, **couper le son**, un
**curseur de volume**, le **choix de source**, et **« diffuser aussi dans… »**
(transfert multi-pièces). La tuile se met à jour **en direct** quand la musique
change.

## Préréglages (« mets du jazz »)

Pour que « mets du jazz » lance quelque chose de précis, définis des raccourcis
dans **`config/media.yml`** (facultatif) :

```yaml
piece_defaut: Salon
presets:
  radio:
    source: "TSF Jazz"           # une source déjà connue du lecteur
  jazz:
    content_id: "spotify:playlist:…"   # ou un contenu (URI Spotify / Music Assistant)
    content_type: "music"
```

Sans préréglage, « mets du jazz » tente de jouer le texte tel quel (utile si ton
intégration accepte une recherche, comme Music Assistant) ; sinon, précise une
**source** que ton lecteur connaît.

## Sécurité & garde-fous

- Le contrôle média passe **toujours par le moteur d'actions** (`ha.media`),
  jamais par Nova en direct — comme toute écriture. Vérifié par les tests.
- C'est une action **non sensible** : pas de proposition, mais **réservée aux
  personnes reconnues** ; l'invité en est écarté (la reconnaissance n'élève
  jamais un droit).
- Fonctionne **en local** : aucun compte tiers, la musique est celle que Nova
  expose déjà.

## Sous le capot

- `core/app/ha/media.py` : lecture des lecteurs (`snapshot`, `player_view`),
  résolution pièce → lecteur, chargement des préréglages.
- `core/app/actions/executors.py` : exécuteur `ha.media` (op : play / pause /
  next / previous / volume / mute / source / play_media / join / unjoin).
- `core/app/brain/toolbox.py` : `musique` (personne reconnue) et `etat_musique`
  (lecture, public).
- `core/app/main.py` : WebSocket `media` (état) et `media_control` (cockpit) ;
  diffusion en direct sur changement d'un `media_player`.
- `config/media.yml` : préréglages & pièce par défaut. `ui/` : tuile ♫ Musique.

## Réglages

`.env` : `SENTINEL_MUSIC` (on/off). Nécessite **Nova connectée** (`HA_URL`) et au
moins un lecteur `media_player`.

## Limites & évolutions

- La **recherche libre** (« mets du Miles Davis ») dépend de l'intégration
  (Spotify officielle, Music Assistant…). Les **préréglages** et les **sources**
  marchent partout. Pistes : intégration d'une recherche musicale dédiée, files
  d'attente, réglage par groupe de pièces.
