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

def match_team_name(candidate, requested):
    a = normalize(candidate)
    b = normalize(requested)

    if not a or not b:
        return False

    if a == b:
        return True

    return min(len(a), len(b)) >= 6 and (a in b or b in a)


def team_history(matches, team, venue=None, limit=10):
    rows = []

    for match in matches:
        home = match_team_name(match["domicile"], team)
        away = match_team_name(match["exterieur"], team)

        if home == away:
            continue

        if not home and not away:
            continue

        if venue == "domicile" and not home:
            continue

        if venue == "extérieur" and not away:
            continue

        if home:
            scored = match["buts_dom"]
            conceded = match["buts_ext"]
            opponent = match["exterieur"]
            location = "Domicile"
        else:
            scored = match["buts_ext"]
            conceded = match["buts_dom"]
            opponent = match["domicile"]
            location = "Extérieur"

        result = (
            "V" if scored > conceded
            else "N" if scored == conceded
            else "D"
        )

        rows.append({
            "Date": match["date"],
            "Adversaire": opponent,
            "Lieu": location,
            "Buts marqués": scored,
            "Buts encaissés": conceded,
            "Résultat": result,
            "Source": match["source"],
        })

    rows.sort(key=lambda row: row["Date"], reverse=True)
    return rows[:limit]


def weighted_stats(rows):
    if not rows:
        return None

    # Les matchs les plus récents pèsent davantage.
    weights = list(range(len(rows), 0, -1))
    denominator = sum(weights)

    goals_for = sum(
        row["Buts marqués"] * weight
        for row, weight in zip(rows, weights)
    ) / denominator

    goals_against = sum(
        row["Buts encaissés"] * weight
        for row, weight in zip(rows, weights)
    ) / denominator

    points = sum(
        (
            3 if row["Résultat"] == "V"
            else 1 if row["Résultat"] == "N"
            else 0
        ) * weight
        for row, weight in zip(rows, weights)
    ) / denominator

    return {
        "nombre": len(rows),
        "forme": points / 3 * 100,
        "marques": goals_for,
        "encaisses": goals_against,
        "sans_encaisser": (
            sum(row["Buts encaissés"] == 0 for row in rows)
            / len(rows) * 100
        ),
    }



def predict(home_rows, away_rows, home_absent=None, away_absent=None):
    # Normaliser les listes d'absents.
    home_absent = home_absent or []
    away_absent = away_absent or []

    # Il faut au moins trois matchs par équipe.
    if len(home_rows) < 3 or len(away_rows) < 3:
        return None

    hs = weighted_stats(home_rows)
    aws = weighted_stats(away_rows)

    home_venue = [
        row for row in home_rows
        if row["Lieu"] == "Domicile"
    ]
    away_venue = [
        row for row in away_rows
        if row["Lieu"] == "Extérieur"
    ]

    hvs = (
        weighted_stats(home_venue)
        if len(home_venue) >= 3 else hs
    )
    avs = (
        weighted_stats(away_venue)
        if len(away_venue) >= 3 else aws
    )

    expected_home = max(
        0.15,
        min(
            3.8,
            0.55 * hvs["marques"]
            + 0.35 * avs["encaisses"]
            + 0.15
        )
    )

    expected_away = max(
        0.15,
        min(
            3.8,
            0.55 * avs["marques"]
            + 0.35 * hvs["encaisses"]
        )
    )

    matrix = [
        (
            poisson(expected_home, i)
            * poisson(expected_away, j),
            i,
            j,
        )
        for i in range(8)
        for j in range(8)
    ]

    p1 = sum(p for p, i, j in matrix if i > j)
    pn = sum(p for p, i, j in matrix if i == j)
    p2 = sum(p for p, i, j in matrix if i < j)

    total = p1 + pn + p2

    return {
        "home_stats": hs,
        "away_stats": aws,
        "scores": sorted(matrix, reverse=True)[:5],
        "probabilities": (
            p1 / total * 100,
            pn / total * 100,
            p2 / total * 100,
        ),
        "expected_goals": (expected_home, expected_away),
        "home_venue_count": len(home_venue),
        "away_venue_count": len(away_venue),
        "home_absent": home_absent,
        "away_absent": away_absent,
    }
    


