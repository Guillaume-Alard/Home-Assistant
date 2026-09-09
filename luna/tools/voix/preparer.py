#!/usr/bin/env python3
"""Bâtit un jeu de données LJSpeech à partir du corpus et de ses blocs audio.

Quand la source est une plateforme TTS, on connaît déjà le texte : les deux
étapes les plus ingrates de la chaîne — découper à l'oreille puis transcrire
avec Whisper — n'ont plus lieu d'être. On découpe aux silences, et on aligne
sur les lignes du bloc **dans l'ordre**. Si les comptes tombent juste, la
transcription est exacte par construction ; s'ils ne tombent pas juste, le
script le dit au lieu de produire un jeu de données silencieusement décalé.

Un décalage d'une ligne empoisonne tout ce qui suit : le modèle apprendrait à
prononcer chaque phrase avec le texte de la précédente. C'est la seule erreur
de cette chaîne qui ne se voit pas avant d'écouter le modèle entraîné.

    ./preparer.py --groupes 5000   # comment répartir les blocs par requête
    ./preparer.py --verifier       # les comptes tombent-ils juste ?
    ./preparer.py                  # écrit dataset/

Arborescence attendue :

    corpus.txt
    blocs/bloc-01.wav              un fichier pour le bloc 01
    blocs/bloc-01-06.wav           ou un fichier pour les blocs 01 à 06

La plupart des plateformes acceptent plusieurs milliers de caractères par
requête : synthétiser plusieurs blocs d'un coup divise le nombre d'allers-retours
par sept. Le prix est qu'un mauvais découpage invalide tout le groupe au lieu
d'un seul bloc — `--groupes` propose une répartition, à toi de choisir la taille.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).parent
CORPUS = RACINE / "corpus.txt"
BLOCS = RACINE / "blocs"
SORTIE = RACINE / "dataset"

#: Piper veut du mono 22 050 Hz 16 bits. Le reste est du confort de mesure.
FREQUENCE = 22050
#: Marge autour de chaque segment : sans elle, on rogne l'attaque des
#: consonnes sourdes, que le détecteur de silence range du côté du silence.
MARGE = 0.12
#: Un segment plus court n'est pas une phrase, c'est un souffle ou un clic.
DUREE_MINIMALE = 0.35


def lire_corpus() -> list[tuple[int, list[str]]]:
    """Rend [(numéro de bloc, [phrases])], dans l'ordre du fichier."""
    blocs: list[tuple[int, list[str]]] = []
    for ligne in CORPUS.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if entete := re.match(r"^#\s*BLOC\s+(\d+)", ligne):
            blocs.append((int(entete.group(1)), []))
        elif ligne and not ligne.startswith("#"):
            if not blocs:
                sys.exit("corpus.txt : une phrase apparaît avant le premier « # BLOC ».")
            blocs[-1][1].append(ligne)
    return blocs


def ffmpeg(*arguments: str) -> str:
    """Lance ffmpeg et rend sa sortie d'erreur — c'est là qu'il parle."""
    acheve = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return acheve.stderr


def duree(fichier: Path) -> float:
    sortie = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(fichier)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(sortie)


def normaliser(source: Path, cible: Path) -> None:
    """Un seul passage de loudnorm, sur le bloc entier.

    Normaliser chaque segment séparément donnerait à chacun son propre niveau :
    le modèle apprendrait des sautes de volume entre phrases voisines.
    """
    ffmpeg("-y", "-i", str(source), "-ac", "1", "-ar", str(FREQUENCE),
           "-sample_fmt", "s16", "-af", "loudnorm=I=-23:LRA=7:TP=-2",
           str(cible))


