import math
from datetime import date, time
from io import StringIO

import pandas as pd
import requests
import streamlit as st

BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data"
TIMEOUT = 25

FOTMOB_API = "https://www.fotmob.com/api"

LEAGUES = {
    "Angleterre — Premier League": "ENG_1_premier-league",
    "Angleterre — Championship": "ENG_2_championship",
    "Espagne — La Liga": "ESP_1_la-liga",
    "Allemagne — Bundesliga": "GER_1_bundesliga",
    "Italie — Serie A": "ITA_1_serie-a",
    "France — Ligue 1": "FRA_1_ligue-1",
    "France — Ligue 2": "FRA_2_ligue-2",
    "Pays-Bas — Eredivisie": "NED_1_eredivisie",
    "Portugal — Primeira Liga": "POR_1_primeira-liga",
    "Belgique — Pro League": "BEL_1_pro-league",
    "Turquie — Süper Lig": "TUR_1_super-lig",
    "Grèce — Super League": "GRE_1_super-league",
    "États-Unis — MLS": "USA_1_mls",
    "Brésil — Serie A": "BRA_1_serie-a",
    "UEFA — Ligue des champions": "UEFA_1_champions-league",
    "UEFA — Ligue Europa": "UEFA_2_europa-league",
    "UEFA — Ligue Conférence": "UEFA_3_conference-league",
}

st.set_page_config(page_title="Pronostics football", page_icon="⚽", layout="wide")
st.title("⚽ Générateur de pronostics football")
st.caption("Version sans clé API — forme récente et estimations statistiques.")
st.info("Les données proviennent de fichiers CSV publics actualisés quotidiennement. Ce n'est pas du direct et la couverture varie selon le championnat.")

@st.cache_data(ttl=3600, show_spinner=False)
def load_csv(url: str) -> pd.DataFrame:
    response = requests.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text))

def load_league(slug: str) -> pd.DataFrame:
    frames = []
    for season in ("2627", "2526"):
        url = f"{BASE}/{slug}/results_{season}.csv"
        try:
            df = load_csv(url)
            required = {"date", "home", "away", "ft_home", "ft_away"}
            if df.empty or not required.issubset(df.columns):
                continue
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df["ft_home"] = pd.to_numeric(df["ft_home"], errors="coerce")
            df["ft_away"] = pd.to_numeric(df["ft_away"], errors="coerce")
            df = df.dropna(subset=["date", "home", "away", "ft_home", "ft_away"])
            frames.append(df)
        except Exception:
            continue
    if not frames:
        raise RuntimeError("Aucun fichier de résultats disponible pour ce championnat. Essaie un autre championnat.")
    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(subset=["date", "home", "away"], keep="last")
    return result.sort_values("date", ascending=False)

@st.cache_data(ttl=3600, show_spinner=False)
def fotmob_search_team(name):
    try:
        response = requests.get(
            f"{FOTMOB_API}/search/suggest",
            params={"term": name},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )
        response.raise_for_status()
        data = response.json()

        for item in data.get("suggestions", []):
            if item.get("type") == "team":
                return {
                    "id": item.get("id"),
                    "name": item.get("name")
                }
        return None
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fotmob_get_squad(team_id):
    try:
        response = requests.get(
            f"{FOTMOB_API}/data/teams",
            params={"id": team_id},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )
        response.raise_for_status()
        data = response.json()

        players = []
        for group in data.get("squad", []):
            position = group.get("title", "Poste inconnu")
            for player in group.get("members", []):
                players.append({
                    "id": player.get("id"),
                    "name": player.get("name"),
                    "position": player.get("role") or position
                })

        return players
    except Exception:
        return None

def team_matches(df: pd.DataFrame, team: str, venue: str | None = None) -> list[dict]:
    games = df[(df["home"] == team) | (df["away"] == team)].sort_values("date", ascending=False)
    rows = []
    for _, m in games.iterrows():
        is_home = m["home"] == team
        if venue == "home" and not is_home:
            continue
        if venue == "away" and is_home:
            continue
        gf = int(m["ft_home"] if is_home else m["ft_away"])
        ga = int(m["ft_away"] if is_home else m["ft_home"])
        rows.append({
            "Date": m["date"].strftime("%Y-%m-%d"),
            "Adversaire": m["away"] if is_home else m["home"],
            "Terrain": "Domicile" if is_home else "Extérieur",
            "Score": f"{int(m['ft_home'])}-{int(m['ft_away'])}",
            "BP": gf, "BC": ga,
            "Résultat": "V" if gf > ga else ("N" if gf == ga else "D"),
        })
        if len(rows) >= 10:
            break
    return rows

