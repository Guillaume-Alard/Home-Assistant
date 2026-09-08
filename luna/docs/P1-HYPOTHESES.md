# Luna — P1 : contradictions et hypothèses

**Statut : validé le 8 septembre 2026.** A1 à A7 acceptés, modèle par défaut
`claude-sonnet-5`. P1 est implémentée sur cette base — voir
[`P1-CONTRATS.md` §16](P1-CONTRATS.md) pour les écarts constatés en chemin.

Ce document reste tel qu'il a été soumis : c'est la trace de ce qui a été
décidé, et pourquoi. Seul le tableau de validation, en fin de page, est à jour.

Deux parties :

- **Partie A — sept points où le cahier des charges se contredit ou laisse un
  trou.** Ils ne se règlent pas par une hypothèse : il me faut ta décision.
- **Partie B — trente hypothèses de travail.** Si tu ne dis rien, je code
  celles-ci. Chacune indique ce qui change si elle est fausse.

---

# Partie A — Ce qui ne tient pas en l'état

## A1. Il manque un troisième livrable : l'intégration

**Le problème.** §3 annonce « deux livrables distincts » : l'add-on et la carte.
§3 impose aussi que la carte communique « exclusivement via
`hass.connection.sendMessagePromise()` et `hass.callService()` ».

Ces deux phrases sont incompatibles. `sendMessagePromise()` envoie un message sur
l'API WebSocket de Home Assistant. Pour qu'un type de message `luna/…` y soit
reconnu, il faut que quelqu'un appelle `websocket_api.async_register_command()`
**à l'intérieur du processus Python de Home Assistant**. Un add-on est un
conteneur séparé : il ne peut pas le faire. Même chose pour `callService()` — un
service n'existe que s'il est enregistré par une intégration.

Autrement dit : avec seulement un add-on et une carte, la carte n'a aucun moyen
de parler à Luna en respectant la règle « jamais de `fetch()` ».

**Ce que je propose.** Un troisième artefact, minuscule : une intégration
personnalisée `custom_components/luna/`, ~250 lignes, qui ne fait que trois
choses — enregistrer les commandes `luna/*`, les relayer à l'add-on, et exposer
un `binary_sensor.luna_en_ligne`. Aucune logique métier : c'est un nerf, pas un
cerveau. Le cerveau reste dans l'add-on, comme le veut §3.

```
carte ──WS HA──► intégration ──WS interne──► add-on ──API──► Claude
 (visage)         (nerf, ~250 l.)            (cerveau)
                                                 └──WS HA──► Home Assistant (bras)
```

Bonus non négligeable : l'intégration connaît l'utilisateur HA authentifié
derrière la connexion WebSocket (`connection.user`). C'est un signal d'identité
gratuit, non biométrique, disponible dès P1 (voir A6).

**Les deux autres options, pour mémoire :**

| Option | Pourquoi je ne la retiens pas |
|---|---|
| Tout mettre dans l'intégration, pas d'add-on | Contredit §3 frontalement. Et l'architecture en couches de §3.1 vit mal dans un `custom_component` : cycle de vie lié aux redémarrages de HA, dépendances déclarées dans `manifest.json`, base SQLite et jobs nocturnes moins propres. |
| La carte tape l'Ingress de l'add-on en `fetch()` | Lecture littérale de §3, « jamais de `fetch()` vers un hôte externe » — l'Ingress est de même origine, donc techniquement autorisé. Mais récupérer une session Ingress depuis une carte Lovelace est fragile et non documenté, et on perd `connection.user`. |

> **Décision attendue :** j'ajoute l'intégration comme troisième livrable ? Le
> cahier des charges passerait de « deux livrables » à trois.

## A2. Les ouvrants et l'alarme sont dans deux camps à la fois

**Le problème.** Trois passages se contredisent :

- **F1** : « Luna exécute les demandes […] éclairages, **ouvrants**, scènes,
  **alarme Alarmo** ».
- **F3**, niveau *Critique* : « Alarme, ouvrants, suppression de données —
  **hors périmètre v1** ».