def segments(fichier: Path, seuil_db: int, silence_minimal: float) -> list[tuple[float, float]]:
    """Les plages de parole : le complément des silences détectés."""
    journal = ffmpeg("-i", str(fichier),
                     "-af", f"silencedetect=noise={seuil_db}dB:d={silence_minimal}",
                     "-f", "null", "-")
    silences: list[tuple[float, float]] = []
    debut: float | None = None
    for ligne in journal.splitlines():
        if trouve := re.search(r"silence_start:\s*(-?[\d.]+)", ligne):
            debut = max(0.0, float(trouve.group(1)))
        elif trouve := re.search(r"silence_end:\s*([\d.]+)", ligne):
            if debut is not None:
                silences.append((debut, float(trouve.group(1))))
                debut = None
    totale = duree(fichier)
    if debut is not None:
        silences.append((debut, totale))

    plages: list[tuple[float, float]] = []
    curseur = 0.0
    for creux_debut, creux_fin in silences:
        if creux_debut - curseur >= DUREE_MINIMALE:
            plages.append((curseur, creux_debut))
        curseur = creux_fin
    if totale - curseur >= DUREE_MINIMALE:
        plages.append((curseur, totale))
    return plages


def decouper(source: Path, debut: float, fin: float, cible: Path) -> None:
    ffmpeg("-y", "-ss", f"{max(0.0, debut - MARGE):.3f}", "-to", f"{fin + MARGE:.3f}",
           "-i", str(source), "-ac", "1", "-ar", str(FREQUENCE),
           "-sample_fmt", "s16", str(cible))


def fichiers_de_blocs(blocs: list[tuple[int, list[str]]]) -> tuple[list[tuple[Path, list[str]]], list[int]]:
    """Associe chaque fichier audio aux phrases qu'il doit contenir.

    Un fichier vaut soit un bloc (`bloc-07.wav`), soit une plage de blocs
    consécutifs (`bloc-07-12.wav`). Dans le second cas les phrases sont
    concaténées dans l'ordre des blocs — le même ordre que celui du collage
    dans la plateforme.
    """
    par_numero = dict(blocs)
    trouves: list[tuple[Path, list[str], set[int]]] = []
    for fichier in sorted(BLOCS.glob("bloc-*.wav")):
        nom = re.match(r"^bloc-(\d+)(?:-(\d+))?$", fichier.stem)
        if nom is None:
            print(f"nom ignoré : {fichier.name} (attendu bloc-nn.wav ou bloc-nn-mm.wav)")
            continue
        premier = int(nom.group(1))
        dernier = int(nom.group(2) or nom.group(1))
        couverts = list(range(premier, dernier + 1))
        if inconnus := [n for n in couverts if n not in par_numero]:
            print(f"nom ignoré : {fichier.name} — bloc(s) inexistant(s) : {inconnus}")
            continue
        phrases = [phrase for n in couverts for phrase in par_numero[n]]
        trouves.append((fichier, phrases, set(couverts)))

    vus: set[int] = set()
    for fichier, _, couverts in trouves:
        if chevauche := sorted(vus & couverts):
            sys.exit(f"{fichier.name} recouvre des blocs déjà pris : {chevauche}")
        vus |= couverts

    return [(f, p) for f, p, _ in trouves], sorted(set(par_numero) - vus)


def proposer_groupes(blocs: list[tuple[int, list[str]]], plafond: int) -> None:
    """Répartit les blocs en requêtes tenant sous la limite de la plateforme."""
    marge = int(plafond * 0.94)  # de quoi absorber un retour à la ligne de plus
    groupes: list[list[tuple[int, list[str]]]] = []
    courant: list[tuple[int, list[str]]] = []
    taille = 0
    for numero, phrases in blocs:
        n = sum(len(p) + 1 for p in phrases)
        if courant and taille + n > marge:
            groupes.append(courant)
            courant, taille = [], 0
        courant.append((numero, phrases))
        taille += n
    if courant:
        groupes.append(courant)

    print(f"{len(groupes)} requêtes pour {len(blocs)} blocs, sous {plafond} caractères :\n")
    for membres in groupes:
        premier, dernier = membres[0][0], membres[-1][0]
        taille = sum(len(p) + 1 for _, ph in membres for p in ph)
        nb = sum(len(ph) for _, ph in membres)
        nom = f"bloc-{premier:02d}-{dernier:02d}.wav" if premier != dernier else f"bloc-{premier:02d}.wav"
        print(f"  {nom:20s} blocs {premier:02d}–{dernier:02d}   {taille:5d} car   {nb:3d} phrases")
    print(
        "\nColle les phrases de ces blocs à la suite, SANS les lignes « # BLOC nn » :\n"
        "la plateforme les lirait à voix haute, ce qui ajouterait un segment et\n"
        "décalerait tout l'alignement du groupe."
    )


