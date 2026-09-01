# Ajouter un outil au cerveau

Les outils du LLM vivent dans [`core/app/brain/toolbox.py`](../core/app/brain/toolbox.py).
Règle d'or, non négociable : **un outil de lecture est libre ; un outil d'action
n'appelle jamais Nova directement — il passe par le moteur d'actions**
(`ActionEngine`), qui applique la liste blanche, la double confirmation et le
journal. Le test `tests/test_invariant.py` casse si cette règle est violée.

## La recette (exemple : un outil de lecture)

1. **Déclare l'outil** dans `Toolbox.specs()` — l'ordre de la liste doit rester
   stable (le cache de prompt en dépend), ajoute donc en fin de liste :

   ```python
   {
       "name": "qualite_air",
       "description": "Lit les capteurs de qualité d'air (CO2, particules) par pièce.",
       "input_schema": {
           "type": "object",
           "properties": {"zone": {"type": "string"}},
       },
   },
   ```

2. **Implémente le handler** — le nom de méthode dérive du nom de l'outil :

   ```python
   async def _tool_qualite_air(self, args, _utterance, _source):
       ...lecture via self._ha (get_state, states_snapshot…)...
       return _compact(resultat), False       # (contenu, is_error)
   ```

3. **Libellé d'activité** (facultatif) : ajoute une entrée à `ACTIVITY_LABELS`
   pour que l'UI affiche « consulte les capteurs… » pendant l'appel.

4. Ajoute un test dans `core/tests/test_toolbox.py`, puis `pytest`.

## Pour un outil d'ACTION

Même recette, mais le handler invoque le moteur — jamais `self._ha.call_service` :

```python
async def _tool_mon_action(self, args, utterance, source):
    outcome = await self._engine.run_direct(          # ou self._engine.propose(...)
        "ha.turn_on", {"entity_ids": [...]},
        utterance=utterance, source=f"{source} (via LLM)",
    )
    return outcome.text, not outcome.ok
```

- `run_direct` n'accepte que les actions du registre marquées `direct` —
  ajouter une action au registre se fait dans `core/app/actions/executors.py`
  (avec validation stricte des paramètres et niveau de risque).
- Pour tout le reste : `self._engine.propose(...)` — l'action attendra
  l'approbation de Guillaume.
- Ne mets jamais un outil de déverrouillage/désarmement à disposition du LLM :
  ces actions sensibles se demandent à la voix (double confirmation) ou par
  proposition approuvée dans l'UI.

## Conseils

- Renvoie du JSON **compact et borné** (`_compact(...)[:6000]`) : chaque
  caractère part dans le contexte du modèle et se paie.
- Messages d'erreur en français, actionnables (« Pièce inconnue : … ») —
  le modèle les lit et rebondit dessus.
- Mets à jour le `SYSTEM_PROMPT` (`core/app/brain/llm.py`) si le nouvel outil
  change ce que Sentinel « sait faire ».
