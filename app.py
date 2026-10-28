import math
import re
from datetime import date, time, timedelta
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
st.info("Sources : SportScore (matchs, sans clé) + Openfootball (historique, sans clé). "
        "Les effectifs et blessures ne sont pas disponibles sans clé API.")

HEADERS = {"User-Agent": "Mozilla/5.0 FootballPredictionApp/1.0"}

# SportScore : pas de clé, mais attribution obligatoire
SPORTSCORE_BASE = "https://sportscore.com"
OPENFOOTBALL_RAW = "https://raw.githubusercontent.com/openfootball/football.json/master"

# Correspondance compétition → slug SportScore / fichier Openfootball
LEAGUES = {
    "Ligue 1 (France)": {
        "sportscore": "ligue-1",
        "openfootball": "fr.1",
    },
    "Premier League (Angleterre)": {
        "sportscore": "premier-league",
        "openfootball": "en.1",
    },
    "Liga (Espagne)": {
        "sportscore": "la-liga",
        "openfootball": "es.1",
    },
    "Serie A (Italie)": {
        "sportscore": "serie-a",
        "openfootball": "it.1",
    },
    "Bundesliga (Allemagne)": {
        "sportscore": "bundesliga",
        "openfootball": "de.1",
    },
    # Ajoute d'autres compétitions si SportScore/Openfootball les couvrent
}


def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def get_json(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=(4, 12))
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None


# ---------- SportScore ----------

@st.cache_data(ttl=1800, show_spinner=False)
def league_teams_sportscore(league_slug):
    """Récupère les équipes via le classement SportScore (pas d'endpoint équipes direct)."""
    data = get_json(f"{SPORTSCORE_BASE}/api/football/standings/{league_slug}")
    if not data:
        return []

    # La structure exacte peut varier ; on essaie plusieurs clés
    rows = None
    for key in ("data", "standings", "table", "rows"):
        if isinstance(data.get(key), list):
            rows = data[key]
            break

    if not rows:
        return []

    teams = []
    seen = set()
    for row in rows:
        team = row.get("team") if isinstance(row.get("team"), dict) else row
        name = team.get("name") or team.get("team_name")
        slug = team.get("slug") or team.get("id")
        if name and slug and norm(name) not in seen:
            seen.add(norm(name))
            teams.append({"name": str(name), "slug": str(slug)})

    return sorted(teams, key=lambda x: x["name"].casefold())


@st.cache_data(ttl=1800, show_spinner=False)
def recent_results_sportscore(team_slug, team_name):
    """Récupère les derniers matchs d'une équipe via SportScore team_schedule."""
    data = get_json(
        f"{SPORTSCORE_BASE}/api/football/team/{team_slug}/schedule",
        params={"limit": 15},
    )
    if not data:
        return []

    matches = data.get("data") or data.get("matches") or []
    if not isinstance(matches, list):
        return []

    rows = []
    for m in matches:
        status = (m.get("status") or "").lower()
        if status not in ("finished", "ft", "ended"):
            continue

        start = m.get("start_at") or m.get("date")
        dt = pd.to_datetime(start, utc=True, errors="coerce")
        if pd.isna(dt) or dt.date() > date.today():
            continue

        home = m.get("home_team") or {}
        away = m.get("away_team") or {}
        home_name = home.get("name") or home.get("name_short") or ""
        away_name = away.get("name") or away.get("name_short") or ""

        is_home = norm(home_name) == norm(team_name) or norm(team_name) in norm(home_name)
        is_away = norm(away_name) == norm(team_name) or norm(team_name) in norm(away_name)
        if not (is_home or is_away):
            continue

        hs = m.get("home_score")
        as_ = m.get("away_score")
        if hs is None or as_ is None:
            continue
        try:
            hs, as_ = int(hs), int(as_)
        except (TypeError, ValueError):
            continue

        goals_for = hs if is_home else as_
        goals_against = as_ if is_home else hs

        rows.append({
            "Date": dt,
            "Adversaire": away_name if is_home else home_name,
            "Lieu": "Domicile" if is_home else "Extérieur",
            "Buts marqués": goals_for,
            "Buts encaissés": goals_against,
            "Résultat": "V" if goals_for > goals_against else "N" if goals_for == goals_against else "D",
        })

    rows.sort(key=lambda r: r["Date"].timestamp(), reverse=True)
    return rows[:10]


