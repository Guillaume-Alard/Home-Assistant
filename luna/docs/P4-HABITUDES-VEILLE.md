# Luna — P4 : habitudes et veille. Hypothèses et contrats

**Statut : validé (D1–D8) et implémenté.** Les écarts entre ce qui était prévu
ici et ce qui a été construit sont listés en **partie G**, avec leurs raisons.
La recette de la partie E dit où en est chaque point.

Sortie testable attendue (§11) : **un rappel de coucher pertinent, une alerte
ouvrant.**

---

## 0. La phase à surveiller

§1 est écrit pour cette phase-là : « L'ancien Sentinelle a accumulé des
fonctions non désirées. **Si une fonction n'est pas listée dans ce document,
elle n'existe pas.** »

P4 est celle qui invite à la dérive : une fois que Luna observe la maison et
prend la parole, tout devient tentant. Ce document tient donc une liste de ce
que P4 **ne fait pas**, aussi longue que celle de ce qu'elle fait — partie D.

Deux phrases du cahier des charges gouvernent tout le reste :

> §5, F5 : « La détection reste dans HA (template sensors, automatisations,
> Alarmo). Luna gère la *pertinence*, la *formulation* et le *moment*. Ne jamais
> confier la détection critique au modèle de langage. »

> §4, F4 : l'entretien nocturne « **propose**, il ne décide pas ».

---

# Partie A — Huit décisions

## D1. La détection vit dans Home Assistant, déclarativement. Luna n'observe que le verdict.

**Ce que je propose.** Chaque règle de veille nomme une entité `binary_sensor`
de Home Assistant. Luna la regarde passer à `on` et décide quoi en faire. Elle
ne calcule jamais « fenêtre ouverte **et** nuit **et** alarme non armée » :
cette condition est un *template sensor* écrit dans `configuration.yaml`.

```yaml
# configuration.yaml — la détection, chez elle
template:
  - binary_sensor:
      - name: "Ouvrant oublié la nuit"
        unique_id: luna_ouvrant_oublie
        state: >
          {{ is_state('binary_sensor.ouvrants_maison', 'on')
             and is_state('binary_sensor.contexte_coucher', 'on') }}
```

```yaml
# options de l'add-on Luna — la pertinence, la formulation, le moment
veille:
  - entite: binary_sensor.luna_ouvrant_oublie
    categorie: ouvrant
    niveau: warning
    message: "Un ouvrant est resté ouvert et il est tard."
```

**Pourquoi c'est le bon découpage, au-delà du fait que §5 le dit :**

| | |
|---|---|
| La condition est **visible et testable dans Home Assistant** | Guillaume la voit dans les outils de développement, sans lire de code Python |
| Elle **survit à Luna** | Si l'add-on est arrêté, le capteur reste juste, et une automatisation HA peut toujours s'en servir |
| Elle **ne peut pas mentir** | Aucun modèle de langage sur le chemin ; §9.2 tenu par construction, pas par vigilance |

**Ce que Luna ajoute, et que Home Assistant ne sait pas faire :** ne pas
harceler, respecter les heures de silence, ne pas répéter ce qui a déjà été
ignoré, retenir un « ne plus me le dire », formuler en français, choisir le
moment.

**Le coût.** Guillaume doit écrire ces capteurs. Je livre un jeu de départ
documenté, à coller. C'est aussi exactement ce que la gardienne de P5 saura
proposer plus tard.

## D2. Deux sources de faits, deux statuts. Le modèle propose, il ne décide pas.

§4 veut des faits atomiques. La question qu'il ne tranche pas, c'est **qui les
extrait**.

| Source | Ce qu'elle produit | Statut à la création |
|---|---|---|
| **Observateurs déterministes** (du code, dans L2) — heure du dernier passage en contexte « coucher », séquences récurrentes d'entités | Des faits mesurés | **`active`**, avec une confiance construite sur le nombre d'observations |
| **Le modèle**, une fois par nuit, sur les échanges récents — « je me couche vers 23 h », « je n'aime pas quand le couloir est à fond » | Des faits déduits d'une conversation | **`needs_review`** : ils apparaissent dans le tiroir, Guillaume accepte ou refuse |

