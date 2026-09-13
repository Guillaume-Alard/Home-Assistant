# Les agents de Sentinel (multi-agent)

Sentinel est l'**orchestrateur**. Pour une tâche qui relève clairement d'un
domaine, il peut la **déléguer** à un sous-agent spécialisé, puis **synthétiser**
sa réponse. Un agent = un **rôle** : un prompt système + un **sous-ensemble** des
outils existants + un modèle optionnel.

> Peu d'agents, mais chacun avec une vraie responsabilité, des outils dédiés et
> des permissions propres.

## La règle de sécurité (le principe fondateur, à travers la délégation)

Un sous-agent n'est **jamais** un moyen d'en faire plus :

- Il tourne avec la **même identité** (`who`) que la demande d'origine — la
  reconnaissance de locuteur n'élève **jamais** un droit, même en déléguant.
- Il ne voit **que** les outils de son périmètre (un sous-ensemble de la Toolbox) —
  jamais une capacité nouvelle. Un double contrôle refuse tout outil hors périmètre.
- Tout appel d'outil repart par la Toolbox et le **moteur « propose puis approuve »** :
  aucune écriture directe, l'invariant statique reste vrai.
- Pas de **délégation récursive** : un agent n'a jamais l'outil `deleguer`, et sa
  boucle est bornée (même plafond d'étapes que l'orchestrateur).
- Une action **sensible** reste validable **depuis l'interface uniquement**, quel
  que soit l'agent qui la propose.

## Comment ça marche

1. L'orchestrateur (Luna) appelle l'outil `deleguer(agent, tache)` quand la tâche
   relève d'un agent.
2. Le sous-agent tourne sa **propre** boucle (son prompt, ses outils, éventuellement
   son modèle) et renvoie un **texte**.
3. Luna **synthétise** ce résultat pour Guillaume (elle ne recopie pas tout brut).

`deleguer` est réservé aux **personnes reconnues** (comme la domotique courante) ;
l'agent hérite ensuite de l'identité du demandeur.

## Missions (délégation observable)

Une délégation est une **mission** : le cockpit sait, en direct, **quel** agent
travaille et **sur quoi**. Le cerveau émet un évènement `mission` au **début** et
à la **fin** de chaque `run_agent`, et l'activité des outils qui suit (« consulte
Nova… ») est **rattachée** à l'agent en cours (« 🔎 Research · consulte Nova… »).

C'est **purement observable** : rien n'est ouvert, aucun chemin d'exécution n'est
modifié — juste de la visibilité. Les droits restent ceux du demandeur, et une
action sensible reste validable depuis l'interface uniquement.

Dans le **cockpit**, le tiroir **Agents** (icône « diagramme », en haut à droite)
montre une **carte par agent** — son rôle, sa **posture** (`lecture` / `propose` /
`agit`, *calculée* d'après ses outils réels), son **modèle** (sélecteur) et son état
en direct (« en mission… » quand il travaille) — plus un **journal des délégations
récentes** (agent, tâche, résultat). Le roster vient du serveur (`agent_roster`) ;
les cartes n'**affichent** — sauf le sélecteur de modèle, qui n'ouvre aucun droit.

Chaque mission terminée note aussi ses **tokens** et un **coût estimé** : un modèle
**local** apparaît « gratuit » (ta machine), un modèle cloud avec son prix indicatif
(grille `pricing.py`), ou « coût n.c. » si le prix est inconnu. Les tokens sont
rattachés à la mission via un accumulateur *par tâche* (`_MISSION_USAGE`) — jamais
mélangés avec le tour principal ni avec une autre délégation.

## Les agents

| Agent | Rôle | Écrit ? |
|---|---|---|
| 🔎 **Research** | Cherche, compare, **vérifie** avec sources (recherche web + lectures). | ❌ lecture seule |
| 🏠 **Home** | Diagnostique la maison (Home Assistant) et **agit** sur la domotique courante ; le reste passe par une proposition. | ✅ via le moteur |
| 💻 **Developer** | Relit le propre code de Luna et **propose** une évolution (diff) que Guillaume valide. | ❌ propose seulement |
| 🖥️ **Infra** | Diagnostique la santé des systèmes (conteneurs, ressources, Nova) et **propose** une remédiation. | ❌ propose seulement |
| 👁️ **Vision** | Regarde une caméra de Nova et décrit ce qui est visible, factuellement. | ❌ lecture seule |

**Home** est le seul agent qui **agit** sur la maison : il n'écrit toutefois
**jamais** directement — ses actions passent par
`action_domotique`/`creer_proposition`, donc par le moteur « propose puis
approuve », et une action sensible reste validable depuis l'interface uniquement.
Il n'a aucun outil d'administration (santé système, dev, MCP…), réservés à
l'orchestrateur ou à d'autres rôles.

**Developer** et **Infra** ne **proposent** que : Developer relit le code
(`lire_mon_code`) et soumet un diff (`proposer_evolution`) que Guillaume applique —
il n'applique jamais rien lui-même et ne touche ni garde-fou de sécurité ni secret
(refusé d'office) ; Infra diagnostique (`sante_systemes`, `audit_systemes`) puis, si
besoin, propose une remédiation (`creer_proposition`) — il n'exécute rien. **Vision**
regarde (`regarder`) et décrit : une observation, jamais une action.

En ajouter un autre ne demande qu'une **entrée de registre** dans
`core/app/brain/agents.py` — prompt, sous-ensemble d'outils, modèle optionnel.
Chacun n'expose qu'un sous-ensemble d'outils **déjà** soumis au moteur : ajouter
un agent n'ouvre aucune capacité nouvelle, ne fait que **cadrer** un rôle.

## Un modèle par agent

Chaque agent peut tourner sur **son propre modèle**. Dans le cockpit, sa carte
(tiroir **Agents**) a un sélecteur **Modèle** : **Auto** (il hérite du fournisseur
actif) ou un fournisseur précis — Claude, un cloud, ou le **modèle local** (Qwen sur
la RTX 2070 de Nebula). Idée : confier les rôles de **lecture** (Research, Vision) à
un modèle local gratuit, et garder Claude pour l'**orchestration** et les tâches
délicates.

- L'assignation est **persistée** et **éditée à chaud** (message WS
  `agent_set_provider` ; stockée dans le même blob `llm_config`, clé `agents`).
- **Résolution** : assignation du cockpit → `provider` éventuel du registre →
  fournisseur **actif**. Une préférence **indisponible** (p. ex. le local pas encore
  configuré) **retombe** proprement sur l'actif — jamais d'échec.
- **Aucun droit en plus.** Le choix ne porte **que** sur le modèle qui parle :
  outils, périmètre et moteur « propose puis approuve » restent identiques.
- La **recherche web** (native Claude) n'existe que sur Claude et pour une personne
  reconnue ; un agent sur un autre modèle ne l'a pas (le reste marche).

## Sécurité — récapitulatif

| Garde-fou | Comment |
|---|---|
| Jamais d'élévation | L'agent hérite de `who` ; `deleguer` réservé aux personnes reconnues. |
| Périmètre d'outils | L'agent ne voit que ses outils ; hors périmètre = refusé (double contrôle). |
| Jamais d'écriture directe | Les actions passent par le moteur « propose puis approuve ». |
| Pas de récursion | Un agent n'a pas `deleguer` ; boucle bornée. |
| Sensible = interface | Inchangé, quel que soit l'agent. |
