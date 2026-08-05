#!/usr/bin/env python3
"""Ce qui est réellement gagnable, rang par rang, en euros.

Pourquoi ce fichier existe
--------------------------
Le ROI d'une rétro-simulation sur 100 tirages ne mesure PAS la qualité d'une
stratégie : il est dominé par la chance. Mesuré sur ce moteur, la même
stratégie rejouée avec une autre graine donne de 27 € à 172 € sur les mêmes
100 tirages. Comparer deux versions sur ce chiffre revient à comparer deux
lancers de dé.

Ce script donne à la place les quantités qui NE bougent pas : les
probabilités exactes, les rapports réellement payés par la FDJ, et
l'espérance de gain qui en découle.

    python3 tools/ce_qui_est_gagnable.py [jackpot_loto] [jackpot_euro]
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
    parametres_ev,
    pop_rel_grille,
    proba_jackpot,
    rang_gagne,
    scores_anti,
)

LIB_LOTO = {1: "5 numéros + Chance", 2: "5 numéros", 3: "4 + Chance",
            4: "4 numéros", 5: "3 + Chance", 6: "3 numéros",
            7: "2 + Chance", 8: "2 numéros", 9: "Chance seul"}
LIB_EURO = {1: "5 + 2 étoiles", 2: "5 + 1", 3: "5 + 0", 4: "4 + 2",
            5: "4 + 1", 6: "3 + 2", 7: "4 + 0", 8: "2 + 2", 9: "3 + 1",
            10: "3 + 0", 11: "1 + 2", 12: "2 + 1", 13: "2 + 0"}


def probas(cfg) -> dict[int, float]:
    n, k = cfg["n_max"], cfg["pick"]
    bmax, bp = cfg["bonus_max"], cfg["bonus_pick"]
    total = math.comb(n, k) * math.comb(bmax, bp)
    p: dict[int, float] = {}
    for m in range(k + 1):
        for b in range(bp + 1):
            r = rang_gagne(cfg, m, b)
            if r is None:
                continue
            cas = (math.comb(k, m) * math.comb(n - k, k - m)
                   * math.comb(bp, b) * math.comb(bmax - bp, bp - b))
            p[r] = p.get(r, 0.0) + cas / total
    return p


def analyser(cle: str, jackpot: float) -> None:
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    calib = calibration_empirique(cfg, tir)
    p = probas(cfg)
    lib = LIB_LOTO if cfg["bonus_pick"] == 1 else LIB_EURO

    print(f"\n{'='*76}\n{cfg['nom']} — grille à {cfg['prix']:.2f} €, "
          f"jackpot supposé {jackpot/1e6:.0f} M€\n{'='*76}")

    # rapport médian réellement payé, par rang
    med = {}
    for r in sorted(p):
        vals = [t["rapports"][r] for t in tir if t["rapports"].get(r)]
        if vals:
            med[r] = statistics.median(vals)

    print(f"\n{'rang':>4} {'ce qu il faut':22} {'1 chance sur':>14} "
          f"{'gain médian':>13} {'espérance':>12}")
    print("-" * 70)
    ev_fixe = 0.0
    for r in sorted(p):
        gain = med.get(r, 0.0)
        contrib = p[r] * gain
        if r != 1:
            ev_fixe += contrib
        print(f"{r:>4} {lib.get(r, '?'):22} {1/p[r]:>14,.0f} "
              f"{gain:>12,.2f} € {contrib:>11.4f} €".replace(",", " "))

    print("-" * 70)
    print(f"     {'TOTAL hors jackpot':22} {'':>14} {'':>13} "
          f"{ev_fixe:>11.4f} €")

    # --- le jackpot, avec et sans anti-partage ---------------------------
    ev_p = parametres_ev(cfg, tir)
    p1 = p[1]
    n_est = ev_p["n_est"]
    anti, _ = scores_anti(cfg, calib)
    ordre = sorted(anti, key=lambda n: -anti[n])
    grille_anti = tuple(sorted(ordre[:cfg["pick"]]))
    pr_anti = pop_rel_grille(cfg, grille_anti, calib)

    print(f"\n  Participation estimée : {n_est:,.0f} grilles par tirage"
          .replace(",", " "))
    print(f"  P(jackpot) = 1 sur {proba_jackpot(cfg):,}".replace(",", " "))

    print(f"\n  {'':30} {'co-gagnants':>12} {'part du jackpot':>17} "
          f"{'espérance':>12}")
    for nom, pr in (("grille quelconque", 1.0),
                    ("grille anti-partage du moteur", pr_anti)):
        partageurs = n_est * p1 * pr
        part = jackpot / (1.0 + partageurs)
        print(f"  {nom:30} {partageurs:>12.3f} {part:>15,.0f} € "
              f"{p1 * part:>10.4f} €".replace(",", " "))

    partageurs = n_est * p1 * pr_anti
    ev = ev_fixe + p1 * jackpot / (1 + partageurs) - cfg["prix"]
    ev_neutre = (ev_fixe + p1 * jackpot / (1 + n_est * p1)
                 - cfg["prix"])
    print(f"\n  ESPÉRANCE PAR GRILLE (2,{int(cfg['prix']*100)%100:02d} € misés) :")
    print(f"    grille quelconque              {ev_neutre:+.4f} €   "
          f"soit {100*ev_neutre/cfg['prix']:+.1f} %")
    print(f"    grille anti-partage            {ev:+.4f} €   "
          f"soit {100*ev/cfg['prix']:+.1f} %")
    print(f"    ce que l'anti-partage rapporte {ev-ev_neutre:+.4f} € "
          f"par grille, soit {100*(ev-ev_neutre)/cfg['prix']:+.2f} %")

    # --- combien de temps pour voir la différence ? -----------------------
    print("\n  À QUEL HORIZON CETTE DIFFÉRENCE DEVIENT-ELLE VISIBLE ?")
    print("    Le gain de l'anti-partage ne se réalise QUE si tu touches le")
    print(f"    jackpot. Probabilité : 1 sur {proba_jackpot(cfg):,}."
          .replace(",", " "))
    tirages_par_an = len(cfg["jours"]) * 52
    ans = proba_jackpot(cfg) / tirages_par_an
    print(f"    À {tirages_par_an} tirages par an, une grille par tirage : "
          f"{ans:,.0f} ans en moyenne.".replace(",", " "))
    print(f"    Sur 100 tirages, la probabilité d'en voir un seul est de "
          f"{100 * (1 - (1-p1)**100):.6f} %.")
    print("    => AUCUNE rétro-simulation sur 100 tirages ne peut mesurer")
    print("       cet effet. Ce qu'on y voit est du bruit sur les petits rangs.")


def main() -> int:
    jl = float(sys.argv[1]) if len(sys.argv) > 1 else 7_000_000
    je = float(sys.argv[2]) if len(sys.argv) > 2 else 110_000_000
    print("""
CE QUI EST GAGNABLE — les quantités qui ne dépendent pas de la chance

Les rapports sont les MÉDIANES réellement payées par la FDJ sur tout
l'historique. Les probabilités sont exactes, par dénombrement.
""")
    analyser("loto", jl)
    analyser("euromillions", je)
    print(f"\n{'='*76}")
    print("""LECTURE

L'espérance est NÉGATIVE dans tous les cas : c'est la définition d'une
loterie, la FDJ reverse environ la moitié des mises. Aucun algorithme ne
peut inverser ce signe, parce qu'aucun algorithme ne change les
probabilités — elles sont fixées par le tirage, pas par la grille.

Ce que l'anti-partage change, c'est le MONTANT en cas de jackpot, en
réduisant le nombre de gens avec qui le partager. C'est réel et mesuré,
mais cela ne se voit pas sur 100 tirages : sur 100 tirages, on ne touche
pas le jackpot.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