C'est la lecture littérale de §4 : « Il **propose**, il ne décide pas. » Un fait
déduit d'une phrase n'entre jamais en vigueur tout seul.

**Le modèle ne touche jamais à F5.** La veille ne consulte que des capteurs
Home Assistant (D1). §9.2 n'est pas une consigne de prompt ici, c'est un fait
d'architecture : le moteur de veille n'a aucun accès au cerveau.

## D3. L'entretien nocturne coûte un appel par nuit, et il est borné.

§4 le veut quotidien. Sans garde-fou, c'est une facture qui grandit avec la
maison.

- **Un seul appel**, à 3 h 30 (réglable).
- **Au plus 200 événements** repris, les plus récents.
- **Sauté s'il n'y a rien de neuf** depuis la dernière fois.
- **Sortie structurée** (`output_config.format`) : le modèle rend une liste de
  faits typés, pas de la prose à réinterpréter.

Ordre de grandeur : quelques milliers de jetons en entrée, quelques centaines
en sortie. Moins d'un centime par nuit sur `claude-sonnet-5`. C'est chiffré ici
pour qu'on s'en aperçoive si ça dérive.

## D4. Un rappel va dans le tiroir, et se dit sur une carte ouverte. Rien d'autre.

§5 ne mentionne **aucune notification mobile**. §1 est catégorique : ce qui n'y
est pas n'existe pas. Sentinelle en avait ; Luna n'en aura pas.

Reste une gêne honnête : un rappel de coucher que personne ne regarde n'est pas
un rappel. Annoncer sur une enceinte réglerait ça — mais §5 ne le demande pas
non plus. **Je ne le construis pas sans que tu le demandes** (question Q1). La
plomberie est prévue pour que ce soit un provider à ajouter, pas une refonte.

## D5. La couche 1 de §7 est déjà là. Il n'y a rien à construire.

§7 décrit un cache audio : « Les alertes de veille sont templatées […] générées
une fois en qualité maximale, stockées en fichiers sur Nova, rejouées telles
quelles. »

**Home Assistant le fait déjà.** Son composant `tts` tient un cache **mémoire
et fichier**, indexé sur le texte, le moteur et les options. *Vérifié en
2026.2.3.* Il suffit donc que les phrases d'alerte soient **templatées**, donc
identiques à l'octet près d'une fois sur l'autre, et le cache s'occupe du reste.

C'est le meilleur genre de décision : celle qui supprime du code. Et si un jour
une phrase mérite un enregistrement fait à la main, `tts.async_override_result`
est le crochet prévu pour ça — il existe déjà.

## D6. La décroissance, écrite en clair.

§4 : « Chaque catégorie de fait porte un taux de décroissance : une habitude de
coucher se périme plus vite qu'une préférence de température. » Voici le
combien :

```
confiance(fait) = maturité × fraîcheur

  maturité  = 1 − 0,5 ^ (observations / 2)     → 1 obs : 0,29   3 obs : 0,65
                                                 5 obs : 0,82   10 obs : 0,97
  fraîcheur = 0,5 ^ (jours_depuis_derniere / demi_vie)

Demi-vies par catégorie :
  heure_de_coucher          30 jours    une habitude se déplace
  sequence_recurrente       45 jours
  preference_eclairage      90 jours
  preference_temperature   180 jours    une préférence dure
  fait_declare             365 jours    « Clara est allergique aux chats »

Sous 0,25 de confiance, un fait ne sert plus à rien : il n'est pas supprimé
(§4 : « jamais supprimé »), il cesse d'être proposé.
```

Ce calcul vit dans L0, sans dépendance — comme la fusion d'identité de P3, il
se teste au jour près.

