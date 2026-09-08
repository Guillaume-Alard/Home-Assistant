"""Luna — le cerveau, add-on Home Assistant.

Quatre couches, verrouillées par import-linter (voir .importlinter) :

    kernel      L0  contrats, schémas, bus, réglages, erreurs, autonomie
    providers   L1  Claude, Home Assistant, SQLite          — n'importe que L0
    engine      L2  orchestrateur, arbitre, outils          — n'importe que L0
    interfaces  L3  relais, HTTP, amorçage                  — importe tout
"""

__version__ = "0.1.0"