def poisson(lam: float, max_goals: int = 8):
    return [math.exp(-lam) * lam**k / math.factorial(k) for k in range(max_goals + 1)]

def prediction(home_rows: list[dict], away_rows: list[dict]) -> dict:
    if not home_rows or not away_rows:
        raise ValueError("Il manque des matchs terminés pour une équipe. Essaie un autre championnat.")
    hgf = sum(r["BP"] for r in home_rows) / len(home_rows)
    hga = sum(r["BC"] for r in home_rows) / len(home_rows)
    agf = sum(r["BP"] for r in away_rows) / len(away_rows)
    aga = sum(r["BC"] for r in away_rows) / len(away_rows)
    h_home = [r for r in home_rows if r["Terrain"] == "Domicile"]
    a_away = [r for r in away_rows if r["Terrain"] == "Extérieur"]
    hgf_home = sum(r["BP"] for r in h_home) / len(h_home) if len(h_home) >= 3 else hgf
    hga_home = sum(r["BC"] for r in h_home) / len(h_home) if len(h_home) >= 3 else hga
    agf_away = sum(r["BP"] for r in a_away) / len(a_away) if len(a_away) >= 3 else agf
    aga_away = sum(r["BC"] for r in a_away) / len(a_away) if len(a_away) >= 3 else aga
    lh = max(0.05, 1.06 * (0.60 * hgf_home + 0.40 * aga_away))
    la = max(0.05, 0.94 * (0.60 * agf_away + 0.40 * hga_home))
    ph, pa = poisson(lh), poisson(la)
    p1 = px = p2 = 0.0
    scores = []
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            p = pi * pj
            scores.append((i, j, p))
            if i > j:
                p1 += p
            elif i == j:
                px += p
            else:
                p2 += p
    total = p1 + px + p2
    return {
        "p1": p1 / total, "px": px / total, "p2": p2 / total,
        "scores": sorted(scores, key=lambda x: x[2], reverse=True)[:2],
        "lh": lh, "la": la,
    }

def stats(rows: list[dict]) -> dict:
    n = len(rows)
    w = sum(r["Résultat"] == "V" for r in rows)
    d = sum(r["Résultat"] == "N" for r in rows)
    l = sum(r["Résultat"] == "D" for r in rows)
    return {
        "n": n, "w": w, "d": d, "l": l, "pts": 3*w+d,
        "gf": sum(r["BP"] for r in rows), "ga": sum(r["BC"] for r in rows),
        "form": "".join(r["Résultat"] for r in rows),
        "avg_gf": sum(r["BP"] for r in rows)/n if n else 0,
        "avg_ga": sum(r["BC"] for r in rows)/n if n else 0,
    }

with st.sidebar:
    st.header("Source des données")
    st.write("Open-matchday — CSV public")
    st.markdown("[Ouvrir la source](https://github.com/oladeeayo/open-matchday)")
    st.caption("Aucune clé API. Données non live, actualisées quotidiennement.")
    league_name = st.selectbox("Championnat", list(LEAGUES.keys()))

try:
    with st.spinner("Chargement des résultats publics..."):
        df = load_league(LEAGUES[league_name])
except Exception as e:
    st.error(f"Impossible de charger ce championnat : {e}")
    st.stop()

teams = sorted(set(df["home"].dropna().astype(str)) | set(df["away"].dropna().astype(str)))
if len(teams) < 2:
    st.error("Pas assez d'équipes dans les données de ce championnat.")
    st.stop()

st.header("1. Choisir le match")
c1, c2 = st.columns(2)
with c1:
    home = st.selectbox("Équipe à domicile", teams, index=0)
with c2:
    away = st.selectbox("Équipe à l'extérieur", teams, index=1 if len(teams) > 1 else 0)
c3, c4 = st.columns(2)
with c3:
    match_date = st.date_input("Date du match", value=date.today())
with c4:
    match_time = st.time_input("Heure locale", value=time(17, 0))


st.header("2. Sélection des joueurs absents")

st.caption(
    "Saisis les effectifs connus. Une case à cocher apparaîtra pour "
    "chaque joueur que tu pourras déclarer absent."
)

col_home, col_away = st.columns(2)

