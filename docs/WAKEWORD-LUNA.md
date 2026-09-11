# Faire de « Luna » le vrai mot d'éveil

openWakeWord ne fournit **aucun modèle « Luna » d'origine** : ses mots prêts à
l'emploi sont **« hey jarvis », « ok nabu », « alexa », « hey mycroft »,
« hey rhasspy »**. Tant que tu n'as pas de modèle `luna`, dire « Luna » ne
déclenche **jamais** rien — et depuis le dernier correctif, la veille te le **dit**
(elle liste les mots réellement chargés) au lieu d'écouter dans le vide.

Deux chemins, du plus rapide au plus « sur mesure ».

---

## Option A — tout de suite : un mot pré-entraîné

Le plus simple pour que la veille marche **maintenant**, en attendant « Luna » :

1. **Cockpit** → Paramètres › **Voix & réveil** › *Mot de réveil* → choisis
   **« Hey Jarvis »** (marqué « chargé »). C'est immédiat.
2. **Carte Home Assistant** → le mot se choisit dans le **pipeline Assist** de HA
   (Paramètres › Assistants vocaux → openWakeWord).

Thématiquement « hey jarvis » colle plutôt bien à l'esprit. Mais si tu veux
vraiment « Luna », passe à l'option B.

---

## Option B — entraîner un modèle « Luna »

openWakeWord fournit un **entraîneur automatique** : tu n'as **rien à enregistrer
toi-même**, il synthétise des milliers d'exemples du mot et entraîne un petit
modèle `.tflite`. Compte **~1 h**, gratuit, dans le navigateur.

### 1. Générer le modèle (Google Colab)

1. Ouvre le notebook officiel d'entraînement automatique :
   <https://github.com/dscripka/openWakeWord#training-new-models>
   (le lien « automatic model training » pointe vers un Colab prêt à l'emploi).
2. Dans le Colab, règle le **mot cible** sur **`luna`** (ou la graphie qui te
   convient — voir la note « accent » plus bas).
3. Lance toutes les cellules. À la fin, **télécharge le fichier `luna.tflite`**.

> Pas de Colab / envie de local ? Le dépôt openWakeWord explique aussi
> l'entraînement en local (Python + les outils du projet). Le Colab reste le plus
> simple.

### 2. Poser le modèle sur Sentinel (Nebula)

Le dossier `config/wakewords/` du dépôt est monté dans le conteneur openWakeWord
(en `/custom`). Donc :

1. Copie `luna.tflite` dans **`config/wakewords/`** sur Nebula.
2. Redémarre le service openWakeWord (il découvre les modèles de `/custom` au
   démarrage) :
   ```bash
   docker compose up -d sentinel-openwakeword
   ```
3. **Cockpit** → Paramètres › Voix & réveil : « **luna** » apparaît maintenant
   dans la liste (marqué « chargé »). Choisis-le. C'est mémorisé.
   *(Ou, si tu préfères le figer au démarrage : `WAKEWORD_MODEL=luna` dans `.env`.)*

### 3. Poser le même modèle sur Home Assistant (pour la carte)

La carte HA utilise l'openWakeWord **de HA** (l'add-on « openWakeWord »), séparé de
celui de Sentinel. Le chemin exact (donné par l'add-on lui-même) :

1. **Add-on Samba share** installé → copie `luna.tflite` dans le dossier partagé
   **`/share/openwakeword/`** (crée-le s'il n'existe pas). C'est là que l'add-on
   openWakeWord charge automatiquement les modèles personnalisés.
2. **Recharge l'intégration Wyoming** d'openWakeWord : Paramètres › Appareils et
   services › **Wyoming Protocol** → l'entrée openWakeWord → menu ⋮ → **Recharger**.
   (Un redémarrage de l'add-on marche aussi.)
3. Le mot « luna » devient **sélectionnable** dans ton **pipeline Assist** :
   Paramètres › Assistants vocaux → ton assistant → *Mot d'éveil* → « luna ».

> **L'entité `wake_word.openwakeword` à l'état « unknown » n'est PAS une panne** :
> c'est son état **au repos** (aucune détection encore). La preuve que l'add-on
> tourne est dans ses **logs** (« Ready », « Successfully sent discovery… »). Ce
> qui compte, c'est le **mot choisi dans le pipeline** et que tu le prononces.

---

## Bien choisir et régler « Luna »

- **« Luna » est court** (deux syllabes). Les mots d'éveil courts déclenchent plus
  de **faux positifs** (la maison « s'éveille » toute seule). Si ça arrive trop :
  - Entraîne plutôt **« hey luna »** ou **« ok luna »** (plus distinctifs).
  - Ou monte le **seuil de détection** d'openWakeWord (voir la doc de l'add-on /
    de l'image : option de *threshold*).
- **Accent** : l'entraîneur synthétise des voix ; le rendu FR de « Luna » (plutôt
  « Louna ») peut demander d'essayer la graphie `louna` si `luna` te reconnaît mal.
- **Teste les deux veilles séparément** : le cockpit (openWakeWord de Sentinel) et
  la carte (openWakeWord de HA) ont chacun leur modèle — pose `luna.tflite` **aux
  deux** endroits.

## Ça ne se déclenche toujours pas ?

- **Le cockpit dit « luna n'est pas chargé (chargés : hey jarvis, …) »** → le
  fichier `luna.tflite` n'est pas vu par le serveur : vérifie qu'il est bien dans
  `config/wakewords/` et que `sentinel-openwakeword` a été redémarré.
- **Rien, aucun message** → tu es peut-être sur une **ancienne version** : vérifie
  le repère de build dans l'en-tête du cockpit (« v0.1 · <date> ») après un
  `git pull` + `docker compose up -d --build`.
- **Le micro ne capte pas** (surtout PC) → contexte **HTTPS** requis, permission
  micro accordée, et le bon micro sélectionné par le navigateur.