## D7. `events` s'ajoute, `action_log` reste.

§9.1 impose `action_log`, §4 impose `events`. Les deux restent : chaque entrée
de `action_log` est **aussi** écrite dans `events`, qui devient le journal
immuable de tout. Rien à migrer, rien à détruire — le schéma passe en v3 par
simple ajout de tables.

## D8. Une alerte propose. Elle n'agit jamais toute seule.

Le tiroir de §8 offre `Agir` / `Ignorer` / `Ne plus me le dire`. Le bouton
`Agir` ne court-circuite rien : il fabrique un appel d'outil qui passe par
**l'arbitre d'autonomie**, exactement comme une demande de Guillaume. Éteindre
le séjour reste du niveau 2 ; fermer un ouvrant reste du niveau 5, donc refusé.

Rien dans P4 ne touche à `kernel/autonomy.py` ni à `engine/arbiter.py`. Comme
en P2 et P3, c'est le signe que le découpage tient.

---

# Partie B — Hypothèses

| # | Hypothèse | Si c'est faux |
|---|---|---|
| H53 | Les règles de veille sont déclarées dans les options de l'add-on : entité, catégorie, niveau, message, et un `silence` optionnel. | Le seul endroit où Guillaume peut les changer sans redéployer de code. |
| H54 | Je livre un jeu de *template sensors* de départ, à coller dans `configuration.yaml` : ouvrant oublié, éclairage oublié au coucher, alarme non armée en absence. Les trois cas de §5, F5. | Sans eux, la veille n'a rien à regarder. Voir Q2. |
| H55 | Entretien nocturne à **3 h 30**, réglable. | Assez tard pour que la journée soit finie, assez tôt pour que le rapport soit prêt au réveil. |
| H56 | Au plus **200 événements** par passage, sauté si rien de neuf. | Sans plafond, la facture grandit avec la maison. |
| H57 | Une même alerte n'est pas répétée avant **4 heures**, quelle que soit l'agitation du capteur. | C'est la différence entre une veille et un harcèlement. |
| H58 | **Heures de silence : 22 h 30 – 7 h 00**, sauf pour les alertes `critical`. | Un rappel de coucher a le droit de parler à 23 h ; une ampoule oubliée dans le garage, non. |
| H59 | Un fait `needs_review` non traité **expire au bout de 14 jours**. | Sinon la file de relecture devient un cimetière que personne n'ouvre. |
| H60 | Les faits, les événements et les scores ne quittent **jamais** Nova (§4). Seul l'entretien nocturne envoie des extraits d'échanges au modèle — ce sont des conversations, pas des habitudes. | La distinction compte : Luna n'envoie jamais « Guillaume se couche à 23 h 20 » à un tiers. |
| H61 | Le modèle rend ses faits en **sortie structurée** typée, jamais en prose. | Une prose à réinterpréter est une source d'erreurs silencieuses. |
| H62 | Un fait contredit passe en `superseded` et une relation `supersedes` est écrite. Jamais de suppression (§4). | L'historique reste consultable, comme le veut le cahier des charges. |
| H63 | Les observateurs déterministes tournent **sur événement**, jamais en boucle de scrutation. | Le N95 fait déjà tourner Whisper et une empreinte de locuteur. Une boucle qui interroge la maison toutes les secondes serait le premier vrai gaspillage du projet. |

---

# Partie C — Contrats

## C.1 Commandes qui deviennent réelles

Toutes documentées depuis P1, jamais implémentées jusqu'ici.

