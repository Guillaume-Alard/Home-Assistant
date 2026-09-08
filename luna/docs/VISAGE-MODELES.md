# Luna — les deux modèles de visage : les récupérer, les vérifier, les déposer

**Ce document se lit avant que P6 existe.** Il te permet de vérifier, sur
Orion, avec de vraies photos de ta maison, que la reconnaissance de visage
marchera — ou qu'elle ne marchera pas — **avant que j'écrive une ligne de code**.

C'est la même logique que §11 : une phase, un livrable testable. Ici le
livrable est un chiffre, et il tient dans une commande.

---

## 1. Une correction à P6, trouvée en écrivant ceci

La décision **V7** disait : « pas d'`opencv-python` (plus de 60 Mo pour une
transformation qu'on écrit en vingt lignes) ». **J'avais tort**, et il vaut
mieux le dire ici que le découvrir en codant.

Les vingt lignes en question, c'est l'**alignement** — recentrer un visage sur
ses cinq points de repère. C'est vrai, c'est vingt lignes de `numpy`.

Ce que j'avais oublié, c'est la **décodification du détecteur**. YuNet est un
détecteur à ancres : sa sortie brute est un jeu de tenseurs par échelle, qu'il
faut décoder avec des *priors* générés à la bonne taille, puis passer dans une
suppression de non-maxima. C'est cent à deux cents lignes, très sensibles à la
version du modèle, et chacune de ces lignes est un endroit où produire des
vecteurs **plausibles mais faux** — le pire mode de défaillance possible pour
de la biométrie, parce qu'il ne se voit pas.

`opencv-python-headless` fournit `FaceDetectorYN` et `FaceRecognizerSF`, qui
font détection, alignement et vecteur en trois appels. Le provider passe de
~200 lignes fragiles à ~40 lignes ennuyeuses.

| | Écrire à la main | `opencv-python-headless` |
|---|---|---|
| Poids ajouté à l'image | 0 | ~45 Mo |
| Lignes de préparation | 150 à 200 | ~40 |
| Risque de vecteurs faux sans qu'on le voie | Réel | Nul |
| Ce que le script de ce document vérifie | Un chemin différent de la production | **Exactement le chemin de production** |

Pour une phase dont §11 dit qu'elle vaut « un troisième signal » et dont j'ai
estimé la valeur à un tapotement, **45 Mo est moins cher que 200 lignes de
vision par ordinateur écrites à la main**. Sur une machine dont §2 dit que le
facteur limitant est le CPU, pas le disque, le choix est clair.

Deux remarques pour ne pas se mentir :

- Ça met **deux moteurs d'inférence** dans l'image : `onnxruntime` pour la voix
  (P3, inchangée) et `cv2.dnn` pour le visage. Ce n'est pas élégant. C'est
  moins grave qu'un alignement subtilement faux.
- §13 écarte « YOLOv8 et dlib (impossibles sur N95 sans GPU) ». OpenCV n'est ni
  l'un ni l'autre, et YuNet a été conçu pour tourner sur CPU. §10 écarte
  l'inférence GPU : `opencv-python-headless` n'en fait aucune.

**Si tu valides P6, V7 et H83 seront corrigées en ce sens.** La décision reste
la tienne — si tu préfères zéro dépendance de plus, dis-le et j'écris les deux
cents lignes, en te prévenant que je les testerai deux fois plutôt qu'une.

---

## 2. Ce que tu vas déposer

Deux fichiers, tous les deux du dépôt **OpenCV Zoo** — un catalogue de modèles
choisis pour tourner sur CPU, versionnés et documentés au même endroit.

| | Fichier | Poids | Ce qu'il fait |
|---|---|---|---|
| Détection | `face_detection_yunet_2023mar.onnx` | ~230 Ko | Trouve les visages et leurs cinq points de repère |
| Vecteur | `face_recognition_sface_2021dec.onnx` | ~37 Mo | Transforme un visage aligné en 128 nombres |

Les prendre dans le **même dépôt** n'est pas un détail : ils sont faits pour
aller ensemble, la démo officielle les enchaîne, et les points de repère de
l'un sont exactement ce qu'attend l'alignement de l'autre.

---