- **§9**, niveau 5 : « Alarme, ouvrants, suppression de données, envoi vers
  l'extérieur — **Hors périmètre v1** ».

F1 dit que Luna les pilote ; F3 et §9 disent que non.

**Ce que je propose.** F3 et §9 gagnent — ce sont les règles non négociables, et
elles sont d'accord entre elles. En v1 :

- Luna **lit** l'état des ouvrants et de l'alarme (niveau 1, libre). Nécessaire
  pour F5, qui doit signaler « la baie vitrée est ouverte ».
- Luna **n'agit pas** dessus. Une demande explicite (« ferme les volets »)
  reçoit un refus motivé, journalisé, du type : *« Les volets sont au niveau 5,
  hors de mon périmètre pour l'instant. Tu peux le faire depuis Loggia. »*
- Le refus est **structurel**, pas une consigne de prompt : les services
  `cover.*`, `lock.*` et `alarm_control_panel.*` ne sont tout simplement pas
  dans la table des outils exposés à Claude. Le modèle ne peut pas les appeler,
  même s'il le voulait.

> **Décision attendue :** je fige la liste des domaines pilotables en v1 à
> `light`, `switch`, `scene`, `media_player`, `script` ? (Voir §7 des contrats
> pour le registre complet.)

## A3. Caddy sur Nova, sinon la règle d'indépendance saute

**Le problème.** §4 recommande « Caddy en reverse proxy sur le réseau local ».
§2 pose la règle d'indépendance : « Si Nebula et Orion sont éteintes, Luna
fonctionne normalement. »

Si Caddy tourne sur Nebula — l'endroit naturel, il y a déjà Docker — alors
éteindre le NAS coupe le HTTPS, donc le micro, donc la moitié de Luna. La règle
d'indépendance est violée par le prérequis censé la servir.

**Ce que je propose.** Le proxy tourne **sur Nova**, en add-on HAOS.

> **Mise à jour du 8 septembre.** Le principe tient — le proxy est sur Nova —
> mais ce n'est plus Caddy. Faute de domaine en propre, le certificat vient de
> l'add-on officiel **Duck DNS**, et l'add-on officiel **NGINX SSL proxy** le
> sert sur le 443. Caddy n'était recommandé que parce qu'il sait *aussi*
> obtenir le certificat ; ce n'est plus lui qui s'en charge, et un add-on Caddy
> capable de DNS-01 demanderait une image personnalisée à maintenir. Procédure
> complète dans [`P0-HTTPS.md`](P0-HTTPS.md).

## A4. Un dépôt HACS ne porte qu'une seule catégorie

**Le problème.** Trois artefacts (add-on, intégration, carte) relèvent de trois
conventions de dépôt différentes, et HACS n'accepte qu'une catégorie par dépôt.
Un monorepo `Alardware/luna` ne peut pas être à la fois dépôt d'intégration et
dépôt de plugin Lovelace.

**Ce que je propose.** **Pas de HACS du tout en v1.** C'est une installation
personnelle sur une seule machine :

- l'add-on : dépôt d'add-ons local, cloné dans `/addons/luna` sur Nova ;
- l'intégration : `custom_components/luna/` copié dans `/config/` ;
- la carte : `luna-card.js` copié dans `/config/www/`, déclarée en ressource.

