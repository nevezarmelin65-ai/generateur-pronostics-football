
import math
import re
import unicodedata
from datetime import date, time
from io import StringIO

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Générateur de pronostics football",
    page_icon="⚽",
    layout="wide",
)

st.title("⚽ Générateur de pronostics football")
st.write("Analyse statistique des matchs à partir de résultats publics.")
st.info(
    "Les probabilités sont des estimations. "
    "Les données indisponibles ne sont jamais inventées."
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 FootballPredictionApp/4.0"
}
TIMEOUT = 12

FD_BASE = "https://www.football-data.co.uk/mmz4281"
OPENFOOTBALL_BASE = (
    "https://raw.githubusercontent.com/openfootball/football.json/master"
)

# Compétitions couvertes par football-data.co.uk
LEAGUES = {
    "Angleterre - Premier League": "E0",
    "Angleterre - Championship": "E1",
    "Écosse - Premiership": "SC0",
    "Allemagne - Bundesliga": "D1",
    "Allemagne - 2. Bundesliga": "D2",
    "Italie - Serie A": "I1",
    "Italie - Serie B": "I2",
    "Espagne - La Liga": "SP1",
    "Espagne - Segunda División": "SP2",
    "France - Ligue 1": "F1",
    "France - Ligue 2": "F2",
    "Pays-Bas - Eredivisie": "N1",
    "Belgique - Pro League": "B1",
    "Portugal - Primeira Liga": "P1",
    "Turquie - Süper Lig": "T1",
    "Grèce - Super League": "G1",
}

# Sources complémentaires à vérifier selon la compétition
OPENFOOTBALL_LEAGUES = {
    "Ligue des champions": ["uefa-champions-league"],
    "Ligue Europa": ["uefa-europa-league"],
    "États-Unis - MLS": ["usa"],
    "Arabie saoudite - Saudi Pro League": ["saudi-arabia"],
    "Danemark - Superliga": ["denmark"],
    "Russie - Premier League": ["russia"],
    "Coupe du monde": ["world-cup"],
    "Brésil - Série A": ["brazil"],
    "Mexique - Liga MX": ["mexico"],
}

ALL_LEAGUES = list(LEAGUES) + list(OPENFOOTBALL_LEAGUES)


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", value.lower())


def season_codes(target):
    """Retourne les deux saisons récentes."""
    start = target.year if target.month >= 7 else target.year - 1
    return [
        f"{year % 100:02d}{(year + 1) % 100:02d}"
        for year in (start, start - 1)
    ]


def poisson(lam, goals):
    return math.exp(-lam) * lam**goals / math.factorial(goals)