with col_home:
    st.subheader(f"🏠 {home}")
    roster_home_text = st.text_area(
        "Effectif — équipe à domicile",
        placeholder="Un joueur par ligne\nExemple :\nJoueur 1\nJoueur 2",
        key="roster_home",
    )
    roster_home = list(dict.fromkeys(
        name.strip()
        for name in roster_home_text.splitlines()
        if name.strip()
    ))
    absent_home = []
    for i, player in enumerate(roster_home):
        if st.checkbox(
            f"{player} — absent",
            key=f"abs_home_{i}_{player}",
        ):
            absent_home.append(player)

with col_away:
    st.subheader(f"✈️ {away}")
    roster_away_text = st.text_area(
        "Effectif — équipe à l'extérieur",
        placeholder="Un joueur par ligne\nExemple :\nJoueur 1\nJoueur 2",
        key="roster_away",
    )
    roster_away = list(dict.fromkeys(
        name.strip()
        for name in roster_away_text.splitlines()
        if name.strip()
    ))
    absent_away = []
    for i, player in enumerate(roster_away):
        if st.checkbox(
            f"{player} — absent",
            key=f"abs_away_{i}_{player}",
        ):
            absent_away.append(player)
    

if home == away:
    st.warning("Choisis deux équipes différentes.")
elif st.button("Analyser le match", type="primary", use_container_width=True):
    hrows = team_matches(df, home)
    arows = team_matches(df, away)
    if not hrows or not arows:
        st.error("Pas assez de résultats terminés pour une des équipes. Essaie un autre championnat.")
        st.stop()

    st.subheader("2. Forme récente — jusqu'à 10 matchs terminés")
    c1, c2 = st.columns(2)
    for col, name, rows in [(c1, home, hrows), (c2, away, arows)]:
        s = stats(rows)
        with col:
            st.markdown(f"### {name}")
            st.write(f"Forme (plus récent → plus ancien) : **{s['form']}**")
            m1, m2, m3 = st.columns(3)
            m1.metric("Victoires", s["w"])
            m2.metric("Nuls", s["d"])
            m3.metric("Défaites", s["l"])
            m1, m2, m3 = st.columns(3)
            m1.metric("Points", f"{s['pts']}/{3*s['n']}")
            m2.metric("Buts marqués", s["gf"])
            m3.metric("Buts encaissés", s["ga"])
            st.write(f"Moyenne marquée : **{s['avg_gf']:.2f}** par match")
            st.write(f"Moyenne encaissée : **{s['avg_ga']:.2f}** par match")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("3. Pronostic statistique")
    pred = prediction(hrows, arows)
    c1, cx, c2 = st.columns(3)
    c1.metric(f"Victoire {home}", f"{pred['p1']*100:.1f}%")
    cx.metric("Match nul", f"{pred['px']*100:.1f}%")
    c2.metric(f"Victoire {away}", f"{pred['p2']*100:.1f}%")
    st.markdown("#### Deux scores exacts les plus probables selon le modèle")
    s1, s2 = st.columns(2)
    for col, (gh, ga, p) in zip((s1, s2), pred["scores"]):
        col.metric("Score estimé", f"{gh} – {ga}", f"{p*100:.1f}% selon le modèle")
    st.write(f"Buts attendus estimés : **{home} {pred['lh']:.2f} — {pred['la']:.2f} {away}**")
    st.subheader("4. Limites")
   
    st.subheader("5. Joueurs déclarés absents")

    c1, c2 = st.columns(2)

    with c1:
        st.write(f"**{home}**")
        st.write(", ".join(absent_home) if absent_home else "Aucun absent sélectionné")

    with c2:
        st.write(f"**{away}**")
        st.write(", ".join(absent_away) if absent_away else "Aucun absent sélectionné")

    st.warning(
        "Les absences sélectionnées sont affichées à titre informatif. "
        "Elles ne modifient pas encore les probabilités du modèle."
    )
    
    st.markdown(
        "- Les pourcentages sont des estimations, pas des garanties.\n"
        "- Les données sont actualisées quotidiennement, pas en direct.\n"
        "- Les blessures, compositions, xG et cotes ne sont pas fournis par cette source.\n"
        "- Les 10 matchs peuvent inclure la saison précédente si les fichiers sont disponibles.\n"
        "- Certains championnats peuvent ne pas être disponibles ou avoir des données incomplètes."
    )
    st.caption(f"Source : open-matchday — {league_name}. Vérifie les résultats avant toute décision.")

st.divider()
st.caption("Source : https://github.com/oladeeayo/open-matchday — usage informatif non commercial. Les pronostics ne garantissent aucun gain.")
