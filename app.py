import math
import re
from datetime import date, time, timedelta
from urllib.parse import quote
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Générateur de pronostics football",
    page_icon="⚽",
    layout="wide",
)

st.title("⚽ Générateur de pronostics football")
st.write("Choisis une compétition et deux équipes. Les données manquantes ne sont pas inventées.")
st.info("Les sources publiques peuvent être incomplètes. Un pronostic reste une estimation.")

HEADERS = {"User-Agent": "Mozilla/5.0 FootballPredictionApp/1.0"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"

LEAGUES = {
    "Ligue 1 (France)": "fra.1",
    "Premier League (Angleterre)": "eng.1",
    "Liga (Espagne)": "esp.1",
    "Serie A (Italie)": "ita.1",
    "Bundesliga (Allemagne)": "ger.1",
    "Süper Lig (Turquie)": "tur.1",
    "Super League (Grèce)": "gre.1",
    "Premier League (Russie)": "rus.1",
    "Superliga (Danemark)": "den.1",
    "Eredivisie (Pays-Bas)": "ned.1",
    "Ligue des champions": "uefa.champions",
    "Ligue Europa": "uefa.europa",
    "MLS (États-Unis)": "usa.1",
    "Saudi Pro League": "ksa.1",
    "Coupe du monde": "fifa.world",
}


def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def get_json(url, params=None):
    try:
        response = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=(4, 10),
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None
    except (requests.RequestException, ValueError):
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def league_teams(slug):
    data = get_json(f"{ESPN}/{slug}/teams?limit=1000") or {}
    found = {}

    def walk(obj):
        if isinstance(obj, dict):
            tid = obj.get("id")
            name = (
                obj.get("displayName")
                or obj.get("shortDisplayName")
                or obj.get("name")
            )
            if tid and name:
                found[str(tid)] = {
                    "id": str(tid),
                    "name": str(name),
                    "slug": slug,
                }
            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)
    return sorted(found.values(), key=lambda x: x["name"].casefold())


def parse_schedule(data, team_name):
    rows = []

    if not isinstance(data, dict):
        return rows

    events = data.get("events") or data.get("schedule") or []
    target = norm(team_name)

    for event in events:
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

        ht = home.get("team") or {}
        at = away.get("team") or {}

        home_name = ht.get("displayName") or ht.get("name") or ""
        away_name = at.get("displayName") or at.get("name") or ""

        home_match = norm(home_name) == target
        away_match = norm(away_name) == target

        # Correspondance partielle pour les noms légèrement différents.
        if not home_match and len(target) >= 6:
            home_match = target in norm(home_name) or norm(home_name) in target

        if not away_match and len(target) >= 6:
            away_match = target in norm(away_name) or norm(away_name) in target

        if not (home_match or away_match):
            continue

        status = (
            (competition.get("status") or {})
            .get("type", {})
            .get("completed")
        )
        if status is False:
            continue

        try:
            home_goals = int(float(home["score"]))
            away_goals = int(float(away["score"]))
        except (KeyError, TypeError, ValueError):
            continue

        match_date = pd.to_datetime(
            event.get("date") or competition.get("date"),
            utc=True,
            errors="coerce",
        )

        if pd.isna(match_date):
            continue

        # Ne jamais utiliser un match futur.
        if match_date.date() > date.today():
            continue

        is_home = home_match

        goals_for, goals_against = (
            (home_goals, away_goals)
            if is_home
            else (away_goals, home_goals)
        )

        rows.append({
            "Date": match_date,
            "Adversaire": away_name if is_home else home_name,
            "Lieu": "Domicile" if is_home else "Extérieur",
            "Buts marqués": goals_for,
            "Buts encaissés": goals_against,
            "Résultat": (
                "V" if goals_for > goals_against
                else "N" if goals_for == goals_against
                else "D"
            ),
        })

    rows.sort(
        key=lambda row: (
            row["Date"].timestamp()
            if pd.notna(row["Date"])
            else 0
        ),
        reverse=True,
    )

    unique = {}
    for row in rows:
        key = (
            str(row["Date"]),
            norm(row["Adversaire"]),
            row["Buts marqués"],
            row["Buts encaissés"],
        )
        unique[key] = row

    return list(unique.values())[:10]


