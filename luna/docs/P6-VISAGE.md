# Luna — P6 : le visage, troisième signal. Hypothèses et contrats

**Statut : en attente de validation.** Aucun code n'est écrit tant que ce
document n'est pas tranché (§11 du cahier des charges).

Sortie testable attendue (§11) : **un troisième signal d'identité.**

---

## 0. Ce que le cahier des charges a déjà tranché

Contrairement aux phases précédentes, P6 arrive avec presque toutes ses
contraintes écrites. Il vaut mieux les relire avant de décider quoi que ce soit,
parce qu'elles éliminent d'avance la plupart des chemins qu'on aurait pu
prendre.

| Où | Ce que ça dit | Ce que ça élimine |
|---|---|---|
| §10 | « Reconnaissance faciale en flux vidéo continu (impossible sur N95) — **photo à la demande uniquement** » | Toute analyse de flux. Il n'y aura pas de caméra qui regarde |
| §6 | « **Aucune frame caméra ne transite par le relais Nabu Casa.** » | L'accès distant, sans discussion possible |
| §6 | « une photo trompe une caméra sans capteur de profondeur. **Aucune action du niveau « critique » ne doit être déverrouillée par la biométrie** » | Le visage comme clé. Il ne déverrouille rien, jamais |
| §9.4 | « Aucune donnée biométrique persistée côté navigateur » | Un cache de visages dans l'iPad |
| §10, §13 | « Inférence GPU en production » ; « vision YOLOv8 et dlib (impossibles sur N95 sans GPU) » | PyTorch, YOLO, dlib, `face_recognition` |
| §2 | « Orion ne sert qu'à *fabriquer* des artefacts (modèles de voix, **futurs modèles de visage**) déposés sur Nova » | Un modèle téléchargé à l'exécution |
| §4 | `getUserMedia` (micro **et caméra**) est refusé hors contexte sécurisé | Tout, tant que **P0 n'est pas fait** |

Une contradiction mineure, pour mémoire : le tableau de §6 range la
reconnaissance faciale en **P5**, la feuille de route de §11 en **P6**. C'est
§11 qui gouverne — P5 a été la gardienne, comme prévu.

---

## 1. Ce que P6 vaut vraiment, avant de la construire

Je dois commencer par là, parce que c'est la phase où je suis le moins sûr que
le jeu en vaille la chandelle, et tu dois pouvoir en décider les yeux ouverts.

**Rappel de ce que P3 a établi.** Le signal d'identité le plus fort n'est pas
biométrique : c'est l'utilisateur Home Assistant authentifié. Sur ton téléphone,
sur celui de Clara, Luna sait déjà à qui elle parle — depuis P1. La voix ne sert
que là où ce signal est muet : **l'iPad partagé du couloir**, et rien d'autre.

Le visage sert donc au même endroit, et exactement dans trois cas :

| Cas | Ce que le visage apporte |
|---|---|
| Quelqu'un s'approche de l'iPad et touche l'écran | Luna sait qui c'est **avant qu'un mot soit prononcé**. Aujourd'hui elle attend une phrase, ou un tapotement sur « qui parle ? » |
| La voix hésite entre deux personnes | Un départage, au lieu d'une question. La fusion de P3 demande déjà « c'est toi ou Clara ? » quand la marge est trop mince |
| Quelqu'un ne peut pas parler | Liam qui a la bouche pleine, quelqu'un au téléphone |

**Et voilà le coût honnête : P6 fait gagner un tapotement.** Le panneau « qui
parle ? » existe depuis P3, il marche, il fait une question et une réponse.
Le visage le remplace par un regard.

À mettre en face :

- Elle est **bloquée sur P0** comme la voix. Sans HTTPS local, `getUserMedia`
  refuse la caméra exactement comme il refuse le micro. Ça fait deux
  fonctionnalités qui attendent le même week-end de DuckDNS.
- Elle met une **caméra dans la boucle**, dans un couloir, là où passent des
  gens qui n'ont rien demandé.
- Elle demande un modèle de plus sur Nova.