## 3. Les récupérer, sur Orion

```bash
# Le piège numéro un : ce dépôt utilise git-lfs. Sans lfs, tu récupères des
# fichiers texte de 130 octets qui *ressemblent* à des modèles, et OpenCV
# t'annonce une erreur incompréhensible au chargement.
sudo apt install git-lfs && git lfs install

git clone --depth 1 https://github.com/opencv/opencv_zoo.git
cd opencv_zoo
git lfs pull --include "models/face_detection_yunet/*"
git lfs pull --include "models/face_recognition_sface/*"
```

**Vérifie tout de suite les tailles.** C'est le seul contrôle qui attrape le
piège lfs :

```bash
ls -lh models/face_detection_yunet/*.onnx models/face_recognition_sface/*.onnx
# attendu : ~230K et ~37M. Si tu vois 130 octets, git-lfs n'a pas fait son travail.
```

### Les licences, et pourquoi je te le demande

§13 du cahier des charges consacre un paragraphe entier au sujet : jarvis-OS
est en AGPL, et c'est précisément pour ça qu'aucun de ses fichiers n'a été
repris. La même rigueur vaut ici.

```bash
cat models/face_detection_yunet/LICENSE
cat models/face_recognition_sface/LICENSE
```

Chaque modèle du zoo porte **sa propre licence**. Lis-les avant de déposer quoi
que ce soit sur Nova. Un usage privé, chez toi, ne pose de problème pour aucune
licence courante — mais Luna est destinée à une publication sous l'org
Alardware, et c'est à ce moment-là que ça comptera. Autant le savoir maintenant.

---

## 4. Les photos : ce qui compte vraiment

Le test ne vaut que si les photos ressemblent à ce que Luna verra. Trois règles,
et la première est la plus importante :

1. **Prends-les avec la caméra frontale de l'iPad**, à sa place sur le mur, à
   la distance où on se tient devant. Une belle photo de téléphone donnerait un
   résultat flatteur et faux : un capteur frontal de tablette, dans un couloir,
   c'est autre chose.
2. **Dans la lumière du couloir**, telle qu'elle est. Le soir aussi, si c'est
   là que ça servira.
3. **Trois photos par personne**, légèrement différentes : de face, un peu de
   trois quarts, et une avec des lunettes ou un bonnet si ça arrive.

Nomme-les `prénom-1.jpg`, `prénom-2.jpg`, `prénom-3.jpg` — le script se sert du
préfixe pour savoir qui est qui.

> **Ce moment est aussi la réponse à Q1.** Prendre trois photos de Clara pour
> ce test, c'est déjà lui demander son accord. Si cette conversation est
> désagréable à avoir, c'est une information sur P6, et il faut l'écouter.
> Pour Liam, voir la note du §7.

---

## 5. Le script de vérification

Il fait exactement ce que fera le provider de P6 : détecter, aligner, calculer
un vecteur, comparer. Pas une approximation — le même chemin.

```bash
python3 -m venv ~/luna-visage && source ~/luna-visage/bin/activate
pip install "opencv-python-headless>=4.9" numpy
```

Enregistre ceci en `verifier-visages.py`, dans le dossier qui contient tes
photos et les deux `.onnx` :

