
import streamlit as st
import requests
import unicodedata
import re
import math
from datetime import date, time, timedelta, datetime

st.set_page_config(
    page_title="Générateur de pronostics football",
    page_icon="⚽",
    layout="wide"
)

st.title("⚽ Générateur de pronostics football")
st.write(
    "Sources : ESPN puis OpenFootball. "
    "Aucun score fictif n'est ajouté."
)

HEADERS = {"User-Agent": "Mozilla/5.0 FootballPredictionApp/1.0"}
TIMEOUT = 15

# ESPN : identifiants publics des compétitions
ESPN_LEAGUES = {
    "France - Ligue 1": "fra.1",
    "Angleterre - Premier League": "eng.1",
    "Espagne - Liga": "esp.1",
    "Italie - Serie A": "ita.1",
    "Allemagne - Bundesliga": "ger.1",
    "Ligue des champions": "uefa.champions",
    "Ligue Europa": "uefa.europa",
    "États-Unis - MLS": "usa.1",
    "Arabie saoudite": "ksa.1",
}

# OpenFootball : fichiers publics JSON sans clé API
OPENFOOTBALL_LEAGUES = {
    "France - Ligue 1": "fr.1",
    "Angleterre - Premier League": "en.1",
    "Espagne - Liga": "es.1",
    "Italie - Serie A": "it.1",
    "Allemagne - Bundesliga": "de.1",
}

RAW_BASE = (
    "https://raw.githubusercontent.com/"
    "openfootball/football.json/master"
)


def normalize(value):
    value = unicodedata.normalize(
        "NFKD", str(value or "")
    ).encode("ascii", "ignore").decode().lower()

    value = re.sub(
        r"\b(fc|cf|afc|sc|club|football|the)\b", " ", value
    )
    return re.sub(r"[^a-z0-9]", "", value)


ALIASES = {
    "psg": "parissaintgermain",
    "parissaintgermain": "parissaintgermain",
    "parissaintgermainfc": "parissaintgermain",
    "ol": "olympiquelyonnais",
    "lyon": "olympiquelyonnais",
    "olympiquelyonnais": "olympiquelyonnais",
    "sevilla": "sevilla",
    "seville": "sevilla",
    "sevillafc": "sevilla",
    "osasuna": "osasuna",
    "manutd": "manchesterunited",
    "manchesterunited": "manchesterunited",
    "manunited": "manchesterunited",
    "mancity": "manchestercity",
    "manchestercity": "manchestercity",
    "atleticomadrid": "atleticomadrid",
    "atleticodemadrid": "atleticomadrid",
}


def canonical(name):
    n = normalize(name)
    return ALIASES.get(n, n)


def same_team(target, candidate):
    a, b = canonical(target), canonical(candidate)

    if not a or not b:
        return False
    if a == b:
        return True

    # Correspondance partielle seulement pour les noms assez longs.
    return len(a) >= 6 and len(b) >= 6 and (
        a in b or b in a
    )


def parse_date(value):
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).date()
    except (ValueError, TypeError):
        try:
            return date.fromisoformat(str(value)[:10])
        except (ValueError, TypeError):
            return None


def make_game(day, home, away, hg, ag, source, league=""):
    try:
        hg, ag = int(hg), int(ag)
    except (ValueError, TypeError):
        return None

    if hg < 0 or ag < 0:
        return None

    parsed_day = parse_date(day)
    if not parsed_day:
        return None

    return {
        "date": parsed_day,
        "home": str(home).strip(),
        "away": str(away).strip(),
        "hg": hg,
        "ag": ag,
        "source": source,
        "league": league,
    }


