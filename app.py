import os
import math
from datetime import date, time
from typing import Any

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://v3.football.api-sports.io"
TIMEOUT = 20

st.set_page_config(page_title="Générateur de pronostics football", page_icon="⚽", layout="wide")

st.title("⚽ Générateur de pronostics football")
st.caption("Version 1 — données API en direct, analyse explicable, aucune donnée de match inventée.")

with st.sidebar:
    st.header("Connexion aux données")
    api_key_input = st.text_input(
        "Clé API-Football",
        value="",
        type="password",
        help="Crée un compte sur api-football.com puis copie ta clé API. La clé reste dans cette session et n'est pas affichée."
    )
    env_key = os.getenv("APISPORTS_KEY", "")
    api_key = api_key_input.strip() or env_key
    st.markdown("[Créer un compte / voir les limites](https://www.api-football.com/pricing)")
    st.caption("L'offre gratuite annoncée par le fournisseur est limitée à 100 requêtes par jour. La couverture dépend de la compétition.")
    st.divider()
    st.markdown("**Sources et limites**")
    st.caption("Source principale : API-Football / API-Sports. Les compositions, blessures et statistiques détaillées ne sont affichées que si l'API renvoie des données.")

st.info("Pour démarrer : ajoute ta clé API dans la barre latérale. Sans clé, le site ne fabrique pas de statistiques et ne peut pas calculer de pronostic réel.")

def api_get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("Clé API absente. Ajoute-la dans la barre latérale.")
    response = requests.get(
        f"{API_BASE}/{endpoint}",
        headers={"x-apisports-key": api_key},
        params=params,
        timeout=TIMEOUT,
    )
    if response.status_code == 429:
        raise RuntimeError("Limite de requêtes API atteinte (HTTP 429). Réessaie plus tard.")
    if response.status_code in (401, 403):
        raise RuntimeError("Clé API invalide ou accès non autorisé (HTTP 401/403). Vérifie ton compte et la couverture.")
    response.raise_for_status()
    payload = response.json()
    errors = payload.get("errors")
    if errors:
        raise RuntimeError(f"Erreur renvoyée par l'API : {errors}")
    return payload

@st.cache_data(ttl=900, show_spinner=False)
def find_teams_cached(query: str, key: str):
    # Cache key is included so changing credentials does not reuse another account's results.
    response = requests.get(
        f"{API_BASE}/teams",
        headers={"x-apisports-key": key},
        params={"search": query},
        timeout=TIMEOUT,
    )
    if response.status_code == 429:
        raise RuntimeError("Limite de requêtes API atteinte.")
    if response.status_code in (401, 403):
        raise RuntimeError("Clé API invalide ou accès non autorisé.")
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    return data.get("response", [])

def api_get_with_key(endpoint: str, params: dict[str, Any], key: str):
    response = requests.get(
        f"{API_BASE}/{endpoint}",
        headers={"x-apisports-key": key},
        params=params,
        timeout=TIMEOUT,
    )
    if response.status_code == 429:
        raise RuntimeError("Limite de requêtes API atteinte (HTTP 429).")
    if response.status_code in (401, 403):
        raise RuntimeError("Clé API invalide ou accès non autorisé.")
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    return data.get("response", [])

def team_label(item):
    team = item.get("team", {})
    country = item.get("venue", {}).get("city") or ""
    return f'{team.get("name", "Équipe inconnue")} — ID {team.get("id")}' + (f" ({country})" if country else "")

def get_team_matches(team_id: int, key: str):
    return api_get_with_key("fixtures", {"team": team_id, "last": 10}, key)

def normalize_matches(fixtures: list[dict[str, Any]], team_id: int):
    rows = []
    for item in fixtures:
        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        home = teams.get("home", {})
        away = teams.get("away", {})
        gh, ga = goals.get("home"), goals.get("away")
        status = fixture.get("status", {}).get("short")
        # Only completed matches with a real numeric score are accepted.
        if status not in ("FT", "AET", "PEN") or not isinstance(gh, int) or not isinstance(ga, int):
            continue
        if home.get("id") == team_id:
            venue, gf, gc, opponent = "Domicile", gh, ga, away.get("name", "Inconnu")
            result = "V" if gh > ga else ("N" if gh == ga else "D")
        elif away.get("id") == team_id:
            venue, gf, gc, opponent = "Extérieur", ga, gh, home.get("name", "Inconnu")
            result = "V" if ga > gh else ("N" if ga == gh else "D")
        else:
            continue
        rows.append({
            "Date": fixture.get("date", "")[:10],
            "Adversaire": opponent,
            "Terrain": venue,
            "Score": f"{gh}-{ga}",
            "BP": gf,
            "BC": gc,
            "Résultat": result,
            "_date": fixture.get("date", ""),
        })
    rows.sort(key=lambda r: r["_date"], reverse=True)
    return rows[:10]