```python
#!/usr/bin/env python3
"""Les deux modèles de visage, éprouvés sur de vraies photos de la maison.

Il répond à trois questions, dans cet ordre d'importance :
  1. Les modèles se chargent-ils, et voient-ils un visage ?
  2. Séparent-ils les gens de cette maison-ci ?
  3. Combien de temps prennent-ils ?
"""

from itertools import combinations
from pathlib import Path
from statistics import median
import sys
import time

import cv2

YUNET = "face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"


def charger():
    for fichier in (YUNET, SFACE):
        if not Path(fichier).is_file():
            sys.exit(f"Manquant : {fichier}")
    detecteur = cv2.FaceDetectorYN.create(YUNET, "", (320, 320), 0.9, 0.3, 5000)
    encodeur = cv2.FaceRecognizerSF.create(SFACE, "")
    return detecteur, encodeur


def vecteur(detecteur, encodeur, chemin):
    """Une photo → 128 nombres. `None` si aucun visage."""
    image = cv2.imread(str(chemin))
    if image is None:
        sys.exit(f"Illisible : {chemin}")
    hauteur, largeur = image.shape[:2]
    detecteur.setInputSize((largeur, hauteur))
    _, visages = detecteur.detect(image)
    if visages is None or len(visages) == 0:
        return None
    # Le plus grand : celui qui est devant l'écran, pas celui du fond.
    plus_grand = max(visages, key=lambda v: v[2] * v[3])
    return encodeur.feature(encodeur.alignCrop(image, plus_grand))


def main():
    detecteur, encodeur = charger()
    photos = sorted(
        p for p in Path().glob("*.jpg")
        if "-" in p.stem
    ) + sorted(p for p in Path().glob("*.png") if "-" in p.stem)
    if len(photos) < 4:
        sys.exit("Il faut au moins deux photos de deux personnes différentes.")

    vecteurs, durees, sans_visage = {}, [], []
    for photo in photos:
        depart = time.perf_counter()
        v = vecteur(detecteur, encodeur, photo)
        durees.append((time.perf_counter() - depart) * 1000)
        if v is None:
            sans_visage.append(photo.name)
        else:
            vecteurs[photo] = v

    print(f"\n{len(vecteurs)}/{len(photos)} photos exploitables")
    if sans_visage:
        print("  Aucun visage détecté dans :", ", ".join(sans_visage))
    print(f"Temps médian par photo : {median(durees):.0f} ms\n")

    memes, autres = [], []
    for a, b in combinations(sorted(vecteurs), 2):
        score = encodeur.match(
            vecteurs[a], vecteurs[b], cv2.FaceRecognizerSF_FR_COSINE
        )
        qui_a, qui_b = a.stem.split("-")[0], b.stem.split("-")[0]
        (memes if qui_a == qui_b else autres).append((score, a.name, b.name))

    for titre, lot in (("Même personne", memes), ("Personnes différentes", autres)):
        print(titre)
        for score, x, y in sorted(lot, reverse=True):
            print(f"  {score:5.3f}  {x} ↔ {y}")
        print()

    if not memes or not autres:
        sys.exit("Il faut au moins deux photos d'une personne ET deux personnes.")

    pire_meme, meilleur_autre = min(s for s, *_ in memes), max(s for s, *_ in autres)
    print(f"Pire ressemblance entre deux photos de la même personne : {pire_meme:.3f}")
    print(f"Meilleure ressemblance entre deux personnes différentes : {meilleur_autre:.3f}")
    ecart = pire_meme - meilleur_autre
    print(f"Écart : {ecart:+.3f}\n")

    if ecart > 0.15:
        print(f"→ Franc. Un seuil autour de {(pire_meme + meilleur_autre) / 2:.2f} sépare ta maison.")
    elif ecart > 0:
        print("→ Étroit. Ça marchera, mais Luna demandera souvent qui est là.")
    else:
        print("→ Les modèles ne séparent pas ces visages-là. Ne construis pas P6 sur ça.")


if __name__ == "__main__":
    main()
```

```bash
python3 verifier-visages.py
```

---

## 6. Lire le résultat

Ce qui compte n'est pas la ressemblance moyenne, c'est **l'écart** entre la
pire ressemblance d'une même personne et la meilleure ressemblance entre deux
personnes. C'est lui qui dit s'il existe un seuil qui marche chez toi.

| Écart | Ce que ça veut dire | Ce que j'en fais |
|---|---|---|
| **> 0,15** | Les deux nuages sont bien séparés | Je construis P6, avec un seuil au milieu de l'écart |
| **0 à 0,15** | Ça sépare, mais de justesse | Je construis, en réglant la marge de P3 plus haut — Luna demandera plus souvent, et c'est le bon réflexe |
| **≤ 0** | Une photo de toi ressemble plus à Clara qu'à une autre photo de toi | **On ne construit pas.** Refaire les photos d'abord ; si ça persiste, P6 n'est pas faisable dans ce couloir |

La documentation d'OpenCV donne 0,363 comme seuil cosinus de référence pour
SFace. Ne t'y fie pas : c'est une moyenne sur un jeu de test public. Le seuil
qui compte est celui que **tes** photos donnent, exactement comme les seuils de
P3 sont des points de départ « à régler sur de vraies voix » (H48).