@st.cache_data(ttl=1800, show_spinner=False)
def recent_results(team_id, slug, name):
    """
    Première méthode : calendrier de l'équipe.
    Deuxième méthode : tableaux de scores ESPN sur les 180 derniers jours.
    """
    schedule_urls = [
        f"{ESPN}/{slug}/teams/{quote(str(team_id))}/schedule",
        f"{ESPN}/{slug}/teams/{quote(str(team_id))}/schedule"
        f"?season={date.today().year}",
    ]

    for url in schedule_urls:
        rows = parse_schedule(get_json(url), name)
        if rows:
            return rows[:10]

    # Méthode de secours : recherches par périodes de 30 jours.
    all_rows = []
    today = date.today()

    for days_back in range(180, 0, -30):
        start = today - timedelta(days=days_back)
        end = min(
            today - timedelta(days=max(days_back - 29, 1)),
            today - timedelta(days=1),
        )

        if start > end:
            continue

        params = {
            "dates": f"{start:%Y%m%d}-{end:%Y%m%d}",
            "limit": 500,
        }

        data = get_json(
            f"{ESPN}/{slug}/scoreboard",
            params=params,
        )

        if not data:
            continue

        all_rows.extend(parse_schedule(data, name))

    # Supprimer les doublons et trier par date.
    unique = {}

    for row in all_rows:
        key = (
            str(row["Date"]),
            norm(row["Adversaire"]),
            row["Buts marqués"],
            row["Buts encaissés"],
        )
        unique[key] = row

    rows = list(unique.values())
    rows.sort(
        key=lambda row: (
            row["Date"].timestamp()
            if pd.notna(row["Date"])
            else 0
        ),
        reverse=True,
    )

    return rows[:10]


@st.cache_data(ttl=1800, show_spinner=False)
def injuries(team_id, slug):
    data = get_json(
        f"{ESPN}/{slug}/teams/{quote(str(team_id))}/injuries"
    )

    if not data:
        return None

    found = []
    seen = set()

    def walk(obj):
        if isinstance(obj, dict):
            name = (
                obj.get("displayName")
                or obj.get("fullName")
                or obj.get("athleteName")
            )

            parts = [
                str(obj[key]).strip()
                for key in (
                    "status",
                    "type",
                    "details",
                    "description",
                    "date",
                )
                if isinstance(obj.get(key), (str, int, float))
                and str(obj[key]).strip()
            ]

            if name and parts:
                label = " — ".join(parts)
                item = (str(name), label)

                if item not in seen:
                    seen.add(item)
                    found.append({
                        "Joueur": str(name),
                        "Information publiée": label,
                    })

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)
    return found


@st.cache_data(ttl=1800, show_spinner=False)
def roster(team_id, slug):
    data = get_json(
        f"{ESPN}/{slug}/teams/{quote(str(team_id))}/roster"
    )

    if not data:
        return []

    found = {}

    def walk(obj):
        if isinstance(obj, dict):
            athlete = (
                obj.get("athlete")
                if isinstance(obj.get("athlete"), dict)
                else obj
            )

            name = (
                athlete.get("fullName")
                or athlete.get("displayName")
                or athlete.get("name")
            )
            player_id = athlete.get("id")

            if name and (
                player_id
                or "position" in athlete
                or "jersey" in athlete
            ):
                position = athlete.get("position") or {}

                if isinstance(position, dict):
                    position = (
                        position.get("displayName")
                        or position.get("name")
                        or "Poste non indiqué"
                    )

                found[str(player_id or name)] = {
                    "name": str(name),
                    "position": str(position),
                }

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)

    return sorted(
        found.values(),
        key=lambda x: x["name"].casefold(),
    )


def team_stats(rows):
    if not rows:
        return None

    weights = list(range(len(rows), 0, -1))
    denominator = sum(weights)

    points = sum(
        (
            3 if row["Résultat"] == "V"
            else 1 if row["Résultat"] == "N"
            else 0
        ) * weight
        for row, weight in zip(rows, weights)
    )

    goals_for = sum(
        row["Buts marqués"] * weight
        for row, weight in zip(rows, weights)
    ) / denominator

    goals_against = sum(
        row["Buts encaissés"] * weight
        for row, weight in zip(rows, weights)
    ) / denominator

    return {
        "forme": 100 * points / (3 * denominator),
        "marques": goals_for,
        "encaisses": goals_against,
        "nombre": len(rows),
    }


