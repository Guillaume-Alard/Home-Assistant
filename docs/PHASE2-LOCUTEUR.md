# Reconnaissance de locuteur (Phase 2)

Luna reconnaît **qui parle** — Guillaume, une personne de la maisonnée (conjointe,
fils…), ou une voix inconnue — pour **personnaliser** son discours et sa mémoire,
et rester **prudente** avec un invité. Cette page fait d'abord l'analyse demandée
(solutions, vie privée, intégration), puis décrit ce qui est en place et comment
l'utiliser.

## Règle de sécurité cardinale

**La reconnaissance vocale ne peut JAMAIS élever un droit.** Le déverrouillage et
le désarmement restent réservés à l'**interface**, pour tout le monde, reconnu ou
non — exactement comme avant. La voix ne peut, au mieux, que *confirmer* le
propriétaire. Elle sert à **personnaliser** et, pour un inconnu, à **restreindre**.
Une usurpation vocale (imitation, enregistrement rejoué) ne donne donc accès à
rien de sensible : ce n'est pas un facteur d'authentification, c'est un confort.

## Analyse des solutions open-source

Toutes produisent une **empreinte vocale** (un vecteur) ; on identifie ensuite par
similarité cosinus avec les voix enrôlées. Comparées pour un usage **CPU, local,
maison** :

| Solution | Empreinte | Poids / dépendances | Remarque |
|---|---|---|---|
| **Resemblyzer** (GE2E d-vector) | 256 | torch (CPU ~1–1,5 Go) | API simple, robuste, idéale maison. **Retenu.** |
| **SpeechBrain ECAPA-TDNN** | 192 | torch + speechbrain + HF (plus lourd) | Plus précis, meilleur pour un GPU/plus de disque. Voie d'évolution. |
| **pyannote.audio** | var. | torch + jeton HF | Pensé diarisation ; surdimensionné ici. |
| Classique (MFCC + GMM) | — | léger (numpy) | Sans torch, mais nettement moins fiable. Écarté. |

**Choix : Resemblyzer**, le meilleur compromis simplicité/robustesse/poids sur CPU,
et suffisant pour distinguer 3–4 voix de la maison après enrôlement. Migration
possible vers ECAPA plus tard **sans toucher au cœur** : seul le service d'empreinte
changerait (le vecteur reste un vecteur, la comparaison est indépendante de la taille).

## Contraintes de vie privée

- **100 % local.** L'empreinte est calculée sur Nebula par le service
  `sentinel-speaker` ; rien ne part vers un tiers. Les vecteurs sont stockés dans
  la même base SQLite que la conversation (`data/sentinel.db`).
- **Empreinte, pas enregistrement.** On ne conserve **pas** l'audio d'enrôlement,
  seulement le vecteur (non ré-inversible en voix intelligible).
- **Mémoire cloisonnée par personne.** Chaque profil a **sa** mémoire (Phase 1,
  colonne `subject`). Luna n'utilise pas les souvenirs de Guillaume pour quelqu'un
  d'autre, et la page *Paramètres › Mémoire* ne montre que ceux de Guillaume — pas
  de surveillance croisée de la maisonnée.
- **Invité = discrétion.** Une voix non reconnue n'ouvre aucune action et n'accède
  à aucune mémoire personnelle.
- **Désactivable en une ligne** (`SPEAKER_HOST=` vide) : Luna revient au
  comportement d'avant la Phase 2 (tout le monde traité comme le propriétaire).

## Niveaux de confiance

| Niveau | Qui | Peut agir sur la domotique | Outils d'admin (dev, conteneurs, journaux) | Mémoire | Actions sensibles |
|---|---|---|---|---|---|
| **owner** | Guillaume (écrit/UI/Assist, ou voix reconnue comme lui) | oui | oui | la sienne | **UI seulement** |
| **household** | personne reconnue (conjointe, fils…) | oui | non | la sienne | **UI seulement** |
| **guest** | voix non reconnue / non enrôlée | non | non | aucune | **UI seulement** |

Les canaux **sans voix** (message écrit dans le cockpit, Assist authentifié par
jeton) sont traités comme le **propriétaire** : la reconnaissance ne concerne que
la voix. Le seuil de reconnaissance est réglable (`SPEAKER_THRESHOLD`, défaut 0,75).

## Plan d'intégration (et ce qui est en place)

1. **Service `sentinel-speaker`** (`speaker/`) : FastAPI + Resemblyzer, `POST /embed`
   (PCM → vecteur). Apatride : il ne connaît ni les profils ni les droits.
2. **Cœur** :
   - `voice/wyoming.py::SpeakerEmbedder` — client HTTP du service.
   - `store` — tables `speakers` / `speaker_samples` (+ `speaker_profiles()`).
   - `identity.py` — `Speaker`, `cosine`, `identify` (matching pur, testé).
   - `main.py::identify_speaker` — appelé dans le tour vocal : empreinte →
     meilleur profil au-dessus du seuil, sinon *invité*. **Service en panne ou
     personne enrôlée → propriétaire** (on ne verrouille jamais Guillaume dehors).
   - Le locuteur est ensuite passé aux intents (`can_act`), au cerveau (bloc de
     prompt « à qui tu parles ») et à la Toolbox (droits par outil).
3. **Enrôlement** (WebSocket) : `speaker_add` / `speaker_delete` /
   `speaker_enroll_start` + audio + `speaker_enroll_end`. L'UI enregistre de courts
   échantillons au micro.
4. **UI** : *Paramètres › Profils vocaux* (créer, enrôler, supprimer) et un
   indicateur « qui parle » dans la colonne Voix.

## Utilisation (sur Nebula)

1. `SPEAKER_THRESHOLD` est déjà à 0,75 dans `.env` (ajuste si besoin). Le service
   démarre avec `docker compose up -d --build` (image ~1–1,5 Go, construite une fois).
2. Dans **Paramètres › Profils vocaux** : crée un profil (coche « C'est Guillaume »
   pour le tien), puis clique **🎙 échantillon** et parle ~3 s. **3 échantillons ou
   plus** par personne donnent une reconnaissance fiable.
3. Parle à Luna : l'indicateur « qui parle » affiche la personne reconnue (ou
   « invité »). Enrôle-toi en premier, sinon ta propre voix sera prise pour un invité.

> L'enrôlement et la reconnaissance passent par le **micro**, qui exige un
> **contexte sécurisé (HTTPS)** — comme pour parler à Luna. En HTTP simple, il n'y
> a pas de tour vocal, donc pas de reconnaissance : tout passe par l'écrit, traité
> comme le propriétaire.

## Réglages (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `SPEAKER_HOST` | `sentinel-speaker` | Vide = reconnaissance désactivée (tout le monde = propriétaire). |
| `SPEAKER_THRESHOLD` | `0.75` | Seuil de similarité (plus haut = plus strict). |

## Limites & évolutions

- Reconnaissance sur une **seule** personne à la fois (celle qui a parlé), pas de
  diarisation multi-voix dans un même énoncé.
- Précision liée à la qualité/au nombre d'échantillons et au bruit ambiant ;
  ajuster `SPEAKER_THRESHOLD` au besoin.
- Évolution possible : ECAPA-TDNN (précision), gestion des profils de la maisonnée
  dans l'UI, ré-enrôlement guidé. Rien de tout cela ne touche la règle de sécurité.
