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
speak: voix          # voix (défaut) | toujours | jamais
```

`speak: voix` ne lit à voix haute que ce qui a été demandé de vive voix —
personne ne veut être lu parce qu'il a tapé une question.

## Ce qu'elle fait

- **Orbe** SVG animé en CSS, cinq états. En P1, seuls `idle`, `thinking` et
  `speaking` sont atteignables ; `listening` arrive en P2, `alert` en P4.
- **Fil** de conversation, bulles horodatées, restauré après rechargement.
- **Saisie** texte et **micro en appui-pour-parler** : la carte transcrit par le
  pipeline Assist (transcription seule) puis envoie le texte comme s'il avait
  été tapé. La réponse est lue par `luna/speak`, dans la voix du pipeline.
- **Badge d'identité** : avatar, prénom, confiance (masquée tant qu'un seul
  signal existe, elle ne devient parlante qu'en P3).
- **Propositions** de niveau ≥ 3, avec leur justification et deux boutons.
- **Tiroir « Veille »**, vide jusqu'en P4, avec `Agir` / `Ignorer` /
  `Ne plus me le dire`.
- **Mode dégradé** : bandeau, saisie coupée, fil consultable, entités Home
  Assistant toujours lisibles.

## Sur iPad, deux pièges déjà traités

1. **Le déblocage audio.** iOS refuse tout `play()` qui n'a pas été précédé d'un
   `play()` déclenché par un vrai geste. Le lecteur est donc amorcé avec un
   silence de 44 octets au premier appui sur le micro. Sans ça, Luna serait
   muette — et seulement sur iPad.
2. **La fréquence d'échantillonnage.** iOS ne laisse pas choisir celle du
   `AudioContext`. Le rééchantillonnage vers 16 kHz dans l'`AudioWorklet` n'est
   pas une optimisation, c'est une obligation.

S'y ajoute l'appui maintenu, qui ouvre le menu contextuel et fait perdre le
`pointerup` — donc la fin de l'enregistrement. D'où `touch-action: none`,
`-webkit-touch-callout: none` et l'annulation de `contextmenu` sur le bouton.

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

38 tests dans un vrai Chromium, lancé avec un micro synthétique : charte
visuelle, flux de conversation, propositions, mode dégradé, fuites de
souscription, et tout le chemin vocal — capture réelle, `AudioWorklet` réel,
trames PCM réelles, préfixées de l'octet de canal binaire.

Le banc est servi en HTTP local plutôt qu'ouvert en `file://` : une origine
opaque interdit de charger un `AudioWorklet` depuis un Blob, ce que la carte
fait pour tenir la promesse « un seul fichier ». On teste donc le chemin réel.

Le `hass` est simulé — il rejoue le contrat §4, celui-là même que l'intégration
et l'add-on implémentent de l'autre côté.
