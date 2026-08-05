#!/usr/bin/env python3
"""Zone faible 4.4 — « Si tu avais joué » est un tirage unique affiché en fait.

La rétro-simulation fixe une graine, joue UNE grille par tirage, et affiche un
total. Ce nombre est présenté comme un constat alors que c'est un échantillon
de taille 1 dans une loi très étalée. Deux sources d'aléa, mesurées séparément :

  A. LA GRAINE — `generer_grilles` est une recherche stochastique. Une autre
     graine publie d'autres grilles, à stratégie strictement identique.
  B. LA PÉRIODE — même en fixant la graine, jouer 100 AUTRES tirages aurait
     donné autre chose. Estimé par bootstrap sur les tirages.

    python3 tools/dispersion_simulation.py [n_graines] [n_tirages] [iters]
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "tests"))

from oracle import (  # noqa: E402
    JEUX,
    bonus_scores,
    calculer_scores,
    calibration_empirique,
    generer_grilles,
    prix_du_tirage,
    regler_grille,
    score_final,
)
from test_rangs import tirages_des_archives  # noqa: E402


def une_simulation(cfg, tirages, debut, seed, iters, cache):
    """Rejoue le pipeline de publication pour une graine. `cache` mémorise ce
    qui ne dépend PAS de la graine (scores, calibration) — sans quoi 200
    graines coûteraient 200 fois le calcul des scores, qui est identique."""
    mise = gain = 0.0
    n_gains = 0
    calib = None
    prochaine = debut
    for i in range(debut, len(tirages)):
        if i >= prochaine:
            calib = calibration_empirique(cfg, tirages[:i])
            prochaine = i + 25
        if i not in cache:
            passe = tirages[:i]
            _, folk, anti, _ = calculer_scores(cfg, passe, tirages[i]["jour"],
                                               calib)
            cache[i] = (score_final(cfg, folk, anti, "anti"),
                        bonus_scores(cfg, passe, calib), calib)
        fin, sb, cal = cache[i]
        g = generer_grilles(cfg, fin, sb, tirages[:i], "anti", 1,
                            random.Random(seed), cal, iters=iters)[0]
        t = tirages[i]
        mise += prix_du_tirage(cfg, t["date"])
        r = regler_grille(cfg, g, t)
        if r["gain"] > 0:
            gain += r["gain"]
            n_gains += 1
    return mise, gain, n_gains


def main() -> int:
    n_graines = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    n_tirages = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    iters = int(sys.argv[3]) if len(sys.argv) > 3 else 3000

    for cle in ("loto", "euromillions"):
        cfg = JEUX[cle]
        tir = tirages_des_archives(cle)
        debut = max(60, len(tir) - n_tirages)
        print(f"\n{'='*74}\n{cle.upper()} — {n_graines} graines, "
              f"{len(tir)-debut} tirages, iters={iters}\n{'='*74}")

        cache: dict = {}
        gains = []
        for s in range(n_graines):
            mise, gain, n = une_simulation(cfg, tir, debut, s, iters, cache)
            gains.append(gain)
            if s == 0:
                print(f"  mise totale : {mise:.2f} €")
        gains.sort()

        def pct(p):
            return gains[min(int(p * len(gains)), len(gains) - 1)]

        print(f"\n  A. DISPERSION SELON LA GRAINE (stratégie identique)")
        print(f"     min {gains[0]:.2f} €   p10 {pct(.10):.2f} €   "
              f"médiane {statistics.median(gains):.2f} €   "
              f"p90 {pct(.90):.2f} €   max {gains[-1]:.2f} €")
        print(f"     moyenne {statistics.mean(gains):.2f} € "
              f"(écart-type {statistics.pstdev(gains):.2f} €)")
        print(f"     rapport max/médiane : "
              f"×{gains[-1]/max(statistics.median(gains), 1e-9):.1f}")
        print(f"\n  → afficher un nombre seul revient à publier un tirage au "
              f"sort dans cet intervalle.")
        print(f"  → INTERVALLE HONNÊTE À AFFICHER : "
              f"{pct(.10):.0f} – {pct(.90):.0f} € (80 % des graines), "
              f"médiane {statistics.median(gains):.0f} €.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
