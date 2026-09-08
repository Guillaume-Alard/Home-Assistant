# `luna-card.js` — le visage

Un seul fichier, JavaScript natif, aucun build, aucun npm, aucun CDN, aucune
dépendance à l'exécution (§8 du cahier des charges).

## Installer

Copier `luna-card.js` dans `/config/www/` sur Nova, puis déclarer la ressource :

**Paramètres → Tableaux de bord → ⋮ → Ressources → Ajouter**
`/local/luna-card.js`, type **Module JavaScript**.

Puis, dans Loggia, ajouter une carte :

```yaml
type: custom:luna-card
height: 620          # px, minimum 400
drawers:
  veille: true       # le tiroir existe dès P1, vide jusqu'en P4
greeting: true       # message d'accueil si le fil est vide
```

## Ce qu'elle fait

- **Orbe** SVG animé en CSS, cinq états. En P1, seuls `idle`, `thinking` et
  `speaking` sont atteignables ; `listening` arrive en P2, `alert` en P4.
- **Fil** de conversation, bulles horodatées, restauré après rechargement.
- **Saisie** texte ; le bouton micro existe mais est grisé — la voix est en P2,
  et il le dit plutôt que de ne rien faire.
- **Badge d'identité** : avatar, prénom, confiance (masquée tant qu'un seul
  signal existe, elle ne devient parlante qu'en P3).
- **Propositions** de niveau ≥ 3, avec leur justification et deux boutons.
- **Tiroir « Veille »**, vide jusqu'en P4, avec `Agir` / `Ignorer` /
  `Ne plus me le dire`.
- **Mode dégradé** : bandeau, saisie coupée, fil consultable, entités Home
  Assistant toujours lisibles.

## Trois choses à ne pas casser

1. **Le désabonnement.** Lovelace détruit et recrée les cartes à chaque
   changement de vue. Une souscription oubliée dans `disconnectedCallback` fuit
   à chaque aller-retour entre onglets, et l'add-on garde un flux ouvert.
   `TestFuites` le vérifie.
2. **Le rendu incrémental.** `set hass` est appelé plusieurs fois par seconde.
   Re-rendre le fil à chaque appel mettrait le N95 à genoux et casserait le
   défilement.
3. **`textContent`, jamais `innerHTML`.** Tout le contenu vient de l'extérieur.
   `test_texte_non_interprete` le vérifie.

## Tester

```bash
pip install -r requirements-dev.txt
playwright install chromium     # ou : export LUNA_CHROMIUM=/chemin/vers/chrome
pytest -q
```

23 tests dans un vrai Chromium : charte visuelle, flux de conversation,
propositions, mode dégradé, fuites de souscription, messages du micro. Le
`hass` est simulé — il rejoue le contrat §4, celui-là même que l'intégration
et l'add-on implémentent de l'autre côté.
