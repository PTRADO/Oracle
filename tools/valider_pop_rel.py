#!/usr/bin/env python3
"""Zone faible 4.2 — `pop_rel` confronté à de vrais événements de partage.

Le multiplicateur de partage n'avait jamais été vérifié contre la réalité.
Ce script le fait de la façon la plus exigeante possible.

LE PRINCIPE
-----------
La calibration n'apprend QUE sur les rangs peuplés : au Loto les rangs 4 à 9,
c'est-à-dire au plus 4 bons numéros. Le rang 2 (les 5 bons numéros, sans le
n° Chance) n'entre jamais dans l'estimation — il est trop creux pour cela.

Or c'est exactement le rang qui compte : partager le jackpot, c'est apparier
les 5 boules. On dispose donc d'un test HORS ÉCHANTILLON dans la seule
dimension qui intéresse le produit.

LA PRÉDICTION, ET ELLE EST CHIFFRÉE
-----------------------------------
`popularite_log` est construit à l'échelle m = pick. Le modèle prédit donc

    log E[gagnants au rang « 5 bons »] = a + log(participation) + 1,0 × indice

Pas « un coefficient positif » : un coefficient de UN. Un modèle qui se
trompe d'échelle d'un facteur 5 — comme la v2.3 — sort ici un coefficient de
5 ou de 0,2, pas de 1.

La participation est mesurée par un rang que les 5 boules n'affectent pas (ou
presque) : au Loto le rang 9 (« n° Chance seul », m effectif 0,385). Ce rang
capte lui-même une fraction m/pick de l'indice, d'où le coefficient attendu
    1 − m9/pick = 1 − 0,385/5 = 0,923.
"""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "tests"))

from test_rangs import tirages_des_archives  # noqa: E402

from oracle import (  # noqa: E402
    JEUX,
    calibration_empirique,
    popularite_log,
    rangs_mb,
)


def poisson_irls(y, x, offset, tours=60):
    """log E[y] = a + b·x + offset, par Newton-Raphson. Rend (a, b, se_b)."""
    a, b = 0.0, 0.0
    for _ in range(tours):
        s00 = s01 = s11 = g0 = g1 = 0.0
        for yi, xi, oi in zip(y, x, offset, strict=True):
            mu = math.exp(a + b * xi + oi)
            r = yi - mu
            g0 += r
            g1 += r * xi
            s00 += mu
            s01 += mu * xi
            s11 += mu * xi * xi
        det = s00 * s11 - s01 * s01
        if abs(det) < 1e-12:
            break
        da = (s11 * g0 - s01 * g1) / det
        db = (-s01 * g0 + s00 * g1) / det
        a, b = a + da, b + db
        if abs(da) + abs(db) < 1e-12:
            break
    se_b = math.sqrt(s00 / det) if det > 0 else float("nan")
    return a, b, se_b


def analyser(cle: str, rang_cible: int, rang_offset: int) -> None:
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    mb = rangs_mb(cfg)
    m_cible = mb[rang_cible][0]
    m_off = mb[rang_offset][0]

    print(f"\n{'='*74}\n{cle.upper()} — rang {rang_cible} "
          f"(m={m_cible:.0f}) testé, participation par le rang {rang_offset} "
          f"(m={m_off:.3f})\n{'='*74}")

    # Moitié 1 pour apprendre, moitié 2 pour tester : aucune fuite possible.
    moitie = len(tir) // 2
    calib = calibration_empirique(cfg, tir[:moitie])
    if calib is None:
        print("  calibration impossible")
        return
    assert rang_cible not in calib["rangs"], (
        f"le rang {rang_cible} entre dans l'estimation — test invalide")
    print(f"  appris sur {calib['n_tirages']} tirages, rangs {calib['rangs']}")

    y, x, off = [], [], []
    for t in tir[moitie:]:
        w = t["gagnants"].get(rang_cible)
        n = t["gagnants"].get(rang_offset)
        if w is None or not n:
            continue
        y.append(float(w))
        x.append(popularite_log(cfg, t["balls"], calib))
        off.append(math.log(n))

    total = sum(y)
    print(f"  testé sur {len(y)} tirages, {total:.0f} gagnants au rang "
          f"{rang_cible} (médiane {statistics.median(y):.0f})")
    if total < 50:
        print("  trop peu d'événements — non concluant")
        return

    a, b, se = poisson_irls(y, x, off)
    attendu = 1.0 - m_off / cfg["pick"]
    print(f"\n  coefficient mesuré : b = {b:+.3f} ± {se:.3f}")
    print(f"  prédit par le modèle : {attendu:.3f}   "
          f"(écart : {abs(b - attendu)/se:.1f} σ)")
    print(f"  hypothèse nulle « aucun effet de partage » b = 0 : "
          f"{abs(b)/se:.1f} σ")
    ech_v23 = attendu * 0.17
    print(f"  échelle de la v2.3 (~0,17×) prédirait {ech_v23:.3f} : "
          f"{abs(b - ech_v23)/se:.1f} σ")

    # Lecture concrète : quartiles extrêmes de l'indice
    paires = sorted(zip(x, y, off, strict=True))
    q = len(paires) // 4
    for nom, bloc in (("indice le plus BAS  (grilles délaissées)", paires[:q]),
                      ("indice le plus HAUT (grilles sur-jouées)", paires[-q:])):
        gag = sum(v for _, v, _ in bloc)
        part = sum(math.exp(o) for _, _, o in bloc)
        print(f"  {nom} : {gag:.0f} gagnants pour "
              f"{part/1e6:.2f} M de participation → "
              f"{1e6*gag/part:.2f} par million")


def main() -> int:
    # Loto : rang 2 = 5 bons numéros ; participation par le rang 9.
    analyser("loto", rang_cible=2, rang_offset=9)
    # EuroMillions : rang 3 = 5 bons, 0 étoile ; participation par le rang 13.
    analyser("euromillions", rang_cible=3, rang_offset=13)
    return 0


if __name__ == "__main__":
    sys.exit(main())
