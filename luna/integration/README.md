# Intégration Luna — le nerf

Le troisième livrable, celui que le cahier des charges n'avait pas prévu
(décision A1). Une carte Lovelace ne peut parler qu'à Home Assistant : pour
qu'un message `luna/…` existe sur son API WebSocket, il faut appeler
`websocket_api.async_register_command` **dans le processus Python de Home
Assistant**, ce qu'un add-on — conteneur séparé — ne peut pas faire.

Elle fait trois choses, et rien d'autre :

1. tenir une connexion au relais de l'add-on (`client.py`) ;
2. enregistrer les commandes `luna/*` appelées par la carte (`websocket.py`) ;
3. exposer `binary_sensor.luna_en_ligne`, que la carte lit pour détecter le
   mode dégradé sans le moindre aller-retour (`binary_sensor.py`).

Aucune logique métier. Si une règle de §9 se retrouve un jour ici, c'est que
quelque chose a dérivé.

## Le contexte, et pourquoi il compte

`websocket.py` construit le contexte de chaque requête à partir de
`connection.user` — l'utilisateur Home Assistant réellement authentifié.
La carte ne le fournit pas et ne peut pas l'influencer : c'est ce qui empêche
un client de se déclarer administrateur. L'add-on résout ensuite le profil Luna
à partir de ce nom (décision A6), avec sa propre table : une seule source de
vérité, des deux côtés du relais.

## Installer

Copier `custom_components/luna/` dans le `/config/` de Nova, redémarrer Home
Assistant, puis **Paramètres → Appareils et services → Ajouter une intégration
→ Luna**. Renseigner l'hôte (`local-luna` sur HAOS), le port (8099) et le secret
du relais — le même que l'option `relay_secret` de l'add-on.

Le formulaire ouvre vraiment la connexion avant d'enregistrer : une entrée qui
s'installerait sans avoir joint l'add-on donnerait un capteur éteint et aucune
explication, exactement l'échec silencieux que §8 interdit.

## Tester

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install homeassistant pytest-homeassistant-custom-component
pytest -q
```

17 tests dans une **vraie** instance de Home Assistant, montée en mémoire par
`pytest-homeassistant-custom-component` : le formulaire, le chargement, le
capteur, les commandes WebSocket et le mode dégradé passent par le vrai code de
HA. Seul le relais de l'add-on est simulé — le cerveau a ses 151 tests à lui.