```jsonc
// §12 — GET /suggestions
{ "type": "luna/suggestions" }
→ { "suggestions": [
      { "id": "s_01J…", "key": "coucher|guillaume",
        "title": "Il est l'heure d'aller te coucher.",
        "why": "Tu te couches vers 23 h 20 les soirs de semaine, il est 23 h 35.",
        "score": 0.68, "level": 0, "actions": [] } ] }

// §12 — GET /profile/{user}/patterns
{ "type": "luna/patterns", "profile": "guillaume" }
→ { "patterns": [
      { "id": "f_01J…", "predicate": "heure_de_coucher", "value": "23:20",
        "days": ["mon","tue","wed","thu"], "entity_id": null,
        "confidence": 0.74, "observations": 23,
        "last_seen": "2026-09-07T23:18:00+02:00", "status": "active" } ] }

// §12 — POST /feedback
{ "type": "luna/alerts/feedback", "suggestion_id": "s_01J…", "action": "muted" }
→ { "ok": true, "muted_until": "2026-10-08T23:00:00+02:00" }

// P4 — la file de relecture de D2
{ "type": "luna/facts" }
→ { "facts": [
      { "id": "f_01J…", "predicate": "preference_eclairage",
        "value": "couloir tamisé le soir", "profile": "guillaume",
        "why": "Tu me l'as dit le 6 septembre.", "confidence": 0.4 } ] }

{ "type": "luna/facts/decide", "fact_id": "f_01J…", "decision": "accept" }
→ { "status": "active" }
```

`luna/feed` émet enfin `alert` et `alert_cleared`, documentés depuis P1.

## C.2 La boucle de feedback de §12

Déjà écrite en toutes lettres dans `P1-CONTRATS.md` §6, appliquée telle quelle :

```
score ∈ [0, 1], initialisé à 0,5
accepted → +0,15, rejections ← 0
rejected → −0,25, rejections ← rejections + 1
muted    → muted_until ← maintenant + 30 jours
rejections ≥ 3 → muted_until ← maintenant + 30 jours, rejections ← 0
score < 0,2 ou muted_until dans le futur → la suggestion ne remonte pas
```

Le compteur porte sur la **clé** (`prédicat|profil|entité`), pas sur
l'instance : refuser trois fois « éteins le séjour le soir » mute la règle, pas
trois occurrences distinctes.

## C.3 Schéma SQLite ajouté (v3)

```sql
-- §4 : « Journal immuable de tout ce qui arrive »
CREATE TABLE events (
    id        TEXT PRIMARY KEY,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,   -- message | state | action | alert | identity
    profile   TEXT,
    entity_id TEXT,
    payload   TEXT NOT NULL
);
CREATE INDEX idx_events_ts   ON events(ts);
CREATE INDEX idx_events_kind ON events(kind, ts);

-- §4 : les faits atomiques
CREATE TABLE facts (
    id           TEXT PRIMARY KEY,
    predicate    TEXT NOT NULL,
    value        TEXT NOT NULL,
    profile      TEXT,
    entity_id    TEXT,
    category     TEXT NOT NULL,   -- porte la demi-vie de D6
    status       TEXT NOT NULL,   -- active | superseded | needs_review
    source       TEXT NOT NULL,   -- observateur | modele
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    observations INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX idx_facts_cle
    ON facts(predicate, IFNULL(profile,''), IFNULL(entity_id,''), status)
    WHERE status = 'active';

-- §4 : « renforcement sans duplication »
CREATE TABLE fact_observations (
    id       TEXT PRIMARY KEY,
    fact_id  TEXT NOT NULL REFERENCES facts(id),
    ts       TEXT NOT NULL,
    source   TEXT NOT NULL,
    event_id TEXT
);

-- §4 : supersedes | contradicts | supports
CREATE TABLE fact_relations (
    id      TEXT PRIMARY KEY,
    de_id   TEXT NOT NULL REFERENCES facts(id),
    vers_id TEXT NOT NULL REFERENCES facts(id),
    genre   TEXT NOT NULL,
    ts      TEXT NOT NULL
);

-- §12 : la boucle de feedback
CREATE TABLE suggestion_scores (
    cle         TEXT PRIMARY KEY,
    score       REAL NOT NULL DEFAULT 0.5,
    rejections  INTEGER NOT NULL DEFAULT 0,
    muted_until TEXT,
    updated_at  TEXT NOT NULL
);
```

