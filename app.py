import streamlit as st
import requests
import pandas as pd
import math
import re
from datetime import date, time
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup

st.set_page_config(page_title="Générateur de pronostics football", page_icon="⚽", layout="wide")
st.title("⚽ Générateur de pronostics football")
st.write("Saisis seulement les deux équipes, la date et l'heure locale. Le programme recherche les données publiques disponibles sans clé API.")
st.caption("Important : les sites peuvent bloquer les requêtes automatiques. Les données non récupérées seront signalées, jamais inventées.")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "application/json,text/html,*/*",
    "Referer": "https://www.fotmob.com/"
}
FOTMOB_API = "https://www.fotmob.com/api"
TM_BASE = "https://www.transfermarkt.com"

def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

@st.cache_data(ttl=1800, show_spinner=False)
def fotmob_search_team(name):
    for url in [
        f"{FOTMOB_API}/search/suggest?term={quote(name)}",
        f"https://www.fotmob.com/api/search/suggest?term={quote(name)}"
    ]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            r.raise_for_status()
            data = r.json()
            items = data.get("teams", []) or data.get("suggestions", [])
            for item in items:
                if isinstance(item, dict) and (item.get("id") or item.get("primaryId")):
                    return item
        except Exception:
            continue
    return None

@st.cache_data(ttl=1800, show_spinner=False)
def fotmob_team_details(team_id):
    try:
        r = requests.get(f"{FOTMOB_API}/teams?id={team_id}", headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

@st.cache_data(ttl=900, show_spinner=False)
def fotmob_match_details(match_id):
    try:
        r = requests.get(f"{FOTMOB_API}/matchDetails?matchId={match_id}", headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def get_fixtures(team_data):
    if not isinstance(team_data, dict):
        return []
    data = team_data.get("fixtures", {})
    if isinstance(data, dict):
        return data.get("allFixtures", []) or data.get("fixtures", []) or []
    return data if isinstance(data, list) else []

def fixture_names(fixture):
    home = fixture.get("home", {}) or {}
    away = fixture.get("away", {}) or {}
    return home.get("name", fixture.get("homeTeam", "")), away.get("name", fixture.get("awayTeam", ""))

def fixture_datetime(fixture):
    values = [fixture.get("utcTime"), fixture.get("time"), fixture.get("date")]
    status = fixture.get("status")
    if isinstance(status, dict):
        values.append(status.get("utcTime"))
    for value in values:
        if isinstance(value, str):
            parsed = pd.to_datetime(value, utc=True, errors="coerce")
            if pd.notna(parsed):
                return parsed
    return pd.NaT

def get_recent_results(team_data, team_name):
    rows = []
    for fixture in get_fixtures(team_data):
        home = fixture.get("home", {}) or {}
        away = fixture.get("away", {}) or {}
        hs = home.get("score", fixture.get("homeScore"))
        aws = away.get("score", fixture.get("awayScore"))
        try:
            hs, aws = int(hs), int(aws)
        except (TypeError, ValueError):
            continue
        hn, an = fixture_names(fixture)
        is_home = norm(hn) == norm(team_name)
        if not is_home and norm(an) != norm(team_name):
            continue
        gf, ga = (hs, aws) if is_home else (aws, hs)
        rows.append({
            "Date (UTC)": fixture_datetime(fixture),
            "Adversaire": an if is_home else hn,
            "Lieu": "Domicile" if is_home else "Extérieur",
            "Buts marqués": gf, "Buts encaissés": ga,
            "Résultat": "V" if gf > ga else ("N" if gf == ga else "D")
        })
    rows.sort(key=lambda row: row["Date (UTC)"] if pd.notna(row["Date (UTC)"]) else pd.Timestamp("1900-01-01", tz="UTC"), reverse=True)
    return rows[:10]

def form_stats(rows):
    if not rows:
        return None
    # Les matchs récents reçoivent un poids supérieur.
    weights = list(range(len(rows), 0, -1))
    denom = sum(weights)
    points = sum((3 if row["Résultat"] == "V" else 1 if row["Résultat"] == "N" else 0) * weight for row, weight in zip(rows, weights))
    scored = sum(row["Buts marqués"] * weight for row, weight in zip(rows, weights)) / denom
    conceded = sum(row["Buts encaissés"] * weight for row, weight in zip(rows, weights)) / denom
    return {"form": points / (3 * denom) * 100, "scored": scored, "conceded": conceded, "count": len(rows)}

def poisson(lam, goals):
    return math.exp(-lam) * lam ** goals / math.factorial(goals)

def predict(home_rows, away_rows):
    home, away = form_stats(home_rows), form_stats(away_rows)
    if not home or not away:
        return None
    # Pas de bonus de classement : priorité à la forme pondérée et aux buts.
    lam_home = max(0.2, min(3.2, 0.65 * home["scored"] + 0.35 * away["conceded"] + 0.18))
    lam_away = max(0.2, min(3.2, 0.65 * away["scored"] + 0.35 * home["conceded"]))
    matrix = [(poisson(lam_home, i) * poisson(lam_away, j), i, j) for i in range(7) for j in range(7)]
    matrix.sort(reverse=True)
    ph = sum(p for p, i, j in matrix if i > j)
    pdw = sum(p for p, i, j in matrix if i == j)
    pa = sum(p for p, i, j in matrix if i < j)
    total = ph + pdw + pa
    return {
        "home": home, "away": away, "scores": matrix[:2],
        "probabilities": (100 * ph / total, 100 * pdw / total, 100 * pa / total)
    }

@st.cache_data(ttl=3600, show_spinner=False)
def transfermarkt_team_absences(team_name):
    """Essaie de trouver la page Transfermarkt et sa page blessures. Peut échouer si le site bloque le scraping."""
    result = {"team_url": None, "injury_url": None, "rows": [], "status": "non récupéré"}
    try:
        search_url = f"{TM_BASE}/schnellsuche/ergebnis/schnellsuche?query={quote(team_name)}"
        response = requests.get(search_url, headers={**HEADERS, "Referer": TM_BASE}, timeout=15)
        if response.status_code != 200:
            result["status"] = f"recherche bloquée (HTTP {response.status_code})"
            return result
        soup = BeautifulSoup(response.text, "html.parser")
        team_link = None
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)
            if "/startseite/verein/" in href and norm(team_name) in norm(text):
                team_link = urljoin(TM_BASE, href)
                break
        if not team_link:
            for a in soup.select("a[href*='/startseite/verein/']"):
                team_link = urljoin(TM_BASE, a.get("href"))
                break
        if not team_link:
            result["status"] = "équipe introuvable dans la recherche publique"
            return result
        result["team_url"] = team_link
        match = re.search(r"/verein/(\d+)", team_link)
        if not match:
            result["status"] = "identifiant de club non trouvé"
            return result
        club_id = match.group(1)
        injury_url = f"{TM_BASE}/verletzungen/verein/{club_id}"
        result["injury_url"] = injury_url
        ir = requests.get(injury_url, headers={**HEADERS, "Referer": team_link}, timeout=15)
        if ir.status_code != 200:
            result["status"] = f"page des blessures bloquée (HTTP {ir.status_code})"
            return result
        tables = pd.read_html(ir.text)
        for table in tables:
            if not table.empty:
                result["rows"].extend(table.fillna("").astype(str).to_dict("records"))
        result["status"] = "page consultée; tableau disponible" if result["rows"] else "page consultée, aucun tableau exploitable"
        return result
    except Exception as exc:
        result["status"] = f"site inaccessible ou structure modifiée ({type(exc).__name__})"
        return result

@st.cache_data(ttl=3600, show_spinner=False)
def open_matchday_status():
    """Vérifie l'accès au dépôt public cité; n'invente pas de données si la structure n'est pas connue."""
    url = "https://api.github.com/repos/open-matchday/open-matchday"
    try:
        r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {"ok": True, "message": f"Dépôt accessible : {data.get('full_name', 'Open-Matchday')}", "url": data.get("html_url", "https://github.com/open-matchday")}
        return {"ok": False, "message": f"Dépôt non accessible via cette adresse (HTTP {r.status_code}); aucune donnée Open-Matchday utilisée.", "url": "https://github.com/open-matchday"}
    except Exception:
        return {"ok": False, "message": "Vérification automatique Open-Matchday impossible depuis l’application.", "url": "https://github.com/open-matchday"}

with st.form("match_form"):
    c1, c2 = st.columns(2)
    home_name = c1.text_input("Équipe à domicile", placeholder="Ex. Arsenal")
    away_name = c2.text_input("Équipe à l'extérieur", placeholder="Ex. Chelsea")
    c3, c4 = st.columns(2)
    match_date = c3.date_input("Date du match", value=date.today())
    local_time = c4.time_input("Heure locale du match", value=time(18, 0))
    submitted = st.form_submit_button("🔎 Rechercher et analyser", use_container_width=True)

if submitted:
    if not home_name.strip() or not away_name.strip():
        st.error("Entre le nom des deux équipes.")
    elif norm(home_name) == norm(away_name):
        st.error("Choisis deux équipes différentes.")
    else:
        with st.spinner("Recherche des équipes et résultats publics sur FotMob…"):
            home_item = fotmob_search_team(home_name.strip())
            away_item = fotmob_search_team(away_name.strip())
            home_data = fotmob_team_details(home_item.get("id") or home_item.get("primaryId")) if home_item else None
            away_data = fotmob_team_details(away_item.get("id") or away_item.get("primaryId")) if away_item else None
        st.subheader(f"{home_item.get('name', home_name) if home_item else home_name} — {away_item.get('name', away_name) if away_item else away_name}")
        st.caption(f"Date saisie : {match_date.strftime('%d/%m/%Y')} · Heure locale saisie : {local_time.strftime('%H:%M')}")
        if not home_item or not away_item or not home_data or not away_data:
            st.error("FotMob n’a pas renvoyé toutes les données. Essaie les noms complets des équipes ou réessaie plus tard. Les points d’accès publics peuvent changer ou limiter l’accès.")
        else:
            actual_home = home_item.get("name") or home_name
            actual_away = away_item.get("name") or away_name
            found = None
            for fixture in get_fixtures(home_data) + get_fixtures(away_data):
                fn_home, fn_away = fixture_names(fixture)
                dt = fixture_datetime(fixture)
                if norm(fn_home) == norm(actual_home) and norm(fn_away) == norm(actual_away) and pd.notna(dt) and dt.date() == match_date:
                    found = fixture
                    break
            if found:
                st.success("Match correspondant retrouvé dans le calendrier public de FotMob.")
                match_id = found.get("id") or found.get("matchId")
                detail = fotmob_match_details(match_id) if match_id else None
                if detail:
                    content = detail.get("content", {}) if isinstance(detail, dict) else {}
                    lineup = content.get("lineup")
                    injuries = content.get("injuries")
                    odds = content.get("odds")
                    if lineup:
                        st.info("La fiche du match contient des données de composition. Vérifie si elles sont officielles ou probables.")
                    else:
                        st.warning("Aucune composition exploitable n’a été renvoyée. Cela ne signifie pas qu’aucun titulaire ou absent n’existe.")
                    if injuries:
                        st.info("La fiche du match contient une section blessure/absence.")
                    else:
                        st.warning("Les absences n’ont pas pu être confirmées automatiquement pour ce match.")
                    if odds:
                        st.info("La fiche publique contient une section de cotes.")
                    else:
                        st.warning("Aucune cote exploitable n’a été renvoyée. Aucune cote n’est inventée.")
                else:
                    st.warning("Les détails du match n’ont pas pu être récupérés.")
            else:
                st.warning("Aucun match exact n’a été trouvé dans les calendriers récupérés à cette date. Vérifie l’orthographe et la date.")

            home_rows = get_recent_results(home_data, actual_home)
            away_rows = get_recent_results(away_data, actual_away)
            prediction = predict(home_rows, away_rows)
            if prediction:
                p_home, p_draw, p_away = prediction["probabilities"]
                st.subheader("📊 Pronostic basé sur la forme récente")
                st.caption("Les matchs les plus récents pèsent davantage. Le classement n’accorde aucun avantage automatique au leader.")
                c1, c2, c3 = st.columns(3)
                c1.metric("Victoire domicile", f"{p_home:.1f}%")
                c2.metric("Match nul", f"{p_draw:.1f}%")
                c3.metric("Victoire extérieur", f"{p_away:.1f}%")
                c1, c2 = st.columns(2)
                c1.metric("Score probable 1", f"{prediction['scores'][0][1]} - {prediction['scores'][0][2]}")
                c2.metric("Score probable 2", f"{prediction['scores'][1][1]} - {prediction['scores'][1][2]}")
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**{actual_home}**")
                    st.write(f"Matchs analysés : {prediction['home']['count']}/10")
                    st.write(f"Indice de forme pondéré : {prediction['home']['form']:.1f}/100")
                    st.write(f"Buts marqués/match : {prediction['home']['scored']:.2f}")
                    st.write(f"Buts encaissés/match : {prediction['home']['conceded']:.2f}")
                    if home_rows:
                        st.dataframe(pd.DataFrame(home_rows), use_container_width=True, hide_index=True)
                with c2:
                    st.markdown(f"**{actual_away}**")
                    st.write(f"Matchs analysés : {prediction['away']['count']}/10")
                    st.write(f"Indice de forme pondéré : {prediction['away']['form']:.1f}/100")
                    st.write(f"Buts marqués/match : {prediction['away']['scored']:.2f}")
                    st.write(f"Buts encaissés/match : {prediction['away']['conceded']:.2f}")
                    if away_rows:
                        st.dataframe(pd.DataFrame(away_rows), use_container_width=True, hide_index=True)
            else:
                st.warning("Les résultats récents ne sont pas disponibles dans un format exploitable. Aucun pronostic chiffré fiable ne peut être calculé.")

        st.subheader("🔍 Vérification des sources complémentaires")
        with st.spinner("Tentative de lecture publique de Transfermarkt et vérification d’Open-Matchday…"):
            tm_home = transfermarkt_team_absences(home_name.strip())
            tm_away = transfermarkt_team_absences(away_name.strip())
            om = open_matchday_status()
        for label, info in [(home_name, tm_home), (away_name, tm_away)]:
            with st.expander(f"Transfermarkt — {label}"):
                st.write(info["status"])
                if info.get("team_url"):
                    st.markdown(f"[Page du club]({info['team_url']})")
                if info.get("injury_url"):
                    st.markdown(f"[Page des blessures]({info['injury_url']})")
                if info.get("rows"):
                    st.dataframe(pd.DataFrame(info["rows"]), use_container_width=True, hide_index=True)
                else:
                    st.write("Aucune liste d’absents vérifiée n’a été récupérée automatiquement.")
        with st.expander("Open-Matchday"):
            st.write(om["message"])
            st.markdown(f"[Ouvrir le dépôt public]({om['url']})")
            st.write("Cette version vérifie l’accès au dépôt, mais n’importe pas de résultats Open-Matchday sans avoir identifié un fichier de données compatible. Les résultats utilisés pour le calcul proviennent de FotMob si disponibles.")

        st.warning("Un signal statistique ou une variation de cote ne prouve pas qu’un match est truqué. Cette version n’établit pas encore un historique multi-bookmakers des cotes. Vérifie les informations importantes avant de parier.")

with st.expander("Sources publiques"):
    st.markdown("- [FotMob](https://www.fotmob.com/) — résultats et fiches de match, selon disponibilité.")
    st.markdown("- [Transfermarkt](https://www.transfermarkt.com/) — tentative de récupération des pages blessures; le site peut bloquer l’automatisation.")
    st.markdown("- [Open-Matchday](https://github.com/open-matchday) — dépôt public à vérifier; les données ne sont pas présumées accessibles.")
    st.caption("Cette application utilise des interfaces publiques non officielles. Les sites peuvent changer, limiter ou bloquer les requêtes. Elle ne nécessite pas de clé API, mais ne peut garantir que chaque donnée sera disponible.")
