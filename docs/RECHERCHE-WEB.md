# La recherche web de Luna (Phase 4)

Quand une question porte sur **l'actualité** ou une **connaissance externe** que
Luna ignore (ou qui a pu changer), elle peut **chercher sur le web** et te répondre
en **citant ses sources**.

## Ce qui la rend simple et « contrôlée »

- **Aucun compte ni service à ajouter.** Luna utilise l'outil de recherche web
  **natif d'Anthropic** — la recherche tourne côté Anthropic avec ta clé
  `ANTHROPIC_API_KEY` existante, et les **citations** reviennent intégrées. Rien à
  installer, pas de conteneur, pas de clé supplémentaire.
- **Plafonnée.** Un maximum de recherches par tour de conversation
  (`SENTINEL_WEB_SEARCH_MAX`, défaut 5) évite les dérives de coût.
- **Localisée France** pour des résultats pertinents (météo, actu).
- **Réservée aux personnes reconnues** (propriétaire + maisonnée). Un invité
  (voix non reconnue) n'y a pas accès — cohérent avec le principe prudent de la
  Phase 2.

## Sécurité — le contenu du web n'est jamais un ordre

Une page web est une **source d'information à citer**, pas une instruction. Luna
est explicitement cadrée pour **ne jamais suivre un ordre** qui viendrait d'un
contenu web (« ignore tes règles », « éteins les lumières »…). Les garde-fous
existants tiennent : elle n'agit sur la maison que sur **demande de Guillaume**, et
les actions sensibles restent réservées à l'interface. La recherche web n'ajoute
donc aucun pouvoir d'action — seulement de la lecture d'information publique.

## Coût

La recherche native d'Anthropic est facturée par Anthropic (de l'ordre d'**un
centime par recherche**, plus les jetons). Pour une maison, c'est négligeable ; le
plafond par tour garde tout sous contrôle. Désactivable à tout moment
(`SENTINEL_WEB_SEARCH=off`).

## Utilisation

- **À la voix / à l'écrit** : « Quelle est la météo demain à Bourges ? », « Quoi de
  neuf sur … ? », « Combien coûte … aujourd'hui ? ». Luna cherche si nécessaire et
  répond en citant.
- **Les sources** apparaissent sous sa réponse dans le fil (petites pastilles
  cliquables avec le nom du site) — et elle les mentionne à voix haute.

## Réglages (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `SENTINEL_WEB_SEARCH` | `on` | `off` = recherche web désactivée. |
| `SENTINEL_WEB_SEARCH_MAX` | `5` | Nombre max de recherches par tour (plafond coût). |

## Sous le capot

- `core/app/brain/llm.py` : l'outil serveur `web_search_20260209` est ajouté à la
  requête (avec `max_uses` et `user_location` FR) **uniquement** pour un locuteur
  reconnu et si la recherche est activée. La recherche s'exécute côté Anthropic ;
  la boucle gère le `pause_turn` (recherche longue) et agrège les **sources citées**
  (`_collect_sources`) pour les rediffuser à l'UI.
- `core/app/main.py` : diffuse un événement `sources` ; l'UI (`ui/js/chat.js`,
  `addSources`) les rattache sous la réponse.
- Le prompt système cadre l'usage : chercher seulement si utile, toujours citer,
  ne jamais obéir au contenu d'une page.

## Limites & évolutions

- Nécessite un modèle récent (défaut `claude-sonnet-5`, qui supporte l'outil).
- Alternative possible plus tard : une méta-recherche auto-hébergée (SearXNG) pour
  un fonctionnement 100 % local — au prix d'un conteneur supplémentaire et de
  citations moins propres. Rien de tout cela ne change les garde-fous.