def poisson_probs(lam: float, max_goals: int = 8):
    probs = []
    for k in range(max_goals + 1):
        probs.append(math.exp(-lam) * (lam ** k) / math.factorial(k))
    return probs

def model_prediction(home_rows, away_rows):
    # Use available recent matches only. A light venue blend is applied when enough venue-specific games exist.
    # No default/fake team stats are used; if either team has no completed matches, stop.
    if not home_rows or not away_rows:
        raise ValueError("Impossible de calculer : l'API n'a pas renvoyé de matchs terminés pour les deux équipes.")
    h_gf = sum(r["BP"] for r in home_rows) / len(home_rows)
    h_ga = sum(r["BC"] for r in home_rows) / len(home_rows)
    a_gf = sum(r["BP"] for r in away_rows) / len(away_rows)
    a_ga = sum(r["BC"] for r in away_rows) / len(away_rows)

    home_specific = [r for r in home_rows if r["Terrain"] == "Domicile"]
    away_specific = [r for r in away_rows if r["Terrain"] == "Extérieur"]
    # Only blend venue-specific evidence when there are at least 3 observations.
    h_home_gf = sum(r["BP"] for r in home_specific) / len(home_specific) if len(home_specific) >= 3 else None
    h_home_ga = sum(r["BC"] for r in home_specific) / len(home_specific) if len(home_specific) >= 3 else None
    a_away_gf = sum(r["BP"] for r in away_specific) / len(away_specific) if len(away_specific) >= 3 else None
    a_away_ga = sum(r["BC"] for r in away_specific) / len(away_specific) if len(away_specific) >= 3 else None

    # Attack estimate = average of team's scoring rate and opponent's conceded rate.
    home_attack = 0.65 * h_gf + 0.35 * (a_away_ga if a_away_ga is not None else a_ga)
    away_attack = 0.65 * a_gf + 0.35 * (h_home_ga if h_home_ga is not None else h_ga)
    # Conservative home advantage, explicitly documented as a model assumption rather than observed fact.
    lam_home = max(0.05, 1.06 * home_attack)
    lam_away = max(0.05, 0.94 * away_attack)

    ph = poisson_probs(lam_home)
    pa = poisson_probs(lam_away)
    p_home = p_draw = p_away = 0.0
    score_probs = []
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            p = pi * pj
            score_probs.append((i, j, p))
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    total = p_home + p_draw + p_away
    p_home, p_draw, p_away = [x / total for x in (p_home, p_draw, p_away)]
    top_scores = sorted(score_probs, key=lambda x: x[2], reverse=True)[:2]
    return {
        "lambda_home": lam_home,
        "lambda_away": lam_away,
        "p_home": p_home,
        "p_draw": p_draw,
        "p_away": p_away,
        "top_scores": top_scores,
        "stats": {
            "home_gf": h_gf, "home_ga": h_ga, "away_gf": a_gf, "away_ga": a_ga,
            "home_n": len(home_rows), "away_n": len(away_rows),
            "home_specific_n": len(home_specific), "away_specific_n": len(away_specific),
            "home_specific_used": h_home_gf is not None,
            "away_specific_used": a_away_gf is not None,
        }
    }

def form_summary(rows):
    n = len(rows)
    wins = sum(r["Résultat"] == "V" for r in rows)
    draws = sum(r["Résultat"] == "N" for r in rows)
    losses = sum(r["Résultat"] == "D" for r in rows)
    points = wins * 3 + draws
    gf = sum(r["BP"] for r in rows)
    ga = sum(r["BC"] for r in rows)
    return {
        "n": n, "wins": wins, "draws": draws, "losses": losses, "points": points,
        "gf": gf, "ga": ga, "gf_avg": gf/n if n else None, "ga_avg": ga/n if n else None,
        "form": "".join(r["Résultat"] for r in rows)
    }

st.header("1. Saisir le match")
col1, col2 = st.columns(2)
with col1:
    home_query = st.text_input("Équipe à domicile", placeholder="Ex. Arsenal")
with col2:
    away_query = st.text_input("Équipe à l'extérieur", placeholder="Ex. Chelsea")
col3, col4 = st.columns(2)
with col3:
    match_date = st.date_input("Date du match", value=date.today())
with col4:
    match_time = st.time_input("Heure locale du match", value=time(17, 0))
st.caption("La date et l'heure servent à identifier le match et à rechercher une éventuelle composition. L'heure saisie est considérée comme l'heure locale de l'utilisateur; les horaires retournés par l'API sont conservés dans leur fuseau source (UTC).")

if st.button("Analyser le match", type="primary", use_container_width=True):
    st.session_state["analysis_active"] = True