L'index unique partiel est ce qui applique « un fait ré-observé est renforcé,
jamais dupliqué » — au niveau de la base, pas de la discipline.

## C.4 Nouvelles pièces, par couche

| Couche | Fichier | Rôle |
|---|---|---|
| L0 | `kernel/facts.py` | Le calcul de confiance et de décroissance de D6. Pur, testable au jour près |
| L0 | `kernel/veille.py` | Les règles de silence, de répétition et de score de D6/C.2 |
| L1 | `providers/store.py` | Les cinq tables |
| L2 | `engine/observers.py` | Les observateurs déterministes : coucher, séquences |
| L2 | `engine/veille.py` | Le moteur : entités surveillées → alertes pertinentes |
| L2 | `engine/nightly.py` | L'entretien nocturne de D3 |
| L2 | `engine/scheduler.py` | Un ordonnanceur minimal : l'entretien, la purge, l'expiration |
| L3 | `interfaces/relay.py` | Les cinq commandes |
| L3 | `interfaces/http.py` | Les trois routes §12, qui cessent de répondre 501 |
| carte | `luna-card.js` | Le tiroir Veille vivant, la file de relecture |

Rien dans `kernel/autonomy.py`, `kernel/permissions.py` ni `engine/arbiter.py`.

---

# Partie D — Ce que P4 ne fait pas

Aussi important que le reste (§1, §10).

- ❌ **Aucune notification mobile.** §5 n'en parle pas. Sentinelle en avait.
- ❌ **Aucune annonce sur enceinte** tant que tu ne l'as pas demandée (Q1).
- ❌ **Aucune automatisation créée par Luna.** Elle lit des capteurs, elle n'en
  écrit pas. Proposer des correctifs, c'est F2, donc P5.
- ❌ **Aucune boucle de scrutation.** Les observateurs réagissent aux
  changements d'état ; rien n'interroge la maison en continu (H63).
- ❌ **Aucune détection dans le modèle de langage.** Le moteur de veille n'a
  pas accès au cerveau (D1, §9.2).
- ❌ **Aucune action déclenchée seule.** `Agir` passe par l'arbitre comme le
  reste (D8).
- ❌ **Aucun fait déduit d'une conversation n'entre en vigueur sans relecture**
  (D2, §4).
- ❌ **Aucune donnée d'habitude ne quitte la maison** (§4, H60).

---

# Partie E — Recette de P4

Sortie testable de §11 : « Un rappel de coucher pertinent, une alerte ouvrant. »

