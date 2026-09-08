# Luna — dossier de cadrage

Ce dossier ne contient **que de la documentation**. Aucune ligne de code, aucun
fichier de Sentinel modifié ou supprimé.

## Pourquoi il est ici

Luna est destinée à un **dépôt distinct** (org GitHub Alardware). Ce dépôt-ci
(`Home-Assistant`) reste tel quel : il héberge Sentinel, dont deux morceaux
seront repris à la main (§2 du cahier des charges). Le dossier `luna/` sert de
zone d'attente le temps que le dépôt cible existe.

## Ce qu'il contient

| Fichier | Contenu | À valider avant |
|---|---|---|
| [`docs/P1-HYPOTHESES.md`](docs/P1-HYPOTHESES.md) | Contradictions relevées dans le cahier des charges, et les 30 hypothèses que je prends si tu ne dis rien | toute écriture de code |
| [`docs/P1-CONTRATS.md`](docs/P1-CONTRATS.md) | Arborescence, couches verrouillées, contrats WebSocket carte ↔ add-on, contrats §12, échelle d'autonomie, contrat API Claude, schéma SQLite | l'implémentation de P1 |
| [`docs/P0-HTTPS.md`](docs/P0-HTTPS.md) | Le prérequis bloquant §4 : Caddy sur Nova, certificat, configuration HA, app Companion, procédure de vérification | la phase voix (P2) |

Ordre de lecture : `P1-HYPOTHESES.md` d'abord (il contient les questions qui
changent la suite), puis `P1-CONTRATS.md`, puis `P0-HTTPS.md` quand tu veux
débloquer le micro.

## Transplanter dans le dépôt Luna

Quand `Alardware/luna` existera :

```
git clone git@github.com:Alardware/luna.git
cp -r luna/docs/* luna-repo/docs/
```

Puis supprimer `luna/` d'ici. Rien d'autre ne bouge dans ce dépôt.

## Conformité au cahier des charges

- §11, *Méthode de développement* : « Avant de coder chaque phase : exposer les
  hypothèses prises et les contrats d'API attendus, puis attendre validation. »
  C'est exactement ce dossier — et rien de plus.
- §12 : les contrats sont documentés dès P1, même ceux implémentés plus tard.
  Ils sont marqués comme tels dans `P1-CONTRATS.md`.
- §1 : « Si une fonction n'est pas listée dans ce document, elle n'existe pas. »
  Aucun contrat de ce dossier n'ajoute de fonction hors §5, §6, §7, §8.