def fetch_matches(league, target):
    matches = []
    errors = []

    if league in LEAGUES:
        code = LEAGUES[league]

        csv_matches, csv_errors = load_csv_results(
            code, target.isoformat()
        )
        matches.extend(csv_matches)
        errors.extend(csv_errors)

    elif league in OPENFOOTBALL_LEAGUES:
        of_matches, of_errors = load_openfootball_results(
            tuple(OPENFOOTBALL_LEAGUES[league]),
            target.isoformat(),
        )
        matches.extend(of_matches)
        errors.extend(of_errors)

    unique = {}

    for match in matches:
        key = (
            match["date"],
            normalize(match["domicile"]),
            normalize(match["exterieur"]),
            match["buts_dom"],
            match["buts_ext"],
        )
        unique.setdefault(key, match)

    return (
        sorted(
            unique.values(),
            key=lambda item: item["date"],
            reverse=True,
        ),
        errors,
)
   
# =========================
# PARTIE 4/4 : INTERFACE
# =========================

st.subheader("1. Choisir la compétition")

league = st.selectbox("Compétition", ALL_LEAGUES)

col1, col2 = st.columns(2)

with col1:
    home_name = st.text_input("Équipe à domicile", placeholder="Ex. : PSG")

with col2:
    away_name = st.text_input("Équipe à l'extérieur", placeholder="Ex. : Lyon")

match_date = st.date_input("Date du match")
match_time = st.time_input("Heure locale du match")

with st.expander("Cotes des bookmakers (facultatif)"):
    odd_home = st.number_input(
        "Cote victoire domicile", min_value=0.0, value=0.0, step=0.05
    )
    odd_draw = st.number_input(
        "Cote match nul", min_value=0.0, value=0.0, step=0.05
    )
    odd_away = st.number_input(
        "Cote victoire extérieur", min_value=0.0, value=0.0, step=0.05
    )