**Ma recommandation : oui, mais en dernier, et sans se raconter d'histoires.**
C'est la phase au moins bon rapport effort/valeur des six, et la seule dont on
peut se passer sans que rien ne manque. Elle est écrite dans §11, donc je la
construis si tu le dis — mais si tu préfères mettre ce temps dans P0 et laisser
tourner P4 et P5 quelques semaines sur de vraies données, c'est ce que je ferais.

La bonne nouvelle, si tu dis oui : **ce sera la plus petite phase du projet.**
Voir V4.

---

# Partie A — Huit décisions

Les lettres A à E sont prises par les phases précédentes, et F est déjà le
préfixe des fonctions de §5. Donc **V**, comme visage.

## V1. Une photo, à un instant précis, sur un geste délibéré

§10 impose « photo à la demande uniquement ». Reste à définir *quelle* demande,
et c'est là que se joue la différence entre un assistant et une caméra de
surveillance.

La caméra ne s'allume **que si les cinq conditions sont réunies** :

| # | Condition | Pourquoi |
|---|---|---|
| 1 | L'appareil est déclaré **partagé** dans les options | Sur un téléphone, la session Home Assistant sait déjà. Une caméra n'y apporterait rien du tout |
| 2 | L'identité de l'appareil est **inconnue ou périmée** | Si Luna sait déjà qui est là (P3, `expires_at`), elle n'a rien à regarder |
| 3 | Un **geste délibéré** vient d'avoir lieu : toucher la carte, presser le micro | Jamais au chargement de la page, jamais sur une minuterie, jamais « quand quelqu'un approche » |
| 4 | On est sur le **réseau local** | §6, et la barrière de P3 est déjà écrite |
| 5 | Le contexte est **sécurisé** (HTTPS) | `getUserMedia` n'a pas d'autre option |

Une seule image, en 480 × 480 environ, prise puis relâchée. Le navigateur
allume son propre témoin de caméra, et la carte en affiche un aussi : personne
ne doit se demander si l'objectif est actif.

## V2. La frame ne traverse jamais le relais Nabu Casa — et rien ne reste

§6 le dit mot pour mot, et P3 a déjà construit la barrière : l'intégration
refuse la biométrie **avant** de relayer quoi que ce soit, et un test vérifie
qu'aucun octet n'atteint le faux relais quand `est_local()` est faux. P6 réutilise
cette fonction, avec le test jumeau sur les images.

Le trajet complet d'une frame, et il est court :

```
 ①  iPad : une image, en mémoire du navigateur
 ②  → intégration, par la WebSocket locale de Home Assistant
 ③  L'intégration vérifie « local ? ». Si non : refus, rien n'est relayé
 ④  → add-on, par le relais interne
 ⑤  add-on : détection du visage, alignement, vecteur
 ⑥  L'image est relâchée. Seul le vecteur survit
```

Ce qui est **écrit sur le disque** : un vecteur de quelques centaines de
flottants, et le nom du modèle qui l'a produit. Jamais l'image. C'est H50 de
P3, appliqué à une modalité où ça compte encore plus.

Ce qui n'est **jamais fait**, à l'étape ⑤ : envoyer l'image à Claude. L'API sait
lire des images ; c'est précisément pour ça qu'il faut l'écrire. Le contrat du
cerveau ne comporte aucune méthode qui accepte des octets, et un test statique
le vérifie — comme P5 vérifie qu'aucun fichier ne nomme une commande d'écriture.

## V3. Le visage ne déverrouille rien. Il ne fait que personnaliser.

§6 : « La reconnaissance vocale et faciale 2D sert à la **personnalisation**,
pas à la sécurité. » §9.4 : « Pas de biométrie pour la sécurité. »

C'est déjà la décision C7 de P3, déjà construite, déjà testée : valider une
proposition demande la **session Home Assistant**, jamais une ressemblance.
P6 n'ajoute strictement rien sur ce chemin, et le test de P3 le prouve encore
après.

Concrètement : un visage reconnu change **quoi** Luna répond et **comment**
elle le dit. Il ne change jamais **ce qu'elle a le droit de faire**. Une photo
de Guillaume brandie devant l'iPad obtient la météo personnalisée de Guillaume.
Elle n'obtient rien d'autre, et surtout pas de valider quoi que ce soit.

