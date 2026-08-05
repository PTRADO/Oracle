#!/usr/bin/env python3
# ============================================================================
# ORACLE — fabrique d'icônes
#
# Dessine la marque (boule de tirage + anneau d'or + point rouge) et l'exporte
# en PNG à toutes les tailles utiles, plus le .ico. La source vectorielle
# docs/favicon.svg reste écrite à la main : ce script en est le jumeau
# raster, pixel par pixel, sans aucune dépendance.
#
# Usage :  python tools/icones.py
#
# Stdlib pure — comme le reste du dépôt. Le rendu est analytique (sphère
# éclairée, ellipse signée), suréchantillonné, puis encodé en PNG à la main.
# ============================================================================

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SORTIE = Path(__file__).resolve().parent.parent / "docs"

# ---------------------------------------------------------------------------
# Palette — reprise à l'identique de docs/index.html
# ---------------------------------------------------------------------------
FOND = (0x05 / 255, 0x07 / 255, 0x0F / 255)
BLEU = (0x43 / 255, 0x53 / 255, 0xFF / 255)
VIOLET = (0x8B / 255, 0x4D / 255, 0xFF / 255)
ROUGE = (0xFF / 255, 0x5A / 255, 0x4E / 255)
OR_CLAIR = (0xFF / 255, 0xE9 / 255, 0xB4 / 255)
OR_MOYEN = (0xF0 / 255, 0xB9 / 255, 0x52 / 255)
OR_SOMBRE = (0xA9 / 255, 0x72 / 255, 0x1E / 255)
NACRE = (1.0, 1.0, 1.0)
NACRE_OMBRE = (0x4E / 255, 0x5B / 255, 0x7C / 255)

# ---------------------------------------------------------------------------
# Géométrie, en fraction du côté (0 → 1). Le centre est en (0.5, 0.5).
# ---------------------------------------------------------------------------
RAYON_BOULE = 0.250
ANNEAU_A = 0.400          # demi grand axe
ANNEAU_B = 0.146          # demi petit axe
ANNEAU_EP = 0.0215        # demi épaisseur
INCLINAISON = math.radians(-20.0)
POINT_PHI = math.radians(32.0)   # position du point rouge sur l'anneau
POINT_R = 0.0430

LUM = (-0.42, -0.55, 0.72)        # direction de la lumière (y vers le bas)
_N = math.sqrt(sum(c * c for c in LUM))
LUM = tuple(c / _N for c in LUM)


# ---------------------------------------------------------------------------
# Petits outils de couleur
# ---------------------------------------------------------------------------
def borne(x: float, bas: float = 0.0, haut: float = 1.0) -> float:
    return bas if x < bas else haut if x > haut else x


def lisse(bord0: float, bord1: float, x: float) -> float:
    """Interpolation douce (smoothstep) entre deux bords."""
    if bord1 == bord0:
        return 0.0 if x < bord0 else 1.0
    t = borne((x - bord0) / (bord1 - bord0))
    return t * t * (3.0 - 2.0 * t)


def melange(a, b, t: float):
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def poser(fond, dessus, alpha: float):
    """Compositing « source over »."""
    if alpha <= 0.0:
        return fond
    if alpha >= 1.0:
        return dessus
    return melange(fond, dessus, alpha)


def ajouter(fond, couleur, force: float):
    """Ajout lumineux, borné."""
    if force <= 0.0:
        return fond
    return (borne(fond[0] + couleur[0] * force),
            borne(fond[1] + couleur[1] * force),
            borne(fond[2] + couleur[2] * force))


