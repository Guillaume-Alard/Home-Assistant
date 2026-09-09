# Luna — voix custom (side-quest §7)

**Ne bloque rien.** P2 tourne avec une voix Piper standard ; changer de voix plus
tard, c'est une ligne d'option dans l'add-on Piper et une dans le pipeline
Assist. Cette page est là pour quand tu auras une soirée à y passer, pas avant.

---

## 1. La mauvaise nouvelle d'abord

**Un fichier audio n'est pas un jeu de données.** Piper ne s'entraîne pas sur
« un enregistrement » : il lui faut un corpus au format LJSpeech, c'est-à-dire
des **centaines de courts extraits** de 2 à 10 secondes, chacun accompagné de sa
transcription exacte.

| Ce qu'il faut | Pourquoi |
|---|---|
| **30 min minimum**, 1 h confortable (§7 dit « ~1 h ») | En dessous de 10 minutes, le modèle apprend le timbre mais pas la prosodie : ça sonne robotique et haché |
| **Un seul locuteur** | Deux voix dans le corpus et le modèle fait la moyenne des deux |
| **Aucune musique, aucun bruit de fond** | Piper apprend tout ce qu'il entend, y compris le souffle et la réverbération de la pièce |
| **Un enregistrement homogène** | Micro, distance et pièce constants ; mélanger deux sources donne un modèle qui change de voix en cours de phrase |
| **Pas de saturation** | Un signal écrêté s'apprend comme tel |

Ton fichier est donc une **matière première**, pas un corpus. Le travail
préparatoire — découper, transcrire, normaliser — représente l'essentiel de
l'effort ; l'entraînement lui-même est une nuit de calcul pendant laquelle tu
dors.

> **Colle-moi les sorties de §3.1 avant de commencer.** Durée, canaux, débit,
> écrêtage : ces quatre chiffres disent en cinq minutes si le fichier tient la
> route, et évitent d'y passer une soirée pour rien.

---

## 2. Où ça tourne

Sur **Orion**, la RTX 2070. C'est exactement le rôle que §2 lui donne : « Orion
ne sert qu'à *fabriquer* des artefacts qui sont ensuite déposés sur Nova. »

Le modèle produit est un fichier `.onnx` qui tourne ensuite **sur CPU**, sur
Nova, gratuitement et indéfiniment. Une fois déposé, Orion peut rester éteint —
la règle d'indépendance de §2 est respectée.

---

## 3. La chaîne, étape par étape

> Les commandes ci-dessous donnent la **forme**. L'outillage d'entraînement de
> Piper a changé de dépôt et de nom de module au fil des versions : vérifie le
> README du dépôt que tu installes et ajuste les noms d'options. Ce qui ne
> change pas, c'est la structure du jeu de données et l'ordre des étapes.

### 3.1 — Jauger le fichier

Il faut `ffmpeg` — et il sert ensuite à tout le reste de la chaîne, donc
autant l'installer maintenant. Sous Windows :

```powershell
winget install --id Gyan.FFmpeg -e
```

**Fermer et rouvrir le terminal** après l'installation : le `PATH` n'est relu
qu'au démarrage du shell, et sans ça `ffprobe` reste introuvable alors qu'il est
bien là.

```bash
# Durée, débit, canaux
ffprobe -hide_banner -i voix.wav

# Niveau sonore et écrêtage
ffmpeg -i voix.wav -af "volumedetect" -f null - 2>&1 | grep -E "max_volume|mean_volume"
```

Sous PowerShell, la seconde ligne s'écrit autrement — `grep` n'existe pas, et le
périphérique nul s'appelle `NUL`. Le plus sûr est de déléguer à `cmd`, qui gère
la redirection de la sortie d'erreur comme du texte :

```powershell
cmd /c "ffmpeg -hide_banner -i voix.wav -af volumedetect -f null NUL 2>&1" | findstr volume
```

Si `max_volume` vaut 0.0 dB, le signal est probablement écrêté : mauvais départ.
Si la durée totale est sous dix minutes, l'affinage Piper est hors de portée :
va directement au **§7**, qui dit quoi faire à la place.

### 3.2 — Découper en phrases

