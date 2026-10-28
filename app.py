import math
import re
from datetime import date, time
from urllib.parse import quote
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Générateur de pronostics football", page_icon="⚽", layout="wide")
st.title("⚽ Générateur de pronostics football")
st.write("Choisis une compétition et deux équipes. Les données manquantes ne sont pas inventées.")
st.info("Les sources publiques peuvent être incomplètes. Un pronostic reste une estimation.")

HEADERS = {"User-Agent": "Mozilla/5.0 FootballPredictionApp/1.0"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
LEAGUES = {
    "Ligue 1 (France)": "fra.1", "Premier League (Angleterre)": "eng.1",
    "Liga (Espagne)": "esp.1", "Serie A (Italie)": "ita.1",
    "Bundesliga (Allemagne)": "ger.1", "Süper Lig (Turquie)": "tur.1",
    "Super League (Grèce)": "gre.1", "Premier League (Russie)": "rus.1",
    "Superliga (Danemark)": "den.1", "Eredivisie (Pays-Bas)": "ned.1",
    "Ligue des champions": "uefa.champions", "Ligue Europa": "uefa.europa",
    "MLS (États-Unis)": "usa.1", "Saudi Pro League": "ksa.1",
    "Coupe du monde": "fifa.world",
}

def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

def get_json(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=(4, 8))
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
            name = obj.get("displayName") or obj.get("shortDisplayName") or obj.get("name")
            if tid and name:
                found[str(tid)] = {"id": str(tid), "name": str(name), "slug": slug}
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
    target = norm(team_name)
    for event in data.get("events", []) or data.get("schedule", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competitors = competitions[0].get("competitors") or []
        if len(competitors) < 2:
            continue
        home = next((x for x in competitors if x.get("homeAway") == "home"), competitors[0])
        away = next((x for x in competitors if x.get("homeAway") == "away"), competitors[1])
        ht, at = home.get("team") or {}, away.get("team") or {}
        hn = ht.get("displayName") or ht.get("name") or ""
        an = at.get("displayName") or at.get("name") or ""
        is_home, is_away = norm(hn) == target, norm(an) == target
        if not (is_home or is_away):
            continue
        try:
            hg, ag = int(float(home["score"])), int(float(away["score"]))
        except (KeyError, TypeError, ValueError):
            continue
        dt = pd.to_datetime(event.get("date"), utc=True, errors="coerce")
        if pd.notna(dt) and dt > pd.Timestamp.now(tz="UTC"):
            continue
        gf, ga = (hg, ag) if is_home else (ag, hg)
        rows.append({
            "Date": dt, "Adversaire": an if is_home else hn,
            "Lieu": "Domicile" if is_home else "Extérieur",
            "Buts marqués": gf, "Buts encaissés": ga,
            "Résultat": "V" if gf > ga else "N" if gf == ga else "D",
        })
    rows.sort(key=lambda r: r["Date"] if pd.notna(r["Date"]) else pd.Timestamp("1900-01-01", tz="UTC"), reverse=True)
    return rows[:10]

@st.cache_data(ttl=1800, show_spinner=False)
def recent_results(team_id, slug, name):
    for url in (
        f"{ESPN}/{slug}/teams/{quote(str(team_id))}/schedule",
        f"{ESPN}/{slug}/teams/{quote(str(team_id))}/schedule?season={date.today().year}",
    ):
        rows = parse_schedule(get_json(url), name)
        if rows:
            return rows
    return []

@st.cache_data(ttl=1800, show_spinner=False)
def injuries(team_id, slug):
    data = get_json(f"{ESPN}/{slug}/teams/{quote(str(team_id))}/injuries")
    if not data:
        return None
    found, seen = [], set()
    def walk(obj):
        if isinstance(obj, dict):
            name = obj.get("displayName") or obj.get("fullName") or obj.get("athleteName")
            parts = [str(obj[k]).strip() for k in ("status", "type", "details", "description", "date")
                     if isinstance(obj.get(k), (str, int, float)) and str(obj[k]).strip()]
            if name and parts and (str(name), " — ".join(parts)) not in seen:
                label = " — ".join(parts)
                seen.add((str(name), label))
                found.append({"Joueur": str(name), "Information publiée": label})
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(data)
    return found

@st.cache_data(ttl=1800, show_spinner=False)
def roster(team_id, slug):
    data = get_json(f"{ESPN}/{slug}/teams/{quote(str(team_id))}/roster")
    if not data:
        return []
    found = {}
    def walk(obj):
        if isinstance(obj, dict):
            athlete = obj.get("athlete") if isinstance(obj.get("athlete"), dict) else obj
            name = athlete.get("fullName") or athlete.get("displayName") or athlete.get("name")
            pid = athlete.get("id")
            if name and (pid or "position" in athlete or "jersey" in athlete):
                pos = athlete.get("position") or {}
                if isinstance(pos, dict):
                    pos = pos.get("displayName") or pos.get("name") or "Poste non indiqué"
                found[str(pid or name)] = {"name": str(name), "position": str(pos)}
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(data)
    return sorted(found.values(), key=lambda x: x["name"].casefold())

def team_stats(rows):
    if not rows:
        return None
    weights = list(range(len(rows), 0, -1))
    den = sum(weights)
    points = sum((3 if r["Résultat"] == "V" else 1 if r["Résultat"] == "N" else 0) * w for r, w in zip(rows, weights))
    return {
        "forme": 100 * points / (3 * den),
        "marques": sum(r["Buts marqués"] * w for r, w in zip(rows, weights)) / den,
        "encaisses": sum(r["Buts encaissés"] * w for r, w in zip(rows, weights)) / den,
        "nombre": len(rows),
    }

def poisson(lam, goals):
    return math.exp(-lam) * lam**goals / math.factorial(goals)

def predict(home_rows, away_rows):
    hs, aws = team_stats(home_rows), team_stats(away_rows)
    if hs is None or aws is None:
        return None
    lh = max(.15, min(3.5, .65 * hs["marques"] + .35 * aws["encaisses"] + .12))
    la = max(.15, min(3.5, .65 * aws["marques"] + .35 * hs["encaisses"]))
    matrix = sorted(
        [(poisson(lh, i) * poisson(la, j), i, j) for i in range(8) for j in range(8)],
        reverse=True,
    )
    p1 = sum(p for p, i, j in matrix if i > j)
    pn = sum(p for p, i, j in matrix if i == j)
    p2 = sum(p for p, i, j in matrix if i < j)
    total = p1 + pn + p2
    return hs, aws, matrix[:2], (p1 / total * 100, pn / total * 100, p2 / total * 100)

st.subheader("1. Compétition et équipes")
competition = st.selectbox("Compétition", list(LEAGUES))
slug = LEAGUES[competition]
with st.spinner("Chargement des équipes…"):
    teams = league_teams(slug)
if len(teams) >= 2:
    by_name = {t["name"]: t for t in teams}
    names = list(by_name)
    c1, c2 = st.columns(2)
    home_name = c1.selectbox("Équipe à domicile", names)
    away_name = c2.selectbox("Équipe à l'extérieur", [n for n in names if n != home_name])
    home_team, away_team = by_name[home_name], by_name[away_name]
else:
    st.warning("La source ne fournit pas les équipes pour cette compétition. Essaie une autre compétition.")
    home_team = away_team = None

selected_home, selected_away = [], []
if home_team and away_team:
    st.subheader("2. Joueurs absents — sélection manuelle")
    st.caption("Coche uniquement les absences que tu as confirmées. Une liste indisponible ne signifie pas zéro absent.")
    ch, ca = st.columns(2)
    with ch:
        with st.expander(f"Effectif — {home_team['name']}"):
            players = roster(home_team["id"], slug)
            if players:
                selected_home = [p["name"] for p in players if st.checkbox(f"{p['name']} — {p['position']}", key=f"h_{home_team['id']}_{p['name']}")]
            else:
                st.warning("Effectif nommé indisponible via ESPN.")
    with ca:
        with st.expander(f"Effectif — {away_team['name']}"):
            players = roster(away_team["id"], slug)
            if players:
                selected_away = [p["name"] for p in players if st.checkbox(f"{p['name']} — {p['position']}", key=f"a_{away_team['id']}_{p['name']}")]
            else:
                st.warning("Effectif nommé indisponible via ESPN.")

with st.form("match_form"):
    st.subheader("3. Informations du match")
    dcol, tcol = st.columns(2)
    match_date = dcol.date_input("Date du match", value=date.today())
    match_time = tcol.time_input("Heure locale", value=time(20, 30))
    st.caption("Cotes facultatives, ordre 1-N-2. Laisse 0 si tu ne les connais pas.")
    oc = st.columns(3)
    odd1 = oc[0].number_input("Cote 1", min_value=0.0, value=0.0, step=0.05, format="%.2f")
    oddn = oc[1].number_input("Cote N", min_value=0.0, value=0.0, step=0.05, format="%.2f")
    odd2 = oc[2].number_input("Cote 2", min_value=0.0, value=0.0, step=0.05, format="%.2f")
    submitted = st.form_submit_button("🔎 Analyser le match", use_container_width=True, disabled=not (home_team and away_team))

if submitted:
    progress = st.progress(0, text="0 % — Préparation")
    status = st.empty()
    status.write("Étape 1/4 : équipes sélectionnées.")
    progress.progress(15, text="15 % — Équipes sélectionnées")
    status.write("Étape 2/4 : récupération des derniers résultats.")
    hr = recent_results(home_team["id"], slug, home_team["name"])
    ar = recent_results(away_team["id"], slug, away_team["name"])
    progress.progress(55, text="55 % — Résultats récupérés")
    status.write("Étape 3/4 : vérification des rapports de blessures.")
    hi = injuries(home_team["id"], slug)
    ai = injuries(away_team["id"], slug)
    progress.progress(80, text="80 % — Données publiques vérifiées")
    status.write("Étape 4/4 : calcul des probabilités.")
    result = predict(hr, ar)
    st.subheader(f"{home_team['name']} — {away_team['name']}")
    st.caption(f"Date saisie : {match_date:%d/%m/%Y} · Heure locale : {match_time:%H:%M}")
    if result is None:
        progress.progress(100, text="100 % — Analyse terminée")
        status.warning("Pronostic impossible : les résultats récents sont insuffisants. Aucun score ni pourcentage n'a été inventé.")
        st.write(f"Résultats récupérés pour {home_team['name']} : {len(hr)}")
        st.write(f"Résultats récupérés pour {away_team['name']} : {len(ar)}")
    else:
        hs, aws, scores, probs = result
        progress.progress(100, text="100 % — Analyse terminée")
        status.success("Analyse terminée.")
        p1, pn, p2 = st.columns(3)
        p1.metric("Victoire domicile (1)", f"{probs[0]:.1f} %")
        pn.metric("Match nul (N)", f"{probs[1]:.1f} %")
        p2.metric("Victoire extérieur (2)", f"{probs[2]:.1f} %")
        s1, s2 = st.columns(2)
        s1.metric("Score probable 1", f"{scores[0][1]} - {scores[0][2]}")
        s2.metric("Score probable 2", f"{scores[1][1]} - {scores[1][2]}")
        st.subheader("Forme et buts observés")
        st.dataframe(pd.DataFrame([
            {"Équipe": home_team["name"], "Matchs disponibles": hs["nombre"], "Forme pondérée": f"{hs['forme']:.1f} %", "Buts marqués/match": f"{hs['marques']:.2f}", "Buts encaissés/match": f"{hs['encaisses']:.2f}"},
            {"Équipe": away_team["name"], "Matchs disponibles": aws["nombre"], "Forme pondérée": f"{aws['forme']:.1f} %", "Buts marqués/match": f"{aws['marques']:.2f}", "Buts encaissés/match": f"{aws['encaisses']:.2f}"},
        ]), use_container_width=True, hide_index=True)
        st.subheader("Absences cochées")
        st.write(f"**{home_team['name']} :** {', '.join(selected_home) if selected_home else 'aucun joueur coché'}")
        st.write(f"**{away_team['name']} :** {', '.join(selected_away) if selected_away else 'aucun joueur coché'}")
        st.caption("Les absences cochées sont affichées mais ne modifient pas encore les probabilités.")
        st.subheader("Rapports de blessures")
        for label, data in ((home_team["name"], hi), (away_team["name"], ai)):
            st.markdown(f"**{label}**")
            if data is None:
                st.warning("Aucun rapport exploitable reçu ; cela ne prouve pas qu'il n'y a aucun absent.")
            elif data:
                st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
            else:
                st.info("Aucune entrée de blessure retournée par ESPN.")
        if all(x > 1 for x in (odd1, oddn, odd2)):
            raw = [1 / odd1, 1 / oddn, 1 / odd2]
            margin = sum(raw)
            market = [x / margin * 100 for x in raw]
            st.subheader("Comparaison avec les cotes saisies")
            for col, label, mp, pp in zip(st.columns(3), ("1 domicile", "N nul", "2 extérieur"), market, probs):
                col.metric(label, f"Modèle {pp:.1f} %", f"Cotes {mp:.1f} %")
        else:
            st.info("Saisis trois cotes supérieures à 1 pour comparer modèle et marché.")
        with st.expander(f"Résultats récents — {home_team['name']}"):
            st.dataframe(pd.DataFrame(hr), use_container_width=True, hide_index=True) if hr else st.warning("Aucun résultat reçu.")
        with st.expander(f"Résultats récents — {away_team['name']}"):
            st.dataframe(pd.DataFrame(ar), use_container_width=True, hide_index=True) if ar else st.warning("Aucun résultat reçu.")

st.divider()
st.caption("Source : endpoints publics ESPN. Leur couverture varie selon les compétitions et les données disponibles.")
        