run = st.session_state.get("analysis_active", False)

if run:
    if not api_key:
        st.error("Ajoute une clé API-Football dans la barre latérale avant de lancer l'analyse.")
        st.stop()
    if not home_query.strip() or not away_query.strip():
        st.error("Saisis le nom des deux équipes.")
        st.stop()
    if home_query.strip().casefold() == away_query.strip().casefold():
        st.error("Les deux noms d'équipe sont identiques. Vérifie la saisie.")
        st.stop()

    try:
        with st.spinner("Recherche des équipes dans la base de données..."):
            home_candidates = find_teams_cached(home_query.strip(), api_key)
            away_candidates = find_teams_cached(away_query.strip(), api_key)
        if not home_candidates:
            st.error(f"Aucune équipe trouvée pour « {home_query} ». Essaie le nom officiel ou une autre orthographe.")
            st.stop()
        if not away_candidates:
            st.error(f"Aucune équipe trouvée pour « {away_query} ». Essaie le nom officiel ou une autre orthographe.")
            st.stop()

        st.subheader("2. Vérifier l'identité des équipes")
        st.caption("Choisis les bonnes équipes dans les résultats de l'API avant le calcul.")
        labels_h = [team_label(x) for x in home_candidates[:20]]
        labels_a = [team_label(x) for x in away_candidates[:20]]
        sel1, sel2 = st.columns(2)
        with sel1:
            selected_home_label = st.selectbox("Équipe à domicile trouvée", labels_h, key="home_select")
        with sel2:
            selected_away_label = st.selectbox("Équipe à l'extérieur trouvée", labels_a, key="away_select")
        home_item = home_candidates[labels_h.index(selected_home_label)]
        away_item = away_candidates[labels_a.index(selected_away_label)]
        home_team = home_item["team"]
        away_team = away_item["team"]

        if home_team["id"] == away_team["id"]:
            st.error("Tu as sélectionné la même équipe des deux côtés.")
            st.stop()

        with st.spinner("Récupération des 10 derniers matchs terminés..."):
            home_fixtures = get_team_matches(home_team["id"], api_key)
            away_fixtures = get_team_matches(away_team["id"], api_key)
        home_rows = normalize_matches(home_fixtures, home_team["id"])
        away_rows = normalize_matches(away_fixtures, away_team["id"])

        st.subheader("3. Forme, attaque et défense")
        hsum, asum = form_summary(home_rows), form_summary(away_rows)
        c1, c2 = st.columns(2)
        for col, name, rows, summ in [(c1, home_team["name"], home_rows, hsum), (c2, away_team["name"], away_rows, asum)]:
            with col:
                st.markdown(f"### {name}")
                st.write(f"Forme (du plus récent au plus ancien) : **{summ['form'] or 'Indisponible'}**")
                m1, m2, m3 = st.columns(3)
                m1.metric("Victoires", summ["wins"])
                m2.metric("Nuls", summ["draws"])
                m3.metric("Défaites", summ["losses"])
                m1, m2, m3 = st.columns(3)
                m1.metric("Points", f"{summ['points']}/{3*summ['n']}" if summ["n"] else "N/D")
                m2.metric("Buts marqués", summ["gf"])
                m3.metric("Buts encaissés", summ["ga"])
                st.write(f"Moyenne buts marqués : **{summ['gf_avg']:.2f}**" if summ["n"] else "Moyenne buts marqués : N/D")
                st.write(f"Moyenne buts encaissés : **{summ['ga_avg']:.2f}**" if summ["n"] else "Moyenne buts encaissés : N/D")
                if rows:
                    df = pd.DataFrame([{k:v for k,v in r.items() if not k.startswith("_")} for r in rows])
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.warning("L'API n'a renvoyé aucun match terminé exploitable pour cette équipe.")
        st.subheader("4. Probabilités calculées")
        if not home_rows or not away_rows:
            st.error("Pronostic bloqué : il manque des résultats vérifiés pour au moins une équipe. Aucune probabilité fictive ne sera affichée.")
            st.stop()

        pred = model_prediction(home_rows, away_rows)
        p1, px, p2 = st.columns(3)
        p1.metric(f"Victoire {home_team['name']}", f"{pred['p_home']*100:.1f}%")
        px.metric("Match nul", f"{pred['p_draw']*100:.1f}%")
        p2.metric(f"Victoire {away_team['name']}", f"{pred['p_away']*100:.1f}%")
        st.caption(f"Somme des probabilités : {(pred['p_home']+pred['p_draw']+pred['p_away'])*100:.1f}% (les petites différences éventuelles viennent des arrondis).")
        st.markdown("#### Deux scores exacts les plus probables selon le modèle")
        score_cols = st.columns(2)
        for col, (gh, ga, probability) in zip(score_cols, pred["top_scores"]):
            col.metric(f"{home_team['name']} — {away_team['name']}", f"{gh} – {ga}", f"{probability*100:.1f}% de probabilité du score")
        st.write(f"Buts attendus estimés : **{home_team['name']} {pred['lambda_home']:.2f}** — **{pred['lambda_away']:.2f} {away_team['name']}**")

        st.subheader("5. Joueurs absents et compositions")
        st.warning("Cette version ne déduit pas les absences à partir des statistiques. Pour éviter toute invention, les absences et titulaires sont marqués « non vérifiés » tant qu'un match précis et des données API couvertes ne les confirment pas.")
        # Look up a matching fixture on the requested date; if found, query its lineup and injuries.
        try:
            date_fixtures = api_get_with_key("fixtures", {"date": match_date.isoformat()}, api_key)
            candidates = []
            for f in date_fixtures:
                ht = f.get("teams", {}).get("home", {})
                at = f.get("teams", {}).get("away", {})
                if ht.get("id") == home_team["id"] and at.get("id") == away_team["id"]:
                    candidates.append(f)
            if candidates:
                match = candidates[0]
                fixture_id = match.get("fixture", {}).get("id")
                st.success(f"Match correspondant trouvé dans l'API : ID {fixture_id}.")
                try:
                    lineups = api_get_with_key("fixtures/lineups", {"fixture": fixture_id}, api_key)
                    if lineups:
                        st.markdown("**Compositions renvoyées par l'API**")
                        for entry in lineups:
                            teamname = entry.get("team", {}).get("name", "Équipe")
                            startxi = entry.get("startXI") or []
                            names = [x.get("player", {}).get("name") for x in startxi if x.get("player", {}).get("name")]
                            st.write(f"{teamname} : " + (", ".join(names) if names else "composition indisponible dans cette réponse"))
                    else:
                        st.info("Aucune composition publiée dans la réponse API à cet instant.")
                except Exception as e:
                    st.info(f"Compositions non vérifiées : {e}")
                try:
                    injuries = api_get_with_key("injuries", {"fixture": fixture_id}, api_key)
                    if injuries:
                        st.markdown("**Absences / blessures renvoyées par l'API**")
                        st.dataframe(pd.json_normalize(injuries), use_container_width=True, hide_index=True)
                    else:
                        st.info("Aucune blessure renvoyée pour ce match. Cela ne signifie pas qu'il n'y a aucune absence.")
                except Exception as e:
                    st.info(f"Absences non vérifiées : {e}")
            else:
                st.info("Aucun match exact domicile/extérieur trouvé dans les rencontres de cette date. Les compositions et absences restent non vérifiées.")
        except Exception as e:
            st.info(f"Vérification du match et des compositions non disponible : {e}")

        st.subheader("6. Explication du calcul et limites")
        st.markdown(
            f"""
            - Matchs utilisés : **{hsum['n']}** pour {home_team['name']} et **{asum['n']}** pour {away_team['name']}.
            - Moyennes brutes sur les matchs récents : {home_team['name']} marque **{hsum['gf_avg']:.2f}** et encaisse **{hsum['ga_avg']:.2f}** buts par match ; {away_team['name']} marque **{asum['gf_avg']:.2f}** et encaisse **{asum['ga_avg']:.2f}**.
            - Estimation des buts : moyenne pondérée entre le taux de buts marqués d'une équipe et le taux de buts encaissés par l'adversaire. Les données spécifiques domicile/extérieur ne sont intégrées que si au moins 3 matchs correspondants sont disponibles.
            - Le modèle utilise une distribution de Poisson indépendante et un ajustement domicile fixe de 1,06 / 0,94. Ce facteur est une **hypothèse de départ**, pas une statistique observée sur ces équipes.
            - Les niveaux des adversaires ne sont pas encore corrigés dans cette V1 ; les probabilités sont donc des estimations exploratoires, pas des garanties. Les scores sont calculés à partir des moyennes, pas copiés d'un pronostiqueur.
            - Les matchs amicaux, les changements d'entraîneur, la qualité des adversaires et les absences peuvent limiter la fiabilité. Les résultats API doivent être vérifiés pour la compétition et la saison.
            """
        )
        st.caption(f"Source des résultats : API-Football (API-Sports). Analyse exécutée le {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}.")
    except requests.exceptions.Timeout:
        st.error("La source de données n'a pas répondu à temps. Réessaie plus tard.")
    except requests.exceptions.RequestException as e:
        st.error(f"Erreur réseau lors de l'accès à l'API : {e}")
    except Exception as e:
        st.error(str(e))

st.divider()
st.caption("Cette application est un prototype statistique. Elle ne garantit pas les résultats sportifs et ne doit pas être considérée comme une certitude de gain.")
