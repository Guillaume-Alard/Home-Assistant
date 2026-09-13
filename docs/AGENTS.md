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

## Les agents

| Agent | Rôle | Écrit ? |
|---|---|---|
| 🔎 **Research** | Cherche, compare, **vérifie** avec sources (recherche web + lectures). | ❌ lecture seule |
| 🏠 **Home** | Diagnostique la maison (Home Assistant) et **agit** sur la domotique courante ; le reste passe par une proposition. | ✅ via le moteur |

**Home** est le premier agent qui **agit** : il n'écrit toutefois **jamais**
directement — ses actions passent par `action_domotique`/`creer_proposition`,
donc par le moteur « propose puis approuve », et une action sensible reste
validable depuis l'interface uniquement. Il n'a aucun outil d'administration
(santé système, dev, MCP…), réservés à l'orchestrateur ou à d'autres rôles.

En ajouter un autre ne demande qu'une **entrée de registre** dans
`core/app/brain/agents.py` — prompt, sous-ensemble d'outils, modèle optionnel.
Prochains candidats naturels, en réutilisant l'existant :

- 💻 **Developer** → l'atelier (worker Claude Code déjà isolé) ;
- 🖥️ **Infra** → docker-proxy + santé ;
- 👁️ **Vision** → l'outil `regarder`.

Chacun n'exposera qu'un sous-ensemble d'outils **déjà** soumis au moteur : ajouter
un agent n'ouvre aucune capacité nouvelle, ne fait que **cadrer** un rôle.

## Modèle par agent

Un agent peut préciser un `provider` (Claude ou un fournisseur compatible OpenAI
local, ex. un Qwen). Sans précision, il utilise le fournisseur actif. La
recherche web (native Claude) n'est disponible que sur Claude et pour une personne
reconnue.

## Sécurité — récapitulatif

| Garde-fou | Comment |
|---|---|
| Jamais d'élévation | L'agent hérite de `who` ; `deleguer` réservé aux personnes reconnues. |
| Périmètre d'outils | L'agent ne voit que ses outils ; hors périmètre = refusé (double contrôle). |
| Jamais d'écriture directe | Les actions passent par le moteur « propose puis approuve ». |
| Pas de récursion | Un agent n'a pas `deleguer` ; boucle bornée. |
| Sensible = interface | Inchangé, quel que soit l'agent. |