```bash
# Repérer les silences pour couper aux bons endroits
ffmpeg -i voix.wav -af "silencedetect=noise=-35dB:d=0.4" -f null - 2>&1 \
  | grep silence_
```

Plus commode qu'un découpage à la main : `auditok` ou le découpage par segments
de `faster-whisper`, qui fait la coupe **et** la transcription en une passe.

### 3.3 — Transcrire

```bash
pip install faster-whisper
```

```python
from faster_whisper import WhisperModel

# On est hors production : autant prendre le gros modèle, c'est Orion qui paye.
modele = WhisperModel("large-v3", device="cuda", compute_type="float16")
segments, _ = modele.transcribe("voix.wav", language="fr", vad_filter=True)
for i, s in enumerate(segments):
    print(f"{i:05d}|{s.text.strip()}")   # à croiser avec les découpes audio
```

**Relis les transcriptions.** Une transcription fausse apprend au modèle à
prononcer un mot pour un autre. C'est l'étape la plus ingrate et la plus
rentable.

### 3.4 — Normaliser

Piper veut du WAV mono, 22 050 Hz, 16 bits :

```bash
for f in decoupes/*.wav; do
  ffmpeg -i "$f" -ac 1 -ar 22050 -sample_fmt s16 \
    -af "loudnorm=I=-23:LRA=7:TP=-2,silenceremove=start_periods=1:start_threshold=-40dB" \
    "dataset/wav/$(basename "$f")"
done
```

### 3.5 — Le format LJSpeech

```
dataset/
├── metadata.csv
└── wav/
    ├── 00001.wav
    ├── 00002.wav
    └── …
```

`metadata.csv`, séparateur `|`, sans en-tête :

```
00001|Bonsoir Guillaume, la maison est calme.
00002|La fenêtre du salon est restée ouverte.
```

Fais en sorte que le corpus **ressemble à ce que Luna dira** : des phrases
courtes, des questions, des énumérations, des chiffres et des heures. §7 le dit
déjà : « un corpus uniforme produit un modèle plat ».

### 3.6 — Prétraiter et affiner

**Affiner, pas partir de zéro.** Entraîner un modèle Piper depuis rien demande
des dizaines d'heures d'audio et des jours de GPU. Repartir d'un point de
contrôle français existant demande une nuit.

```bash
python3 -m piper_train.preprocess \
  --language fr --input-dir dataset --output-dir entrainement \
  --dataset-format ljspeech --single-speaker --sample-rate 22050

python3 -m piper_train \
  --dataset-dir entrainement \
  --accelerator gpu --devices 1 \
  --batch-size 12 \
  --resume_from_checkpoint fr_FR-siwis-medium.ckpt \
  --checkpoint-epochs 5 --max_epochs 4000 \
  --quality medium --precision 32
```

Sur une RTX 2070 (8 Go), `--batch-size 12` est un point de départ raisonnable :
si tu tombes en mémoire, descends à 8. Une nuit suffit largement pour un
affinage.

### 3.7 — Exporter et déposer

```bash
python3 -m piper_train.export_onnx entrainement/lightning_logs/version_0/checkpoints/last.ckpt \
  luna.onnx
cp entrainement/config.json luna.onnx.json
```

Puis sur Nova, dans `/share/piper/` (le dossier que l'add-on Piper explore pour
les voix ajoutées — **à vérifier** dans sa configuration au moment où tu le
feras), et enfin choisir la voix dans le pipeline Assist.

Rien à recompiler côté Luna : `luna/speak` et l'agent de conversation prennent
la voix du pipeline. Tu changes la voix, Luna change de voix.

---

## 4. À quoi t'attendre

§7 le dit sans détour, et c'est vrai : **« le résultat sera *proche*, pas
identique »**.

- Un affinage sur 30 à 60 minutes donne un timbre reconnaissable et une
  prosodie correcte. Ce n'est pas un clone.
- Si ta source est elle-même synthétique, Piper hérite de ses défauts : les
  liaisons ratées, les nombres mal dits, les fins de phrase plates.
- Les nombres, les heures et les noms propres sont les premiers à trahir. Mets-en
  dans le corpus.

---

