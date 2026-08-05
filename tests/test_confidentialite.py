"""Le dépôt est PUBLIC. Ces tests empêchent d'y republier ce qui doit rester
local : documents de stratégie, notes de travail, données de jeu personnelles.

Ils ne regardent pas le disque mais ce que git SUIT réellement — un fichier
présent en local mais ignoré est normal ; le même fichier indexé ne l'est pas.
Le jour où quelqu'un fait `git add -f` ou vide le .gitignore, la CI rougit.
"""
from __future__ import annotations

import hashlib
import re
import subprocess

import pytest
from conftest import RACINE


def suivis() -> list[str]:
    """Fichiers réellement suivis par git (pas le contenu du disque)."""
    r = subprocess.run(["git", "ls-files"], cwd=RACINE,
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("hors dépôt git")
    return [f for f in r.stdout.splitlines() if f.strip()]


# Fichiers internes : stratégie, objectifs, notes. Rien de tout cela n'a à
# être publié — le dépôt ne montre que le produit.
INTERNES = re.compile(
    r"^(MASTERPROMPT\.md|NOTES\.md|TODO\.md|notes/|prive/)"
    r"|\.(local|perso)\.md$", re.I)

# Données de jeu personnelles (phase 5) : ce que le propriétaire a réellement
# joué. Publier ça revient à publier un registre de comportement de jeu.
PERSONNELLES = re.compile(r"(^|/)(mes_grilles|paris|mes_)", re.I)


def test_aucun_document_interne_n_est_suivi():
    fautifs = [f for f in suivis() if INTERNES.search(f)]
    assert not fautifs, (
        f"documents internes suivis par git : {fautifs} — ils doivent rester "
        "locaux (voir .gitignore)")


def test_aucune_donnee_de_jeu_personnelle_n_est_suivie():
    fautifs = [f for f in suivis() if PERSONNELLES.search(f)]
    assert not fautifs, (
        f"données de jeu personnelles suivies par git : {fautifs} — elles "
        "n'ont rien à faire dans un dépôt public")


def test_le_gitignore_couvre_toujours_les_documents_internes():
    """Garde-fou du garde-fou : si quelqu'un vide le .gitignore, ces règles
    doivent réapparaître avant qu'un fichier interne ne se glisse dedans."""
    contenu = (RACINE / ".gitignore").read_text(encoding="utf-8")
    for motif in ("MASTERPROMPT.md", "notes/", "mes_grilles"):
        assert motif in contenu, f"règle manquante dans .gitignore : {motif}"


# Identifiants bannis, stockés en EMPPREINTES et non en clair : une première
# version de ce test listait les noms littéralement — et publiait donc, dans
# un dépôt public, exactement ce qu'elle prétendait interdire. Le contrôle
# compare l'empreinte de chaque mot rencontré à celles-ci.
# Limite assumée : un nom courant reste devinable par force brute sur un
# dictionnaire. Le but n'est pas de résister à une attaque ciblée, c'est que
# le dépôt ne NOMME personne.
EMPREINTES_INTERDITES = frozenset({
    "d38681074467c0bc147b17a9a12b9efa",
    "96561a8ade45118d7872c311330b94be",
    "faf5748b85f02aa56dc0b396c27354dc",
    "bc008f65d4df99948c7dd9fb56f1ea5c",
})

MOT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def _empreinte(mot: str) -> str:
    normalise = re.sub(r"[-_]", "", mot).lower()
    return hashlib.sha256(normalise.encode()).hexdigest()[:32]


def test_aucun_fichier_suivi_ne_nomme_le_proprietaire():
    """Le dépôt ne doit relier le projet à aucune personne : ni nom réel, ni
    compte tiers. Seul le pseudo du dépôt est admis."""
    fautifs = []
    for f in suivis():
        chemin = RACINE / f
        if not chemin.is_file() or chemin.suffix.lower() in (".zip", ".pyc"):
            continue
        try:
            texte = chemin.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue          # binaire : couvert par l'exclusion d'extension
        for mot in MOT.findall(texte):
            if _empreinte(mot) in EMPREINTES_INTERDITES:
                fautifs.append(f)
                break
    assert not fautifs, f"identité personnelle présente dans : {fautifs}"