| # | Vérification | Où c'est vérifié |
|---|---|---|
| 1 | Le capteur `binary_sensor.luna_ouvrant_oublie` passe à `on` → une alerte apparaît dans le tiroir, avec sa justification | ✅ `test_veille.py::test_un_capteur_qui_passe_a_on_produit_une_alerte`, `test_carte.py::test_une_alerte_arrive_par_le_feed` |
| 2 | Le même capteur s'agite dix fois en une heure → **une seule** alerte (H57) | ✅ `test_dix_soubresauts_en_une_heure_font_une_alerte` |
| 3 | Une ampoule oubliée à 3 h du matin → rien avant 7 h (H58) | ✅ `test_une_ampoule_oubliee_a_trois_heures_attend_sept_heures` |
| 4 | « Ne plus me le dire » → la règle ne remonte plus pendant 30 jours (§12) | ✅ `test_ne_plus_me_le_dire_fait_taire_la_regle_trente_jours` |
| 5 | Refusée plusieurs fois → même effet, sans avoir cliqué « ne plus » (§12) | ✅ `test_refusee_encore_et_encore_vaut_une_sourdine` — **deux** refus, voir G4 |
| 6 | Vingt soirs de coucher observés → un fait `heure_de_coucher` de confiance > 0,7 | ✅ `test_vingt_soirs_donnent_une_habitude_sure` |
| 7 | Aucun coucher pendant deux mois → la confiance retombe sous 0,25, le fait n'est plus proposé mais **existe toujours** (§4) | ✅ `test_deux_mois_sans_coucher_font_retomber_sous_le_seuil`, `test_une_habitude_perimee_fait_taire_le_rappel` |
| 8 | Un rappel de coucher pertinent, à la bonne heure, avec sa raison | ⏳ **sur Nova**, c'est la sortie de §11. La mécanique est vérifiée par `TestRappelDeCoucher` ; ce qui reste à juger, c'est la pertinence réelle |
| 9 | L'entretien nocturne extrait un fait d'une conversation → il arrive en `needs_review`, pas en vigueur | ✅ `test_un_fait_extrait_arrive_en_relecture` |
| 10 | `Agir` sur une alerte d'ouvrant → **refus de niveau 5**, journalisé (D8) | ✅ `test_agir_sur_un_ouvrant_est_refuse_au_niveau_cinq`, et côté carte `test_un_refus_de_niveau_cinq_laisse_lalerte_et_dit_pourquoi` |
| 11 | L'entretien saute s'il n'y a rien de neuf, et ne dépasse jamais 200 événements | ✅ `test_il_saute_quand_il_ny_a_rien_de_neuf`, `test_il_ne_depasse_jamais_son_plafond` |
| 12 | `ruff`, `lint-imports`, `pytest` verts | ✅ 297 tests add-on, 43 intégration, 68 carte ; 4 contrats de couches tenus |

Le point 8 demande de vraies soirées : c'est le seul que la vraie maison peut
juger.

---

# Partie F — Ce dont j'ai besoin de toi

> **Q1 — Faut-il annoncer sur une enceinte ?** §5 ne le demande pas, donc je ne
> le construis pas. Mais un rappel de coucher que personne ne regarde n'est pas
> un rappel. Si tu veux qu'il sorte sur un `media_player`, dis-le : c'est un
> provider à ajouter et une option, pas une refonte — et ça reste du niveau 2,
> donc encadré par §9.

> **Q2 — Les capteurs.** As-tu déjà des *template sensors* pour le contexte
> « coucher », les ouvrants regroupés, ou l'absence ? Sinon je livre un jeu de
> départ à coller. Et quelles sont les entités de tes huit ouvrants — §5 les
> mentionne mais ne les nomme pas.

> **Q3 — Les heures de silence.** 22 h 30 – 7 h 00 me paraît juste pour une
> maison avec un enfant. Dis-moi si tu vois autrement, et si le rappel de
> coucher doit lui-même pouvoir parler après 22 h 30 — moi je pense que oui,
> c'est tout son intérêt.

Aucune de ces réponses ne bloque le code, sauf **Q1**, qui ajouterait une pièce.
Dis-moi si tu valides D1 à D8, et je code P4.

---

# Partie G — Ce qui a bougé pendant l'écriture

Comme en P1 (§16), P2 (partie F) et P3 (partie F) : ce qui est parti d'ici et
ce que le code a fait à la place, avec la raison.

## G1. La détection ne suffisait pas — il fallait aussi livrer les capteurs

D1 disait « je livre un jeu de départ documenté ». C'est
[`docs/P4-CAPTEURS.md`](P4-CAPTEURS.md), et il est plus long que prévu : les
quatre capteurs de §5, F5, plus le contexte « coucher » sans lequel le rappel
de §11 n'existe pas. Il documente aussi comment vérifier chacun dans les outils
de développement — parce qu'une règle qu'on ne sait pas tester ne sera jamais
corrigée.

## G2. `Agir` a demandé un point d'entrée public sur l'arbitre

