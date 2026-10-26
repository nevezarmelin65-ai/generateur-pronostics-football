import math
from datetime import date, time
from io import StringIO

import pandas as pd
import requests
import streamlit as st

BASE = "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data"
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
st.title("⚽ Générateur de pronostics football — version améliorée")
st.info("Résultats actualisés quotidiennement, pas en direct. Les alertes de risque ne prouvent jamais qu'un match est truqué.")

@st.cache_data(ttl=3600, show_spinner=False)
def csv(url):
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return pd.read_csv(StringIO(r.text))

@st.cache_data(ttl=3600, show_spinner=False)
def load_league(slug):
    frames = []
    for season in ("2627", "2526"):
        try:
            d = csv(f"{BASE}/{slug}/results_{season}.csv")
            req = {"date", "home", "away", "ft_home", "ft_away"}
            if d.empty or not req.issubset(d.columns):
                continue
            d["date"] = pd.to_datetime(d["date"], errors="coerce")
            d["ft_home"] = pd.to_numeric(d["ft_home"], errors="coerce")
            d["ft_away"] = pd.to_numeric(d["ft_away"], errors="coerce")
            d = d.dropna(subset=["date", "home", "away", "ft_home", "ft_away"])
            frames.append(d)
        except Exception:
            pass
    if not frames:
        raise RuntimeError("Pas de données disponibles pour ce championnat. Essaie-en un autre.")
    d = pd.concat(frames, ignore_index=True).drop_duplicates(["date", "home", "away"])
    return d.sort_values("date", ascending=False)

def matches(df, team):
    g = df[(df.home == team) | (df.away == team)].sort_values("date", ascending=False).head(10)
    out = []
    for _, m in g.iterrows():
        home = m.home == team
        gf, ga = (int(m.ft_home), int(m.ft_away)) if home else (int(m.ft_away), int(m.ft_home))
        out.append({"Date": m.date.strftime("%Y-%m-%d"), "Adversaire": m.away if home else m.home,
                    "Terrain": "Domicile" if home else "Extérieur", "Score": f"{int(m.ft_home)}-{int(m.ft_away)}",
                    "BP": gf, "BC": ga, "Résultat": "V" if gf > ga else ("N" if gf == ga else "D")})
    return out

def pois(lam, n=8):
    return [math.exp(-lam) * lam**k / math.factorial(k) for k in range(n+1)]

def predict(hr, ar):
    if not hr or not ar:
        raise ValueError("Pas assez de matchs récents pour cette équipe.")
    hh = [r for r in hr if r["Terrain"] == "Domicile"]
    aa = [r for r in ar if r["Terrain"] == "Extérieur"]
    avg = lambda rows, key: sum(r[key] for r in rows)/len(rows) if rows else 0
    lh = max(.05, 1.06*(.6*avg(hh, "BP") + .4*avg(aa, "BC"))) if len(hh)>=3 and len(aa)>=3 else max(.05, (avg(hr,"BP")+avg(ar,"BC"))/2)
    la = max(.05, .94*(.6*avg(aa, "BP") + .4*avg(hh, "BC"))) if len(hh)>=3 and len(aa)>=3 else max(.05, (avg(ar,"BP")+avg(hr,"BC"))/2)
    scores=[]; p1=px=p2=0
    for i, pi in enumerate(pois(lh)):
        for j, pj in enumerate(pois(la)):
            p=pi*pj; scores.append((i,j,p))
            if i>j: p1+=p
            elif i==j: px+=p
            else: p2+=p
    total=p1+px+p2
    return [p1/total, px/total, p2/total], sorted(scores,key=lambda x:x[2],reverse=True)[:2], lh, la

def market_probs(odds):
    inv=[1/x for x in odds]
    return [x/sum(inv) for x in inv]

with st.sidebar:
    st.subheader("Sources à consulter")
    st.markdown("- [Résultats et statistiques — Open-Matchday](https://github.com/oladeeayo/open-matchday)")
    st.markdown("- [Blessures/suspensions — Transfermarkt](https://www.transfermarkt.com/)")
    st.markdown("- [Compositions/statistiques — FotMob](https://www.fotmob.com/)")
    st.caption("Les absences ne sont pas récupérées automatiquement : vérifie-les et saisis-les ci-dessous.")
    league_name=st.selectbox("Championnat", list(LEAGUES))
try:
    with st.spinner("Chargement des données…"):
        df=load_league(LEAGUES[league_name])
except Exception as e:
    st.error(str(e)); st.stop()

teams=sorted(set(df.home.dropna().astype(str)) | set(df.away.dropna().astype(str)))
st.subheader("1. Choisir le match")
c1,c2=st.columns(2)
home=c1.selectbox("Équipe à domicile", teams, index=0)
away=c2.selectbox("Équipe à l'extérieur", teams, index=1 if len(teams)>1 else 0)
c3,c4=st.columns(2)
c3.date_input("Date du match", value=date.today())
c4.time_input("Heure du match", value=time(17,0))

st.subheader("2. Absences (à vérifier)")
a1,a2=st.columns(2)
with a1:
    h_abs=st.number_input(f"Absents confirmés — {home}",0,25,0)
    h_key=st.number_input(f"Titulaires importants absents — {home}",0,11,0)
