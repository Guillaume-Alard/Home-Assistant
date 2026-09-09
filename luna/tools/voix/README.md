# Corpus de voix — de la plateforme TTS au jeu de données Piper

Quand la voix source vient d'une plateforme de synthèse, on **connaît déjà le
texte**. Les deux étapes les plus ingrates de [`VOIX-CUSTOM.md`](../../docs/VOIX-CUSTOM.md)
— découper à l'oreille (§3.2), transcrire avec Whisper (§3.3) — n'ont plus lieu
d'être : on découpe aux silences et on aligne sur les lignes du bloc.

## Ce qu'il y a ici

| Fichier | Rôle |
|---|---|
| `corpus.txt` | 563 phrases en 45 blocs, ~30 500 caractères, ~31 min de parole |
| `preparer.py` | Découpe, normalise, aligne, écrit `dataset/` au format LJSpeech |

## La marche à suivre

**0. Répartir.** Demande au script comment grouper les blocs pour la limite de
ta plateforme :

```bash
./preparer.py --groupes 5000
```

Il propose les plages et les noms de fichiers correspondants. À 5 000
caractères, quarante-cinq blocs tiennent en **sept requêtes**.

**1. Synthétiser.** Un fichier par requête, déposé dans `blocs/` : `bloc-07.wav`
pour un bloc seul, `bloc-01-06.wav` pour une plage. Le script accepte les deux
et concatène les phrases dans l'ordre des blocs.

⚠️ **Ne colle jamais les lignes `# BLOC nn`.** La plateforme les lirait à voix
haute — « bloc zéro un » — ce qui ajoute un segment et décale tout l'alignement
du groupe. Seules les phrases.

⚠️ **N'utilise aucun marqueur d'émotion, de pause ou de son.** Ils changent
l'audio sans apparaître dans la transcription : le modèle apprendrait à
prononcer un texte qu'il n'a pas.

Garde **le même modèle, la même voix, la même vitesse, la même hauteur et le
même volume** d'un fichier à l'autre. Changer un curseur en cours de corpus
apprend au modèle à changer de voix en cours de phrase.

Grouper divise le nombre d'allers-retours par sept, au prix d'un risque : un
mauvais découpage invalide tout le groupe au lieu d'un seul bloc. Le script le
détecte et refuse d'écrire, mais il faut resynthétiser le groupe entier. Si tu
préfères la sécurité, garde un fichier par bloc.

**2. Vérifier avant d'écrire.**

```bash
./preparer.py --verifier
```

Chaque ligne compare le nombre de segments détectés au nombre de phrases du
bloc. Tant que ce n'est pas `ok`, ne va pas plus loin : un décalage d'une ligne
apprend au modèle à prononcer chaque phrase avec le texte de la précédente, et
ça ne se voit qu'après l'entraînement.

Trop de segments → le seuil coupe dans les phrases : `--seuil -40` ou
`--silence 0.6`.
Trop peu → deux phrases sont collées : `--seuil -30` ou `--silence 0.3`.

**3. Écrire le jeu de données.**

```bash
./preparer.py
```

Produit `dataset/wav/00001.wav …` et `dataset/metadata.csv`, en mono
22 050 Hz 16 bits — ce que veut Piper. Un seul passage de `loudnorm` par bloc,
pas par segment : normaliser chaque extrait séparément donnerait à chacun son
propre niveau, et le modèle apprendrait les sautes de volume.

**4. Écouter cinq extraits au hasard** et lire la ligne correspondante de
`metadata.csv`. C'est la seule vérification qui attrape un décalage.

Ensuite, `VOIX-CUSTOM.md` §3.6 pour l'affinage sur Orion.

## Pourquoi les nombres sont écrits en toutes lettres

La plateforme TTS et l'`espeak-ng` de Piper n'étendent pas les chiffres de la
même façon — « 22 h 30 » peut donner *vingt-deux heures trente* chez l'une et
*vingt-deux heures trente minutes* chez l'autre. Le modèle apprendrait alors à
dire un mot pour un autre.

En écrivant *vingt-deux heures trente*, les deux voient la même chose. Et comme
Piper phonémise avant que le modèle ne voie quoi que ce soit, il n'y perd
aucune capacité à lire des chiffres plus tard.

## Rallonger le corpus

Trente et une minutes passent le minimum de `VOIX-CUSTOM.md` sans atteindre
l'heure confortable. Pour rallonger : ajouter des blocs à la suite, numérotés
dans la continuité, en gardant le même esprit — des phrases courtes **et**
longues, des questions, des heures, des nombres, des noms propres, et de la
prose ordinaire qui n'a rien à voir avec la maison. Un corpus qui ne parle que
de lumières produit un modèle qui ne sait dire que ça.