Un `make deploy` (rsync via l'add-on SSH) suffit. Si tu veux HACS plus tard, il
faudra éclater en trois dépôts — c'est réversible, mais autant le savoir.

## A5. Les routes REST de §12 ne peuvent pas être appelées par la carte

**Le problème.** §12 définit cinq routes REST (`GET /suggestions`,
`POST /feedback`…). §8 interdit à la carte de faire des `fetch()`. Donc personne
ne les appelle.

**Ce que je propose.** Les routes §12 sont l'**API interne de l'add-on**,
consommée par l'intégration et par `curl` en débogage — jamais par le
navigateur. Chaque route reçoit une commande WebSocket `luna/*` jumelle pour la
carte. La correspondance 1:1 est dans [`P1-CONTRATS.md` §6](P1-CONTRATS.md).
Rien n'est perdu, tout reste documenté dès P1 comme le demande §12.

## A6. Quel est le profil actif en P1, alors que l'identité arrive en P3 ?

**Le problème.** §9 niveau 2 : « Libre **si le profil actif l'autorise** ». Mais
l'identité (présence, voix) est en P3. En P1 il n'y a pas de profil actif, donc
la règle d'autonomie n'a rien sur quoi s'appuyer.

**Ce que je propose.** L'intégration connaît l'utilisateur HA authentifié
(`connection.user`) : c'est un signal fiable, non biométrique, et déjà présent.
En P1, une table de correspondance dans les options de l'add-on :

```yaml
profils:
  - utilisateur_ha: "Guillaume"   # nom ou id de l'utilisateur HA
    profil: guillaume
  - utilisateur_ha: "Clara"
    profil: clara
# tout utilisateur non listé → guest
```

Score de confiance fixé à `1.0` avec `source: "ha_user"`. En P3, la fusion des
signaux (présence + voix) vient s'ajouter à celui-là au lieu de le remplacer —
le contrat de `luna/identity` est déjà écrit dans ce sens.

Ce n'est **pas** de l'anticipation sur P3 : c'est le minimum sans lequel §9 ne
peut pas s'appliquer en P1.

## A7. §9 impose une journalisation dès P1, mais le modèle de mémoire est en P4

**Le problème.** §9.1 : « Toute action de niveau ≥ 3 est journalisée avec sa
justification, qu'elle soit acceptée ou refusée. » C'est une règle non
négociable, donc active dès P1. Mais le modèle de données (F4 : `events`,
`facts`, `fact_observations`, `fact_relations`) est prévu en P4.

**Ce que je propose.** P1 crée **deux tables seulement** : `conversations` /
`messages` (pour le fil de §8) et `action_log` (pour §9.1). Les quatre tables de
F4 arrivent en P4, sans migration destructive — `action_log` alimentera
`events`. Schéma dans [`P1-CONTRATS.md` §11](P1-CONTRATS.md).

---

# Partie B — Hypothèses de travail

Si tu ne dis rien sur une ligne, je code ce qui est écrit dedans.

## Plateforme et matériel

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H1 | Nova tourne **Home Assistant OS** (pas Container ni Core) ; le Supervisor et les add-ons sont disponibles. | Sans Supervisor, pas d'add-on : tout bascule dans l'intégration, et l'architecture de §3 change complètement. **Le plus structurant de la liste.** |
| H2 | Architecture **amd64 uniquement**. Je ne construis pas d'image aarch64. | Ajouter une cible de build ; sans conséquence sur le code. |
| H3 | L'add-on est **construit sur Nova** à partir du `Dockerfile` (add-on local). Première construction longue sur le N95, mises à jour ensuite incrémentales. | Alternative : image pré-construite publiée sur `ghcr.io` par GitHub Actions, et `image:` dans `config.yaml`. Demande un jeton ghcr sur Nova si le dépôt est privé. |
| H4 | Fuseau `Europe/Paris`, tout en français : code, commentaires, messages d'erreur, commits, documentation. | Rien, mais autant le figer maintenant. |
| H5 | Python **3.12**, image de base `ghcr.io/home-assistant/amd64-base-python`. | Version à ajuster selon ce que fournit l'image de base au moment du build. |

## Home Assistant

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H6 | L'add-on parle à HA via le **Supervisor** : `homeassistant_api: true` dans `config.yaml`, puis `ws://supervisor/core/websocket` avec le `SUPERVISOR_TOKEN`. Pas de jeton longue durée à gérer. | Repli : jeton longue durée dans les options de l'add-on. Fonctionne, mais un secret de plus à faire tourner. |
| H7 | L'intégration joint l'add-on sur le réseau Docker du Supervisor, à `http://local-luna:8099`. **Nom d'hôte à confirmer sur Nova.** | Le `config_flow` de l'intégration expose un champ `hôte:port` en repli manuel. Prévu dès le début. |
| H8 | Le port `8099` de l'add-on n'est **pas publié** sur l'hôte. Joignable seulement depuis le réseau des add-ons. | Si le nom d'hôte de H7 ne marche pas, publier le port et viser l'IP de Nova — moins propre, à éviter. |
| H9 | Authentification du relais : **secret partagé**, généré à la main, mis dans les options de l'add-on et saisi une fois dans le `config_flow`. | Sans secret, n'importe quel add-on du réseau interne peut piloter Luna. Le coût est de cinq lignes ; je le garde. |
| H10 | Loggia est un dashboard Lovelace existant sur Nova. La carte est ajoutée en ressource `module` pointant `/local/luna-card.js`. | Si Loggia est en mode YAML (storage désactivé), l'ajout de ressource se fait dans `configuration.yaml` — même résultat. |
| H11 | Alarmo est installé et expose une entité `alarm_control_panel.*`. Utilisée en **lecture seule** (voir A2). | F5 perd son alerte « alarme non armée ». À signaler en P4. |
| H12 | Les huit capteurs d'ouvrants de F5 sont des `binary_sensor` avec `device_class: door`, `window` ou `garage_door`, et sont rattachés à des *areas*. | Il faudra une liste explicite d'`entity_id` dans les options plutôt qu'une découverte automatique. |

## Add-on et couches

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H13 | Paquet racine `luna`, sous-paquets `kernel` / `providers` / `engine` / `interfaces` pour L0 / L1 / L2 / L3. | Nommage seul ; les contrats `import-linter` suivent. |
| H14 | `import-linter` avec **trois contrats `forbidden`**, comme §3.1 l'écrit. Un contrat `layers` serait équivalent et plus court, mais je suis le cahier des charges. | Dis-le si tu préfères `layers` : c'est une ligne. |
| H15 | Un **quatrième contrat** interdit à L0 d'importer les bibliothèques tierces du projet (`anthropic`, `websockets`, `aiohttp`). §3.1 dit « stdlib + pydantic uniquement » — `import-linter` ne le vérifie pas via les trois contrats de couches. | Sans ce contrat, la règle L0 la plus importante n'est pas vérifiée automatiquement, donc pas vérifiée du tout. |
| H16 | Bus d'événements : `asyncio` pub/sub **en mémoire**, dans L0. Pas de Redis, pas de courtier. | Contrainte N95 : c'est le bon choix, mais ça veut dire pas de persistance des événements au redémarrage. |
| H17 | Base **SQLite** à `/data/luna.db` (volume persistant de l'add-on). Accès via `aiosqlite`, mode WAL. | `/data` est le seul chemin garanti persistant et sauvegardé par HAOS. |
| H18 | Deux tables en P1 : `conversations`/`messages` et `action_log`. Migrations par numéro de version dans une table `schema_version`. | Voir A7. |
| H19 | Tests : `pytest` + `pytest-asyncio`. Le client HA est testé contre un **faux serveur WebSocket** ; l'API Claude contre des réponses enregistrées. **Aucun appel réseau réel en CI, aucun crédit consommé.** | Sinon la CI coûte de l'argent à chaque push. |
| H20 | CI GitHub Actions : `ruff check`, `ruff format --check`, `lint-imports`, `pytest`. Bloquante sur `main` et sur toute branche. | §3.1 exige `import-linter` et `ruff` en CI « à chaque push ». |

## API Claude

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H21 | Modèle par défaut **`claude-opus-5`** (contexte 1M, 5 $/M en entrée, 25 $/M en sortie). Configurable dans les options de l'add-on. | **C'est ta facture, donc ton choix.** `claude-sonnet-5` est à 2 $/M en entrée et 10 $/M en sortie — deux fois et demie moins cher — et suffit très probablement pour de la conversation domestique outillée ; Sentinel tournait déjà dessus. Dis-moi lequel tu veux par défaut, en gardant H25 en tête. |
| H22 | **Réflexion adaptative** : `thinking: {type: "adaptive"}`, avec `output_config: {effort: "low"}` pour la conversation courante. Luna doit répondre vite et court ; les réponses sont lues à voix haute. | `effort` plus haut = réponses plus fouillées, plus lentes, plus chères. Réglable par option. |
| H23 | **Repli sur refus activé** : `betas: ["server-side-fallback-2026-07-01"]` + `fallbacks: "default"`. Si un classificateur de sûreté décline une requête, l'API bascule seule sur un autre modèle au lieu de renvoyer une réponse vide. | Sans ça, un refus se manifeste par `stop_reason: "refusal"` et un contenu vide — Luna paraîtrait muette sans raison. Je te le signale parce que c'est activé par défaut de mon côté ; dis-le si tu n'en veux pas. |
| H24 | **Mise en cache du prompt** : un point de rupture `cache_control` après le bloc système figé. Tout ce qui varie (heure, état de la maison, profil actif) passe **après**, jamais dedans. Vérifié par un test qui assert `usage.cache_read_input_tokens > 0` au deuxième tour. | C'est le levier de coût numéro un. Une seule date glissée dans le bloc système et le cache ne prend jamais — d'où le test. |
| H25 | Le contexte volatil (heure, pièce, profil, contexte « coucher ») est injecté en **message système de milieu de conversation** (`{"role": "system"}` dans `messages`), une capacité de `claude-opus-5`. Le préfixe mis en cache reste intact. | Si on retient `claude-sonnet-5` (H21), cette capacité n'existe pas : il faut retomber sur un bloc texte après les résultats d'outils. Ça marche aussi, c'est juste moins propre. **H21 et H25 sont liés.** |
| H26 | `max_tokens: 8192`, en streaming. Les réponses de Luna sont courtes par construction (prompt système) ; c'est un plafond de sécurité, pas une cible. | Un plafond atteint tronque la réponse en plein milieu. 8192 est large pour de l'oral. |
| H27 | Outils déclarés en `strict: true`. Résultats d'outils parallèles renvoyés **dans un seul message utilisateur**. | Éclater les résultats apprend au modèle à ne plus paralléliser. |
| H28 | La **classification du niveau d'autonomie ne passe jamais par le modèle**. Elle est faite par le registre de L0 à partir du couple `domaine.service` résolu, après que Claude a demandé l'outil. Écho de §9.2. | C'est l'invariant de sécurité. Un test statique vérifie qu'aucun exécuteur n'appelle `call_service` sans passer par l'arbitre. |

## Carte

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H29 | Carte en **JavaScript natif** (`HTMLElement` + `customElements.define`), pas LitElement. §8 autorise les deux ; le natif ne dépend d'aucun détail interne du frontend HA, donc ne casse pas à une mise à jour. Zéro build, zéro npm, zéro CDN — comme exigé. | LitElement donnerait un rendu incrémental gratuit. Sur un fil de conversation, la différence est marginale. |
| H30 | L'orbe est dessiné en **SVG animé par CSS**, pas en canvas : moins de CPU au repos sur le N95, et `prefers-reduced-motion` est respecté gratuitement. Cinq états : `idle`, `listening`, `thinking`, `speaking`, `alert`. | Le canvas permettrait des effets plus riches. Sur un N95 qui fait déjà tourner Whisper, je préfère le SVG. |

---

# Ce que je ne fais **pas** en P1

Explicitement, pour que ça ne dérive pas (§1, §10) :

- ❌ Aucun STT, aucun TTS, aucun pipeline Assist — c'est P2.
- ❌ Aucune empreinte vocale, aucune fusion de signaux — c'est P3.
- ❌ Aucune table `facts`, aucun apprentissage, aucun entretien nocturne — c'est P4.
- ❌ Aucun moteur de veille, aucune alerte, aucun tiroir « Veille » fonctionnel —
  c'est P4. Le tiroir existe dans la carte mais affiche « rien à signaler ».
- ❌ Aucune surveillance de l'installation — c'est P5.
- ❌ Aucune caméra, aucune reconnaissance faciale — c'est P6.
- ❌ Aucune action de niveau 5 : ouvrants, alarme, suppression, envoi externe.
- ❌ Rien de repris de jarvis-OS ligne à ligne (§13, AGPL-3.0). Les trois idées
  citées sont réimplémentées de zéro.
- ❌ Rien repris de Sentinel sauf deux fichiers, relus et réécrits :
  `core/app/brain/llm.py` et `core/app/ha/client.py`.

---

# Tableau de validation

Coche, corrige, ou réponds en vrac — j'adapte le contrat avant de coder.

| Point | Ma proposition | Verdict |
|---|---|---|
| **A1** Troisième livrable : intégration `custom_components/luna/` | oui | ✅ validé — livré, ~600 lignes avec les tests |
| **A2** Ouvrants et alarme en lecture seule en v1 | oui, §9 gagne sur F1 | ✅ validé — refus structurel, ces services ne sont pas déclarés à Claude |
| **A3** Le proxy tourne sur Nova, pas sur Nebula | oui | ✅ validé sur le principe — mais **Caddy est remplacé** par les add-ons Duck DNS + NGINX SSL proxy, voir plus bas et [`P0-HTTPS.md`](P0-HTTPS.md) |
| **A4** Pas de HACS en v1, déploiement par copie | oui | ✅ validé — `ops/deploy.sh` |
| **A5** Routes §12 = API interne, jumelles WS pour la carte | oui | ✅ validé — les cinq routes répondent `501`, les onze commandes existent |
| **A6** Profil P1 = utilisateur HA authentifié | oui | ✅ validé — et **durci** : le profil est résolu par l'add-on, pas annoncé par l'intégration |
| **A7** P1 crée `action_log` seule, pas le modèle F4 | oui | ✅ validé — trois tables |
| **H1** Nova est bien en HAOS | à confirmer | ✅ **confirmé** le 8 septembre 2026 — l'architecture de §3 tient telle quelle |
| **H21** Modèle par défaut | `claude-opus-5` ou `claude-sonnet-5` | ✅ **`claude-sonnet-5`** |
| **H23** Repli automatique sur refus | activé | ⚠️ **abandonné** — le paramètre `fallbacks` est réservé à Opus 5 et Fable. Un `stop_reason: "refusal"` est traité explicitement et produit un message lisible plutôt qu'un silence. |
| **H25** Contexte volatil en message système de milieu de conversation | oui | ⚠️ **remplacé** — capacité absente de Sonnet 5. Second bloc `system` après le point de rupture : même effet, cache préservé. |
| Nom de domaine interne + hébergeur DNS | voir [`P0-HTTPS.md`](P0-HTTPS.md) | ✅ **tranché** — aucun domaine possédé, donc **DuckDNS** (gratuit) plutôt qu'un achat |

### Ce qui reste à faire, et par qui

Plus aucune question ouverte : les deux dernières sont tombées le 8 septembre.

- **H1 confirmée.** Nova tourne Home Assistant OS. L'add-on, le Supervisor et
  `ws://supervisor/core/websocket` sont donc disponibles comme supposé.
- **Le domaine.** Guillaume n'en possède aucun, seulement l'adresse Nabu Casa.
  [`P0-HTTPS.md`](P0-HTTPS.md) a été réécrit autour de cette contrainte : un
  sous-domaine **DuckDNS** gratuit et l'add-on officiel du même nom fournissent
  le certificat, l'add-on **NGINX SSL proxy** le sert sur le 443. Un vrai
  domaine reste souhaitable à terme, mais rien n'attend après lui.

  Écart avec §4 du cahier des charges : Caddy est remplacé par ces deux add-ons
  officiels. Raison en §3 de `P0-HTTPS.md` — Caddy était recommandé parce qu'il
  sait aussi obtenir le certificat, ce dont DuckDNS se charge désormais, et un
  add-on Caddy capable de DNS-01 demanderait une image personnalisée à
  maintenir. Le bénéfice recherché — garder le 8123 en clair — est conservé.

Il ne reste donc que de l'exécution sur Nova, décrite pas à pas.
