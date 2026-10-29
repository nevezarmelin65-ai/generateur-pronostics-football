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

@st.cache_data(ttl=1800, show_spinner=False)
def load_csv_results(league_code, target_iso):
    target = date.fromisoformat(target_iso)
    frames = []
    errors = []

    for season in season_codes(target):
        url = f"{FD_BASE}/{season}/{league_code}.csv"

        try:
            response = requests.get(
                url, headers=HEADERS, timeout=TIMEOUT
            )
            response.raise_for_status()

            if not response.text.strip():
                errors.append(f"{season}: fichier vide")
                continue

            df = pd.read_csv(
                StringIO(response.text),
                on_bad_lines="skip"
            )

            required = {
                "HomeTeam", "AwayTeam", "FTHG", "FTAG", "Date"
            }
            if not required.issubset(df.columns):
                errors.append(f"{season}: colonnes manquantes")
                continue

            df["_date"] = pd.to_datetime(
                df["Date"], dayfirst=True, errors="coerce"
            )
            df["_hg"] = pd.to_numeric(df["FTHG"], errors="coerce")
            df["_ag"] = pd.to_numeric(df["FTAG"], errors="coerce")

            df = df.dropna(
                subset=[
                    "_date", "_hg", "_ag",
                    "HomeTeam", "AwayTeam"
                ]
            )

            # Ne conserver que les matchs antérieurs à l'analyse.
            df = df[df["_date"].dt.date < target]
            frames.append(df)

        except Exception as exc:
            errors.append(
                f"{season}: {type(exc).__name__}: {exc}"
            )

    if not frames:
        return [], errors or ["Aucun résultat exploitable."]

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(
        subset=["_date", "HomeTeam", "AwayTeam", "_hg", "_ag"]
    )
    df = df.sort_values("_date", ascending=False)

    matches = []

    for _, row in df.iterrows():
        matches.append({
            "date": row["_date"].date(),
            "domicile": str(row["HomeTeam"]),
            "exterieur": str(row["AwayTeam"]),
            "buts_dom": int(row["_hg"]),
            "buts_ext": int(row["_ag"]),
            "source": "football-data.co.uk",
        })

    return matches, errors


def parse_openfootball_matches(data, target):
    matches = []

    if not isinstance(data, dict):
        return matches

    for item in data.get("matches", []):
        if not isinstance(item, dict):
            continue

        raw_date = item.get("date")
        if not raw_date:
            continue

        parsed_date = pd.to_datetime(
            raw_date, errors="coerce"
        )
        if pd.isna(parsed_date):
            continue

        match_date = parsed_date.date()
        if match_date >= target:
            continue

        home = item.get("team1") or item.get("homeTeam")
        away = item.get("team2") or item.get("awayTeam")

        if isinstance(home, dict):
            home = home.get("name")
        if isinstance(away, dict):
            away = away.get("name")

        score = item.get("score") or {}
        full_time = score.get("ft", {}) if isinstance(score, dict) else {}

        if isinstance(full_time, dict):
            hg = full_time.get("home", full_time.get("team1"))
            ag = full_time.get("away", full_time.get("team2"))
        elif isinstance(full_time, list) and len(full_time) >= 2:
            hg, ag = full_time[0], full_time[1]
        else:
            hg = ag = None

        try:
            hg, ag = int(hg), int(ag)
        except (TypeError, ValueError):
            # Ne jamais inventer un score absent.
            continue

        if home and away:
            matches.append({
                "date": match_date,
                "domicile": str(home),
                "exterieur": str(away),
                "buts_dom": hg,
                "buts_ext": ag,
                "source": "OpenFootball",
            })

    return matches


@st.cache_data(ttl=1800, show_spinner=False)
def load_openfootball_results(slugs, target_iso):
    target = date.fromisoformat(target_iso)
    matches = []
    errors = []

    start_year = (
        target.year if target.month >= 7 else target.year - 1
    )
    seasons = [
        f"{start_year}-{start_year + 1}",
        f"{start_year - 1}-{start_year}",
    ]

    for slug in slugs:
        found = False

        for season in seasons:
            url = f"{OPENFOOTBALL_BASE}/{season}/{slug}.json"

            try:
                response = requests.get(
                    url, headers=HEADERS, timeout=TIMEOUT
                )

                if response.status_code == 404:
                    continue

                response.raise_for_status()
                parsed = parse_openfootball_matches(
                    response.json(), target
                )

                if parsed:
                    matches.extend(parsed)
                    found = True
                    break

            except (requests.RequestException, ValueError) as exc:
                errors.append(
                    f"OpenFootball {slug}: {type(exc).__name__}"
                )

        if not found:
            errors.append(
                f"OpenFootball : aucun résultat exploitable pour {slug}."
            )

    return matches, errors