## V4. Ce sera la plus petite phase du projet, parce que P3 a été bien découpée

Presque tout existe. Le tableau est plus parlant qu'un paragraphe :

| Ce qu'il faut | Ce qui existe déjà | Ce qu'il reste |
|---|---|---|
| Un provider de biométrie remplaçable | `EmpreinteProvider` (P3) : `disponible`, `nom`, `encoder(octets) → vecteur` | La **même forme**, avec des pixels au lieu de PCM |
| Une table d'empreintes | `voice_prints` : vecteur, dimension, **modèle**, source, date | `face_prints`, colonne pour colonne |
| Une fusion multi-signaux | `kernel/identity.py` : présence × voix, seuils, marge, journal | Un facteur de plus (V5) |
| Un parcours d'inscription | `identity/enroll/{start,sample,finish}` (P3) | Le même, avec des consignes au lieu de phrases |
| Un oubli | `identity/forget` (P3) | Rien — il efface déjà par profil |
| Une barrière réseau | `est_local()` + refus avant relais (P3) | Rien |
| Une commande | `luna/identity/face` répond `501` **depuis P1** | Elle cesse de répondre 501 |
| Un panneau dans la carte | Le tiroir « Qui parle » (P3) | Une ligne de plus par profil |

Ce qui est réellement neuf : un provider ONNX, une table, un facteur dans une
formule, et la capture d'une image dans la carte. C'est le dividende d'avoir
écrit P3 en pensant qu'un troisième signal viendrait.

## V5. La fusion à trois signaux, et le désaccord qui se règle tout seul

P3 calcule :

```
score(p) = voix(p) × (1 − 0,5 + 0,5 × présence(p))
```

La présence **pondère** sans jamais opposer son veto — c'était la correction
importante de P3 : un téléphone oublié dans la voiture ne doit pas rendre son
propriétaire méconnaissable.

Le visage n'est pas un contexte, c'est une **confirmation**, au même titre que
la voix (§6 les range ensemble). Les deux se combinent donc entre elles, avant
d'être pondérées par la présence :

```
bio(p)   = 1 − (1 − voix(p)) × (1 − visage(p))
score(p) = bio(p) × (1 − 0,5 + 0,5 × présence(p))
```

Trois propriétés, et la troisième est la raison de cette forme :

1. **Un seul signal disponible → le comportement de P3, à l'identique.** Avec
   `visage = 0`, `bio(p) = voix(p)`. La voix seule continue de marcher
   exactement comme avant, et les tests de P3 restent valides tels quels.
2. **Les deux d'accord → moins de questions.** Voix 0,70 et visage 0,80 sur
   Guillaume donnent 0,94. Deux confirmations faibles font une certitude
   raisonnable, ce qui est le propre d'une fusion.
3. **Les deux en désaccord → Luna demande, sans qu'on ait rien codé pour ça.**
   Si la voix dit Guillaume 0,70 et le visage dit Clara 0,80, les deux profils
   montent, la marge tombe à 0,10 — sous le seuil de 0,15 de P3 — et la règle
   existante déclenche la question « c'est toi ou Clara ? ».

Le troisième point mérite d'être souligné : le désaccord ne demande **aucun cas
particulier**. La règle de marge de P3 le traite déjà, parce qu'elle était
écrite pour ça.

## V6. Le consentement : l'inscription se choisit, la capture non

C'est la vraie différence avec P3, et il ne faut pas la minimiser.

Une empreinte vocale se capture quand on **parle délibérément** à Luna. Un
visage se capture dès qu'un objectif est ouvert — et l'objectif verra des gens
qui n'ont rien demandé : un invité, un livreur, un enfant, quelqu'un qui passe
derrière.

Ce qui est délibéré, et le reste :

- **L'inscription** est un geste explicite, par personne, dans le tiroir de la
  carte : on s'assied, on touche, trois images, c'est fini. Personne n'est
  inscrit sans l'avoir fait.
- **L'effacement** existe déjà (`identity/forget`) et efface tout d'un profil.

