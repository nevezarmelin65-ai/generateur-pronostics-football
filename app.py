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
    
    # Correction de l'extraction des équipes selon la structure réelle de l'API ESPN
    leagues_list = data.get("sports", [{}])[0].get("leagues", [{}])
    teams_list = leagues_list[0].get("teams", []) if leagues_list else []
    
    for item in teams_list:
        obj = item.get("team", {})
        tid = obj.get("id")
        name = obj.get("displayName") or obj.get("shortDisplayName") or obj.get("name")
        if tid and name:
            found[str(tid)] = {"id": str(tid), "name": str(name), "slug": slug}
            
    if not found:  # Fallback sur ta fonction d'origine si structure différente
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
    # Remplacement de l'URL morte par le endpoint général scoreboard qui contient tout l'historique de la saison en cours
    url = f"{ESPN}/{slug}/scoreboard?limit=300"
    rows = parse_schedule(get_json(url), name)
    if rows:
        return rows
    return []

@st.cache_data(ttl=1800, show_spinner=False)
def injuries(team_id, slug):
    # Remplacement par le endpoint valide des blessures par équipe d'ESPN
    data = get_json(f"{ESPN}/{slug}/teams/{quote(str(team_id))}")
    if not data:
        return None
    found, seen = [], set()
    
    # On extrait les blessures de la structure valide de l'équipe
    for player in data.get("team", {}).get("injuries", []):
        name = player.get("athlete", {}).get("displayName") or player.get("displayName")
        status = player.get("status", "Indisponible")
        details = player.get("comment", "")
        if name and (str(name), status) not in seen:
            seen.add((str(name), status))
            found.append({"Joueur": str(name), "Information publiée": f"{status} — {details}"})
            
    if not found:
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
    # Remplacement de l'URL roster morte par l'appel de l'équipe qui embarque ses athlètes actifs
    data = get_json(f"{ESPN}/{slug}/teams/{quote(str(team_id))}")
    if not data:
        return []
    found = {}
    
    # Extraction propre depuis la fiche d'équipe
    for athlete in data.get("team", {}).get("athletes", []):
        name = athlete.get("fullName") or athlete.get("displayName")
        pid = athlete.get("id")
        pos = athlete.get("position", {}).get("displayName", "Poste non indiqué")
        if name:
            found[str(pid or name)] = {"name": str(name), "position": str(pos)}
            
    if not found:
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
