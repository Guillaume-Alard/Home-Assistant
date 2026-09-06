# La mémoire de Luna (Phase 1)

Luna se **souvient** de toi d'un échange à l'autre : tes préférences, tes
habitudes, la façon dont tu aimes qu'on te parle, les faits stables de ta vie.
Elle s'en sert pour **personnaliser ses réponses** — rien de plus.

C'est un principe volontairement strict : la mémoire est un **enrichissement de
contexte**, **jamais** un pouvoir d'agir. Aucun souvenir ne déclenche d'action
sur la maison, n'appelle Nova, ni ne crée de proposition. Et tout reste **local
sur Nebula** : les souvenirs vivent dans la même base SQLite que la conversation
(`data/sentinel.db`) et ne quittent jamais le serveur, sinon comme contexte de
tes propres échanges avec le cerveau (au même titre que l'historique).

## Comment ça marche

- **Apprentissage automatique, sous ton contrôle.** Au fil des échanges, Luna
  retient discrètement (outil `memoriser`) ce qui est *durablement* utile. Elle
  ne note pas les banalités d'un échange ponctuel, et jamais un secret (mot de
  passe, code, données bancaires) — c'est inscrit dans ses règles.
- **Injection dans le prompt.** À chaque réponse, un bloc « ce que je sais de
  toi » est ajouté au prompt système, **après le point de cache** (il évolue,
  donc n'est jamais figé). Les catégories : **préférence · habitude · style de
  langage · à savoir**.
- **Tu gardes la main — totalement.** Dans **Paramètres › Mémoire** tu vois
  *tout* ce que Luna retient, groupé par catégorie ; tu peux **ajouter** un
  souvenir toi-même et **supprimer** n'importe lequel d'un clic. C'est *cette*
  transparence et ce contrôle direct qui font office de garde-fou pour cette
  capacité (la mémoire étant trivialement réversible, elle ne passe pas par le
  circuit « propose puis approuve » réservé aux actions sur le monde réel).
- **À la voix aussi.** « Retiens que… » fait mémoriser ; « oublie que… » fait
  supprimer (outil `oublier`). « Qu'est-ce que tu sais de moi ? » les liste.

## Réglages (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `SENTINEL_MEMORY` | `on` | `off` = souvenirs conservés mais plus injectés dans le contexte. |
| `SENTINEL_MEMORY_WINDOW` | `60` | Nombre de souvenirs (les plus récents) injectés à chaque tour. |

## Sous le capot

- **Stockage** : table `memories` du `Store` (`core/app/store/db.py`) —
  `id`, `subject`, `category`, `content`, `source` (`luna` | `manuel`),
  `created_at`, `updated_at`. La colonne `subject` (défaut `guillaume`) est
  prête pour la **reconnaissance de locuteur (Phase 2)** sans migration : chaque
  personne aura son propre profil.
- **Mise en forme** : `core/app/brain/memory.py` (`format_profile`) rend le bloc
  lisible injecté par `_system_blocks` (`core/app/brain/llm.py`).
- **Outils LLM** : `memoriser`, `lister_souvenirs`, `oublier`
  (`core/app/brain/toolbox.py`) — aucun ne touche le moteur d'actions.
- **API WebSocket** (pour l'UI) : `memoires` (liste), `memoire_add`,
  `memoire_delete`. Après tout changement, la liste est rediffusée à tous les
  appareils connectés.

## Ce que la Phase 1 ne fait pas (volontairement)

- Pas d'action déclenchée par un souvenir — c'est du contexte, point.
- Pas de profils multi-personnes encore : tout est attribué à `guillaume`
  jusqu'à la reconnaissance de locuteur (Phase 2).
