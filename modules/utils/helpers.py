"""
modules/utils/helpers.py — Fonctions utilitaires communes
"""

from datetime import datetime

# Jours et mois en français (utilisé par journal_builder et report)
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre"
]


def format_date_fr(dt: datetime) -> str:
    """Retourne 'lundi 28 mars 2026'"""
    jour = JOURS_FR[dt.weekday()]
    return f"{jour} {dt.day} {MOIS_FR[dt.month - 1]} {dt.year}"


def format_heure(dt: datetime) -> str:
    """Retourne '14h05'"""
    return f"{dt.hour}h{dt.minute:02d}"


def clean_ansi(text: str) -> str:
    """Supprime les codes ANSI des sorties console"""
    import re
    text = re.sub(r'\x1b\[[0-9;]*[mGKHF]', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text.strip()