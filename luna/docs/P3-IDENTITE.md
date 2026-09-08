# Luna — P3 : identité. Hypothèses et contrats

**Statut : validé le 8 septembre 2026, puis implémenté.** C1 à C7 acceptés.
Les écarts constatés pendant l'écriture sont en **partie F** ; le reste du
document décrit ce qui tourne.

Sortie testable attendue (§11) : **Luna distingue Guillaume de Clara.**

---

## 0. Le vrai énoncé du problème

§6 demande trois signaux fusionnés : présence du téléphone, empreinte vocale,
et plus tard le visage. Avant de coder ça, il faut voir **où le problème se
pose réellement**.

| Appareil | Qui parle ? | Ce qu'il faut en plus |
|---|---|---|
| Téléphone de Guillaume, app Companion | **Déjà connu** — la session Home Assistant est la sienne | rien |
| Téléphone de Clara | **Déjà connu**, idem | rien |
| Navigateur de bureau, session personnelle | **Déjà connu** | rien |
| **iPad du couloir, session partagée** | **Inconnu** — tout le monde utilise le même compte | **tout P3** |

Le signal d'identité le plus fort du projet n'est pas biométrique : c'est
l'utilisateur Home Assistant authentifié, et il est livré depuis P1
(décision A6). La voix ne sert que là où ce signal est muet — **l'iPad
partagé du couloir**.

Ce recadrage n'enlève rien à §6, il le rend faisable : au lieu d'intercepter
l'audio de tous les chemins possibles, il suffit de le capter là où la carte
Luna tourne déjà.

---

# Partie A — Sept décisions

## C1. P3 ne traite que les appareils partagés.

**Ce que je propose.** L'empreinte vocale n'est calculée que pour les échanges
venus d'un appareil **sans utilisateur HA distinctif**. Partout ailleurs, la
session authentifiée fait foi et la voix n'est même pas analysée.

Ce que ça évite :

- pas besoin d'intercepter l'audio des satellites — le bouton Assist d'un
  téléphone est déjà authentifié ;
- pas d'enrobage du fournisseur STT de Home Assistant, montage fragile qui
  contredirait B1 ;
- pas de calcul d'empreinte sur des échanges où la réponse est déjà connue.
  Sur un N95, ne pas calculer est la meilleure optimisation.

**Conséquence.** La reconnaissance vocale n'est disponible que là où la carte
Luna tourne. Un satellite ESP32 futur, sans session, resterait `unknown` — ce
sera à traiter le jour où il existera, pas avant.

## C2. L'empreinte vocale tourne en ONNX, jamais en PyTorch.

**Le chiffre qui tranche.** `onnxruntime` pèse **23 Mo**. PyTorch en pèse plus de
huit cents. Sur une machine dont §2 dit que « le CPU N95 est le facteur
limitant », le choix ne se discute pas.

**Ce que je propose.** Un modèle d'empreinte de locuteur (famille ECAPA-TDNN ou
CAM++, 192 à 256 dimensions) exporté en ONNX, chargé par `onnxruntime`, et
traité comme un **provider remplaçable** de L1 : le nom du modèle est stocké
à côté de chaque empreinte, si bien qu'en changer invalide proprement les
empreintes au lieu de produire des comparaisons silencieusement fausses.

**Est-ce que ça contredit B1** (« Luna ne fait ni STT ni TTS ») ? Non. B1 disait
de ne pas réécrire ce que Home Assistant fournit déjà. Home Assistant fournit la
transcription et la synthèse ; il ne fournit **aucune** reconnaissance de
locuteur. Il n'y a rien à déléguer.

> **Le coût est mesuré avant d'être adopté**, comme la latence de Whisper : temps
> d'inférence sur 3 secondes d'audio, sur Nova, pas sur ma machine. Si le N95 ne
> tient pas, le repli est la branche « confiance insuffisante » de C4 — qui
> existe de toute façon.

## C3. La carte envoie une copie de l'audio à Luna, une fois par phrase.

**C'est un écart avec B2**, qui disait qu'aucun octet d'audio ne traverse le
relais de Luna. Il devient faux, mais de façon bornée :