def get_json(url, params=None):
    response = requests.get(
        url, params=params, headers=HEADERS, timeout=TIMEOUT
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=900, show_spinner=False)
def fetch_espn(start_day, end_day):
    games = []
    errors = []

    start_s = start_day.strftime("%Y%m%d")
    end_s = end_day.strftime("%Y%m%d")

    for league_name, code in ESPN_LEAGUES.items():
        url = (
            "https://site.api.espn.com/apis/site/v2/"
            f"sports/soccer/{code}/scoreboard"
        )

        try:
            data = get_json(
                url,
                {"dates": f"{start_s}-{end_s}", "limit": 500}
            )

            for event in data.get("events", []):
                # Le statut est généralement porté par event.status.
                status_obj = event.get("status") or {}
                status = (
                    (status_obj.get("type") or {}).get("state", "")
                )

                competition_list = event.get("competitions") or []
                if not competition_list:
                    continue

                competition = competition_list[0]

                # Secours si le statut est placé au niveau compétition.
                if not status:
                    status = (
                        ((competition.get("status") or {}).get("type")
                         or {}).get("state", "")
                    )

                if status != "post":
                    continue

                competitors = competition.get("competitors") or []
                if len(competitors) < 2:
                    continue

                home = next(
                    (c for c in competitors
                     if c.get("homeAway") == "home"), None
                )
                away = next(
                    (c for c in competitors
                     if c.get("homeAway") == "away"), None
                )

                if not home or not away:
                    continue

                home_team = home.get("team") or {}
                away_team = away.get("team") or {}

                game = make_game(
                    event.get("date"),
                    home_team.get("displayName") or home_team.get("name"),
                    away_team.get("displayName") or away_team.get("name"),
                    home.get("score"),
                    away.get("score"),
                    "ESPN",
                    league_name,
                )

                if game:
                    games.append(game)

        except Exception as exc:
            errors.append(
                f"{league_name} : {type(exc).__name__} - {str(exc)[:120]}"
            )

    return games, errors


def season_candidates(target_day):
    # Saisons européennes généralement organisées d'été à été.
    year = target_day.year
    if target_day.month < 7:
        year -= 1

    current = f"{year}-{str(year + 1)[-2:]}"
    previous = f"{year - 1}-{str(year)[-2:]}"
    return [current, previous]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_openfootball(start_iso, end_iso):
    start_day = date.fromisoformat(start_iso)
    end_day = date.fromisoformat(end_iso)
    games = []
    errors = []

    # Plusieurs saisons permettent de retrouver les matchs précédents.
    seasons = set()
    for year in range(start_day.year - 1, end_day.year + 1):
        seasons.add(f"{year}-{str(year + 1)[-2:]}")

    for league_name, code in OPENFOOTBALL_LEAGUES.items():
        for season in sorted(seasons):
            url = f"{RAW_BASE}/{season}/{code}.json"

            try:
                data = get_json(url)

                for match in data.get("matches", []):
                    score = match.get("score") or {}
                    ft = score.get("ft")

                    # ft = score final. Ne pas confondre avec la mi-temps.
                    if not isinstance(ft, list) or len(ft) != 2:
                        continue

                    day = parse_date(match.get("date"))
                    if not day or not (start_day <= day <= end_day):
                        continue

                    game = make_game(
                        match.get("date"),
                        match.get("team1"),
                        match.get("team2"),
                        ft[0],
                        ft[1],
                        "OpenFootball",
                        league_name,
                    )
                    if game:
                        games.append(game)

            except Exception as exc:
                # Certaines saisons/fichiers n'existent pas.
                errors.append(
                    f"{league_name} {season} : "
                    f"{type(exc).__name__}"
                )

    return games, errors


def deduplicate(games):
    unique = {}

    for g in games:
        key = (
            g["date"].isoformat(),
            canonical(g["home"]),
            canonical(g["away"]),
            g["hg"],
            g["ag"],
        )
        if key not in unique:
            unique[key] = g

    return list(unique.values())


