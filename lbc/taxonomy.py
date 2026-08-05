"""Résolution des catégories et des localisations à partir de texte libre.

L'utilisateur — ou l'agent — écrit « montres », « 75 », « Lyon ». Ces tables
traduisent ça en identifiants API sans aller-retour réseau, ce qui évite une
requête (et des tokens) à chaque recherche.
"""

from __future__ import annotations

import re
import unicodedata

# Catégories leboncoin les plus utiles. La liste complète compte ~100 entrées ;
# on garde celles qui portent réellement des pépites, plus les racines.
CATEGORIES: dict[str, str] = {
    "toutes": "0",
    "vehicules": "1",
    "voitures": "2",
    "motos": "3",
    "caravaning": "5",
    "utilitaires": "4",
    "nautisme": "7",
    "immobilier": "8",
    "ventes_immobilieres": "9",
    "locations": "10",
    "colocations": "11",
    "bureaux_commerces": "13",
    "multimedia": "14",
    "informatique": "15",
    "consoles_jeux_video": "43",
    "image_son": "16",
    "telephonie": "17",
    "maison": "18",
    "ameublement": "19",
    "electromenager": "20",
    "arts_de_la_table": "21",
    "decoration": "22",
    "linge_de_maison": "23",
    "bricolage": "24",
    "jardinage": "25",
    "vetements": "27",
    "chaussures": "53",
    "accessoires_bagagerie": "54",
    "montres_bijoux": "55",
    "equipement_bebe": "28",
    "vetements_bebe": "29",
    "loisirs": "30",
    "dvd_films": "31",
    "cd_musique": "32",
    "livres": "33",
    "animaux": "34",
    "velos": "44",
    "sports_hobbies": "35",
    "instruments_de_musique": "36",
    "collection": "40",
    "jeux_jouets": "41",
    "vins_gastronomie": "42",
    "materiel_professionnel": "56",
    "emploi": "71",
    "services": "66",
}

# Synonymes courants → clé canonique ci-dessus.
_CATEGORY_ALIASES: dict[str, str] = {
    "montre": "montres_bijoux",
    "montres": "montres_bijoux",
    "bijoux": "montres_bijoux",
    "horlogerie": "montres_bijoux",
    "velo": "velos",
    "vtt": "velos",
    "hifi": "image_son",
    "hi-fi": "image_son",
    "son": "image_son",
    "audio": "image_son",
    "vinyle": "cd_musique",
    "vinyles": "cd_musique",
    "disques": "cd_musique",
    "sneakers": "chaussures",
    "baskets": "chaussures",
    "chaussure": "chaussures",
    "fringues": "vetements",
    "vetement": "vetements",
    "mode": "vetements",
    "meubles": "ameublement",
    "meuble": "ameublement",
    "mobilier": "ameublement",
    "design": "decoration",
    "outillage": "bricolage",
    "outils": "bricolage",
    "pc": "informatique",
    "ordinateur": "informatique",
    "telephone": "telephonie",
    "smartphone": "telephonie",
    "iphone": "telephonie",
    "console": "consoles_jeux_video",
    "jeux_video": "consoles_jeux_video",
    "guitare": "instruments_de_musique",
    "musique": "instruments_de_musique",
    "photo": "image_son",
    "appareil_photo": "image_son",
    "voiture": "voitures",
    "auto": "voitures",
    "moto": "motos",
    "scooter": "motos",
    "livre": "livres",
    "bd": "livres",
}

REGIONS: dict[str, str] = {
    "ile_de_france": "12",
    "auvergne_rhone_alpes": "22",
    "provence_alpes_cote_d_azur": "21",
    "occitanie": "16",
    "nouvelle_aquitaine": "20",
    "hauts_de_france": "17",
    "grand_est": "13",
    "pays_de_la_loire": "18",
    "bretagne": "6",
    "normandie": "4",
    "bourgogne_franche_comte": "5",
    "centre_val_de_loire": "8",
    "corse": "9",
}