- **une seule requête par phrase**, pas un flux ; l'audio est déjà en mémoire
  dans la carte, qui l'envoie après le relâchement du micro ;
- **plafonnée à 8 secondes** — une empreinte n'a pas besoin de plus — soit
  environ 256 ko en base64 ;
- **uniquement sur un appareil partagé** (C1) ;
- **l'audio brut n'est jamais écrit sur disque.** Seul le vecteur est conservé.

**Le contrôle « est-on en local ? » se fait dans l'intégration, avant de
relayer.** §6 est catégorique : « La biométrie n'est active que sur le réseau
local. Aucune frame caméra ne transite par le relais Nabu Casa. » Mettre le
contrôle dans l'add-on serait trop tard : l'audio aurait déjà traversé le relais.
Home Assistant expose `is_cloud_connection()` — *vérifié en 2026.2.3* — et
`util.network.is_local()` pour l'accès direct depuis internet.

## C4. La fusion rend un profil, une confiance, et une question quand ça ne suffit pas.

§6 dit « Luna calcule un score de confiance agrégé et un profil actif », sans
dire comment. Voici la formule, pour qu'elle soit discutable et testable :

```
Pour chaque profil connu p :

  presence(p) = 0.85  si son téléphone est à la maison
                0.15  s'il n'y est pas
                0.50  si aucun capteur n'est configuré pour lui

  voix(p)     = max(0, (cos(empreinte, centroïde_p) − 0.55) / 0.45)

  score(p)    = presence(p) × voix(p)

profil    = argmax score
marge     = score₁ − score₂
confiance = score₁ / Σ score

On décide seulement si  score₁ ≥ 0.35  ET  marge ≥ 0.15.
Sinon → `unknown`, et Luna demande.
```

**La marge est ce qui compte vraiment.** La sortie testable de §11 n'est pas
« Luna reconnaît Guillaume », c'est « Luna **distingue** Guillaume de Clara ».
Un score absolu élevé pour les deux ne distingue rien.

**La branche « ça ne suffit pas » n'est pas un pis-aller.** Luna demande
« C'est toi, Guillaume ? », la réponse vaut confirmation pour la durée de vie de
l'identité (C6), et l'échantillon peut enrichir l'empreinte. C'est aussi ce qui
sauve la phase si le modèle de C2 déçoit sur le N95.

## C5. L'identité personnalise. Elle n'autorise jamais.

§6 pose la limite : « La reconnaissance vocale et faciale 2D sert à la
**personnalisation**, pas à la sécurité. » Voici ce que ça donne dans le code,
en trois interdits vérifiés par des tests :

| Ce que la voix peut faire | Ce qu'elle ne peut pas faire |
|---|---|
| Fixer le profil actif, donc le scope `confort` / `personnel` (§3, F3) | Changer le niveau d'autonomie d'une action — il vient du registre de L0, à partir du couple `domaine.service` (§9.2) |
| Personnaliser le ton et le contenu de la réponse | Valider une proposition : `peut_valider` continue de lire `is_admin` **de la session Home Assistant**, jamais du profil vocal |
| Ouvrir l'accès aux données personnelles du profil | Déverrouiller quoi que ce soit de niveau `critique` — qui n'existe de toute façon pas en v1 |

C'est le pendant exact de ce qui a été fait en P2 : « la voix ne donne aucun
droit supplémentaire ». P3 ajoute un profil, pas des droits.

## C6. L'identité est attachée à un appareil, et elle expire.

Deux cartes ouvertes en même temps — l'iPad du couloir et le téléphone de
Clara — n'ont aucune raison d'avoir le même profil actif. L'identité est donc
**par appareil**, pas globale.

- La carte génère un identifiant d'appareil stable, gardé dans `localStorage`.
  Ce n'est pas une donnée biométrique : §9.4 interdit de garder de la biométrie
  côté navigateur, pas un numéro aléatoire.
- Sur un appareil partagé, l'identité **expire après 5 minutes de silence** et
  retombe à `unknown`. Sans ça, Luna continuerait de croire que c'est Guillaume
  trois heures après son départ.
- Sur un appareil à session personnelle, l'utilisateur HA fait foi et n'expire
  jamais : il n'y a rien à deviner.

