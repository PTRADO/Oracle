"""Tests du parser FDJ sur des EXTRAITS RÉELS (tests/fixtures/).

Chaque fixture contient l'en-tête authentique d'une époque FDJ plus 3 tirages
du début et 3 de la fin du fichier — c'est là que se cachent les variantes de
format (rapports « 12000000 » aux débuts, « 6000000,00 » aujourd'hui) et les
encodages mêlés (utf-8 et latin-1 selon les archives).

Les valeurs attendues ci-dessous ont été relevées sur les archives FDJ
téléchargées le 04/08/2026, pas déduites du code.
"""
from __future__ import annotations

import pytest
from conftest import FIXTURES, fixture_texte

from oracle import JEUX, _decoder, parser_csv

# (fixture, jeu, n_tirages, 1er tirage, dernier tirage)
EPOQUES = [
    ("loto_2017", "loto", 6,
     ("2017-03-06", [3, 6, 10, 26, 41], [9]),
     ("2019-02-25", [7, 37, 38, 44, 48], [7])),
    ("loto_201902", "loto", 6,
     ("2019-02-27", [1, 4, 14, 19, 34], [2]),
     ("2019-11-02", [1, 13, 16, 18, 41], [3])),
    ("loto_201911", "loto", 6,
     ("2019-11-06", [23, 27, 42, 43, 44], [10]),
     ("2026-08-03", [5, 25, 37, 41, 42], [10])),
    ("euromillions_4", "euromillions", 6,
     ("2016-09-27", [6, 9, 13, 39, 41], [2, 12]),
     ("2019-02-26", [3, 15, 29, 35, 47], [3, 4])),
    ("euromillions_201902", "euromillions", 6,
     ("2019-03-01", [6, 9, 19, 26, 31], [11, 12]),
     ("2020-01-31", [13, 18, 20, 23, 30], [2, 4])),
    ("euromillions_202002", "euromillions", 6,
     ("2020-02-04", [21, 23, 33, 35, 47], [6, 7]),
     ("2026-08-04", [25, 30, 34, 46, 50], [1, 12])),
]


@pytest.mark.parametrize("nom,jeu,n,premier,dernier", EPOQUES)
def test_epoque_parse_exactement(nom, jeu, n, premier, dernier):
    """Chaque époque réelle se parse, triée, avec les bonnes valeurs."""
    cfg = JEUX[jeu]
    tirages = parser_csv(cfg, fixture_texte(nom))
    assert len(tirages) == n

    for attendu, t in ((premier, tirages[0]), (dernier, tirages[-1])):
        date_iso, balls, bonus = attendu
        assert t["date"].isoformat() == date_iso
        assert list(t["balls"]) == balls
        assert list(t["bonus"]) == bonus


@pytest.mark.parametrize("nom,jeu,n,premier,dernier", EPOQUES)
def test_epoque_invariants(nom, jeu, n, premier, dernier):
    """Invariants de forme valables sur toutes les époques."""
    cfg = JEUX[jeu]
    tirages = parser_csv(cfg, fixture_texte(nom))

    assert [t["date"] for t in tirages] == sorted(t["date"] for t in tirages)
    for t in tirages:
        assert len(t["balls"]) == cfg["pick"]
        assert len(set(t["balls"])) == cfg["pick"]
        assert all(1 <= b <= cfg["n_max"] for b in t["balls"])
        assert len(t["bonus"]) == cfg["bonus_pick"]
        assert all(1 <= b <= cfg["bonus_max"] for b in t["bonus"])
        assert t["jour"] in cfg["jours"]
        assert t["balls"] == tuple(sorted(t["balls"]))


@pytest.mark.parametrize("nom,jeu", [(e[0], e[1]) for e in EPOQUES])
def test_rangs_officiels_tous_presents(nom, jeu):
    """Toutes les colonnes de rangs sont captées : 9 au Loto, 13 à l'Euro."""
    cfg = JEUX[jeu]
    attendus = set(range(1, 10 if cfg["bonus_pick"] == 1 else 14))
    for t in parser_csv(cfg, fixture_texte(nom)):
        assert set(t["gagnants"]) == attendus, t["date"]


def test_euromillions_prend_les_gagnants_francais_pas_europeens():
    """Le CSV Euro donne gagnants « en_france » PUIS « en_europe » par rang.

    L'EV est calculée pour un joueur français : c'est la colonne France qu'il
    faut lire. Les deux colonnes portant le même numéro de rang, seule la
    première rencontrée doit être retenue — sinon la participation estimée
    (et donc toute l'EV) serait celle de l'Europe entière.
    """
    cfg = JEUX["euromillions"]
    tirages = parser_csv(cfg, fixture_texte("euromillions_202002"))
    t = next(t for t in tirages if t["date"].isoformat() == "2026-08-04")
    # Relevé sur l'archive : rang 2 → 0 gagnant en France, 3 en Europe.
    assert t["gagnants"][2] == 0
    # rang 3 → 2 en France, 6 en Europe.
    assert t["gagnants"][3] == 2
    assert t["rapports"][3] == pytest.approx(22484.60)


def test_loto_ignore_le_second_tirage():
    """Le CSV Loto d'avant 2020 embarque un « second tirage » avec ses
    propres colonnes boule_N et rangs. Les confondre fausserait tout."""
    cfg = JEUX["loto"]
    t = parser_csv(cfg, fixture_texte("loto_201911"))[0]
    assert t["date"].isoformat() == "2019-11-06"
    assert list(t["balls"]) == [23, 27, 42, 43, 44]     # et non le 2e tirage
    assert set(t["gagnants"]) == set(range(1, 10))      # 9 rangs, pas 13


def test_variantes_de_format_numerique():
    """« 12000000 » (2019) et « 6000000,00 » (2026) doivent donner des float."""
    cfg = JEUX["loto"]
    tirages = parser_csv(cfg, fixture_texte("loto_201911"))
    assert tirages[0]["rapports"][1] == pytest.approx(12_000_000.0)
    assert tirages[-1]["rapports"][1] == pytest.approx(6_000_000.0)


def test_decodeur_gere_les_deux_encodages():
    """euromillions_4 est en latin-1, euromillions_202002 en utf-8."""
    brut_latin = (FIXTURES / "euromillions_4.csv").read_bytes()
    brut_utf8 = (FIXTURES / "euromillions_202002.csv").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        brut_latin.decode("utf-8")
    brut_utf8.decode("utf-8")                      # doit passer
    for brut in (brut_latin, brut_utf8):
        assert parser_csv(JEUX["euromillions"], _decoder(brut))


# ---- refus des formules incompatibles ---------------------------------------

def test_refuse_le_loto_6_49_davant_2008():
    """Ce fichier a bien boule_1..boule_5 mais pas de n° Chance : le parser
    d'origine l'aurait avalé en perdant silencieusement la 6e boule."""
    with pytest.raises(ValueError, match="Colonnes introuvables"):
        parser_csv(JEUX["loto"], fixture_texte("legacy_loto_6_49"))


def test_mode_tolerant_rend_une_liste_vide():
    """L'auto-découverte trie des archives inconnues : elle a besoin d'un
    parser qui renonce sans lever."""
    assert parser_csv(JEUX["loto"], fixture_texte("legacy_loto_6_49"),
                      tolerant=True) == []


def test_csv_vide_ou_sans_ligne():
    assert parser_csv(JEUX["loto"], "") == []
    assert parser_csv(JEUX["loto"], "\n\n  \n") == []