D8 finissait par « rien dans P4 ne touche à `kernel/autonomy.py` ni à
`engine/arbiter.py` ». La moitié tient : le registre des niveaux n'a pas bougé
d'une ligne. L'arbitre, si — d'une méthode :

```python
async def agir_hors_conversation(self, acte, *, libelle, justification,
                                 contexte, reference, emettre) -> ResultatOutil:
    return await self._appliquer(acte, ...)   # le même chemin, exactement
```

L'alternative était d'exprimer les actions d'une alerte comme des **appels
d'outil** (`commander_lumiere`), qui passaient déjà par `executer()`. Elle a
été écartée pour une raison précise : il n'existe aucun outil `cover`, donc un
`cover.close_cover` déclaré dans une règle n'aurait pas été *refusé au niveau
5* — il aurait été « outil inconnu ». Le point 10 de la recette serait devenu
invérifiable, et la garantie de §9.1 (« journalisée avec sa justification,
acceptée ou refusée ») aurait été perdue là où elle compte le plus.

Le corps de la méthode est un `return await self._appliquer(...)` : aucune
logique de décision n'a été ajoutée, et `tests/test_invariants.py` continue de
prouver que l'arbitre reste le seul appelant de `appeler_service`.

## G3. Un quatrième statut de fait : `rejected`

C.3 prévoyait `active | superseded | needs_review`. Un fait **refusé à la
relecture** n'est aucun des trois : il n'est pas en vigueur, il n'attend plus,
et il n'a été contredit par rien.

Le confondre avec `superseded` coûtait précisément ce que §4 cherche à éviter :
l'entretien de la nuit suivante aurait reproposé le même fait, et Guillaume
l'aurait refusé de nouveau, indéfiniment. `rejected` est ce qui permet à
`observer_fait` de reconnaître une question déjà tranchée. §4 est tenu — le
fait est toujours en base, il ne resservira simplement jamais.

C'est aussi ce statut qu'utilise l'expiration de H59 : au bout de quatorze
jours, un fait à relire que personne n'a ouvert bascule en `rejected` plutôt
que de disparaître.

## G4. §12 se contredisait : deux refus tuaient une règle définitivement

La boucle de C.2, telle qu'elle était écrite :

```
score = 0,5 au départ    rejected → −0,25    score < 0,2 → ne remonte plus
                         3 refus → sourdine de 30 jours
```

Deux refus depuis 0,5 amènent le score à 0,0. La règle cesse alors
d'apparaître — donc personne ne peut plus l'accepter — donc le score ne
remontera jamais. **La règle des trois refus n'était pas atteignable**, et le
plancher, censé être un amortissement, était une condamnation à perpétuité.

Le code fait converger les deux chemins :

```python
if refus >= REFUS_AVANT_SOURDINE or valeur < SEUIL_REMONTEE:
    return Score(score=SEUIL_REMONTEE, refus=0,
                 sourdine_jusqua=maintenant + DUREE_SOURDINE)
```

Un refus qui crève le plancher vaut lui aussi une sourdine de trente jours,
après quoi la règle revient **à l'essai**, au niveau du plancher. Aucun chiffre
de §12 n'a été changé ; c'est leur interaction qui est corrigée. Les deux
règles restent vivantes et testées séparément : depuis un score élevé, c'est
bien le compteur de refus qui déclenche au troisième
(`test_trois_refus_valent_un_ne_plus_me_le_dire`).

Le point 5 de la recette disait « refusée trois fois » ; depuis le score
initial, c'est **deux**. La recette a été corrigée, pas le code.

## G5. Deux seuils de confiance au lieu d'un

D6 posait un seuil unique à 0,25. Mais `maturité(1) = 0,29` : **une seule
soirée observée passait déjà le seuil**, et suffisait à déclencher un rappel de
coucher. C'est exactement le rappel non pertinent que §11 demande d'éviter.

`kernel/facts.py` a donc deux seuils, qui ne répondent pas à la même question :