## C7. §6 et §11 ne disent pas la même chose sur le visage.

Le tableau de §6 place la reconnaissance faciale en **P5**. La feuille de route
de §11 place en P5 « F2 : gardienne de l'installation » et en **P6** la
reconnaissance faciale.

**Ce que je propose :** la feuille de route gagne, elle est plus détaillée et
elle ordonne le projet. Le visage reste en P6. Rien à faire aujourd'hui, mais
autant que ce soit écrit une fois plutôt que redécouvert en P5.

---

# Partie B — Hypothèses

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H42 | Chaque profil a une entité `device_tracker` déclarée dans les options de l'add-on. | Sans capteur, `presence` vaut 0,50 pour ce profil : la voix décide seule, avec moins de marge. Ça marche, c'est juste moins sûr. |
| H43 | L'iPad du couloir utilise **un compte Home Assistant partagé**. C'est ce qui rend P3 nécessaire. | Voir Q2. Si chacun s'y connecte avec son compte, P3 se réduit à presque rien — et ce serait une bonne nouvelle. |
| H44 | Modèle d'empreinte : famille ECAPA-TDNN / CAM++, ONNX, 16 kHz, sortie 192 ou 256 dimensions, sous 50 Mo. | Le provider est remplaçable et le nom du modèle est stocké avec chaque empreinte : en changer invalide les empreintes proprement. |
| H45 | Inscription : **5 phrases**, environ 15 secondes de parole utile par personne. | En dessous, le centroïde est instable et la marge de C4 s'effondre. On peut enrichir plus tard avec les confirmations. |
| H46 | Les phrases d'inscription sont **variées** : une question, une énumération, des chiffres, une phrase longue. Même raison qu'en §7 pour la voix custom — un corpus uniforme donne un modèle plat. | Une inscription sur cinq phrases identiques reconnaît la phrase, pas la personne. |
| H47 | L'audio d'identification est le **même** que celui envoyé au pipeline : 16 kHz, mono, PCM 16 bits. La carte en garde une copie pendant l'énoncé. | Aucun encodage supplémentaire, aucun deuxième passage micro. |
| H48 | Seuils de départ : `cos ≥ 0,55` pour compter, `score₁ ≥ 0,35`, `marge ≥ 0,15`, TTL 5 min. | Ce sont des **points de départ à régler sur des vraies voix**. La table `identity_log` existe pour ça : elle garde chaque décision et son score, de quoi régler les seuils sur des données plutôt qu'au jugé. |
| H49 | Les empreintes vivent dans la base SQLite de Nova, jamais ailleurs. §4 (F4) : « Aucune donnée d'habitude ne quitte la maison » — a fortiori une empreinte vocale. | — |
| H50 | L'audio brut n'est **jamais** écrit sur disque, ni en inscription ni en identification. Seul le vecteur est conservé. | C'est ce qui rend l'inscription acceptable pour quelqu'un d'autre que soi. |
| H51 | En accès distant, `luna/identity/voice` est **refusée par l'intégration**, avec un message renvoyant au PIN de Loggia (§6). | Le contrôle en amont est le seul qui garantisse qu'aucun octet ne traverse le relais Nabu Casa. |
| H52 | Le profil `liam` est inscriptible mais son empreinte se périme plus vite : une voix d'enfant change. Un rappel de ré-inscription au bout de 3 mois. | Voir Q3. Sans ça, Luna cessera de le reconnaître sans qu'on comprenne pourquoi. |

---

# Partie C — Contrats

## C.1 Commandes

