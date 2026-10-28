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
    "Les probabilités sont des estimations. Les données indisponibles "
    "ne sont jamais remplacées par des statistiques inventées."
)

HEADERS = {"User-Agent": "Mozilla/5.0 FootballPredictionApp/3.0"}
TIMEOUT = 12
FD_BASE = "https://www.football-data.co.uk/mmz4281"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# Compétitions avec fichiers de résultats football-data.co.uk.
LEAGUES = {
    "Angleterre - Premier League": ("E0", "eng.1"),
    "Angleterre - Championship": ("E1", "eng.2"),
    "Écosse - Premiership": ("SC0", "sco.1"),
    "Allemagne - Bundesliga": ("D1", "ger.1"),
    "Allemagne - 2. Bundesliga": ("D2", "ger.2"),
    "Italie - Serie A": ("I1", "ita.1"),
    "Italie - Serie B": ("I2", "ita.2"),
    "Espagne - La Liga": ("SP1", "esp.1"),
    "Espagne - Segunda División": ("SP2", "esp.2"),
    "France - Ligue 1": ("F1", "fra.1"),
    "France - Ligue 2": ("F2", "fra.2"),
    "Pays-Bas - Eredivisie": ("N1", "ned.1"),
    "Belgique - Pro League": ("B1", "bel.1"),
    "Portugal - Primeira Liga": ("P1", "por.1"),
    "Turquie - Süper Lig": ("T1", "tur.1"),
    "Grèce - Super League": ("G1", "gre.1"),
}

ESPN_LEAGUES = {
    "Ligue des champions": "uefa.champions",
    "Ligue Europa": "uefa.europa",
    "États-Unis - MLS": "usa.1",
    "Arabie saoudite - Saudi Pro League": "ksa.1",
    "Danemark - Superliga": "den.1",
    "Russie - Premier League": "rus.1",
    "Coupe du monde": "fifa.world",
    "Brésil - Série A": "bra.1",
    "Mexique - Liga MX": "mex.1",
}

ALL_LEAGUES = list(LEAGUES) + list(ESPN_LEAGUES)


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", value.lower())


def get_json(url, params=None):
    try:
        response = requests.get(
            url, params=params, headers=HEADERS, timeout=TIMEOUT
        )
        response.raise_for_status()
        return response.json(), None
    except (requests.RequestException, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def season_codes(target):
    """Saisons courante et précédente au format football-data."""
    start = target.year if target.month >= 7 else target.year - 1
    years = [start, start - 1]
    return [
        f"{year % 100:02d}{(year + 1) % 100:02d}"
        for year in years
    ]


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
                StringIO(response.text), on_bad_lines="skip"
            )

            required = {"HomeTeam", "AwayTeam", "FTHG", "FTAG", "Date"}
            if not required.issubset(df.columns):
                errors.append(f"{season}: colonnes attendues absentes")
                continue

            df["_date"] = pd.to_datetime(
                df["Date"], dayfirst=True, errors="coerce"
            )
            df["_hg"] = pd.to_numeric(df["FTHG"], errors="coerce")
            df["_ag"] = pd.to_numeric(df["FTAG"], errors="coerce")

            df = df.dropna(
                subset=["_date", "_hg", "_ag", "HomeTeam", "AwayTeam"]
            )
            df = df[df["_date"].dt.date < target]
            frames.append(df)

        except Exception as exc:
            errors.append(f"{season}: {type(exc).__name__}")

    if not frames:
        return [], errors or ["Aucun fichier exploitable reçu."]

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


def parse_espn_events(data, target):
    matches = []

    for event in (data or {}).get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue

        competition = competitions[0]
        competitors = competition.get("competitors") or []
        if len(competitors) < 2:
            continue

        home = next(
            (x for x in competitors if x.get("homeAway") == "home"),
            competitors[0],
        )
        away = next(
            (x for x in competitors if x.get("homeAway") == "away"),
            competitors[1],
        )

        status = (
            (competition.get("status") or {})
            .get("type", {})
            .get("completed")
        )
        if status is False:
            continue

        try:
            hg = int(float(home["score"]))
            ag = int(float(away["score"]))
        except (KeyError, TypeError, ValueError):
            continue

        event_date = pd.to_datetime(
            event.get("date"), utc=True, errors="coerce"
        )
        if pd.isna(event_date):
            continue

        # N'utilise que les matchs antérieurs à la date analysée.
        if event_date.date() >= target:
            continue

        hn = (home.get("team") or {}).get("displayName", "")
        an = (away.get("team") or {}).get("displayName", "")

        if hn and an:
            matches.append({
                "date": event_date.date(),
                "domicile": hn,
                "exterieur": an,
                "buts_dom": hg,
                "buts_ext": ag,
                "source": "ESPN",
            })

    return matches


