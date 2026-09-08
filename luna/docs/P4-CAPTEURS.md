# Luna — P4 : les capteurs à coller

**À faire une fois, dans `configuration.yaml`, avant que la veille serve à
quelque chose.** Luna ne détecte rien elle-même : elle regarde des
`binary_sensor` que tu écris, et décide quoi en faire (§5, F5 — décision D1).

Ce partage n'est pas une contrainte, c'est ce qui rend la veille vérifiable :
tu vois chaque condition dans **Outils de développement → Modèle**, tu la
testes sans lire une ligne de Python, et elle continue de fonctionner même si
l'add-on Luna est arrêté.

---

## 1. Ce que tu dois adapter

Trois listes, en haut du fichier. Le reste se déduit.

| Nom | Ce que c'est | Où le trouver |
|---|---|---|
| `binary_sensor.ouvrants_maison` | Un groupe de tes huit ouvrants | Paramètres → Appareils → **Groupes**, type « capteur binaire », classe `opening` |
| `person.*` | Les personnes de la maison | Déjà là si tu as configuré Home Assistant |
| `alarm_control_panel.alarmo` | Ton panneau Alarmo | Déjà là |

---

## 2. Le contexte « coucher »

C'est le seul capteur que Luna **observe** plutôt que de simplement le
surveiller : son passage à `on` est ce qui construit l'habitude d'heure de
coucher (D2). Sans lui, pas de rappel de coucher — quoi qu'on écrive ailleurs.

```yaml
template:
  - binary_sensor:
      - name: "Contexte coucher"
        unique_id: luna_contexte_coucher
        device_class: running
        # Vrai quand la maison bascule pour la nuit. À toi de dire ce que
        # « bascule » veut dire chez toi : ici, la chambre s'allume après 21 h
        # alors que le séjour est éteint.
        state: >
          {{ is_state('light.chambre', 'on')
             and is_state('light.sejour', 'off')
             and today_at('21:00') <= now() }}
```

Option de l'add-on correspondante :

```yaml
observateurs:
  coucher: binary_sensor.luna_contexte_coucher
  coucher_profil: guillaume
```

> **Si tu n'as pas de quoi écrire cette condition**, un `input_boolean` que tu
> bascules toi-même le soir fait très bien l'affaire pour commencer. Vingt
> soirs suffisent à ce que Luna connaisse ton heure ; ensuite tu pourras
> remplacer l'interrupteur par une vraie condition.

---

## 3. Un ouvrant oublié la nuit

Le premier des deux livrables de §11.

```yaml
template:
  - binary_sensor:
      - name: "Ouvrant oublié la nuit"
        unique_id: luna_ouvrant_oublie
        device_class: problem
        delay_on: "00:02:00"   # deux minutes : on ferme rarement en une seconde
        state: >
          {{ is_state('binary_sensor.ouvrants_maison', 'on')
             and is_state('binary_sensor.luna_contexte_coucher', 'on') }}
```

```yaml
veille:
  - entite: binary_sensor.luna_ouvrant_oublie
    categorie: ouvrant
    niveau: warning
    message: "Un ouvrant est resté ouvert."
    raison: "La maison est en veille et un ouvrant est encore ouvert."
    silence: false        # il a le droit de parler après 22 h 30
    # Aucune action : fermer un ouvrant est de niveau 5, hors périmètre v1
    # (§9). Luna te le signale, elle ne le ferme pas.
```

---

## 4. Une lumière oubliée au coucher

```yaml
template:
  - binary_sensor:
      - name: "Lumière oubliée au coucher"
        unique_id: luna_lumiere_oubliee
        device_class: problem
        delay_on: "00:05:00"
        state: >
          {{ is_state('binary_sensor.luna_contexte_coucher', 'on')
             and expand(states.light)
                 | selectattr('state', 'eq', 'on')
                 | rejectattr('entity_id', 'in', ['light.chambre'])
                 | list | count > 0 }}
```

```yaml
veille:
  - entite: binary_sensor.luna_lumiere_oubliee
    categorie: eclairage
    niveau: info
    message: "Une lumière est restée allumée."
    raison: "Tout le monde est couché et il reste de la lumière."
    actions:
      - domain: light
        service: turn_off
        target:
          entity_id: light.sejour
```

Ici le bouton **Agir** fait vraiment quelque chose : éteindre une lampe est du
niveau 2, Luna a le droit. Elle passe quand même par l'arbitre, comme pour une
demande à la voix — c'est le même chemin, pas un raccourci (D8).

---

## 5. L'alarme non armée en l'absence de tout le monde

```yaml
template:
  - binary_sensor:
      - name: "Alarme non armée en absence"
        unique_id: luna_alarme_non_armee
        device_class: problem
        delay_on: "00:10:00"
        state: >
          {{ states.person | selectattr('state', 'eq', 'home') | list | count == 0
             and is_state('alarm_control_panel.alarmo', 'disarmed') }}
```