```jsonc
// P3 — état courant, pour le badge d'identité (§8, composant 4)
{ "type": "luna/identity", "device": "d_a1b2c3" }
→ { "profile": { "id": "guillaume", "display_name": "Guillaume",
                 "confidence": 0.82,
                 "signals": { "ha_user": 0.0, "presence": 0.85, "voice": 0.91 } },
    "expires_at": "2026-09-08T21:14:00+02:00",
    "enrolled": ["guillaume", "clara"] }

// P3 — la route §12 `POST /identity/voice`, côté carte
{ "type": "luna/identity/voice",
  "device": "d_a1b2c3",
  "audio": "<base64 PCM16 16 kHz mono, 8 s maximum>" }
→ { "profile": "guillaume", "confidence": 0.82, "margin": 0.31,
    "asked": false }
// `asked: true` → la confiance est insuffisante, Luna va poser la question

// P3 — confirmation explicite, la branche « ça ne suffit pas » de C4
{ "type": "luna/identity/confirm",
  "device": "d_a1b2c3", "profile": "guillaume", "accept": true }
→ { "profile": "guillaume", "confidence": 1.0, "expires_at": "…" }

// P3 — inscription
{ "type": "luna/identity/enroll/start", "profile": "clara" }
→ { "session": "e_01J…", "phrases": [ "…", "…", "…", "…", "…" ] }

{ "type": "luna/identity/enroll/sample",
  "session": "e_01J…", "index": 0, "audio": "<base64>" }
→ { "accepted": true, "remaining": 4, "quality": "ok" }
   // quality: ok | trop_court | trop_bruyant | trop_faible

{ "type": "luna/identity/enroll/finish", "session": "e_01J…" }
→ { "profile": "clara", "samples": 5, "coherence": 0.88 }
   // `coherence` : cohésion interne du centroïde. Basse = à refaire.

{ "type": "luna/identity/forget", "profile": "clara" }
→ { "removed": 5 }
```