@st.cache_data(ttl=1800, show_spinner=False)
def load_espn_results(slug, target_iso):
    target = date.fromisoformat(target_iso)
    matches = []
    errors = []

    # Recherche mensuelle : moins de requêtes que par jour.
    start = pd.Timestamp(target) - pd.Timedelta(days=180)

    periods = pd.date_range(
        start=start.normalize(),
        end=pd.Timestamp(target).normalize(),
        freq="MS",
    )

    intervals = []
    cursor = start.normalize()

    for month_start in periods:
        if month_start > cursor:
            intervals.append((cursor, month_start - pd.Timedelta(days=1)))
        cursor = month_start

    intervals.append((cursor, pd.Timestamp(target) - pd.Timedelta(days=1)))

    for begin, end in intervals:
        if begin > end:
            continue

        params = {
            "dates": f"{begin:%Y%m%d}-{end:%Y%m%d}",
            "limit": 500,
        }

        data, error = get_json(
            f"{ESPN_BASE}/{slug}/scoreboard", params
        )

        if data:
            matches.extend(parse_espn_events(data, target))
        elif error:
            errors.append(error)

    unique = {}
    for match in matches:
        key = (
            match["date"],
            normalize(match["domicile"]),
            normalize(match["exterieur"]),
            match["buts_dom"],
            match["buts_ext"],
        )
        unique[key] = match

    return list(unique.values()), errors


def match_team_name(candidate, requested):
    a, b = normalize(candidate), normalize(requested)
    if not a or not b:
        return False
    if a == b:
        return True
    # Correspondance partielle seulement si les deux noms sont assez longs.
    return min(len(a), len(b)) >= 6 and (a in b or b in a)


def team_history(matches, team, venue=None, limit=10):
    rows = []

    for match in matches:
        home = match_team_name(match["domicile"], team)
        away = match_team_name(match["exterieur"], team)

        if home and away:
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
    if len(rows) < 1:
        return None

    # Le match le plus récent a le poids le plus élevé.
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
        (3 if row["Résultat"] == "V"
         else 1 if row["Résultat"] == "N" else 0) * weight
        for row, weight in zip(rows, weights)
    ) / denominator

    return {
        "nombre": len(rows),
        "forme": points / 3 * 100,
        "marques": goals_for,
        "encaisses": goals_against,
        "sans_encaisser": (
            sum(r["Buts encaissés"] == 0 for r in rows)
            / len(rows) * 100
        ),
    }


def poisson(lam, goals):
    return math.exp(-lam) * lam**goals / math.factorial(goals)