if st.button("⚽ Analyser le match", type="primary"):
    if not home_name.strip() or not away_name.strip():
        st.error("Saisis les noms des deux équipes.")
    elif normalize(home_name) == normalize(away_name):
        st.error("Choisis deux équipes différentes.")
    else:
        with st.spinner("Recherche des résultats disponibles..."):
            matches, errors = fetch_matches(league)

        home_rows = team_history(matches, home_name, limit=10)
        away_rows = team_history(matches, away_name, limit=10)

        st.subheader("2. Vérification des données")
        st.write(f"Compétition sélectionnée : **{league}**")
        st.write(f"Match à analyser : **{home_name} – {away_name}**")
        st.write(f"Date prévue : **{match_date} à {match_time}**")
        st.write(f"Résultats récupérés : **{len(matches)}**")
        st.write(f"Matchs trouvés pour {home_name} : **{len(home_rows)}**")
        st.write(f"Matchs trouvés pour {away_name} : **{len(away_rows)}**")

        if errors:
            with st.expander("Sources et éventuels problèmes"):
                for error in errors:
                    st.warning(error)

        result = predict(home_rows, away_rows)

        if result is None:
            st.warning(
                "Données insuffisantes pour produire une prédiction fiable. "
                "Il faut au minimum 3 résultats exploitables pour chaque équipe. "
                "Aucun score ne sera inventé."
            )
        else:
            st.subheader("3. Probabilités du résultat")

            probs = result["probabilities"]
            c1, c2, c3 = st.columns(3)

            c1.metric("Victoire domicile", f'{probs["1"]:.1f}%')
            c2.metric("Match nul", f'{probs["N"]:.1f}%')
            c3.metric("Victoire extérieur", f'{probs["2"]:.1f}%')

            st.caption(
                "Ces pourcentages sont des estimations statistiques, "
                "pas des garanties de résultat."
            )

            st.subheader("4. Scores exacts les plus probables")

            score_cols = st.columns(min(3, len(result["scores"])))

            for index, (score, probability) in enumerate(result["scores"][:3]):
                score_cols[index].metric(
                    f"Option {index + 1}",
                    score,
                    f"{probability:.1f}% estimés"
                )

            st.write(
                f"Buts attendus — {home_name} : "
                f'**{result["lambda_home"]:.2f}**'
            )
            st.write(
                f"Buts attendus — {away_name} : "
                f'**{result["lambda_away"]:.2f}**'
            )

            st.subheader("5. Forme récente et statistiques")

            stats_data = []

            for team, history in [
                (home_name, home_rows),
                (away_name, away_rows)
            ]:
                stats = weighted_stats(history)

                if stats:
                    stats_data.append({
                        "Équipe": team,
                        "Matchs analysés": stats["played"],
                        "Victoires": stats["wins"],
                        "Nuls": stats["draws"],
                        "Défaites": stats["losses"],
                        "Buts marqués/match": round(stats["scored"], 2),
                        "Buts encaissés/match": round(stats["conceded"], 2)
                    })

            if stats_data:
                st.dataframe(
                    pd.DataFrame(stats_data),
                    use_container_width=True,
                    hide_index=True
                )

            with st.expander(f"Historique récent — {home_name}"):
                if home_rows:
                    st.dataframe(
                        pd.DataFrame(home_rows),
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Aucun résultat récent trouvé pour cette équipe.")

            with st.expander(f"Historique récent — {away_name}"):
                if away_rows:
                    st.dataframe(
                        pd.DataFrame(away_rows),
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Aucun résultat récent trouvé pour cette équipe.")

            odds = [odd_home, odd_draw, odd_away]

            if all(odd > 1.0 for odd in odds):
                implied = [100 / odd for odd in odds]
                total = sum(implied)

                st.subheader("6. Comparaison indicative des cotes")
                st.write(
                    "Probabilités brutes implicites des cotes, "
                    "avant correction de la marge du bookmaker :"
                )

                odds_data = pd.DataFrame({
                    "Résultat": [
                        "Victoire domicile",
                        "Match nul",
                        "Victoire extérieur"
                    ],
                    "Cote": odds,
                    "Probabilité brute (%)": [
                        round(value, 1) for value in implied
                    ],
                    "Probabilité normalisée (%)": [
                        round(value / total * 100, 1)
                        for value in implied
                    ]
                })

                st.dataframe(
                    odds_data,
                    use_container_width=True,
                    hide_index=True
                )

st.divider()

st.caption(
    "Sources prévues : football-data.co.uk et OpenFootball. "
    "La disponibilité dépend de la compétition et des fichiers accessibles. "
    "Les absences, compositions officielles et cotes en direct ne sont pas "
    "automatiquement confirmées par cette version. Vérifie les données "
    "avant toute décision."
    )
    
# =========================
# MODULE : SELECTION MANUELLE DES ABSENTS
# =========================

st.subheader("7. Sélection des joueurs absents")

st.info(
    "Coche les joueurs absents pour chaque équipe. "
    "Cette sélection est manuelle : vérifie les informations avant le match."
)

col_home, col_away = st.columns(2)

with col_home:
    st.markdown("### Équipe à domicile")
    home_roster_text = st.text_area(
        "Liste des joueurs à domicile (un nom par ligne)",
        key="home_roster_text",
        placeholder="Colle ici les noms des joueurs..."
    )

with col_away:
    st.markdown("### Équipe à l'extérieur")
    away_roster_text = st.text_area(
        "Liste des joueurs à l'extérieur (un nom par ligne)",
        key="away_roster_text",
        placeholder="Colle ici les noms des joueurs..."
    )

home_players = [
    name.strip()
    for name in home_roster_text.splitlines()
    if name.strip()
]

away_players = [
    name.strip()
    for name in away_roster_text.splitlines()
    if name.strip()
]

home_absent = []
away_absent = []

col_home, col_away = st.columns(2)

with col_home:
    st.markdown("**Coche les absents à domicile**")
    for i, player in enumerate(home_players):
        if st.checkbox(player, key=f"home_absent_{i}_{player}"):
            home_absent.append(player)

with col_away:
    st.markdown("**Coche les absents à l'extérieur**")
    for i, player in enumerate(away_players):
        if st.checkbox(player, key=f"away_absent_{i}_{player}"):
            away_absent.append(player)

st.write(f"Absents à domicile : {len(home_absent)}")
st.write(f"Absents à l'extérieur : {len(away_absent)}")

with st.expander("Vérifier les sélections"):
    st.write("Absents à domicile :", home_absent or "Aucun sélectionné")
    st.write("Absents à l'extérieur :", away_absent or "Aucun sélectionné")

st.caption(
    "Pour le moment, ce module affiche et mémorise les sélections "
    "pendant la session. Les absences ne sont pas encore intégrées "
    "au calcul des probabilités."
)