Erreurs propres à P3 : `remote_biometrics` (accès distant, §6),
`not_enrolled` (aucune empreinte pour ce profil), `audio_too_short`,
`model_unavailable` (le modèle ONNX n'est pas chargé).

## C.2 Contrats existants qui changent

| Contrat | Avant | Après |
|---|---|---|
| `luna/info` → `phases.identity` | `false` | **`true`** |
| `luna/feed` → événement `identity` | documenté *[P3]* | **actif** — c'est ce qui met à jour le badge de §8 sans aller-retour |
| `ContexteRequete.local` | figé à `true` | **réel**, calculé par l'intégration (`is_cloud_connection`, `is_local`) |
| `ContexteRequete` | — | gagne **`device`**, l'identifiant d'appareil de C6 |
| Options de l'add-on | `profils: [{utilisateur_ha, profil}]` | gagne **`presence`** par profil, une entité `device_tracker` |

## C.3 Schéma SQLite ajouté

```sql
-- L'empreinte, jamais l'audio.
CREATE TABLE voice_prints (
    id         TEXT PRIMARY KEY,
    profile    TEXT NOT NULL,
    vector     BLOB NOT NULL,       -- float32, `dim` valeurs
    dim        INTEGER NOT NULL,
    model      TEXT NOT NULL,       -- changer de modèle invalide l'empreinte
    source     TEXT NOT NULL,       -- enrolment | confirmed
    created_at TEXT NOT NULL
);
CREATE INDEX idx_prints_profile ON voice_prints(profile, model);

-- Chaque décision et son score : c'est là-dedans qu'on règle les seuils de
-- H48, sur des vraies voix plutôt qu'au jugé.
CREATE TABLE identity_log (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    device      TEXT NOT NULL,
    decided     TEXT NOT NULL,      -- le profil retenu, ou `unknown`
    confidence  REAL NOT NULL,
    margin      REAL NOT NULL,
    signals_json TEXT NOT NULL,     -- le détail par profil, pour l'analyse
    asked       INTEGER NOT NULL    -- Luna a-t-elle dû poser la question
);
CREATE INDEX idx_identity_log_ts ON identity_log(ts);
```

## C.4 Nouvelles pièces, par couche

| Couche | Fichier | Rôle |
|---|---|---|
| L0 | `kernel/identity.py` | Le calcul de fusion de C4 — pur, sans dépendance, donc testable au vecteur près |
| L1 | `providers/voiceprint.py` | Le modèle ONNX. Chargement paresseux : Luna démarre même si le modèle manque |
| L1 | `providers/store.py` | Les deux tables ci-dessus |
| L2 | `engine/identity.py` | Inscription, identification, durée de vie, journal |
| L3 | `interfaces/relay.py` | Les six commandes |
| intégration | `websocket.py` | Les mêmes, **plus le contrôle « en local ? » avant de relayer** |
| carte | `luna-card.js` | Badge vivant, panneau d'inscription, question de confirmation |

Rien dans `kernel/autonomy.py`, `kernel/permissions.py` ni `engine/arbiter.py` :
comme en P2, c'est le signe que le découpage tient. L'identité choisit **quel**
scope s'applique ; elle ne touche pas à ce que les scopes autorisent.

---

# Partie D — Recette de P3

Sortie testable de §11 : « Luna distingue Guillaume de Clara. »

| # | Vérification | Automatisable |
|---|---|---|
| 1 | Inscrire Guillaume : 5 phrases, `coherence` au-dessus de 0,8 | partiellement — avec des vecteurs synthétiques |
| 2 | Inscrire Clara, idem | idem |
| 3 | Guillaume parle depuis l'iPad → badge « Guillaume », confiance affichée | ⏳ **à faire sur Nova**, c'est la sortie de §11 |
| 4 | Clara parle depuis le même iPad → le badge change | ⏳ **sur Nova** |
| 5 | Une voix inconnue → `unknown`, et Luna demande | ✅ |
| 6 | Téléphone de Clara absent → sa présence baisse, la marge en tient compte | ✅ |
| 7 | En accès distant, `luna/identity/voice` est refusée **avant** de relayer | ✅ — le test vérifie qu'aucun octet ne part |
| 8 | Un profil identifié à la voix **ne peut pas** valider une proposition de niveau 4 sans être administrateur HA | ✅ — c'est l'invariant de C5 |
| 9 | L'identité expire après 5 minutes de silence | ✅ |
| 10 | Temps d'inférence de l'empreinte sur 3 s d'audio | ⏳ **à mesurer sur Nova** |
| 11 | `ruff`, `lint-imports`, `pytest` verts | ✅ |

Les points 3, 4 et 10 demandent de vraies voix et la vraie machine. Tout le
reste se teste ici, y compris le calcul de fusion — c'est justement pour ça
qu'il vit dans L0, sans dépendance.

---

# Partie E — Ce dont j'ai besoin de toi

> **Q1 — Les capteurs de présence.** Quelles entités `device_tracker` pour
> Guillaume, Clara et Liam ? (`Outils de développement → États`, filtrer sur
> `device_tracker.`) Sans elles, la présence vaut 0,50 partout et la voix
> décide seule.

> **Q2 — L'iPad du couloir.** Compte Home Assistant **partagé**, ou chacun s'y
> connecte ? C'est la question la plus structurante de P3 : si chacun a sa
> session, l'identité est déjà résolue et P3 se réduit au badge. Si c'est un
> compte partagé — ce que je suppose — alors tout ce document s'applique.

> **Q3 — Liam.** Faut-il le reconnaître ? Une voix d'enfant change vite : son
> empreinte se périmera en quelques mois, et il faudra le ré-inscrire. Si
> `confort` lui suffit — et c'est déjà tout ce que son scope autorise —, on peut
> ne pas l'inscrire du tout et le laisser en `guest`.

> **Q4 — L'accord de Clara et Liam.** Stocker une empreinte vocale, même
> réduite à un vecteur qui ne quitte pas Nova, ça se demande. Ce n'est pas une
> question technique et je ne la tranche pas ; je la pose une fois pour qu'elle
> ne soit pas oubliée. §6 dit que ces données ne servent qu'à la
> personnalisation, et C5 le rend structurel — c'est la réponse honnête à leur
> donner.

**Q2 reste sans réponse**, et le code n'attend pas après elle : il a été écrit
pour l'hypothèse large (compte partagé). Si chacun se connecte avec son compte
sur l'iPad, rien ne casse — `voice_needed` passe simplement à faux et aucune
empreinte n'est jamais calculée. C'est exactement ce que C1 prévoit.

---

# Partie F — Écarts entre ce contrat et ce qui tourne

| # | Ce que disait le contrat | Ce qui a été fait | Pourquoi |
|---|---|---|---|
| 1 | `score(p) = presence(p) × voix(p)` | `score(p) = voix(p) × (1 − 0,5 + 0,5 × presence(p))` | **Trouvé par un test.** La forme multiplicative donnait à la présence un **droit de veto** : avec `presence = 0,15`, aucun score ne pouvait atteindre le seuil de décision, si franche que soit la voix. Un téléphone oublié dans la voiture rendait son propriétaire méconnaissable. C'est l'inverse de §6, qui dit que le téléphone est une *présomption* et que la voix *confirme*. La nouvelle forme laisse la présence départager deux voix proches sans jamais pouvoir annuler la voix. |
| 2 | `quality: ok \| trop_court \| trop_bruyant \| trop_faible` | `ok \| trop_court \| trop_faible` | Je ne sais pas détecter du bruit de fond de façon fiable sans détecteur d'activité vocale. Annoncer une catégorie qu'on ne sait pas produire aurait été pire que de l'enlever. |
| 3 | Rien de précisé sur l'emplacement du panneau d'identité | Il partage le tiroir avec « Veille », ouvert par le **badge d'identité** | §8 ne prévoyait qu'un tiroir. Mettre l'identité derrière le badge qui l'affiche est l'endroit où on la cherche. |
| 4 | Toutes les commandes d'identité sous la barrière du réseau local | `luna/identity/forget` marche **aussi à distance** | Effacer une empreinte n'est pas de la biométrie : c'est le contraire. Devoir rentrer chez soi pour retirer sa voix serait absurde. |
| 5 | Rien sur le moment de l'identification | Elle a lieu **avant** l'échange, pas après | Le profil fixe le scope des actions (§3, F3). Identifier après reviendrait à évaluer la demande au nom de quelqu'un d'autre. Le coût est un aller-retour avant que Luna ne commence à réfléchir. |
| 6 | Rien sur l'emplacement du modèle | `map: share:ro` dans `config.yaml` | Le `.onnx` se dépose dans `/share`, comme un modèle de voix Piper. Lecture seule : l'add-on n'a aucune raison d'y écrire. |
| 7 | Rien sur la façon dont la carte applique C1 | `luna/info` rend `identity.voice_needed` | Sans ce drapeau, la carte enverrait de l'audio même là où la session Home Assistant a déjà répondu. C1 se décide côté add-on, mais s'applique côté carte. |

---

# Partie G — Recette de P3, état

| # | Vérification | État |
|---|---|---|
| 1 | Inscrire Guillaume, cohérence au-dessus de 0,8 | ✅ testé |
| 2 | Inscrire Clara | ✅ testé |
| 3 | Guillaume parle depuis l'iPad → badge « Guillaume » | ⏳ **sur Nova**, avec de vraies voix |
| 4 | Clara parle depuis le même iPad → le badge change | ⏳ **sur Nova** |
| 5 | Voix inconnue → `unknown`, et Luna demande | ✅ testé, jusqu'au bouton de confirmation dans la carte |
| 6 | Téléphone absent → la marge en tient compte, **sans veto** | ✅ testé — c'est l'écart n° 1 |
| 7 | En distant, `luna/identity/voice` refusée **avant** le relais | ✅ testé : le faux relais ne reçoit aucun octet |
| 8 | Un profil reconnu à la voix ne peut pas valider un niveau 4 | ✅ testé — l'invariant de C5 |
| 9 | L'identité expire après cinq minutes | ✅ testé |
| 10 | Temps d'inférence sur 3 s d'audio | ⏳ **à mesurer sur Nova**, avec le vrai modèle |
| 11 | `ruff`, `lint-imports`, `pytest` verts | ✅ 290 tests |

Les points 3, 4 et 10 demandent de vraies voix et la vraie machine. Tout le
reste est couvert, y compris la fusion — c'est pour ça qu'elle vit dans L0,
sans dépendance : elle se teste au vecteur près.

**Ce qui reste à faire sur Nova, et que le code ne peut pas faire :** choisir et
déposer un modèle ONNX d'empreinte de locuteur dans `/share`, renseigner
l'option `modele_voix`, déclarer les entités `device_tracker` de chacun, puis
inscrire les voix depuis le badge de la carte. Sans modèle, Luna démarre,
converse et pilote la maison comme avant — seule la reconnaissance reste
éteinte, et elle le dit.