# ---------------------------------------------------------------------------
# Le dessin, échantillon par échantillon
# ---------------------------------------------------------------------------
class Marque:
    """La marque Oracle, évaluable en tout point du carré unité."""

    def __init__(self, echelle: float = 1.0, gras: float = 1.0):
        self.rb = RAYON_BOULE * echelle
        self.a = ANNEAU_A * echelle
        self.b = ANNEAU_B * echelle
        self.ep = ANNEAU_EP * echelle * gras
        self.pr = POINT_R * echelle * gras
        self.cos, self.sin = math.cos(INCLINAISON), math.sin(INCLINAISON)
        # Le point rouge chevauche l'anneau, moitié avant.
        px = self.a * math.cos(POINT_PHI)
        py = self.b * math.sin(POINT_PHI)
        self.point = (0.5 + px * self.cos - py * self.sin,
                      0.5 + px * self.sin + py * self.cos)

    # -- fond : nuit profonde + aurore bleu/violet ---------------------------
    def fond(self, x: float, y: float):
        col = FOND
        dx, dy = x - 0.04, y + 0.04
        col = ajouter(col, BLEU, 0.52 * math.exp(-(dx * dx + dy * dy) / 0.155))
        dx, dy = x - 1.03, y - 1.08
        col = ajouter(col, VIOLET, 0.50 * math.exp(-(dx * dx + dy * dy) / 0.150))
        dx, dy = x - 0.5, y - 0.5
        col = melange(col, FOND, lisse(0.16, 0.62, math.hypot(dx, dy)) * 0.34)
        return col

    # -- anneau : distance signée à l'ellipse inclinée -----------------------
    def anneau(self, x: float, y: float):
        """Renvoie (distance au trait, position sur la profondeur -1..1)."""
        dx, dy = x - 0.5, y - 0.5
        xr = dx * self.cos + dy * self.sin
        yr = -dx * self.sin + dy * self.cos
        a2, b2 = self.a * self.a, self.b * self.b
        f = (xr * xr) / a2 + (yr * yr) / b2
        g = 2.0 * math.hypot(xr / a2, yr / b2)
        dist = (f - 1.0) / g if g > 1e-9 else 1.0
        return abs(dist), yr / self.b

    def teinte_or(self, x: float, y: float, avant: bool):
        t = borne(0.5 + (x - y) * 1.15)
        col = melange(OR_SOMBRE, OR_MOYEN, lisse(0.0, 0.55, t))
        col = melange(col, OR_CLAIR, lisse(0.52, 1.0, t))
        return col if avant else melange(col, (0.10, 0.09, 0.16), 0.42)

    # -- boule : sphère nacrée éclairée en haut à gauche ----------------------
    def boule(self, x: float, y: float):
        px, py = (x - 0.5) / self.rb, (y - 0.5) / self.rb
        q = px * px + py * py
        nz = math.sqrt(1.0 - q) if q < 1.0 else 0.0
        diff = borne(px * LUM[0] + py * LUM[1] + nz * LUM[2])
        col = melange(NACRE_OMBRE, NACRE, 0.16 + 0.84 * diff ** 0.55)
        # éclat spéculaire
        hx, hy, hz = LUM[0], LUM[1], LUM[2] + 1.0
        hn = math.sqrt(hx * hx + hy * hy + hz * hz)
        spec = borne(px * hx / hn + py * hy / hn + nz * hz / hn)
        col = ajouter(col, (1.0, 1.0, 1.0), 0.85 * spec ** 90)
        # liseré d'aurore sur le bord, rebond doré en bas
        fres = (1.0 - nz) ** 3.0
        col = ajouter(col, melange(BLEU, VIOLET, borne(0.5 + 0.5 * px)),
                      0.42 * fres)
        col = ajouter(col, OR_MOYEN, 0.30 * fres * lisse(0.0, 0.9, py))
        return col

    # -- l'image complète -----------------------------------------------------
    def pixel(self, x: float, y: float, e: float):
        col = self.fond(x, y)

        # halo doré sous la boule
        d = math.hypot(x - 0.5, y - 0.5)
        col = ajouter(col, OR_MOYEN, 0.20 * math.exp(-((d / (self.rb * 1.5)) ** 2)))

        dist, prof = self.anneau(x, y)
        alpha_anneau = 1.0 - lisse(self.ep - e, self.ep + e, dist)
        halo = math.exp(-((dist / (self.ep * 2.6)) ** 2))

        # moitié arrière de l'anneau, derrière la boule
        if prof < 0.35 and alpha_anneau > 0.0:
            arriere = 1.0 - lisse(-0.30, 0.30, prof)
            col = ajouter(col, OR_MOYEN, 0.22 * halo * arriere)
            col = poser(col, self.teinte_or(x, y, False), alpha_anneau * arriere)

        # la boule
        db = math.hypot(x - 0.5, y - 0.5) - self.rb
        alpha_boule = 1.0 - lisse(-e, e, db)
        if alpha_boule > 0.0:
            col = poser(col, self.boule(x, y), alpha_boule)

        # moitié avant, par-dessus, avec son ombre de contact
        if prof > -0.35 and (alpha_anneau > 0.0 or halo > 0.02):
            devant = lisse(-0.30, 0.30, prof)
            ombre = math.exp(-((dist / (self.ep * 2.2)) ** 2)) * devant
            col = melange(col, (0.02, 0.03, 0.07), 0.45 * ombre * alpha_boule)
            col = ajouter(col, OR_MOYEN, 0.26 * halo * devant * (1 - alpha_boule))
            col = poser(col, self.teinte_or(x, y, True), alpha_anneau * devant)

        # le point rouge — la ponctuation de « ORACLE. »
        pdx, pdy = x - self.point[0], y - self.point[1]
        dp = math.hypot(pdx, pdy)
        col = ajouter(col, ROUGE, 0.42 * math.exp(-((dp / (self.pr * 1.9)) ** 2)))
        ap = 1.0 - lisse(self.pr - e, self.pr + e, dp)
        if ap > 0.0:
            nx, ny = pdx / self.pr, pdy / self.pr
            nz = math.sqrt(max(0.0, 1.0 - nx * nx - ny * ny))
            dl = borne(nx * LUM[0] + ny * LUM[1] + nz * LUM[2])
            chair = melange((0.42, 0.10, 0.10), ROUGE, 0.25 + 0.75 * dl ** 0.7)
            chair = ajouter(chair, (1.0, 0.95, 0.9), 0.55 * dl ** 26)
            col = poser(col, chair, ap)

        return col