def team_results(target, games, cutoff, venue=None):
    results = []

    for g in games:
        # Ne jamais utiliser un match du jour futur ou postérieur au match cible.
        if g["date"] >= cutoff:
            continue

        is_home = same_team(target, g["home"])
        is_away = same_team(target, g["away"])

        if not is_home and not is_away:
            continue

        if venue == "home" and not is_home:
            continue
        if venue == "away" and not is_away:
            continue

        if is_home:
            gf, ga = g["hg"], g["ag"]
            opponent = g["away"]
            location = "Domicile"
        else:
            gf, ga = g["ag"], g["hg"]
            opponent = g["home"]
            location = "Extérieur"

        results.append({
            "date": g["date"],
            "gf": gf,
            "ga": ga,
            "opponent": opponent,
            "location": location,
            "source": g["source"],
            "league": g["league"],
        })

    results.sort(key=lambda x: x["date"], reverse=True)
    return results[:10]


def poisson(lam, goals):
    return math.exp(-lam) * lam ** goals / math.factorial(goals)


def averages(results):
    if not results:
        return None

    return {
        "played": len(results),
        "scored": sum(x["gf"] for x in results) / len(results),
        "conceded": sum(x["ga"] for x in results) / len(results),
        "wins": sum(x["gf"] > x["ga"] for x in results),
        "draws": sum(x["gf"] == x["ga"] for x in results),
        "losses": sum(x["gf"] < x["ga"] for x in results),
    }


def predict(home_results, away_results):
    # Exige des données réelles pour les deux équipes.
    h = averages(home_results)
    a = averages(away_results)

    # Avantage domicile modéré ; paramètres bornés pour limiter les extrêmes.
    lh = max(0.15, min(4.5, 0.55 * h["scored"] + 0.45 * a["conceded"] + 0.18))
    la = max(0.15, min(4.5, 0.55 * a["scored"] + 0.45 * h["conceded"]))

    scores = []
    for i in range(8):
        for j in range(8):
            p = poisson(lh, i) * poisson(la, j)
            scores.append((p, i, j))

    scores.sort(reverse=True)

    # Les probabilités sont calculées sur la grille de scores 0 à 7.
    total = sum(item[0] for item in scores)
    home_win = sum(p for p, i, j in scores if i > j) / total
    draw = sum(p for p, i, j in scores if i == j) / total
    away_win = sum(p for p, i, j in scores if i < j) / total

    return {
        "home_lambda": lh,
        "away_lambda": la,
        "top_scores": scores[:2],
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
    }


with st.form("match_form"):
    c1, c2 = st.columns(2)
    with c1:
        home_name = st.text_input("Équipe à domicile", "Séville")
    with c2:
        away_name = st.text_input("Équipe à l'extérieur", "Osasuna")

    c3, c4 = st.columns(2)
    with c3:
        match_date = st.date_input("Date du match", date.today())
    with c4:
        match_time = st.time_input("Heure du match", time(20, 30))

    lookback = st.slider(
        "Historique à rechercher (jours)",
        min_value=30, max_value=730, value=180, step=30
    )

    submitted = st.form_submit_button("Analyser le match", type="primary")