Ce qui protège les autres :

- La frame vit **en mémoire vive**, le temps de calculer un vecteur, puis elle
  est relâchée. Rien n'est écrit.
- **Un visage non reconnu ne laisse aucune trace.** Pas de galerie
  d'« inconnus », pas de compteur, pas de « quelqu'un est passé à 14 h ». C'est
  l'extension la plus tentante de toutes les fonctions de reconnaissance
  faciale, et la plus nocive : elle est refusée explicitement (partie D).
- Le journal d'identité de P3 enregistre des **décisions et des scores**, pas
  des images ni des visages inconnus.

Reste une question qui n'est pas technique et que je ne peux pas trancher :
est-ce que tout le monde chez toi est d'accord ? Voir Q1.

## V7. Deux modèles ONNX, pas d'OpenCV, pas de GPU

§13 écarte YOLOv8 et dlib comme « impossibles sur N95 sans GPU ». Ce qui est
écarté, c'est cette famille-là — pas la vision, puisque §11 planifie P6. Ce qui
reste tient sur un CPU :

| Étape | Modèle | Poids attendu |
|---|---|---|
| Détection du visage + 5 points de repère | **YuNet** (ONNX) | ~0,3 Mo |
| Alignement sur les 5 points | Une transformation de similitude, en `numpy` | 0 |
| Vecteur du visage | **MobileFaceNet** ou **SFace** (ONNX) | 4 à 40 Mo |

`onnxruntime` et `numpy` sont **déjà des dépendances** depuis P3. Rien à
ajouter. En particulier **pas `opencv-python`** (plus de 60 Mo pour une
transformation qu'on écrit en vingt lignes) — c'est le même raisonnement que
P3, où PyTorch a été écarté au profit d'onnxruntime.

Deux réserves honnêtes :

- **Ces chiffres sont attendus, pas mesurés.** Je n'ai pas fait tourner ces
  modèles sur un N95. C'est l'hypothèse H82, à vérifier sur Nova comme la
  latence de transcription de P2.
- **Le modèle est remplaçable par construction**, comme celui de la voix : son
  nom voyage avec chaque empreinte, et en changer invalide les anciennes au
  lieu de produire des ressemblances silencieusement fausses.

Et comme en P3 : **sans modèle, Luna démarre quand même.** La reconnaissance de
visage s'éteint, elle le dit dans son journal et dans le tiroir, et tout le
reste fonctionne.

## V8. Le modèle se fabrique sur Orion, il se dépose sur Nova

§2 : « Orion ne sert qu'à *fabriquer* des artefacts (modèles de voix, futurs
modèles de visage) qui sont ensuite déposés sur Nova. » Et §9.5 : « Aucune
fonction de production ne dépend de Nebula ou d'Orion. »

Donc : les deux `.onnx` sont récupérés ou convertis sur Orion, déposés dans
`/share`, et déclarés par une option. `luna/.gitignore` refuse déjà `*.onnx` —
la règle écrite en P2 pour le modèle de voix couvre celui-ci sans modification.

Nova n'entraîne rien, ne télécharge rien à l'exécution, et fonctionne si Orion
est éteint.

---