_REGION_ALIASES = {
    "idf": "ile_de_france",
    "paris_region": "ile_de_france",
    "iledefrance": "ile_de_france",
    "paca": "provence_alpes_cote_d_azur",
    "ara": "auvergne_rhone_alpes",
    "rhone_alpes": "auvergne_rhone_alpes",
    "aquitaine": "nouvelle_aquitaine",
    "nord": "hauts_de_france",
    "alsace": "grand_est",
    "lorraine": "grand_est",
}

# Grandes villes → code département, pour accepter « Lyon » sans géocodage.
CITY_TO_DEPARTMENT: dict[str, str] = {
    "paris": "75",
    "marseille": "13",
    "lyon": "69",
    "toulouse": "31",
    "nice": "06",
    "nantes": "44",
    "montpellier": "34",
    "strasbourg": "67",
    "bordeaux": "33",
    "lille": "59",
    "rennes": "35",
    "reims": "51",
    "toulon": "83",
    "saint_etienne": "42",
    "le_havre": "76",
    "grenoble": "38",
    "dijon": "21",
    "angers": "49",
    "nimes": "30",
    "villeurbanne": "69",
    "clermont_ferrand": "63",
    "aix_en_provence": "13",
    "brest": "29",
    "tours": "37",
    "amiens": "80",
    "annecy": "74",
    "limoges": "87",
    "metz": "57",
    "besancon": "25",
    "caen": "14",
    "orleans": "45",
    "rouen": "76",
    "nancy": "54",
    "avignon": "84",
    "poitiers": "86",
    "versailles": "78",
    "pau": "64",
    "la_rochelle": "17",
    "biarritz": "64",
    "cannes": "06",
    "antibes": "06",
}


def slug(value: str) -> str:
    """Minuscule, sans accent, séparateurs unifiés en underscore."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def resolve_category(value: str | None) -> str | None:
    """« montres », « 55 », « Montres & Bijoux » → identifiant de catégorie."""
    if not value:
        return None
    raw = str(value).strip()
    if raw.isdigit():
        return raw
    key = slug(raw)
    key = _CATEGORY_ALIASES.get(key, key)
    if key in CATEGORIES:
        return CATEGORIES[key]
    # Repli : correspondance partielle, pour tolérer « velo_vtt » ou « image son ».
    for name, cid in CATEGORIES.items():
        if key and (key in name or name in key):
            return cid
    return None


def resolve_location(value: str | None) -> dict | None:
    """Construit le bloc `location` de l'API depuis « 75 », « Lyon » ou « PACA »."""
    if not value:
        return None
    raw = str(value).strip()

    # Code postal complet → on remonte au département.
    if re.fullmatch(r"\d{5}", raw):
        return {"locationType": "department", "department_id": _dept_from_zip(raw)}

    # Numéro de département, avec ou sans zéro initial ; 2A/2B pour la Corse.
    if re.fullmatch(r"\d{1,3}|2[ABab]", raw):
        return {"locationType": "department", "department_id": _pad_dept(raw)}

    key = slug(raw)
    if key in CITY_TO_DEPARTMENT:
        return {"locationType": "department", "department_id": CITY_TO_DEPARTMENT[key]}

    region_key = _REGION_ALIASES.get(key, key)
    if region_key in REGIONS:
        return {"locationType": "region", "region_id": REGIONS[region_key]}

    return None


def _pad_dept(value: str) -> str:
    if value.upper() in ("2A", "2B"):
        return value.upper()
    if value.isdigit() and len(value) == 1:
        return "0" + value
    return value


def _dept_from_zip(zipcode: str) -> str:
    prefix = zipcode[:2]
    if prefix == "20":  # Corse : 200xx/201xx = 2A, le reste = 2B
        return "2A" if zipcode[2] in "01" else "2B"
    if prefix in ("97", "98"):
        return zipcode[:3]
    return prefix


def category_name(category_id: str) -> str:
    for name, cid in CATEGORIES.items():
        if cid == str(category_id):
            return name
    return str(category_id)