## 5. La règle que tu as posée toi-même

§7 est explicite, et je ne fais que l'appliquer :

> « Les conditions d'utilisation des plateformes de clonage interdisent
> généralement de réutiliser les sorties pour entraîner un autre modèle, et la
> voix relève d'une œuvre sous licence. **Usage strictement privé ; ne jamais
> diffuser le modèle de voix si Luna est publiée sous l'org GitHub Alardware.** »

Deux cas :

- **C'est ta voix, ou celle de quelqu'un qui t'a dit oui.** Rien à ajouter, et
  c'est de loin le meilleur corpus : tu peux enregistrer exactement les phrases
  qui manquent.
- **C'est une voix sous licence** — un personnage, un comédien, une plateforme.
  Alors la règle ci-dessus s'applique telle quelle : usage privé, jamais publié.

Pour que ce soit tenu par autre chose que ta mémoire, `luna/.gitignore` refuse
désormais `*.onnx` et `*.onnx.json`. Un modèle de voix ne peut plus partir dans
un commit par distraction.

---

## 6. L'alternative honnête

Si le fichier est trop court, trop bruité, ou si tu n'as pas envie d'y passer la
soirée : **prends une voix Piper standard**. Écoute-les sur
`rhasspy.github.io/piper-samples`, filtre sur `fr_FR`, et retiens celle qui te
va. C'est instantané, gratuit, et ça marche.

Luna a un caractère par ce qu'elle dit et par la manière dont elle le formule —
son prompt système y travaille déjà. La voix vient après.

---

## 7. Si tu n'as que deux ou trois minutes

C'est le cas le plus fréquent, et il mérite mieux qu'un « non ». Trois issues,
par ordre de qualité du résultat.

### a. Enregistrer davantage — de loin la meilleure

Si la voix est disponible — la tienne, ou celle de quelqu'un qui a dit oui —
alors 30 à 60 minutes se lisent en une soirée, et le corpus devient **meilleur
que n'importe quel extrait trouvé** : tu enregistres exactement les phrases que
Luna dira. Des heures, des chiffres, des noms propres, des questions, des
annonces courtes. Ce sont précisément les endroits où un modèle affiné trahit,
et les seuls que tu peux couvrir à la source.

Trois consignes qui coûtent zéro et changent tout :

- **Un seul micro, une seule pièce, une seule session** si possible. Changer de
  source en cours de corpus donne un modèle qui change de voix en cours de
  phrase.
- **Viser des pics autour de −3 dB**, pas 0. Un fichier normalisé au plafond
  n'a plus de marge, et l'écrêtage s'apprend comme un trait de la voix.
- **Lire à voix posée**, comme on parle à quelqu'un dans la pièce — pas comme on
  lit un texte. Piper reproduit la prosodie qu'on lui donne.

### b. Le clonage à partir d'un court extrait — et ce qu'il coûte

D'autres familles de modèles clonent une voix à partir de quelques dizaines de
secondes. Deux minutes suffiraient largement. Mais ils sont d'un tout autre
poids que Piper, et **ne tourneront pas en temps réel sur le N95** : les faire
tourner voudrait dire les héberger sur Orion, donc rendre la voix de Luna
dépendante d'une machine qui a le droit d'être éteinte.

C'est exactement ce que §2 interdit : « Si Nebula et Orion sont éteintes, Luna
fonctionne normalement. » Une voix qui disparaît quand le NAS dort n'est pas une
voix, c'est une panne intermittente.

Il reste **un usage honnête** : la couche 1 du TTS de §7 — les **phrases figées**
des alertes de veille, pré-synthétisées et mises en cache. Celles-là ne se
calculent pas en direct, donc Orion peut les fabriquer une fois pour toutes et
s'éteindre. Le prix à payer est réel et il faut le regarder en face : les
alertes parleraient d'une voix, les réponses d'une autre. À ne faire que si le
résultat te plaît vraiment.

### c. Une voix Piper standard

Instantané, gratuit, et ça marche. Voir §6.

Luna a un caractère par ce qu'elle dit et par la façon dont elle le formule —
son prompt système y travaille déjà, et c'est ce qui s'entend en premier. La
voix vient après.