def main() -> int:
    analyse = argparse.ArgumentParser(description=__doc__)
    analyse.add_argument("--verifier", action="store_true",
                         help="compte les segments sans rien écrire")
    analyse.add_argument("--groupes", type=int, metavar="LIMITE",
                         help="propose une répartition des blocs par requête, "
                              "pour une limite de LIMITE caractères")
    analyse.add_argument("--seuil", type=int, default=-35,
                         help="seuil de silence en dB (défaut : -35)")
    analyse.add_argument("--silence", type=float, default=0.4,
                         help="durée minimale d'un silence, en secondes (défaut : 0.4)")
    options = analyse.parse_args()

    for outil in ("ffmpeg", "ffprobe"):
        if shutil.which(outil) is None:
            sys.exit(f"{outil} est introuvable dans le PATH.")

    blocs = lire_corpus()
    if options.groupes:
        proposer_groupes(blocs, options.groupes)
        return 0
    if not BLOCS.is_dir():
        sys.exit(f"Dossier introuvable : {BLOCS}")

    travail = RACINE / ".normalise"
    travail.mkdir(exist_ok=True)
    if not options.verifier:
        (SORTIE / "wav").mkdir(parents=True, exist_ok=True)

    lignes_meta: list[str] = []
    index = 0
    desaccords: list[str] = []
    # Le corpus se synthétise bloc par bloc, sur plusieurs séances : un bloc pas
    # encore enregistré est l'état normal, pas une anomalie. Les confondre avec
    # les vrais écarts noierait les deux qui comptent sous quarante qui ne
    # comptent pas.
    fichiers, manquants = fichiers_de_blocs(blocs)

    for source, phrases in fichiers:
        normalise = travail / source.name
        normaliser(source, normalise)
        plages = segments(normalise, options.seuil, options.silence)

        marque = "ok" if len(plages) == len(phrases) else "ÉCART"
        print(f"{source.stem:14s} : {len(plages):3d} segments / {len(phrases):3d} phrases  {marque}")
        if len(plages) != len(phrases):
            desaccords.append(
                f"{source.name} : {len(plages)} segments pour {len(phrases)} phrases"
            )
            continue
        if options.verifier:
            continue

        for (debut, fin), phrase in zip(plages, phrases):
            index += 1
            nom = f"{index:05d}"
            decouper(normalise, debut, fin, SORTIE / "wav" / f"{nom}.wav")
            lignes_meta.append(f"{nom}|{phrase}")

    if manquants:
        liste = ", ".join(f"{n:02d}" for n in manquants)
        print(f"\n{len(manquants)} bloc(s) pas encore enregistré(s) : {liste}")

    if desaccords:
        print("\n── Blocs à reprendre ──")
        for ligne in desaccords:
            print(f"  {ligne}")
        print(
            "\nTrop de segments : le seuil coupe à l'intérieur des phrases — essaie\n"
            "  --seuil -40, ou --silence 0.6.\n"
            "Trop peu : deux phrases sont collées — essaie --seuil -30, ou\n"
            "--silence 0.3. En dernier recours, resynthétise le bloc en marquant\n"
            "une pause plus nette entre les phrases.\n"
            "Une ligne « # BLOC nn » collée par mégarde dans la plateforme donne\n"
            "exactement un segment de trop : elle est lue à voix haute.\n"
            "Un fichier en écart est ignoré : mieux vaut un corpus plus court qu'un\n"
            "corpus décalé."
        )

    if not options.verifier and lignes_meta:
        (SORTIE / "metadata.csv").write_text("\n".join(lignes_meta) + "\n", encoding="utf-8")
        print(f"\n{len(lignes_meta)} extraits écrits dans {SORTIE}/")
        print("Écoute-en cinq au hasard avant d'entraîner : c'est le seul moyen")
        print("de repérer un décalage texte/audio, et il ne se voit pas autrement.")

    return 1 if desaccords else 0


if __name__ == "__main__":
    raise SystemExit(main())