if submitted:
    if not home_name.strip() or not away_name.strip():
        st.error("Entre les deux noms d'équipes.")
        st.stop()

    if canonical(home_name) == canonical(away_name):
        st.error("Choisis deux équipes différentes.")
        st.stop()

    start_day = match_date - timedelta(days=lookback)
    end_day = match_date - timedelta(days=1)

    with st.spinner("Recherche des résultats réels sur ESPN..."):
        espn_games, espn_errors = fetch_espn(
            start_day, end_day
        )

    home_espn = team_results(home_name, espn_games, match_date)
    away_espn = team_results(away_name, espn_games, match_date)

    # La source secondaire est appelée si ESPN ne fournit pas assez de matchs
    # pour l'une ou l'autre équipe.
    open_games, open_errors = [], []
    if len(home_espn) < 10 or len(away_espn) < 10:
        with st.spinner("Recherche complémentaire sur OpenFootball..."):
            open_games, open_errors = fetch_openfootball(
                start_day.isoformat(), end_day.isoformat()
            )

    all_games = deduplicate(espn_games + open_games)

    home_results = team_results(home_name, all_games, match_date)
    away_results = team_results(away_name, all_games, match_date)

    st.subheader(f"{home_name} — {away_name}")
    st.caption(
        f"Match cible : {match_date:%d/%m/%Y} à {match_time:%H:%M}. "
        f"Historique recherché jusqu'au {end_day:%d/%m/%Y} inclus."
    )

    st.write(
        f"Résultats récupérés : **{home_name} : {len(home_results)}** ; "
        f"**{away_name} : {len(away_results)}**."
    )

    for team_name, results in [
        (home_name, home_results),
        (away_name, away_results),
    ]:
        with st.expander(f"10 derniers matchs — {team_name}", expanded=True):
            if not results:
                st.warning("Aucun résultat vérifié trouvé pour cette équipe.")
            else:
                for r in results:
                    st.write(
                        f"{r['date']:%d/%m/%Y} — "
                        f"{r['gf']}–{r['ga']} contre {r['opponent']} "
                        f"({r['location']}, source : {r['source']})"
                    )

    # Statistiques séparées domicile/extérieur.
    st.subheader("Statistiques domicile / extérieur")
    stats_columns = st.columns(2)

    for col, team_name, venue in [
        (stats_columns[0], home_name, "home"),
        (stats_columns[1], away_name, "away"),
    ]:
        venue_results = team_results(
            team_name, all_games, match_date, venue=venue
        )
        stats = averages(venue_results)

        with col:
            st.markdown(f"**{team_name} — {venue}**")
            if not stats:
                st.write("Pas de données vérifiées pour ce lieu.")
            else:
                st.write(f"Matchs disponibles : {stats['played']}")
                st.write(f"Buts marqués/match : {stats['scored']:.2f}")
                st.write(f"Buts encaissés/match : {stats['conceded']:.2f}")
                st.write(
                    f"V/N/D : {stats['wins']} / "
                    f"{stats['draws']} / {stats['losses']}"
                )

    if len(home_results) < 3 or len(away_results) < 3:
        st.warning(
            "Pronostic non calculé : il faut au moins 3 résultats "
            "réels pour chacune des deux équipes. "
            "Aucun score fictif ne sera affiché."
        )
    else:
        prediction = predict(home_results, away_results)

        st.subheader("Probabilités estimées — 1 / N / 2")
        c1, c2, c3 = st.columns(3)
        c1.metric("1 · Victoire domicile",
                  f"{prediction['home_win'] * 100:.1f}%")
        c2.metric("N · Match nul",
                  f"{prediction['draw'] * 100:.1f}%")
        c3.metric("2 · Victoire extérieur",
                  f"{prediction['away_win'] * 100:.1f}%")

        st.subheader("Deux scores les plus probables")
        for probability, hg, ag in prediction["top_scores"]:
            st.write(
                f"⚽ **{hg}–{ag}** — "
                f"probabilité estimée : {probability * 100:.1f}%"
            )

        st.caption(
            "Modèle de Poisson simple fondé sur les résultats récupérés. "
            "Les probabilités ne tiennent pas compte des blessures, "
            "compositions, suspensions, cotes ou changements tactiques. "
            "Ce sont des estimations, pas des garanties."
        )

    with st.expander("Diagnostic des sources"):
        st.write(f"Matchs ESPN reçus : {len(espn_games)}")
        st.write(f"Matchs OpenFootball reçus : {len(open_games)}")
        st.write(f"Matchs uniques après fusion : {len(all_games)}")

        if espn_errors:
            st.markdown("**Erreurs ESPN**")
            for error in espn_errors:
                st.write("- " + error)

        if open_errors:
            st.markdown("**Fichiers OpenFootball indisponibles**")
            for error in open_errors[:15]:
                st.write("- " + error)

        st.write(
            "Un fichier absent ou une compétition non couverte ne signifie "
            "pas qu'une équipe n'a pas joué. Les sources gratuites peuvent "
            "être incomplètes ou retardées."
)
