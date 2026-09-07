# Les pages web de Luna (Phase 5)

Luna peut **préparer des pages web simples** pour toi — un tableau de bord, une
page de suivi, une note partageable. Elle en rédige le contenu ; **c'est toi qui
relis et publies**. Rien ne va en ligne sans ton geste.

## Le principe : revue humaine avant publication

- **Luna ne fait que rédiger des brouillons** (outils `creer_page`,
  `modifier_page`, `lister_pages` — réservés au propriétaire). Elle **ne peut pas
  publier**.
- **Publier / republier / dépublier / supprimer** sont des actions du **cockpit**
  (Paramètres › Pages web), après **aperçu**. C'est le même esprit que les actions
  sensibles : la mise en ligne passe par toi, jamais par la voix.
- Éditer un brouillon **ne touche pas** la version en ligne : tu **republies**
  quand tu es prêt (l'ancienne version reste servie entre-temps).

## Où ça vit

- **Brouillon** : la copie de travail écrite par Luna. Visible seulement dans le
  cockpit (aperçu), **jamais** exposée à une URL.
- **Publiée** : accessible sur ton réseau à **`http://<sentinel>/p/<nom>`**
  (LAN uniquement, comme tout Sentinel).

## Sécurité

- **Aperçu cloisonné** : le brouillon s'affiche dans une iframe `sandbox`
  (`allow-scripts` sans `allow-same-origin`) — la page de test ne peut pas toucher
  le cockpit.
- **Page publiée verrouillée réseau** : servie avec une **CSP stricte**
  (`connect-src 'none'`). La page peut être interactive (styles/scripts en ligne)
  mais son JS **ne peut jamais contacter le réseau** — donc jamais rappeler l'API
  ou le WebSocket de Sentinel. Aucune ressource externe n'est chargée non plus.
- Les pages générées sont **autonomes** (HTML + CSS/JS en ligne). Pas de données
  live pour l'instant (une page ne peut pas appeler Nova) — volontairement, pour
  rester simple et sûr. À envisager plus tard avec des autorisations explicites.
- Petit rappel : une page publiée partage l'origine de Sentinel ; elle ne contient
  aucun secret (ceux-ci restent côté serveur), et la CSP l'empêche d'exfiltrer
  quoi que ce soit. Ta relecture reste le premier garde-fou — publie ce que tu as vu.

## Utilisation

1. Demande à Luna : « **prépare-moi une page de suivi pour mes courses de la
   semaine** », « **fais un petit tableau de bord des pièces de la maison** »… Elle
   rédige et te dit que la page attend dans **Paramètres › Pages web**.
2. Ouvre **Paramètres › Pages web** → **Aperçu** pour la voir en grand.
3. Si elle te convient → **Publier**. Le lien `/p/<nom>` devient actif.
4. Pour la retoucher : redemande à Luna de la modifier, **relis**, puis **Republie**.
   **Dépublier** la retire du réseau ; **Supprimer** l'efface définitivement.

## Sous le capot

- `core/app/store/db.py` : table `pages` (`html` = brouillon, `published_html` =
  version en ligne, `NULL` si non publiée). `slugify` fabrique un nom d'URL unique.
- `core/app/brain/toolbox.py` : `creer_page` / `modifier_page` / `lister_pages`
  (niveau *owner*) — aucun ne publie.
- `core/app/main.py` : WebSocket `pages` / `page_get` / `page_publish` /
  `page_unpublish` / `page_delete` (actions du cockpit) ; route `GET /p/<slug>`
  qui ne sert que `published_html`, avec la CSP.
- `ui/` : Paramètres › Pages web (liste + aperçu cloisonné + publier/dépublier/
  supprimer).

## Limites & évolutions

- Pages **statiques autonomes** (pas de données live) pour l'instant.
- Évolutions possibles : modèles de pages, données live via une passerelle
  contrôlée, versionnage. Rien de tout cela ne changerait la règle : **tu publies**.