```yaml
veille:
  - entite: binary_sensor.luna_alarme_non_armee
    categorie: alarme
    niveau: critical      # traverse les heures de silence
    message: "L'alarme n'est pas armée et la maison est vide."
    raison: "Personne à la maison depuis dix minutes, Alarmo est désarmé."
```

Pas d'action non plus : armer l'alarme est de niveau 5 (§9). Luna prévient,
c'est tout.

---

## 6. Le rappel de coucher

C'est le second livrable de §11, et le seul qui dépende d'une **habitude**.

```yaml
template:
  - binary_sensor:
      - name: "Heure de se coucher"
        unique_id: luna_heure_de_se_coucher
        device_class: running
        # Vrai quand il est tard et que personne n'est encore couché.
        state: >
          {{ today_at('22:45') <= now()
             and is_state('binary_sensor.luna_contexte_coucher', 'off') }}
```

```yaml
veille:
  - entite: binary_sensor.luna_heure_de_se_coucher
    categorie: coucher
    niveau: info
    message: "Il est l'heure d'aller te coucher."
    raison: "Tu te couches plutôt vers {valeur}, il est {heure}."
    fait: heure_de_coucher    # ← la clé de tout
    silence: false
```

**`fait: heure_de_coucher` est ce qui rend le rappel pertinent.** Sans habitude
observée — ou avec une habitude devenue trop incertaine — la règle ne dit rien
du tout. Un rappel générique à heure fixe, c'est une automatisation Home
Assistant ; ça ne vaut pas la peine d'y mêler Luna.

Trois jetons sont disponibles dans `raison` : `{valeur}` (l'heure observée),
`{heure}` (l'heure qu'il est) et `{observations}` (le nombre de soirs).

---

## 6 bis. Faire dire les alertes à voix haute

Éteint par défaut. Pour l'allumer, une seule ligne compte — le reste a des
valeurs saines :

```yaml
annonce:
  enceinte: media_player.salon    # ← sans elle, rien n'est jamais annoncé
  moteur: tts.piper               # l'entité TTS de ton pipeline Assist
  niveaux: [warning, critical]    # une `info` s'affiche sans couper la pièce
  silence: true                   # ne réveille pas la maison entre 22 h 30 et 7 h
```

Deux choses à savoir avant de l'allumer :

- **`silence: true` couvre aussi les alertes `critical`.** Elles apparaissent
  dans le tiroir à 3 h du matin, elles ne réveillent personne. Si tu veux
  qu'une alerte d'alarme te sorte du lit, mets `silence: false` — c'est un
  choix, pas un défaut.
- **Une règle en `silence: false` parle aussi à voix haute la nuit.** C'est
  voulu : le rappel de coucher qui s'afficherait à 23 h 20 sans jamais se dire
  ne réglerait rien.

Pour couper l'annonce sur une règle précise, ou la forcer sur une `info` —
typiquement le rappel de coucher :

```yaml
veille:
  - entite: binary_sensor.luna_heure_de_se_coucher
    niveau: info
    annonce: true      # ← malgré le niveau `info`
    silence: false
    ...
```

Le texte annoncé est le `message`, **jamais la `raison`** : la raison contient
l'heure, donc un texte neuf à chaque fois, donc une synthèse vocale refaite à
chaque fois. Le message, lui, vient mot pour mot d'ici — Home Assistant le
retrouve dans son cache. Écris-le pour l'oreille.

---

## 7. Vérifier que ça marche

1. **Outils de développement → Modèle** : colle la condition d'un capteur et
   regarde si elle rend `True` quand tu t'y attends.
2. **Outils de développement → États** : force `binary_sensor.luna_ouvrant_oublie`
   à `on`. L'alerte doit apparaître dans le tiroir de la carte en une seconde.
3. **Journal de l'add-on** : au démarrage il annonce le nombre de capteurs
   surveillés. `0` veut dire qu'aucune règle n'est déclarée dans les options.
4. **Le rappel de coucher** demande de la patience : il faut trois soirs pour
   que l'habitude passe le seuil, une vingtaine pour qu'elle soit sûre. Tu peux
   suivre sa progression dans le tiroir, ou par
   `curl http://<add-on>:8099/profile/guillaume/patterns`.

---

## 8. Ce que Luna ne fera pas avec ces capteurs

- Elle n'en **écrit** aucun, et n'en modifie aucun. Proposer des correctifs de
  configuration, c'est la gardienne — phase 5.
- Elle ne les fait pas lire par le modèle de langage. Le moteur de veille n'a
  aucun accès au cerveau (§9.2) : ce n'est pas une consigne de prompt, c'est
  une propriété du code.
- Elle n'envoie **aucune notification mobile**. §5 ne le demande pas.
- Elle ne parle sur une enceinte que si tu lui en déclares une (§6 bis), et
  passe alors par l'arbitre comme pour tout le reste : `tts.speak` est du
  niveau 2, journalisé, avec les droits d'un invité et pas davantage.
