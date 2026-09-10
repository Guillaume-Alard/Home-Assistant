# Plusieurs modèles au choix (multi-LLM)

Luna fonctionne par défaut avec **Claude** (Anthropic), qui reste son cerveau de
**référence**. Tu peux **brancher d'autres modèles** — ChatGPT (OpenAI), Google
Gemini, Groq, OpenRouter — et **basculer de l'un à l'autre à chaud** depuis le
cockpit, sans redémarrer. Utile pour profiter d'une **API gratuite** (Gemini,
Groq) ou pour comparer, sans renoncer à quoi que ce soit côté sécurité.

## Pourquoi Claude reste la référence

- **Meilleur usage des outils.** Claude enchaîne les outils (lire l'état, agir,
  proposer) de façon très fiable. Les autres modèles y arrivent aussi, mais avec
  des résultats **variables selon le modèle** — les petits modèles se trompent
  parfois d'outil ou d'argument. C'est le compromis d'un modèle gratuit.
- **La recherche web n'existe que pour Claude.** C'est un outil *serveur*
  d'Anthropic (intégré, avec citations). Il n'a pas d'équivalent portable : avec un
  autre fournisseur actif, Luna ne cherche pas sur le web (le reste marche).
- Claude est piloté **nativement** (SDK Anthropic) ; les autres passent par leur
  **API compatible OpenAI**, via un adaptateur unique qui les couvre tous.

## Sécurité — identique quel que soit le modèle

C'est le point essentiel : **changer de modèle ne change RIEN aux garde-fous.**
Quel que soit le cerveau qui parle, un appel d'outil repart **par le même chemin** —
la Toolbox, puis le moteur d'actions :

- Les **niveaux de confiance** s'appliquent à l'identique (un invité ne pilote pas
  la maison, la reconnaissance de locuteur n'élève jamais un droit).
- Tout ce qui dépasse la domotique courante reste une **proposition que tu
  valides** ; le déverrouillage et le désarmement restent réservés à l'interface.
- Un modèle alternatif n'a **aucun outil de plus** que Claude, et **aucun accès
  direct** à Nova. Le principe « propose puis approuve » n'est jamais contourné.

Autrement dit, le choix du modèle est une simple préférence de génération de texte :
la maison, elle, est protégée par le moteur, pas par le modèle.

## Activer un fournisseur

Chaque fournisseur s'active en posant **sa clé API** dans `.env`. Le modèle est
préréglé (raisonnable au moment de l'écriture) mais **remplaçable** — les catalogues
bougent, choisis le tien.

| Fournisseur | Clé (`.env`)          | Où l'obtenir                         | Coût            |
| ----------- | --------------------- | ------------------------------------ | --------------- |
| ChatGPT     | `OPENAI_API_KEY`      | platform.openai.com/api-keys         | payant          |
| Gemini      | `GEMINI_API_KEY`      | aistudio.google.com/apikey           | offre gratuite  |
| Groq        | `GROQ_API_KEY`        | console.groq.com/keys                | gratuit, rapide |
| OpenRouter  | `OPENROUTER_API_KEY`  | openrouter.ai/keys                   | selon le modèle |

Modèle par fournisseur : `OPENAI_MODEL`, `GEMINI_MODEL`, `GROQ_MODEL`,
`OPENROUTER_MODEL` (pour OpenRouter, la forme est `éditeur/modèle`, ex.
`meta-llama/llama-3.3-70b-instruct` — voir openrouter.ai/models).

Après avoir ajouté une clé : `docker compose restart sentinel-core`.

## Choisir et basculer

- **Cockpit** → Paramètres › Connexions › carte **« Modèle actif »**. La liste
  déroulante montre chaque fournisseur ; ceux sans clé sont **grisés** avec l'indice
  pour les activer. Choisis-en un : la bascule est **immédiate**, annoncée à tous
  les appareils, et **mémorisée** (elle survit à un redémarrage).
- **Au démarrage**, le fournisseur actif est celui de `SENTINEL_LLM_PROVIDER`
  (`claude` par défaut) — sauf si tu en as choisi un autre dans le cockpit, auquel
  cas ce dernier choix prime.
- Une clé jamais exposée : le cockpit n'affiche que le **nom** et le **modèle**,
  jamais la clé API.

## Bon à savoir

- **Fiabilité des outils.** Si un modèle gratuit se montre approximatif pour piloter
  la maison (mauvais outil, boucle inutile), reviens à Claude d'un clic. C'est
  précisément pour ça que la bascule est à chaud.
- **`max_tokens`.** Certains modèles récents réclament `max_completion_tokens` ;
  l'adaptateur bascule tout seul si l'API le demande.
- **La conversation est partagée.** L'historique est du texte : tu peux changer de
  modèle en cours de route, la suite du fil reste cohérente.
- **Coupé par défaut.** Sans clé alternative, rien ne change : Luna reste sur Claude.

## Sous le capot

- `core/app/brain/providers.py` : les profils (`PRESETS`, `load_profiles`),
  l'adaptateur compatible OpenAI (`OpenAICompatProvider`) et la traduction des
  déclarations d'outils Anthropic → OpenAI.
- `core/app/brain/llm.py` : le `Brain` choisit le fournisseur actif et **dispatche**
  — boucle native Anthropic pour Claude, adaptateur pour les autres. Le callback
  `run_tool` (Toolbox → moteur) est le même dans les deux cas : d'où la sécurité
  identique, garantie aussi par le test `core/tests/test_providers.py`.
- Le fournisseur actif est persisté dans le `Store` (clé `llm_provider`).