def predict(home_rows, away_rows):
    # Trois matchs au minimum ; idéalement dix.
    if len(home_rows) < 3 or len(away_rows) < 3:
        return None

    hs = weighted_stats(home_rows)
    aws = weighted_stats(away_rows)

    home_venue = [
        row for row in home_rows if row["Lieu"] == "Domicile"
    ]
    away_venue = [
        row for row in away_rows if row["Lieu"] == "Extérieur"
    ]

    hvs = weighted_stats(home_venue) if len(home_venue) >= 3 else hs
    avs = weighted_stats(away_venue) if len(away_venue) >= 3 else aws

    expected_home = max(
        0.15, min(3.8, 0.55 * hvs["marques"] + 0.35 * avs["encaisses"] + 0.15)
    )
    expected_away = max(
        0.15, min(3.8, 0.55 * avs["marques"] + 0.35 * hvs["encaisses"])
    )

    matrix = [
        (
            poisson(expected_home, i) * poisson(expected_away, j),
            i, j
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
    }


def fetch_matches(league, target):
    matches, errors = [], []

    if league in LEAGUES:
        code, slug = LEAGUES[league]
        csv_matches, csv_errors = load_csv_results(
            code, target.isoformat()
        )
        matches.extend(csv_matches)
        errors.extend(csv_errors)
    else:
        slug = ESPN_LEAGUES[league]

    # ESPN sert de complément lorsque des résultats sont disponibles.
    espn_matches, espn_errors = load_espn_results(
        slug, target.isoformat()
    )
    matches.extend(espn_matches)
    errors.extend(espn_errors)

    unique = {}
    for match in matches:
        key = (
            match["date"],
            normalize(match["domicile"]),
            normalize(match["exterieur"]),
            match["buts_dom"],
            match["buts_ext"],
        )
        unique[key] = match

    return list(unique.values()), errors


st.subheader("1. Choisir la compétition")
league = st.selectbox("Compétition", ALL_LEAGUES)

st.subheader("2. Choisir les équipes")
c1, c2 = st.columns(2)
home_name = c1.text_input(
    "Équipe à domicile", placeholder="Ex. AC Milan"
)
away_name = c2.text_input(
    "Équipe à l'extérieur", placeholder="Ex. Bologna"
)

with st.form("analysis_form"):
    st.subheader("3. Date, heure et cotes")
    c1, c2 = st.columns(2)
    match_date = c1.date_input("Date du match", value=date.today())
    match_time = c2.time_input("Heure locale", value=time(20, 30))

    st.caption("Cotes facultatives, dans l'ordre 1-N-2.")
    odds_cols = st.columns(3)
    odd1 = odds_cols[0].number_input(
        "Cote 1", min_value=0.0, value=0.0, step=0.05
    )
    oddn = odds_cols[1].number_input(
        "Cote N", min_value=0.0, value=0.0, step=0.05
    )
    odd2 = odds_cols[2].number_input(
        "Cote 2", min_value=0.0, value=0.0, step=0.05
    )

    submitted = st.form_submit_button(
        "🔎 Analyser le match", use_container_width=True
    )


if submitted:
    if not home_name.strip() or not away_name.strip():
        st.error("Entre le nom des deux équipes.")
    elif normalize(home_name) == normalize(away_name):
        st.error("Choisis deux équipes différentes.")
    elif match_date > date.today():
        st.warning(
            "La date du match est future. Seuls les matchs antérieurs "
            "à cette date seront utilisés, si la source les fournit."
        )

    if (
        submitted
        and home_name.strip()
        and away_name.strip()
        and normalize(home_name) != normalize(away_name)
    ):
        with st.spinner("Recherche des résultats publics…"):
            matches, errors = fetch_matches(league, match_date)

        home_rows = team_history(matches, home_name, limit=10)
        away_rows = team_history(matches, away_name, limit=10)
        result = predict(home_rows, away_rows)

        st.subheader(f"{home_name} — {away_name}")
        st.caption(
            f"Match saisi : {match_date:%d/%m/%Y} à {match_time:%H:%M}"
        )
        st.write(
            f"Résultats trouvés : {home_name} : {len(home_rows)} · "
            f"{away_name} : {len(away_rows)}"
        )

        if errors:
            with st.expander("Diagnostic des sources"):
                for error in errors[:12]:
                    st.write(f"• {error}")

        if result is None:
            st.warning(
                "Pronostic indisponible : il faut au moins trois "
                "résultats terminés exploitables par équipe. "
                "Aucun score n'a été inventé."
            )
        else:
            p1, pn, p2 = result["probabilities"]
            cols = st.columns(3)
            cols[0].metric("Victoire domicile (1)", f"{p1:.1f} %")
            cols[1].metric("Match nul (N)", f"{pn:.1f} %")
            cols[2].metric("Victoire extérieur (2)", f"{p2:.1f} %")

            st.subheader("Scores les plus probables")
            cols = st.columns(5)
            for col, (prob, goals_home, goals_away) in zip(
                cols, result["scores"]
            ):
                col.metric(
                    "Score",
                    f"{goals_home} - {goals_away}",
                    f"{prob * 100:.1f} % du modèle",
                )

            st.caption(
                f"Buts attendus : domicile "
                f"{result['expected_goals'][0]:.2f} · extérieur "
                f"{result['expected_goals'][1]:.2f}"
            )

            st.subheader("Forme, attaque et défense")
            hs = result["home_stats"]
            aws = result["away_stats"]

            st.dataframe(pd.DataFrame([
                {
                    "Équipe": home_name,
                    "Matchs": hs["nombre"],
                    "Forme pondérée": f"{hs['forme']:.1f} %",
                    "Buts marqués/match": f"{hs['marques']:.2f}",
                    "Buts encaissés/match": f"{hs['encaisses']:.2f}",
                    "Matchs sans encaisser": f"{hs['sans_encaisser']:.1f} %",
                    "Matchs à domicile trouvés": result["home_venue_count"],
                },
                {
                    "Équipe": away_name,
                    "Matchs": aws["nombre"],
                    "Forme pondérée": f"{aws['forme']:.1f} %",
                    "Buts marqués/match": f"{aws['marques']:.2f}",
                    "Buts encaissés/match": f"{aws['encaisses']:.2f}",
                    "Matchs sans encaisser": f"{aws['sans_encaisser']:.1f} %",
                    "Matchs à l'extérieur trouvés": result["away_venue_count"],
                },
            ]), use_container_width=True, hide_index=True)

            st.subheader("Résultats réellement utilisés")
            with st.expander(f"Matchs de {home_name}"):
                st.dataframe(
                    pd.DataFrame(home_rows),
                    use_container_width=True,
                    hide_index=True,
                )
            with st.expander(f"Matchs de {away_name}"):
                st.dataframe(
                    pd.DataFrame(away_rows),
                    use_container_width=True,
                    hide_index=True,
                )

            st.subheader("Comparaison facultative avec les cotes")
            if all(odd > 1 for odd in (odd1, oddn, odd2)):
                raw = [1 / odd1, 1 / oddn, 1 / odd2]
                total = sum(raw)
                market = [value / total * 100 for value in raw]

                for col, label, market_pct, model_pct in zip(
                    st.columns(3),
                    ("1 domicile", "N nul", "2 extérieur"),
                    market,
                    result["probabilities"],
                ):
                    col.metric(
                        label,
                        f"Modèle {model_pct:.1f} %",
                        f"Marché {market_pct:.1f} %",
                    )
            else:
                st.caption(
                    "Entre trois cotes supérieures à 1 pour afficher "
                    "la comparaison avec le marché."
                )

st.divider()
st.caption(
    "Sources publiques : football-data.co.uk et ESPN. "
    "Les compétitions, saisons et noms d'équipes couverts varient. "
    "Les absences et compositions ne sont pas encore intégrées "
    "au calcul faute de données fiables et uniformes."
)