def poisson(lam, goals):
    return math.exp(-lam) * lam**goals / math.factorial(goals)


def predict(home_rows, away_rows):
    home_stats = team_stats(home_rows)
    away_stats = team_stats(away_rows)

    if home_stats is None or away_stats is None:
        return None

    # Utiliser les résultats à domicile/extérieur lorsqu'il y en a assez.
    home_venue = [
        row for row in home_rows
        if row["Lieu"] == "Domicile"
    ]
    away_venue = [
        row for row in away_rows
        if row["Lieu"] == "Extérieur"
    ]

    home_attack = (
        team_stats(home_venue)
        if len(home_venue) >= 3
        else home_stats
    )
    away_attack = (
        team_stats(away_venue)
        if len(away_venue) >= 3
        else away_stats
    )

    expected_home = max(
        0.15,
        min(
            3.5,
            0.65 * home_attack["marques"]
            + 0.35 * away_attack["encaisses"]
            + 0.12,
        ),
    )

    expected_away = max(
        0.15,
        min(
            3.5,
            0.65 * away_attack["marques"]
            + 0.35 * home_attack["encaisses"],
        ),
    )

    matrix = [
        (
            poisson(expected_home, i) * poisson(expected_away, j),
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

    return (
        home_stats,
        away_stats,
        sorted(matrix, reverse=True)[:5],
        (
            p1 / total * 100,
            pn / total * 100,
            p2 / total * 100,
        ),
    )


st.subheader("1. Compétition et équipes")

competition = st.selectbox("Compétition", list(LEAGUES))
slug = LEAGUES[competition]

with st.spinner("Chargement des équipes…"):
    teams = league_teams(slug)

home_team = None
away_team = None

if len(teams) >= 2:
    by_name = {team["name"]: team for team in teams}
    names = list(by_name)

    col1, col2 = st.columns(2)
    home_name = col1.selectbox("Équipe à domicile", names)
    away_name = col2.selectbox(
        "Équipe à l'extérieur",
        [name for name in names if name != home_name],
    )

    home_team = by_name[home_name]
    away_team = by_name[away_name]
else:
    st.warning(
        "ESPN ne fournit pas de liste d'équipes pour cette compétition. "
        "Essaie une autre compétition."
    )

selected_home = []
selected_away = []

if home_team and away_team:
    st.subheader("2. Joueurs absents — sélection manuelle")
    st.caption(
        "Coche uniquement les absences que tu as confirmées. "
        "Une liste indisponible ne signifie pas zéro absent."
    )

    col_home, col_away = st.columns(2)

    with col_home:
        with st.expander(f"Effectif — {home_team['name']}"):
            players = roster(home_team["id"], slug)

            if players:
                for player in players:
                    if st.checkbox(
                        f"{player['name']} — {player['position']}",
                        key=f"h_{home_team['id']}_{player['name']}",
                    ):
                        selected_home.append(player["name"])
            else:
                st.warning("Effectif nommé indisponible via ESPN.")

    with col_away:
        with st.expander(f"Effectif — {away_team['name']}"):
            players = roster(away_team["id"], slug)

            if players:
                for player in players:
                    if st.checkbox(
                        f"{player['name']} — {player['position']}",
                        key=f"a_{away_team['id']}_{player['name']}",
                    ):
                        selected_away.append(player["name"])
            else:
                st.warning("Effectif nommé indisponible via ESPN.")


with st.form("match_form"):
    st.subheader("3. Informations du match")

    date_col, time_col = st.columns(2)
    match_date = date_col.date_input(
        "Date du match",
        value=date.today(),
    )
    match_time = time_col.time_input(
        "Heure locale",
        value=time(20, 30),
    )

    st.caption("Cotes facultatives, ordre 1-N-2. Laisse 0 si tu ne les connais pas.")

    odds_cols = st.columns(3)
    odd1 = odds_cols[0].number_input(
        "Cote 1",
        min_value=0.0,
        value=0.0,
        step=0.05,
        format="%.2f",
    )
    oddn = odds_cols[1].number_input(
        "Cote N",
        min_value=0.0,
        value=0.0,
        step=0.05,
        format="%.2f",
    )
    odd2 = odds_cols[2].number_input(
        "Cote 2",
        min_value=0.0,
        value=0.0,
        step=0.05,
        format="%.2f",
    )

    submitted = st.form_submit_button(
        "🔎 Analyser le match",
        use_container_width=True,
        disabled=not (home_team and away_team),
    )


if submitted and home_team and away_team:
    progress = st.progress(0, text="Préparation")
    status = st.empty()

    status.write("Étape 1/4 : équipes sélectionnées.")
    progress.progress(15, text="15 % — Équipes sélectionnées")

    status.write("Étape 2/4 : récupération des derniers résultats.")

    with st.spinner("Recherche des résultats…"):
        hr = recent_results(
            home_team["id"], slug, home_team["name"]
        )
        ar = recent_results(
            away_team["id"], slug, away_team["name"]
        )

    progress.progress(55, text="55 % — Résultats recherchés")

    status.write("Étape 3/4 : vérification des rapports de blessures.")

    hi = injuries(home_team["id"], slug)
    ai = injuries(away_team["id"], slug)

    progress.progress(80, text="80 % — Données vérifiées")

    status.write("Étape 4/4 : calcul des probabilités.")
    result = predict(hr, ar)

    progress.progress(100, text="100 % — Analyse terminée")

    st.subheader(f"{home_team['name']} — {away_team['name']}")
    st.caption(
        f"Date saisie : {match_date:%d/%m/%Y} · "
        f"Heure locale : {match_time:%H:%M}"
    )

    if result is None:
        status.warning(
            "Pronostic impossible : les résultats récents sont insuffisants. "
            "Aucun score ni pourcentage n'a été inventé."
        )

        st.write(
            f"Résultats récupérés pour {home_team['name']} : {len(hr)}"
        )
        st.write(
            f"Résultats récupérés pour {away_team['name']} : {len(ar)}"
        )

        if not hr:
            st.warning(
                "Aucun résultat exploitable pour l'équipe à domicile. "
                "La source ESPN peut ne pas fournir son calendrier."
            )
        else:
            st.dataframe(
                pd.DataFrame(hr),
                use_container_width=True,
                hide_index=True,
            )

        if not ar:
            st.warning(
                "Aucun résultat exploitable pour l'équipe à l'extérieur. "
                "La source ESPN peut ne pas fournir son calendrier."
            )
        else:
            st.dataframe(
                pd.DataFrame(ar),
                use_container_width=True,
                hide_index=True,
            )

    else:
        hs, aws, scores, probs = result
        status.success("Analyse terminée.")

        p1, pn, p2 = st.columns(3)
        p1.metric("Victoire domicile (1)", f"{probs[0]:.1f} %")
        pn.metric("Match nul (N)", f"{probs[1]:.1f} %")
        p2.metric("Victoire extérieur (2)", f"{probs[2]:.1f} %")

        st.subheader("Scores les plus probables")
        score_cols = st.columns(len(scores))

        for col, (prob, goals_home, goals_away) in zip(
            score_cols, scores
        ):
            col.metric(
                "Score",
                f"{goals_home} - {goals_away}",
                f"{prob * 100:.1f} % du modèle",
            )

        st.subheader("Forme et buts observés")

        st.dataframe(
            pd.DataFrame([
                {
                    "Équipe": home_team["name"],
                    "Matchs disponibles": hs["nombre"],
                    "Forme pondérée": f"{hs['forme']:.1f} %",
                    "Buts marqués/match": f"{hs['marques']:.2f}",
                    "Buts encaissés/match": f"{hs['encaisses']:.2f}",
                },
                {
                    "Équipe": away_team["name"],
                    "Matchs disponibles": aws["nombre"],
                    "Forme pondérée": f"{aws['forme']:.1f} %",
                    "Buts marqués/match": f"{aws['marques']:.2f}",
                    "Buts encaissés/match": f"{aws['encaisses']:.2f}",
                },
            ]),
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Absences cochées")
        st.write(
            f"**{home_team['name']} :** "
            f"{', '.join(selected_home) if selected_home else 'aucun joueur coché'}"
        )
        st.write()
   