# Partie B — Hypothèses

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H77 | La caméra ne s'ouvre que sur un appareil déclaré `partage`, identité inconnue ou périmée, après un geste délibéré, en local, en HTTPS. Les cinq conditions, jamais quatre. | C'est la différence entre un assistant et une caméra de surveillance. |
| H78 | Une seule image par tentative, en ~480 px, relâchée après le calcul du vecteur. Jamais écrite. | §10 impose la photo à la demande ; le reste est de la discipline mémoire. |
| H79 | Aucune image ne quitte le réseau local (§6), et **aucune n'atteint jamais l'API Claude**. Le contrat du cerveau n'accepte pas d'octets, et un test statique le vérifie. | C'est le refus central de la phase, sur le modèle de celui de P5. |
| H80 | Un visage non reconnu ne laisse **aucune trace** : ni fichier, ni ligne, ni compteur. | Une galerie d'inconnus est l'extension la plus tentante et la plus nocive. |
| H81 | Les empreintes de visage vivent dans `face_prints`, colonne pour colonne comme `voice_prints`, avec le nom du modèle. Schéma **v5**, par simple ajout. | Changer de modèle doit invalider les anciennes, pas produire des ressemblances fausses. |
| H82 | YuNet (~0,3 Mo) et MobileFaceNet/SFace tiennent sur le CPU du N95, sous la seconde pour une image. **Attendu, pas mesuré.** | À vérifier sur Nova. Si c'est faux, la capture devient trop lente pour être agréable, et la phase perd son seul intérêt — le gain d'un tapotement. |
| H83 | `onnxruntime` et `numpy` suffisent. Pas d'`opencv-python`, pas de PyTorch, pas de dlib. | Soixante mégaoctets pour une transformation de vingt lignes, sur une machine dont §2 dit que le CPU est le facteur limitant. |
| H84 | La fusion devient `bio = 1 − (1−voix)(1−visage)`, puis la pondération de présence de P3, inchangée. | Avec `visage = 0` on retrouve exactement P3 : les tests existants restent valides tels quels. |
| H85 | Un désaccord voix/visage fait tomber la marge sous 0,15 et déclenche la question de P3. Aucun cas particulier n'est écrit pour ça. | Si c'est faux, Luna trancherait au hasard entre deux personnes — le pire comportement possible. |
| H86 | L'inscription réutilise `identity/enroll/{start,sample,finish}` avec une modalité, et rend des **consignes** au lieu de phrases. Trois images. | Un second parcours parallèle doublerait la surface pour rien. |
| H87 | Sans modèle déposé, la reconnaissance de visage s'éteint proprement et le dit. Luna démarre, converse et pilote la maison. | C'est le comportement de P3 pour la voix, et il a déjà servi. |
| H88 | La carte affiche un témoin visible pendant que l'objectif est ouvert, en plus de celui du navigateur. | Personne ne doit avoir à se demander si la caméra est active. |

---

# Partie C — Contrats

## C.1 Une commande qui cesse de mentir

Documentée depuis P1, en `501` depuis P1 :

```jsonc
{ "type": "luna/identity/face", "image": "<JPEG en base64>", "device": "d_ab12cd34" }
→ { "profile": "guillaume", "confidence": 0.86, "margin": 0.34, "asked": false }
```

Réponse identique, au champ près, à celle de `luna/identity/voice` — c'est la
même décision, prise avec un signal de plus.

Les refus possibles, tous déjà nommés dans la taxonomie de §8 :

| Code | Quand |
|---|---|
| `remote_biometrics` | Accès distant. Refusé **par l'intégration**, avant tout relais (§6) |
| `model_unavailable` | Aucun modèle déposé sur Nova |
| `no_face` | Aucun visage détecté dans l'image. *Nouveau code* |
| `not_enrolled` | Personne n'a inscrit de visage |

## C.2 L'inscription, avec une modalité

```jsonc
{ "type": "luna/identity/enroll/start", "profile": "clara", "modalite": "visage" }
→ { "session": "e_01J…", "consignes": [
      "Regarde l'écran, bien en face.",
      "Tourne un peu la tête vers la gauche.",
      "Et vers la droite." ] }

{ "type": "luna/identity/enroll/sample", "session": "e_01J…", "index": 0,
  "image": "<JPEG en base64>" }
→ { "accepted": true, "quality": "ok", "remaining": 2 }
```

`modalite` est optionnel et vaut `voix` par défaut : le contrat de P3 continue
de fonctionner sans modification. `phrases` est rendu pour la voix,
`consignes` pour le visage.

`quality` peut valoir `no_face`, `trop_sombre`, `trop_loin`, `ok` — un
échantillon refusé dit **pourquoi**, comme en P3 où un échantillon vocal trop
court le disait.

## C.3 Le rapport d'identité s'étoffe

`luna/identity` rend déjà l'état de l'appareil. Deux champs s'ajoutent, sur le
modèle exact de ceux de la voix :

