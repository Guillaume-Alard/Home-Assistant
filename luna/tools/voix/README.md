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

**1. Synthétiser.** Un bloc = une requête = **un fichier**, déposé dans
`blocs/` sous le nom `bloc-01.wav`, `bloc-02.wav`, … Le numéro doit
correspondre au `# BLOC nn` du corpus. Si ta plateforme accepte plus de mille
caractères par requête, colle plusieurs blocs à la suite — mais alors un seul
fichier pour ces blocs ne marchera pas : garde un fichier par bloc.

Garde **la même voix et les mêmes réglages** d'un bloc à l'autre. Changer de
vitesse ou d'intonation en cours de corpus apprend au modèle à changer de voix
en cours de phrase.

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