# ---------- Openfootball (secours) ----------

@st.cache_data(ttl=3600, show_spinner=False)
def recent_results_openfootball(team_name, openfootball_file):
    """Récupère les matchs terminés depuis openfootball (saison en cours puis précédente)."""
    current_year = date.today().year
    seasons = [f"{current_year - 1}-{str(current_year)[-2:]}", f"{current_year - 2}-{str(current_year - 1)[-2:]}"]

    all_rows = []
    for season in seasons:
        url = f"{OPENFOOTBALL_RAW}/{season}/{openfootball_file}.json"
        data = get_json(url)
        if not data or "rounds" not in data:
            continue

        target = norm(team_name)
        for rnd in data.get("rounds", []):
            for m in rnd.get("matches", []):
                t1 = m.get("team1", "")
                t2 = m.get("team2", "")
                if norm(t1) != target and norm(t2) != target:
                    # correspondance partielle
                    if len(target) < 6:
                        continue
                    if target not in norm(t1) and norm(t1) not in target and target not in norm(t2) and norm(t2) not in target:
                        continue

                score = m.get("score") or {}
                ft = score.get("ft")
                if not ft or len(ft) != 2:
                    continue

                dt = pd.to_datetime(m.get("date"), utc=True, errors="coerce")
                if pd.isna(dt) or dt.date() > date.today():
                    continue

                is_home = norm(t1) == target or target in norm(t1)
                goals_for = ft[0] if is_home else ft[1]
                goals_against = ft[1] if is_home else ft[0]

                all_rows.append({
                    "Date": dt,
                    "Adversaire": t2 if is_home else t1,
                    "Lieu": "Domicile" if is_home else "Extérieur",
                    "Buts marqués": goals_for,
                    "Buts encaissés": goals_against,
                    "Résultat": "V" if goals_for > goals_against else "N" if goals_for == goals_against else "D",
                })

        if all_rows:
            break  # saison en cours suffisante

    unique = {}
    for row in all_rows:
        key = (str(row["Date"]), norm(row["Adversaire"]), row["Buts marqués"], row["Buts encaissés"])
        unique[key] = row
    rows = list(unique.values())
    rows.sort(key=lambda r: r["Date"].timestamp(), reverse=True)
    return rows[:10]


# ---------- Effectifs / blessures : pas de source gratuite sans clé ----------

def roster(team_id, league_id):
    return []


def injuries(team_id, league_id):
    return None


# ---------- Modèle (inchangé) ----------

def team_stats(rows):
    if not rows:
        return None
    weights = list(range(len(rows), 0, -1))
    denominator = sum(weights)
    points = sum((3 if r["Résultat"] == "V" else 1 if r["Résultat"] == "N" else 0) * w for r, w in zip(rows, weights))
    gf = sum(r["Buts marqués"] * w for r, w in zip(rows, weights)) / denominator
    ga = sum(r["Buts encaissés"] * w for r, w in zip(rows, weights)) / denominator
    return {"forme": 100 * points / (3 * denominator), "marques": gf, "encaisses": ga, "nombre": len(rows)}


def poisson(lam, goals):
    return math.exp(-lam) * lam**goals / math.factorial(goals)


def predict(home_rows, away_rows):
    hs = team_stats(home_rows)
    aws = team_stats(away_rows)
    if hs is None or aws is None:
        return None

    home_venue = [r for r in home_rows if r["Lieu"] == "Domicile"]
    away_venue = [r for r in away_rows if r["Lieu"] == "Extérieur"]
    home_attack = team_stats(home_venue) if len(home_venue) >= 3 else hs
    away_attack = team_stats(away_venue) if len(away_venue) >= 3 else aws

    exp_h = max(0.15, min(3.5, 0.65 * home_attack["marques"] + 0.35 * away_attack["encaisses"] + 0.12))
    exp_a = max(0.15, min(3.5, 0.65 * away_attack["marques"] + 0.35 * home_attack["encaisses"]))

    matrix = [(poisson(exp_h, i) * poisson(exp_a, j), i, j) for i in range(8) for j in range(8)]
    p1 = sum(p for p, i, j in matrix if i > j)
    pn = sum(p for p, i, j in matrix if i == j)
    p2 = sum(p for p, i, j in matrix if i < j)
    total = p1 + pn + p2
    return hs, aws, sorted(matrix, reverse=True)[:5], (p1 / total * 100, pn / total * 100, p2 / total * 100)