```jsonc
{
  "profile": { "id": "guillaume", "display_name": "Guillaume",
               "confidence": 0.86,
               "signals": { "presence": 0.9, "voice": 0.70, "face": 0.80 } },
  "enrolled": ["guillaume", "clara"],
  "voice_needed": true,  "voice_available": true,
  "face_needed": true,   "face_available": true,
  "enrolled_face": ["guillaume"]
}
```

`face_available: false` veut dire « aucun modèle déposé », et la carte le dit
au lieu d'afficher un bouton mort (§8).

## C.4 Schéma SQLite ajouté (v5)

```sql
-- Colonne pour colonne comme `voice_prints`. Le vecteur, jamais l'image.
CREATE TABLE face_prints (
    id         TEXT PRIMARY KEY,
    profile    TEXT NOT NULL,
    vector     BLOB NOT NULL,
    dim        INTEGER NOT NULL,
    model      TEXT NOT NULL,
    source     TEXT NOT NULL,   -- enrolment | confirmed
    created_at TEXT NOT NULL
);
CREATE INDEX idx_faces_profile ON face_prints(profile, model);
```

`identity_log` ne change pas : il enregistre déjà des décisions, des scores et
des signaux. Un signal de plus y entre sans modifier une colonne.

## C.5 Nouvelles pièces, par couche

| Couche | Fichier | Rôle |
|---|---|---|
| L0 | `kernel/identity.py` | Le facteur `visage` dans la fusion (V5). Quelques lignes |
| L0 | `kernel/errors.py` | `AucunVisage` |
| L1 | `providers/faceprint.py` | Détection, alignement, vecteur. Même forme que `voiceprint.py` |
| L1 | `providers/store.py` | `face_prints` (schéma v5) |
| L2 | `engine/identity.py` | Le troisième signal dans la décision, et l'inscription par modalité |
| L3 | `interfaces/relay.py` | `identity_face` cesse de répondre 501 |
| intégration | `websocket.py` | `luna/identity/face` relaie, derrière la même barrière locale que la voix |
| carte | `luna-card.js` | La capture d'une image, le témoin, l'inscription |

Rien dans `kernel/autonomy.py`, rien dans `kernel/permissions.py`, rien dans
`engine/arbiter.py` — et cette fois c'est structurel, pas une intention : un
visage ne donne aucun droit (V3).

---

# Partie D — Ce que P6 ne fait pas

Aussi important que le reste (§1, §10). La reconnaissance faciale est la
fonction qui attire le plus d'extensions « évidentes » ; en voici la liste, et
elles sont toutes refusées.

- ❌ **Aucun flux vidéo**, jamais. §10 l'écrit. Une photo, sur un geste.
- ❌ **Aucune image écrite sur le disque**, ni en base, ni en cache, ni dans un
  journal de débogage.
- ❌ **Aucune image vers l'API Claude.** Le contrat du cerveau n'accepte pas
  d'octets, et un test statique le vérifie.
- ❌ **Aucune image hors du réseau local** (§6), refusée par l'intégration avant
  tout relais.
- ❌ **Aucune trace d'un visage non reconnu.** Pas de galerie d'inconnus, pas de
  « quelqu'un est passé à 14 h », pas de compteur.
- ❌ **Aucune caméra de Home Assistant.** Luna ne lit aucune entité `camera`,
  ne prend aucun instantané d'une caméra fixe. Ce serait de la surveillance de
  pièce, et §5 ne le demande pas.
- ❌ **Aucune inférence au-delà de l'identité.** Ni âge, ni humeur, ni port du
  masque, ni « nombre de personnes dans la pièce ». C'est ce que proposent tous
  les SDK de vision, et rien de tout ça n'est dans §5.
- ❌ **Aucun droit accordé par le visage** (§6, §9.4, et déjà C7 de P3).
- ❌ **Aucune détection de vivacité**, et donc aucune prétention à la sécurité.
  §6 dit qu'une photo trompe une caméra 2D ; on ne fait pas semblant du
  contraire.
- ❌ **Aucune inscription implicite.** On ne devient pas connu de Luna en
  passant devant l'iPad.
