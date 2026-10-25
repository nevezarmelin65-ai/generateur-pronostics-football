import streamlit as st
import requests
import pandas as pd
import math
from datetime import date, time
from urllib.parse import quote

st.set_page_config(page_title="Pronostics Football", page_icon="⚽", layout="wide")
st.title("⚽ Générateur de pronostics football")
st.caption("Sans clé API. Les données publiques peuvent être incomplètes ou indisponibles.")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.fotmob.com/"
}
BASE = "https://www.fotmob.com/api"

@st.cache_data(ttl=1800, show_spinner=False)
def search_team(name):
    try:
        r = requests.get(f"{BASE}/search/suggest?term={quote(name)}", headers=HEADERS, timeout=12)
        r.raise_for_status()
        data = r.json()
        items = data.get("teams", []) or data.get("suggestions", [])
        for x in items:
            if isinstance(x, dict) and (x.get("id") or x.get("primaryId")):
                return x
    except Exception:
        pass
    return None

@st.cache_data(ttl=1800, show_spinner=False)
def team_details(team_id):
    try:
        r = requests.get(f"{BASE}/teams?id={team_id}", headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

@st.cache_data(ttl=900, show_spinner=False)
def match_details(match_id):
    try:
        r = requests.get(f"{BASE}/matchDetails?matchId={match_id}", headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def norm(s):
    return "".join(ch.lower() for ch in str(s or "") if ch.isalnum())

def fixtures(data):
    if not isinstance(data, dict):
        return []
    x = data.get("fixtures", {})
    if isinstance(x, dict):
        return x.get("allFixtures", []) or x.get("fixtures", []) or []
    return x if isinstance(x, list) else []

def team_names(f):
    h, a = f.get("home", {}) or {}, f.get("away", {}) or {}
    return h.get("name", f.get("homeTeam", "")), a.get("name", f.get("awayTeam", ""))

def fixture_dt(f):
    for val in (f.get("utcTime"), f.get("time"), f.get("date")):
        if isinstance(val, str):
            d = pd.to_datetime(val, utc=True, errors="coerce")
            if pd.notna(d):
                return d
    status = f.get("status")
    if isinstance(status, dict):
        val = status.get("utcTime")
        if isinstance(val, str):
            return pd.to_datetime(val, utc=True, errors="coerce")
    return pd.NaT

def result_rows(data, team_name):
    rows = []
    for f in fixtures(data):
        h, a = f.get("home", {}) or {}, f.get("away", {}) or {}
        hs = h.get("score", f.get("homeScore"))
        aws = a.get("score", f.get("awayScore"))
        try:
            hs, aws = int(hs), int(aws)
        except (TypeError, ValueError):
            continue
        hn, an = team_names(f)
        is_home = norm(hn) == norm(team_name)
        if not is_home and norm(an) != norm(team_name):
            continue
        gf, ga = (hs, aws) if is_home else (aws, hs)
        rows.append({
            "Date": fixture_dt(f), "Adversaire": an if is_home else hn,
            "Lieu": "Domicile" if is_home else "Extérieur",
            "Buts marqués": gf, "Buts encaissés": ga,
            "Résultat": "V" if gf > ga else ("N" if gf == ga else "D")
        })
    rows.sort(key=lambda x: x["Date"] if pd.notna(x["Date"]) else pd.Timestamp("1900-01-01", tz="UTC"), reverse=True)
    return rows[:10]

def form_stats(rows):
    if not rows:
        return None
    weights = list(range(len(rows), 0, -1))
    total = sum(weights)
    pts = sum((3 if r["Résultat"] == "V" else 1 if r["Résultat"] == "N" else 0) * w for r, w in zip(rows, weights))
    gf = sum(r["Buts marqués"] * w for r, w in zip(rows, weights)) / total
    ga = sum(r["Buts encaissés"] * w for r, w in zip(rows, weights)) / total
    return {"forme": 100 * pts / (3 * total), "gf": gf, "ga": ga, "n": len(rows)}

def pois(lam, k):
    return math.exp(-lam) * lam**k / math.factorial(k)

def predict(hr, ar):
    h, a = form_stats(hr), form_stats(ar)
    if not h or not a:
        return None
    # Forme et buts récents priment. Aucun bonus automatique lié au classement.
    lh = max(.2, min(3.2, .65*h["gf"] + .35*a["ga"] + .18))
    la = max(.2, min(3.2, .65*a["gf"] + .35*h["ga"]))
    scores = [(pois(lh, i)*pois(la, j), i, j) for i in range(7) for j in range(7)]
    scores.sort(reverse=True)
    ph = sum(p for p, i, j in scores if i > j)
    pn = sum(p for p, i, j in scores if i == j)
    pa = sum(p for p, i, j in scores if i < j)
    total = ph + pn + pa
    return h, a, scores, (100*ph/total, 100*pn/total, 100*pa/total)

with st.form("match"):
    c1, c2 = st.columns(2)
    home = c1.text_input("Équipe à domicile", placeholder="Ex. Arsenal")
    away = c2.text_input("Équipe à l'extérieur", placeholder="Ex. Chelsea")
    c3, c4 = st.columns(2)
    match_date = c3.date_input("Date du match", value=date.today())
    match_time = c4.time_input("Heure locale du match", value=time(18, 0))
    go = st.form_submit_button("Rechercher et analyser", use_container_width=True)

st.info("Tu saisis uniquement les deux équipes, la date et l’heure locale. Ne saisis pas les noms des joueurs ni les cotes.")
if go:
    if not home.strip() or not away.strip():
        st.error("Entre les deux noms d’équipe.")
    elif norm(home) == norm(away):
        st.error("Choisis deux équipes différentes.")
    else:
        with st.spinner("Recherche des équipes et des résultats publics sur FotMob…"):
            ht, at = search_team(home.strip()), search_team(away.strip())
            hd = team_details(ht.get("id") or ht.get("primaryId")) if ht else None
            ad = team_details(at.get("id") or at.get("primaryId")) if at else None
        if not ht or not at or not hd or not ad:
            st.error("La recherche publique n’a pas renvoyé les données nécessaires. Essaie les noms complets des équipes. Les services gratuits peuvent limiter l’accès.")
        else:
            hn = ht.get("name") or home
            an = at.get("name") or away
            st.subheader(f"{hn} — {an}")
            st.caption(f"Date : {match_date.strftime('%d/%m/%Y')} · Heure locale saisie : {match_time.strftime('%H:%M')}")
            matched = None
            for f in fixtures(hd) + fixtures(ad):
                fhn, fan = team_names(f)
                dt = fixture_dt(f)
                if norm(fhn) == norm(hn) and norm(fan) == norm(an) and pd.notna(dt) and dt.date() == match_date:
                    matched = f
                    break
            if matched:
                st.success("Match retrouvé dans le calendrier public.")
                mid = matched.get("id") or matched.get("matchId")
                detail = match_details(mid) if mid else None
                if detail:
                    content = detail.get("content", {}) if isinstance(detail, dict) else {}
                    if content.get("lineup"):
                        st.write("Compositions : des données sont présentes. Vérifie si elles sont officielles ou probables.")
                    else:
                        st.warning("Aucune composition exploitable récupérée. Cela ne signifie pas qu’aucun titulaire n’est connu.")
                    if content.get("injuries"):
                        st.write("Absences : une section blessure/absence est présente dans la fiche publique.")
                    else:
                        st.warning("Les absences n’ont pas pu être confirmées automatiquement pour ce match.")
                    if content.get("odds"):
                        st.write("Cotes : des données de cotes sont présentes dans la fiche publique.")
                    else:
                        st.warning("Aucune cote exploitable n’a été récupérée. Aucune cote n’est inventée.")
                else:
                    st.warning("Les détails du match n’ont pas pu être récupérés automatiquement.")
            else:
                st.warning("Aucun match exact trouvé à cette date dans les calendriers récupérés. Vérifie la date et l’orthographe.")

            hr, ar = result_rows(hd, hn), result_rows(ad, an)
            result = predict(hr, ar)
            if result:
                hstats, astats, scores, probs = result
                st.subheader("Pronostic basé sur la forme récente")
                st.caption("La forme récente et les buts priment. Le classement ne donne pas automatiquement l’avantage au leader.")
                x, y, z = st.columns(3)
                x.metric("Victoire domicile", f"{probs[0]:.1f}%")
                y.metric("Match nul", f"{probs[1]:.1f}%")
                z.metric("Victoire extérieur", f"{probs[2]:.1f}%")
                x, y = st.columns(2)
                x.metric("Score probable 1", f"{scores[0][1]} - {scores[0][2]}")
                y.metric("Score probable 2", f"{scores[1][1]} - {scores[1][2]}")
                x, y = st.columns(2)
                with x:
                    st.markdown(f"**{hn}**")
                    st.write(f"Matchs disponibles : {hstats['n']}/10")
                    st.write(f"Indice de forme pondéré : {hstats['forme']:.1f}/100")
                    st.write(f"Buts marqués/match : {hstats['gf']:.2f}")
                    st.write(f"Buts encaissés/match : {hstats['ga']:.2f}")
                    st.dataframe(pd.DataFrame(hr), use_container_width=True, hide_index=True)
                with y:
                    st.markdown(f"**{an}**")
                    st.write(f"Matchs disponibles : {astats['n']}/10")
                    st.write(f"Indice de forme pondéré : {astats['forme']:.1f}/100")
                    st.write(f"Buts marqués/match : {astats['gf']:.2f}")
                    st.write(f"Buts encaissés/match : {astats['ga']:.2f}")
                    st.dataframe(pd.DataFrame(ar), use_container_width=True, hide_index=True)
            else:
                st.warning("Les résultats récents n’ont pas été récupérés dans un format exploitable. Aucun score chiffré fiable n’est calculé.")
            st.markdown("### Sources complémentaires à consulter")
            st.markdown("- [FotMob](https://www.fotmob.com/) — résultats, statistiques, fiches de match.")
            st.markdown("- [Transfermarkt](https://www.transfermarkt.com/) — blessures et suspensions.")
            st.markdown("- [Open-Matchday](https://github.com/open-matchday) — données ouvertes de matchs, si le jeu de données adapté est disponible.")
            st.warning("Limite honnête : cette version tente de lire les données publiques de FotMob sans clé. Elle n’intègre pas encore une collecte automatique garantie de Transfermarkt ni d’Open-Matchday. Les pages publiques peuvent changer ou bloquer l’accès. Une anomalie de cote ne prouve jamais qu’un match est truqué.")

with st.expander("Sources et limites"):
    st.markdown("- [FotMob](https://www.fotmob.com/)")
    st.markdown("- [Transfermarkt](https://www.transfermarkt.com/)")
    st.markdown("- [Open-Matchday](https://github.com/open-matchday)")
    st.caption("Les données absentes ne sont pas inventées. Les compositions officielles sont généralement publiées près du coup d’envoi.")