# ---------- Interface ----------

st.subheader("1. Compétition et équipes")

competition = st.selectbox("Compétition", list(LEAGUES))
config = LEAGUES[competition]

with st.spinner("Chargement des équipes via SportScore…"):
    teams = league_teams_sportscore(config["sportscore"])

home_team = None
away_team = None

if len(teams) >= 2:
    by_name = {t["name"]: t for t in teams}
    names = list(by_name)
    col1, col2 = st.columns(2)
    home_name = col1.selectbox("Équipe à domicile", names)
    away_name = col2.selectbox("Équipe à l'extérieur", [n for n in names if n != home_name])
    home_team = by_name[home_name]
    away_team = by_name[away_name]
else:
    st.warning("SportScore ne fournit pas de liste d'équipes pour cette compétition. Essaie une autre compétition.")

selected_home = []
selected_away = []

if home_team and away_team:
    st.subheader("2. Joueurs absents — sélection manuelle")
    st.caption("⚠️ Aucune source gratuite sans clé ne fournit les effectifs. Saisis les noms manuellement si besoin.")

    col_home, col_away = st.columns(2)
    with col_home:
        manual_home = st.text_area(f"Absents {home_team['name']} (un par ligne)", height=100)
        selected_home = [x.strip() for x in manual_home.splitlines() if x.strip()]
    with col_away:
        manual_away = st.text_area(f"Absents {away_team['name']} (un par ligne)", height=100)
        selected_away = [x.strip() for x in manual_away.splitlines() if x.strip()]


with st.form("match_form"):
    st.subheader("3. Informations du match")
    date_col, time_col = st.columns(2)
    match_date = date_col.date_input("Date du match", value=date.today())
    match_time = time_col.time_input("Heure locale", value=time(20, 30))

    st.caption("Cotes facultatives, ordre 1-N-2.")
    odds_cols = st.columns(3)
    odd1 = odds_cols[0].number_input("Cote 1", min_value=0.0, value=0.0, step=0.05, format="%.2f")
    oddn = odds_cols[1].number_input("Cote N", min_value=0.0, value=0.0, step=0.05, format="%.2f")
    odd2 = odds_cols[2].number_input("Cote 2", min_value=0.0, value=0.0, step=0.05, format="%.2f")

    submitted = st.form_submit_button(
        "🔎 Analyser le match",
        use_container_width=True,
        disabled=not (home_team and away_team),
    )