- ❌ **Aucun visage d'invité.** Un invité est `guest`, et le reste.
- ❌ **Aucune inférence GPU**, aucune dépendance à Orion en production.

---

# Partie E — Recette de P6

Sortie testable de §11 : « un troisième signal d'identité ».

| # | Vérification | Automatisable |
|---|---|---|
| 1 | Une image d'un visage inscrit → le bon profil, avec sa confiance | ✅ |
| 2 | Une image sans visage → `no_face`, avec un message, jamais un silence | ✅ |
| 3 | En accès distant → refus `remote_biometrics`, et **aucun octet n'atteint le relais** | ✅ le test jumeau de celui de P3 |
| 4 | Sans modèle déposé → la reconnaissance s'éteint, Luna démarre et le dit | ✅ |
| 5 | Voix et visage d'accord → confiance supérieure à chacun des deux seul | ✅ |
| 6 | Voix et visage en **désaccord** → la marge tombe, Luna demande | ✅ |
| 7 | Visage seul absent (`visage = 0`) → le score de P3, au chiffre près | ✅ |
| 8 | Aucune image en base après une reconnaissance, réussie ou ratée | ✅ |
| 9 | Un visage inconnu ne laisse **aucune ligne** nulle part | ✅ |
| 10 | Aucun fichier du paquet ne passe d'octets au cerveau | ✅ test statique |
| 11 | `identity/forget` efface aussi les empreintes de visage | ✅ |
| 12 | Changer de modèle invalide les anciennes empreintes au lieu de les comparer | ✅ |
| 13 | La carte n'ouvre pas la caméra sur un appareil non partagé | ✅ Playwright, vrai Chromium |
| 14 | La carte n'ouvre pas la caméra quand l'identité est déjà connue | ✅ |
| 15 | Un témoin visible pendant que l'objectif est ouvert | ✅ |
| 16 | Luna reconnaît Guillaume avant qu'il ait parlé, sur l'iPad du couloir | ⏳ **sur Nova**, c'est la sortie de §11 |
| 17 | Une image traitée en moins d'une seconde sur le N95 (H82) | ⏳ **sur Nova** |
| 18 | `ruff`, `lint-imports`, `pytest` verts | ✅ |

Les points 16 et 17 demandent la vraie machine et le vrai couloir. Le 17 est
celui qui peut faire échouer la phase : si une image prend quatre secondes, le
tapotement gagné est un tapotement perdu.

---

# Partie F — Ce dont j'ai besoin de toi

> **Q1 — Est-ce que tout le monde est d'accord ?** C'est la seule question de ce
> document que je ne peux pas trancher, et elle est bloquante. Une empreinte
> vocale se donne en parlant ; un visage se capture dès qu'un objectif s'ouvre,
> et celui de l'iPad du couloir verra Clara, Liam, et les gens qui passent.
> Rien n'est gardé d'un visage non reconnu — mais « rien n'est gardé » n'est pas
> la même chose que « personne n'est filmé ». Il faut que Clara le sache et le
> veuille, et pour Liam il faut que ce soit ta décision explicite.

> **Q2 — Est-ce que ça vaut le coup ?** Relis la partie 1. P6 fait gagner un
> tapotement, elle est bloquée sur P0 comme la voix, et c'est la seule phase
> dont l'absence ne manquerait à personne. Mon avis : je la construirais en
> dernier, après avoir laissé P4 et P5 tourner quelques semaines sur de vraies
> données. Mais elle est dans §11, et si tu la veux je la fais.

> **Q3 — Où est l'iPad, et que voit sa caméra ?** « Le couloir » suffit pour la
> conception, mais pas pour décider si c'est raisonnable. Une caméra qui donne
> sur une porte de chambre n'est pas une caméra qui donne sur un mur.

> **Q4 — Veux-tu que je te donne les deux modèles à récupérer sur Orion ?** Je
> peux te livrer la procédure exacte — d'où les prendre, comment vérifier qu'ils
> chargent, où les déposer — comme pour la voix. Ça ne coûte rien de l'écrire
> maintenant, même si tu décides de ne pas construire P6 tout de suite.

**Q1 bloque**, les autres non.
