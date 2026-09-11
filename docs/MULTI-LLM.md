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

## Activer un fournisseur — depuis le cockpit, à chaud

**Le plus simple : tout se règle dans le cockpit**, comme dans Home Assistant.
**Paramètres › Moteur › « Intelligence — modèles & génération »** : chaque
fournisseur a sa ligne, avec un **point** (● clé posée / ○ absente), un champ pour
**coller sa clé API**, un champ **modèle**, et un bouton **Activer**. Colle la clé,
appuie sur **Poser** : le fournisseur devient disponible **immédiatement** — sans
`.env`, sans redémarrage. Le choix du modèle se fait dans le même écran.

| Fournisseur | Où obtenir la clé                    | Coût            |
| ----------- | ------------------------------------ | --------------- |
| ChatGPT     | platform.openai.com/api-keys         | payant          |
| Gemini      | aistudio.google.com/apikey           | offre gratuite  |
| Groq        | console.groq.com/keys                | gratuit, rapide |
| OpenRouter  | openrouter.ai/keys                   | selon le modèle |

> **Les clés restent côté serveur.** Une clé posée dans le cockpit est stockée
> **sur Nebula** (dans le `Store`) et **jamais réaffichée** : l'interface n'expose
> qu'un booléen « configuré » (le point ●). Effacer le champ et **Poser** à vide
> supprime la clé et **retombe** sur la valeur d'environnement, s'il y en a une.

**Modèle** : préréglé (raisonnable au moment de l'écriture) mais **remplaçable** —
les catalogues bougent, choisis le tien. Champ vide = modèle par défaut du
fournisseur. Pour OpenRouter, la forme est `éditeur/modèle`, ex.
`meta-llama/llama-3.3-70b-instruct` (voir openrouter.ai/models).

**Alternative `.env` (facultative).** Tu peux toujours préremplir les clés au
démarrage : `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`,
`OPENROUTER_API_KEY`, et le modèle via `OPENAI_MODEL`, `GEMINI_MODEL`,
`GROQ_MODEL`, `OPENROUTER_MODEL`. Une clé posée dans le cockpit **prime** sur
celle de `.env` ; à défaut, c'est `.env` qui sert. Après un changement de `.env` :
`docker compose restart sentinel-core`.

## Choisir et basculer

- **Réglage complet** → Paramètres › **Moteur**. Le bouton **Activer** de chaque
  ligne bascule le cerveau ; un fournisseur sans clé affiche « Pose une clé pour
  l'activer » au lieu du bouton.
- **Bascule rapide** → Paramètres › Connexions › carte **« Modèle actif »** : une
  liste déroulante pour changer d'un geste, plus un raccourci vers l'éditeur.
- Dans les deux cas la bascule est **immédiate**, annoncée à tous les appareils,
  et **mémorisée** (elle survit à un redémarrage).
- **Au démarrage**, le fournisseur actif est le **dernier choisi** dans le cockpit ;
  à défaut `SENTINEL_LLM_PROVIDER` (`claude` par défaut).
- Une clé jamais exposée : le cockpit n'affiche que le **nom**, le **modèle** et
  l'état « configuré », jamais la clé API.

## Régler la génération (à chaud)

Dans la même carte **Moteur**, sous **« Génération »**, trois réglages vivants,
appliqués sans redémarrage et mémorisés :

- **Effort de réflexion** — Rapide / Équilibré / Approfondi (`low`/`medium`/`high`).
- **Tokens max / réponse** — longueur maximale d'une réponse (borné 16–64000).
- **Mémoire de conversation** — nombre de messages repris comme contexte (1–200).

Les valeurs sont **bornées côté serveur** : une saisie absurde est ramenée dans les
limites, jamais une erreur.

## Consommation & crédit restant

**Paramètres › Consommation.** Luna compte, en local, les **tokens** réellement
renvoyés par chaque API — par **modèle**, sur les **30 derniers jours** — et en
déduit un **coût estimé**. Un tableau montre, par modèle : tours, tokens entrée /
sortie, coût estimé. Rien du contenu échangé n'est stocké : **seulement des
compteurs**.

**Honnêteté, en toutes lettres :**

- Le **coût** est une **estimation** à partir d'une grille de prix **indicative**
  (USD par million de tokens, `core/app/brain/pricing.py`, éditable). Les tarifs
  bougent ; la **facture réelle** du fournisseur fait foi. Un modèle sans prix connu
  apparaît sans coût (`—`), et le total est marqué **partiel** (`+`).
- Le **solde restant** n'est un **vrai chiffre** que là où le fournisseur l'expose
  par API. C'est le cas d'**OpenRouter** (crédits − usage, affiché en direct). Pour
  **Claude/Anthropic**, il **n'existe pas d'API de solde** : Sentinel le dit et
  renvoie à `console.anthropic.com` (la carte montre alors la dépense **estimée**,
  pas un solde). **Groq** est gratuit. Les autres : « à vérifier sur la console ».
- Les **clés API** ne quittent jamais le serveur — le solde OpenRouter est
  interrogé **depuis Nebula**, la clé n'apparaît dans aucune réponse à l'UI.

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
  `apply_config(overrides)` reconstruit à chaud les profils (clés/modèles), le
  client Claude et les paramètres de génération (effort/tokens/historique).
- Les réglages du cockpit sont persistés dans le `Store` sous une clé unique
  `llm_config` (JSON : `active`, `providers[id].{key,model}`, `params`). L'ancienne
  clé `llm_provider` (fournisseur actif seul) est **migrée** automatiquement au
  premier démarrage. Les messages WS `llm_set_key` / `llm_set_model` /
  `llm_set_params` / `llm_select` écrivent cette config ; le serveur ne renvoie
  **jamais** de clé (vue publique : un booléen `configured`).
- **Consommation** : chaque tour rapporte ses tokens réels (`usage` de l'API — natif
  Claude et `stream_options.include_usage` côté compatible OpenAI) via le callback
  `on_usage` du `Brain` → `Store.add_usage` (table `llm_usage`, seaux par jour /
  fournisseur / modèle). La commande WS `llm_usage` agrège sur 30 jours, estime le
  coût (`brain/pricing.py`, prix indicatifs éditables) et joint le **solde** —
  réel pour OpenRouter (`/api/v1/credits`, interrogé côté serveur), honnête sinon.