if submitted and home_team and away_team:
    progress = st.progress(0, text="Préparation")
    status = st.empty()

    status.write("Étape 1/3 : récupération des résultats via SportScore…")
    progress.progress(25, text="25 % — SportScore")

    with st.spinner("SportScore…"):
        hr = recent_results_sportscore(home_team["slug"], home_team["name"])
        ar = recent_results_sportscore(away_team["slug"], away_team["name"])

    # Si SportScore ne renvoie rien, tenter Openfootball
    if not hr:
        status.write("SportScore vide pour l'équipe à domicile, tentative Openfootball…")
        hr = recent_results_openfootball(home_team["name"], config["openfootball"])
    if not ar:
        status.write("SportScore vide pour l'équipe à l'extérieur, tentative Openfootball…")
        ar = recent_results_openfootball(away_team["name"], config["openfootball"])

    progress.progress(70, text="70 % — Résultats récupérés")

    status.write("Étape 2/3 : blessures (non disponibles sans clé).")
    hi = injuries(home_team.get("id", ""), config["sportscore"])
    ai = injuries(away_team.get("id", ""), config["sportscore"])

    progress.progress(85, text="85 % — Blessures ignorées")

    status.write("Étape 3/3 : calcul des probabilités.")
    result = predict(hr, ar)
    progress.progress(100, text="100 % — Analyse terminée")

    st.subheader(f"{home_team['name']} — {away_team['name']}")
    st.caption(f"Date saisie : {match_date:%d/%m/%Y} · Heure locale : {match_time:%H:%M}")

    if result is None:
        status.warning("Pronostic impossible : résultats récents insuffisants. Aucun score ni pourcentage n'a été inventé.")
        st.write(f"Résultats récupérés pour {home_team['name']} : {len(hr)}")
        st.write(f"Résultats récupérés pour {away_team['name']} : {len(ar)}")
        if hr:
            st.dataframe(pd.DataFrame(hr), use_container_width=True, hide_index=True)
        if ar:
            st.dataframe(pd.DataFrame(ar), use_container_width=True, hide_index=True)
    else:
        hs, aws, scores, probs = result
        status.success("Analyse terminée.")

        p1, pn, p2 = st.columns(3)
        p1.metric("Victoire domicile (1)", f"{probs[0]:.1f} %")
        pn.metric("Match nul (N)", f"{probs[1]:.1f} %")
        p2.metric("Victoire extérieur (2)", f"{probs[2]:.1f} %")

        st.subheader("Scores les plus probables")
        score_cols = st.columns(len(scores))
        for col, (prob, gh, ga) in zip(score_cols, scores):
            col.metric("Score", f"{gh} - {ga}", f"{prob * 100:.1f} % du modèle")

        st.subheader("Forme et buts observés")
        st.dataframe(pd.DataFrame([
            {"Équipe": home_team["name"], "Matchs": hs["nombre"], "Forme": f"{hs['forme']:.1f} %",
             "Marqués/match": f"{hs['marques']:.2f}", "Encaissés/match": f"{hs['encaisses']:.2f}"},
            {"Équipe": away_team["name"], "Matchs": aws["nombre"], "Forme": f"{aws['forme']:.1f} %",
             "Marqués/match": f"{aws['marques']:.2f}", "Encaissés/match": f"{aws['encaisses']:.2f}"},
        ]), use_container_width=True, hide_index=True)

        st.subheader("Absences cochées")
        st.write(f"**{home_team['name']} :** {', '.join(selected_home) or 'aucun'}")
        st.write(f"**{away_team['name']} :** {', '.join(selected_away) or 'aucun'}")
        st.caption("Affichage informatif uniquement ; ne modifie pas les probabilités.")

        st.subheader("Rapports de blessures")
        st.warning("Aucune source gratuite sans clé ne fournit les blessures. Section désactivée.")

        if all(odd > 1 for odd in (odd1, oddn, odd2)):
            raw = [1 / odd1, 1 / oddn, 1 / odd2]
            margin = sum(raw)
            market = [v / margin * 100 for v in raw]
            st.subheader("Comparaison avec les cotes saisies")
            for col, label, mp, model_pct in zip(st.columns(3), ("1", "N", "2"), market, probs):
                col.metric(label, f"Modèle {model_pct:.1f} %", f"Cotes {mp:.1f} %")
        else:
            st.info("Saisis trois cotes > 1 pour comparer.")

        with st.expander(f"Résultats récents — {home_team['name']}"):
            st.dataframe(pd.DataFrame(hr), use_container_width=True, hide_index=True)
        with st.expander(f"Résultats récents — {away_team['name']}"):
            st.dataframe(pd.DataFrame(ar), use_container_width=True, hide_index=True)

# Attribution obligatoire SportScore
st.divider()
st.markdown(
    '<a href="https://sportscore.com/" rel="dofollow" target="_blank">Powered by SportScore</a> · '
    'Historique : <a href="https://github.com/openfootball/football.json" target="_blank">Openfootball</a>',
    unsafe_allow_html=True,
)
st.caption("Les données indisponibles ne sont pas inventées. Matchgrind n'a pas pu être vérifié et n'est pas intégré.")