with a2:
    a_abs=st.number_input(f"Absents confirmés — {away}",0,25,0)
    a_key=st.number_input(f"Titulaires importants absents — {away}",0,11,0)
checked=st.checkbox("J'ai vérifié les absences sur une source récente")

st.subheader("3. Cotes (facultatif, mais utiles pour examiner les anomalies)")
st.caption("Saisis les cotes décimales d'ouverture puis les cotes actuelles dans l'ordre 1-N-2. Exemple : 2.10, 3.20, 3.50.")
with st.expander("Saisir les cotes"):
    x1,x2,x3=st.columns(3)
    oh=x1.number_input("Ouverture 1",0.0,1000.0,0.0,.05)
    ox=x2.number_input("Ouverture N",0.0,1000.0,0.0,.05)
    oa=x3.number_input("Ouverture 2",0.0,1000.0,0.0,.05)
    y1,y2,y3=st.columns(3)
    ch=y1.number_input("Actuelle 1",0.0,1000.0,0.0,.05)
    cx=y2.number_input("Actuelle N",0.0,1000.0,0.0,.05)
    ca=y3.number_input("Actuelle 2",0.0,1000.0,0.0,.05)

if home == away:
    st.warning("Choisis deux équipes différentes.")
elif st.button("Analyser le match", type="primary", use_container_width=True):
    hr, ar=matches(df,home), matches(df,away)
    if not hr or not ar:
        st.error("Pas assez de résultats récents pour une des équipes.")
        st.stop()
    probs, scores, lh, la=predict(hr,ar)
    st.subheader("4. Forme récente")
    c1,c2=st.columns(2)
    for col,name,rows in [(c1,home,hr),(c2,away,ar)]:
        with col:
            col.markdown(f"**{name}** — forme récent → ancien : {''.join(r['Résultat'] for r in rows)}")
            col.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            col.write(f"Buts marqués : {sum(r['BP'] for r in rows)} | encaissés : {sum(r['BC'] for r in rows)}")
    st.subheader("5. Pronostic statistique")
    c1,c2,c3=st.columns(3)
    c1.metric(f"Victoire {home}",f"{probs[0]*100:.1f}%")
    c2.metric("Match nul",f"{probs[1]*100:.1f}%")
    c3.metric(f"Victoire {away}",f"{probs[2]*100:.1f}%")
    c1,c2=st.columns(2)
    c1.metric("Score probable 1",f"{scores[0][0]} – {scores[0][1]}",f"{scores[0][2]*100:.1f}%")
    c2.metric("Score probable 2",f"{scores[1][0]} – {scores[1][1]}",f"{scores[1][2]*100:.1f}%")
    st.write(f"Buts attendus estimés : {home} {lh:.2f} — {la:.2f} {away}")

    st.subheader("6. Contrôle des absences")
    if checked:
        st.write(f"{home} : {h_abs} absent(s), dont {h_key} titulaire(s) important(s).")
        st.write(f"{away} : {a_abs} absent(s), dont {a_key} titulaire(s) important(s).")
        if h_key>=2 or a_key>=2:
            st.warning("Plusieurs titulaires importants pourraient manquer : réévalue le pronostic après les compositions officielles.")
        st.caption("Les absences sont affichées mais ne changent pas automatiquement les probabilités.")
    else:
        st.warning("Absences non vérifiées : le pronostic est incomplet.")

    st.subheader("7. Indicateur de signaux inhabituels")
    odds=[oh,ox,oa,ch,cx,ca]
    if all(v>1 for v in odds):
        op,cp=market_probs([oh,ox,oa]),market_probs([ch,cx,ca])
        movement=max(abs(cp[i]-op[i]) for i in range(3))*100
        gap=max(abs(cp[i]-probs[i]) for i in range(3))*100
        risk=(2 if movement>=12 else 1 if movement>=7 else 0)+(1 if gap>=20 else 0)
        label="Élevé — vérifier" if risk>=3 else "Moyen — vérifier" if risk==2 else "Faible" if risk==1 else "Aucun signal détecté avec les données saisies"
        st.metric("Niveau de signal",label,f"Indice heuristique {risk}/3")
        st.write(f"Variation maximale des probabilités de marché : {movement:.1f} points.")
        st.write(f"Écart maximal entre modèle et marché : {gap:.1f} points.")
        if movement>=7: st.warning("Variation notable des cotes : vérifie les compositions, blessures et actualités.")
        if gap>=20: st.warning("Le modèle et les cotes divergent fortement : cela peut venir d'informations manquantes ou d'un modèle imparfait.")
    else:
        st.info("Pour examiner les mouvements du marché, renseigne les six cotes. Sans historique des cotes et volume des mises, la détection reste limitée.")
    st.error("Important : cette analyse ne peut pas confirmer qu'un match est truqué. Une variation de cotes peut avoir des causes normales. Une enquête professionnelle nécessite des données de nombreux bookmakers et une vérification humaine.")

st.divider()
st.caption("Données de résultats : Open-Matchday (actualisation quotidienne, usage informatif non commercial). Les absences doivent être vérifiées manuellement. Aucun pronostic ne garantit un gain.")
