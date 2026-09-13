# MCP — brancher des services sans toucher au cœur

Sentinel est **client MCP** (Model Context Protocol). Tu déclares des serveurs
MCP dans `config/mcp.yml` ; leurs outils deviennent découvrables et utilisables
par Luna — **sous le même contrat de sécurité que tout le reste**. C'est la
couche d'extension : ajouter une capacité = ajouter un serveur, sans toucher au
cœur de Sentinel.

> Réservé à Guillaume. Brancher ou piloter un service externe, c'est de
> l'administration : la reconnaissance de locuteur n'élève jamais ce droit.

## Le contrat de sécurité (le principe fondateur, tenu)

Un outil MCP peut faire n'importe quoi. Il ne s'exécute donc **jamais à
l'aveugle** :

| Mode du serveur | Comportement |
|---|---|
| `lecture` | Les outils s'appellent **directement** (requête, lecture). À réserver aux serveurs sûrs. |
| `proposition` *(défaut)* | Chaque appel devient une **proposition** que tu approuves avant exécution. |

Pour un serveur `proposition`, le **niveau d'approbation** suit `risque` :
- `sensible` *(défaut)* → validable **depuis l'interface uniquement**, jamais à
  la voix ;
- `moyen` → approuvable à la voix comme une proposition ordinaire.

L'exécution d'un appel approuvé passe par le **moteur d'actions** (action
`mcp.call`) : journalisée, sérialisée, jamais doublée. Un appel MCP n'est pas une
écriture Nova (`call_service`), l'invariant statique ne le couvre pas — mais le
« propose puis approuve » s'y applique intégralement.

## Configurer

Édite `config/mcp.yml` :

```yaml
servers:
  - nom: recherche
    url: http://sentinel-mcp-recherche:8080/mcp
    mode: lecture               # appel direct (lecture seule)

  - nom: domotique-avancee
    url: http://sentinel-mcp-ha:8080/mcp
    mode: proposition           # défaut : validation avant exécution
    risque: sensible            # défaut : interface uniquement
    # jeton: "…"                # optionnel (Authorization: Bearer)
    # entetes: { X-Clef: "…" }  # en-têtes HTTP additionnels
```

Par défaut, aucun serveur n'est déclaré : l'outillage MCP n'apparaît pas.
`SENTINEL_MCP=off` le retire entièrement (voir `.env.example`).

## Utiliser

Luna dispose de deux outils (réservés à Guillaume) :

- `mcp_outils` — liste les serveurs, leurs outils, le schéma d'arguments et le
  mode. Elle le consulte **avant** d'appeler, pour connaître les arguments.
- `mcp_appeler` — appelle un outil (`serveur`, `outil`, `arguments`). Selon le
  mode : réponse directe (lecture) ou proposition à valider (proposition).

## Transport & limites (v1)

- **Transport « Streamable HTTP »** (endpoint HTTP unique, JSON-RPC 2.0 ;
  réponses `application/json` ou `text/event-stream`). Le transport **stdio**
  n'est pas encore géré — les serveurs MCP se lancent comme des conteneurs
  exposant un endpoint HTTP (cohérent avec l'architecture multi-conteneurs).
- Client **minimal** (pas de dépendance au SDK `mcp`) : `initialize`, session
  (`Mcp-Session-Id`), `tools/list`, `tools/call`. Les *resources* et *prompts*
  MCP ne sont pas encore exposés.
- Validé contre un serveur de test conforme ; **fais un essai** contre ton vrai
  serveur avant de t'y fier.

## Sécurité — récapitulatif

| Garde-fou | Comment |
|---|---|
| Jamais d'effet externe à l'aveugle | `proposition` = validation avant exécution ; `mcp.call` passe par le moteur. |
| Sensible = interface | Un serveur `proposition`/`sensible` ne s'approuve pas à la voix. |
| Réservé au propriétaire | `mcp_outils`/`mcp_appeler` exigent le niveau `owner`. |
| Jamais élévateur | La reconnaissance de locuteur ne débloque pas MCP. |
| Dégradé propre | Serveur en panne → signalé dans `mcp_outils`, jamais d'exception opaque. |