# ---------------------------------------------------------------------------
# Rendu et encodage
# ---------------------------------------------------------------------------
def rendre(taille: int, echelle: float = 1.0, gras: float = 1.0,
           ss: int = 3) -> bytes:
    """Rend l'icône en RGB brut, suréchantillonnage ss×ss."""
    marque = Marque(echelle, gras)
    e = 0.62 / (taille * ss)
    pas = 1.0 / (taille * ss)
    inv = 1.0 / (ss * ss)
    lignes = bytearray()
    for py in range(taille):
        ligne = bytearray()
        for px in range(taille):
            r = v = b = 0.0
            for sy in range(ss):
                y = (py * ss + sy + 0.5) * pas
                for sx in range(ss):
                    x = (px * ss + sx + 0.5) * pas
                    c = marque.pixel(x, y, e)
                    r += c[0]
                    v += c[1]
                    b += c[2]
            ligne += bytes((round(borne(r * inv) * 255),
                            round(borne(v * inv) * 255),
                            round(borne(b * inv) * 255)))
        lignes += b"\x00" + ligne
    return bytes(lignes)


def png(taille: int, brut: bytes) -> bytes:
    """Encode du RGB8 en PNG (stdlib : zlib + struct)."""
    def bloc(nom: bytes, donnees: bytes) -> bytes:
        corps = nom + donnees
        return (struct.pack(">I", len(donnees)) + corps
                + struct.pack(">I", zlib.crc32(corps) & 0xFFFFFFFF))

    entete = struct.pack(">IIBBBBB", taille, taille, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + bloc(b"IHDR", entete)
            + bloc(b"IDAT", zlib.compress(brut, 9))
            + bloc(b"IEND", b""))


def ico(images: dict[int, bytes]) -> bytes:
    """Assemble un .ico contenant des PNG (Vista+, tous navigateurs actuels)."""
    tailles = sorted(images)
    tete = struct.pack("<HHH", 0, 1, len(tailles))
    decalage = len(tete) + 16 * len(tailles)
    entrees, corps = b"", b""
    for t in tailles:
        donnees = images[t]
        entrees += struct.pack("<BBBBHHII", t & 0xFF, t & 0xFF, 0, 0, 1, 32,
                               len(donnees), decalage)
        corps += donnees
        decalage += len(donnees)
    return tete + entrees + corps


# Ce qu'on fabrique : nom, taille, échelle du motif, épaississement, ss.
# L'échelle 0.80 des icônes « maskable » garde tout le motif dans la zone
# sûre (le cercle central de 80 %) quand Android rogne l'icône.
FICHIERS = [
    ("favicon-16.png", 16, 1.00, 1.30, 6),
    ("favicon-32.png", 32, 1.00, 1.18, 5),
    ("favicon-48.png", 48, 1.00, 1.10, 4),
    ("apple-touch-icon.png", 180, 0.90, 1.00, 3),
    ("icon-192.png", 192, 1.00, 1.00, 3),
    ("icon-512.png", 512, 1.00, 1.00, 2),
    ("icon-maskable-512.png", 512, 0.80, 1.00, 2),
]


def main() -> None:
    petits = {}
    for nom, taille, echelle, gras, ss in FICHIERS:
        brut = rendre(taille, echelle, gras, ss)
        donnees = png(taille, brut)
        (SORTIE / nom).write_bytes(donnees)
        print(f"  {nom:26} {len(donnees) / 1024:6.1f} ko")
        if taille in (16, 32, 48):
            petits[taille] = donnees
    (SORTIE / "favicon.ico").write_bytes(ico(petits))
    print(f"  {'favicon.ico':26} "
          f"{len((SORTIE / 'favicon.ico').read_bytes()) / 1024:6.1f} ko")


if __name__ == "__main__":
    main()
