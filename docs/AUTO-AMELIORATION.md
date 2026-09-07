# L'auto-amélioration encadrée de Luna (Phase 6)

Luna peut **te proposer des améliorations de son propre code ou de sa
configuration** — corriger un détail, ajouter une petite capacité, ajuster un
réglage. Elle en rédige un **diff** ; **c'est toi qui relis et appliques**. Rien
ne se modifie tout seul, jamais.

## Le principe : proposer, jamais exécuter

- Luna **ne modifie jamais** son code elle-même. Elle produit un **diff** (outils
  `lire_mon_code`, `proposer_evolution`, `lister_evolutions` — réservés au
  propriétaire). Ce diff devient une **proposition**.
- **Relire, accepter, rejeter, supprimer** sont des actions du **cockpit**
  (Paramètres › Évolutions), après lecture du diff. C'est le même esprit que les
  actions sensibles et les pages web : la décision passe par toi.
- **« Accepter » n'exécute rien.** C'est ta décision, consignée ; tu appliques
  ensuite le diff toi-même (git / déploiement habituel). Aucune ligne de Sentinel
  n'applique automatiquement un diff — c'est **prouvé par des tests-verrous**
  (`core/tests/test_invariant.py`).

## Ce qu'elle ne peut PAS proposer — les limites strictes

Avant même d'être rangé comme proposition, **chaque diff passe au crible de la
politique** (`core/app/selfmod/policy.py`). Sont **refusés d'office** :

- Tout diff qui touche un **garde-fou de sécurité** :
  - les **niveaux de confiance** (`identity.py`, la table de gating des outils) ;
  - le **moteur d'actions** « propose puis approuve » (`actions/engine.py`,
    `actions/executors.py`) et les écritures vers le monde réel (`call_service`,
    `restart_container`, push GitHub) ;
  - l'**isolation de l'atelier** de développement (`worker/`) ;
  - la **gestion des secrets** (`mail/`, `.env`, certificats) ;
  - la **politique elle-même** (elle ne peut pas se désarmer) et les
    **tests-verrous**.
- Tout diff qui **introduit un secret en dur** (clé d'API, jeton…).
- Une proposition de **configuration** qui viserait autre chose qu'une clé de
  réglage de la **liste blanche** (les jetons et clés de sécurité en sont exclus)
  ou qui toucherait le vrai `.env` (elle ne touche que `.env.example`).

En cas de doute, la politique **refuse** — c'est le comportement sûr. Tu peux
toujours faire un tel changement toi-même, à la main.

## Utilisation

1. Demande à Luna : « **propose une amélioration de… (tel comportement)** », ou
   « **ajuste tel réglage** ». Elle **relit d'abord son code** (`lire_mon_code`,
   en seule lecture), puis rédige un diff avec `proposer_evolution` et te dit que
   la proposition attend dans **Paramètres › Évolutions**.
2. Ouvre **Paramètres › Évolutions** → **Relire le diff** pour le lire en entier
   (ajouts en vert, retraits en rouge).
3. Si ça te convient → **Accepter** (ta décision est consignée), puis applique le
   diff comme d'habitude. Sinon → **Rejeter** ou **Supprimer**.

## Sécurité

- **Le lecteur de code est en seule lecture** : `lire_mon_code` ne sert que les
  arbres `app/` et `ui/`, jamais `.env` ni un secret, jamais hors de ces racines.
- **La politique se protège elle-même** : un diff qui la viserait est refusé.
- **Aucune application automatique** : le paquet `selfmod` n'écrit aucun fichier,
  ne lance aucun processus, n'applique aucun patch — vérifié statiquement en CI.
- **Réservé à Guillaume** : la reconnaissance de locuteur n'élève jamais ce droit ;
  la maisonnée et un invité n'y ont pas accès.
- La politique de scan de diff est une **défense en profondeur** ; le vrai garde-fou
  reste **ta relecture** : tu vois le diff entier avant toute décision.

## Sous le capot

- `core/app/selfmod/policy.py` : `evaluate(diff)` → verdict (autorisé / refusé et
  pourquoi). N'applique rien. `is_protected_path`, détection de secrets, liste
  blanche de configuration.
- `core/app/selfmod/source.py` : `SelfSource`, lecture seule de `app/` + `ui/`.
- `core/app/store/db.py` : table `suggestions` (`diff`, `status` pending/
  accepted/rejected).
- `core/app/brain/toolbox.py` : `lire_mon_code` / `proposer_evolution` /
  `lister_evolutions` (niveau *owner*). `proposer_evolution` **passe par la
  politique** avant de stocker quoi que ce soit.
- `core/app/main.py` : WebSocket `evolutions` / `evolution_get` /
  `evolution_accept` / `evolution_reject` / `evolution_delete` (cockpit).
- `core/tests/test_invariant.py` : verrous statiques (aucune application de diff,
  la politique protège tous les garde-fous).
- `ui/` : Paramètres › Évolutions (liste + revue du diff colorisé + décision).

## Limites & évolutions

- Application **manuelle** (par toi) pour l'instant — volontairement : c'est la
  garantie que rien ne s'exécute sans ton geste.
- Évolutions possibles : router une proposition acceptée vers l'atelier de dev
  isolé (pour en faire une vraie branche / PR), un historique des diffs appliqués.
  Rien de tout cela ne changerait la règle : **tu décides, tu appliques**, et les
  garde-fous de sécurité restent **hors de portée** d'une proposition.
