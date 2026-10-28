import math
import re
from datetime import date
import pandas as pd
import requests
import streamlit as st

# Configuration de la page
st.set_page_config(page_title="Générateur de pronostics football", page_icon="⚽", layout="wide")
st.title("⚽ Générateur de pronostics football")
st.write("Choisis une compétition et deux équipes. Les données manquantes ne sont pas inventées.")
st.info("Les sources publiques d'ESPN sont actualisées en temps réel. Un pronostic reste une estimation.")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# Liste des ligues valides et synchronisées avec l'API ESPN
LEAGUES = {
    "Ligue 1 (France)": "fra.1", 
    "Premier League (Angleterre)": "eng.1",
    "Liga (Espagne)": "esp.1", 
    "Serie A (Italie)": "ita.1",
    "Bundesliga (Allemagne)": "ger.1", 
    "Süper Lig (Turquie)": "tur.1",
    "Super League (Grèce)": "gre.1", 
    "Eredivisie (Pays-Bas)": "ned.1",
    "Ligue des champions": "uefa.champions", 
    "Ligue Europa": "uefa.europa",
    "MLS (États-Unis)": "usa.1", 
    "Saudi Pro League": "ksa.1",
    "Coupe du monde": "fifa.world",
}

def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

def get_json(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=(4, 8))
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return None

@st.cache_data(ttl=1800, show_spinner=False)
def league_teams(slug):
    """Récupère la liste des équipes d'une compétition."""
    data = get_json(f"{ESPN_BASE}/{slug}/teams?limit=100") or {}
    found = {}
    for item in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        t = item.get("team", {})
        tid = t.get("id")
        name = t.get("displayName") or t.get("name")
        if tid and name:
            found[str(tid)] = {"id": str(tid), "name": str(name), "slug": slug}
    return sorted(found.values(), key=lambda x: x["name"].casefold())

@st.cache_data(ttl=900, show_spinner=False)
def load_all_results(slug):
    """Récupère les matchs récents depuis le scoreboard de la ligue (Source temps réel)."""
    data = get_json(f"{ESPN_BASE}/{slug}/scoreboard?limit=200") or {}
    return data.get("events", [])

def parse_team_results(events, team_name):
    """Filtre et extrait les 10 derniers matchs d'une équipe spécifique."""
    rows = []
    target = norm(team_name)
    
    for event in events:
        competitions = event.get("competitions", [])
        if not competitions:
            continue
        
        status = competitions[0].get("status", {}).get("type", {}).get("name", "")
        if status != "STATUS_FINAL":  # On ne prend que les matchs terminés
            continue
            
        competitors = competitions[0].get("competitors", [])
        if len(competitors) < 2:
            continue
            
        home = next((x for x in competitors if x.get("homeAway") == "home"), competitors[0])
        away = next((x for x in competitors if x.get("homeAway") == "away"), competitors[1])
        
        ht, at = home.get("team", {}), away.get("team", {})
        hn, an = ht.get("displayName", ""), at.get("displayName", "")
        
        is_home = norm(hn) == target
        is_away = norm(an) == target
        
        if not (is_home or is_away):
            continue
            
        try:
            hg = int(home.get("score", 0))
            ag = int(away.get("score", 0))
        except (ValueError, TypeError):
            continue
            
        dt = pd.to_datetime(event.get("date"), utc=True, errors="coerce")
        gf, ga = (hg, ag) if is_home else (ag, hg)
        
        rows.append({
            "Date": dt.strftime('%d/%m/%Y') if pd.notna(dt) else "Inconnue",
            "Adversaire": an if is_home else hn,
            "Lieu": "Domicile" if is_home else "Extérieur",
            "Buts marqués": gf, "Buts encaissés": ga,
            "Résultat": "V" if gf > ga else "N" if gf == ga else "D",
            "timestamp": dt if pd.notna(dt) else pd.Timestamp("1900-01-01", tz="UTC")
        })
        
    rows.sort(key=lambda r: r["timestamp"], reverse=True)
    return rows[:10]

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
    hs = team_stats(home_rows)
    aws = team_stats(away_rows)
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

# Section UI Streamlit
st.subheader("1. Compétition et équipes")
competition = st.selectbox("Compétition", list(LEAGUES))
slug = LEAGUES[competition]

with st.spinner("Chargement des équipes…"):
    teams = league_teams(slug)

if len(teams) >= 2:
    by_name = {t["name"]: t for t in teams}
    names = list(by_name)
    
    c1, c2 = st.columns(2)
    home_name = c1.selectbox("Équipe à domicile", names, index=0)
    # Pour éviter d'avoir la même équipe sélectionnée par défaut
    away_index = 1 if len(names) > 1 else 0
    away_name = c2.selectbox("Équipe à l'extérieur", names, index=away_index)
    
    if home_name == away_name:
        st.warning("Attention : Vous avez sélectionné la même équipe pour le match.")
    
    # Bouton d'analyse
    if st.button("📊 Générer le pronostic", type="primary"):
        with st.spinner("Analyse des scores en cours..."):
            all_events = load_all_results(slug)
            home_rows = parse_team_results(all_events, home_name)
            away_rows = parse_team_results(all_events, away_name)
            
        if not home_rows or not away_rows:
            st.error("Données historiques insuffisantes pour l'une des deux équipes sur l'API publique actuelle.")
        else:
            # Calcul des prédictions
            res = predict(home_rows, away_rows)
            if res:
                hs, aws, top_scores, probas = res
                p_home, p_draw, p_away = probas
                
                # Affichage des probabilités
                st.subheader("🔮 Probabilités du Match")
                i1, i2, i3 = st.columns(3)
                i1.metric(f"Victoire {home_name}", f"{p_home:.1f}%")
                i2.metric("Match Nul", f"{p_draw:.1f}%")
                i3.metric(f"Victoire {away_name}", f"{p_away:.1f}%")
                
                # Scores exacts les plus probables
                st.subheader("🎯 Scores exacts les plus probables")
                for prob, h_g, a_g in top_scores:
                    st.write(f"• **{h_g} - {a_g}** : ({prob*100:.1f}% de chance)")
                    
                # Historique des formes
                st.subheader("📈 Historique récent utilisé")
                col_h, col_a = st.columns(2)
                with col_h:
                    st.write(f"**Derniers matchs de {home_name} :**")
                    st.dataframe(pd.DataFrame(home_rows)[["Date", "Adversaire", "Lieu", "Buts marqués", "Buts encaissés", "Résultat"]])
                with col_a:
                    st.write(f"**Derniers matchs de {away_name} :**")
                    st.dataframe(pd.DataFrame(away_rows)[["Date", "Adversaire", "Lieu", "Buts marqués", "Buts encaissés", "Résultat"]])
else:
    st.error("Impossible de récupérer les équipes de cette ligue pour le moment.")