| Seuil | Question | Valeur |
|---|---|---|
| `SEUIL_UTILE` | Faut-il encore garder ce fait sous la main ? | 0,25 (celui de D6) |
| `SEUIL_PROPOSITION` | Luna a-t-elle le droit de bâtir un rappel là-dessus ? | 0,50 |

Trois soirs identiques valent 0,65 et franchissent le second ; un seul vaut
0,29 et ne franchit que le premier. Le point 7 de la recette, qui porte sur
l'oubli, continue de viser 0,25.

## G6. Les alertes de nuit sont différées, pas jetées

H58 disait « une ampoule oubliée dans le garage n'a pas le droit de parler la
nuit ». Le point 3 de la recette disait « rien avant 7 h » — ce qui suppose que
quelque chose arrive **à** 7 h.

Une alerte retenue par les heures de silence est donc gardée en attente, et
l'ordonnanceur la reprend toutes les cinq minutes. Si la condition a cessé
entre-temps — la lampe éteinte à 6 h —, elle ne sort jamais. C'est la
différence entre se taire et oublier.

## G7. L'entretien nocturne rend ses faits par un appel d'outil forcé

D3 disait « sortie structurée (`output_config.format`) ». Le code utilise un
**appel d'outil forcé** (`tool_choice`) dont le schéma d'entrée est la liste de
faits attendue. La garantie de typage est la même — le modèle ne peut rendre
que la forme déclarée, `predicat` étant un `enum` — et c'est une surface d'API
que le reste du projet exerce déjà, donc déjà couverte par les tests
enregistrés de `test_claude.py`.

L'`enum` limite le modèle à `preference_eclairage`, `preference_temperature` et
`fait_declare`. `heure_de_coucher` en est **exclu** : c'est un fait mesuré, et
un modèle qui le déduirait d'une phrase le déduirait moins bien que
l'observateur. `nightly.py` refiltre côté Luna, pour ne pas dépendre de la
seule obéissance du modèle.

## G8. Deux défauts trouvés par les tests, dans du code de P1

- **`alert_cleared` lisait `evt.id`** au lieu de `evt.alert_id`. Écrit en P1
  quand rien n'émettait l'événement, donc invisible : une alerte levée ne
  serait jamais partie du tiroir, sans le moindre message.
- **L'ordre de supplantation** : `observer_fait` insérait le nouveau fait avant
  de démettre l'ancien, ce que l'index unique partiel refuse. C'est exactement
  le rôle de cet index — il a fait son travail avant même la première nuit.

Un troisième, trouvé en montant l'aperçu : la carte plantait au montage quand
on l'insère sans `setConfig`. Lovelace configure toujours avant d'insérer, mais
rien ne l'impose, et l'erreur n'apparaissait qu'en console.

## G9. Le panneau d'identité n'est plus fait d'alertes

La feuille de style de P1 réutilisait la classe `.alerte` pour les lignes du
panneau « Qui parle ». Sans conséquence visuelle, mais faux : une ligne
« Clara — voix enregistrée » n'est pas une alerte. La boîte est devenue
`.bloc`, `.alerte` et `.fait` ne portent plus que ce qui les distingue.

## G10. Ce que P4 continue de ne pas faire

La partie D est tenue intégralement, et vérifiée :

- Aucune notification mobile, aucune annonce sur enceinte (Q1 reste ouverte).
- Aucune automatisation créée ni modifiée par Luna.
- Aucune boucle de scrutation : `providers/home.py` publie chaque
  `state_changed` sur le bus, et c'est la seule source des observateurs (H63).
- Aucun accès du moteur de veille au cerveau — vérifié par les contrats de
  couches, pas par relecture.
- Aucune action déclenchée seule : `Agir` passe par l'arbitre (G2).
- Aucun fait de conversation en vigueur sans relecture.
- Aucune donnée d'habitude hors de Nova. Seuls des **extraits de conversation**
  partent vers l'API, une fois par nuit.