### Le temps par photo

C'est l'hypothèse **H82**, et le script y répond à moitié.

- Sur Orion, attends-toi à quelques dizaines de millisecondes. C'est une
  **borne basse** : Orion n'est pas Nova.
- Le N95 sera nettement plus lent. Si Orion donne plus de 200 ms, méfie-toi.
- Le vrai chiffre viendra de Nova, quand P6 tournera : je le ferai écrire dans
  le journal de l'add-on au démarrage, comme la version de Home Assistant.

Au-delà d'une seconde sur Nova, la phase perd son seul intérêt : un tapotement
gagné contre une seconde d'attente n'est pas un gain.

---

## 7. Ce que ton mur m'apprend

Tu m'as dit : l'iPad est **fixé au mur, à l'entrée du couloir**. Trois
conséquences, dont une qui me gêne.

**La bonne.** Une tablette murale, c'est une distance et une hauteur presque
constantes. Les photos d'inscription ressembleront à ce que Luna verra tous les
jours — c'est la meilleure configuration possible pour de la reconnaissance 2D,
bien meilleure qu'un appareil qu'on tient à la main.

**Celle qui gêne : Liam.** Une tablette montée à hauteur d'adulte cadre un
enfant au sommet du crâne, ou pas du tout. Le visage risque de ne tout
simplement pas fonctionner pour lui, et il le saura. Une fonction qui reconnaît
les parents et pas l'enfant, dans un couloir, c'est un message que je préfère
que tu voies venir. **Teste-le avec ses photos** : s'il n'apparaît pas dans les
résultats du script, on a la réponse avant d'avoir construit.

**Celle qui change une ligne de conception.** Une tablette murale est presque
toujours en affichage permanent, et elle sort de veille quand quelqu'un passe.
La condition 3 de **V1** — « un geste délibéré » — devient donc plus stricte :
la caméra ne doit **jamais** s'ouvrir à la sortie de veille, ni au retour de
l'économiseur d'écran, ni quand la page se recharge. Seulement sur un contact
avec la carte ou le micro. Sans ça, un iPad mural devient une caméra qui
s'allume quand on passe — précisément ce que §10 refuse.

Et une remarque qui n'est pas technique : l'objectif d'une tablette d'entrée de
couloir voit le couloir, et ce qu'il y a au bout. Rien n'en est conservé, rien
n'en sort du réseau local, et rien n'est écrit pour un visage inconnu. Mais
« rien n'est gardé » n'est pas « personne n'est filmé », et c'est à toi de
décider si c'est acceptable chez toi.

---

## 8. Déposer sur Nova

Une fois le script satisfaisant, et seulement là :

```bash
scp face_detection_yunet_2023mar.onnx  root@nova.local:/share/luna/
scp face_recognition_sface_2021dec.onnx root@nova.local:/share/luna/
```

Puis dans les options de l'add-on, quand P6 sera construite :

```yaml
modele_visage_detection: "/share/luna/face_detection_yunet_2023mar.onnx"
modele_visage_vecteur:  "/share/luna/face_recognition_sface_2021dec.onnx"
```

`config.yaml` monte déjà `share:ro` — l'add-on lit, il n'écrit jamais là.
`luna/.gitignore` refuse déjà `*.onnx` : la règle écrite en P2 pour le modèle
de voix couvre ceux-ci sans modification, et aucun modèle ne partira dans un
commit.

Sans ces options, Luna démarre, converse, pilote la maison, écoute et surveille
exactement comme aujourd'hui. Seule la reconnaissance de visage reste éteinte,
et elle le dit — c'est le comportement de P3 pour la voix, et il a déjà servi.

---

## 9. Ce que ce document ne fait pas

- Il **n'entraîne rien**. Ces modèles sont pris tels quels ; on ne les
  spécialise pas sur ta famille, et §2 n'en demande pas tant.
- Il **ne construit pas P6**. Q1 n'est pas tranchée, et elle bloque.
- Il **ne mesure pas Nova**. Orion donne une borne basse ; le vrai chiffre
  viendra quand la phase tournera.
