import streamlit as st
import requests, pandas as pd, math, re
from datetime import date, time
from urllib.parse import quote

st.set_page_config(page_title="Générateur de pronostics football", page_icon="⚽", layout="wide")
st.title("⚽ Générateur de pronostics football")
st.write("Analyse les derniers résultats disponibles. Aucune statistique ni aucun score ne sont inventés.")
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Accept-Language":"fr-FR,fr;q=0.9,en;q=0.8"}
ESPN="https://site.api.espn.com/apis/site/v2/sports/soccer"
# ESPN public endpoints, no API key. Availability and coverage vary by competition.
LEAGUES={"Ligue 1 (France)":"fra.1","Premier League (Angleterre)":"eng.1","Liga (Espagne)":"esp.1","Serie A (Italie)":"ita.1","Bundesliga (Allemagne)":"ger.1","Ligue des champions":"uefa.champions","Ligue Europa":"uefa.europa","MLS (États-Unis)":"usa.1","Saudi Pro League":"ksa.1","World Cup":"fifa.world"}

def norm(s):
    s=str(s or "").lower().replace("saint-germain","saint germain")
    s=re.sub(r"\b(fc|cf|sc|afc|club|olympique)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)

def get_json(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=12); r.raise_for_status()
        d=r.json(); return d if isinstance(d,dict) else None
    except Exception:
        return None

@st.cache_data(ttl=1800, show_spinner=False)
def league_teams(slug):
    d=get_json(f"{ESPN}/{slug}/teams") or {}
    teams=d.get("sports",[])
    out=[]
    # ESPN response usually nests teams under sports[].leagues[].teams[].team
    for sport in teams:
        for league in sport.get("leagues",[]):
            for item in league.get("teams",[]):
                t=item.get("team",item)
                if t.get("id") and t.get("displayName"):
                    out.append({"id":str(t["id"]),"name":t["displayName"],"slug":slug})
    # Some endpoint variants return a direct teams list
    for item in d.get("teams",[]):
        t=item.get("team",item)
        if t.get("id") and t.get("displayName"):
            out.append({"id":str(t["id"]),"name":t["displayName"],"slug":slug})
    return out

@st.cache_data(ttl=1800, show_spinner=False)
def find_team(name):
    wanted=norm(name); candidates=[]
    for league_name,slug in LEAGUES.items():
        for t in league_teams(slug):
            n=norm(t["name"])
            if n==wanted or (wanted and (wanted in n or n in wanted)):
                candidates.append({**t,"league":league_name,"match": 3 if n==wanted else 1})
    if not candidates: return None
    candidates.sort(key=lambda x:x["match"],reverse=True)
    return candidates[0]

def parse_schedule(data, team_name):
    rows=[]
    if not isinstance(data,dict): return rows
    events=data.get("events",[])
    # Some ESPN schedule responses wrap events under a different key.
    if not events and isinstance(data.get("schedule"),list): events=data["schedule"]
    for event in events:
        comps=event.get("competitions",[])
        if not comps: continue
        competitors=comps[0].get("competitors",[])
        if len(competitors)<2: continue
        h=next((c for c in competitors if c.get("homeAway")=="home"),None)
        a=next((c for c in competitors if c.get("homeAway")=="away"),None)
        if not h or not a: h,a=competitors[0],competitors[1]
        ht=(h.get("team") or {}).get("displayName",""); at=(a.get("team") or {}).get("displayName","")
        def sc(c):
            try:return int(float(c.get("score")))
            except (TypeError,ValueError):return None
        hs,aws=sc(h),sc(a)
        if hs is None or aws is None: continue
        target=norm(team_name)
        is_home=norm(ht)==target or target in norm(ht) or norm(ht) in target
        is_away=norm(at)==target or target in norm(at) or norm(at) in target
        if not is_home and not is_away: continue
        opponent=at if is_home else ht; gf,ga=(hs,aws) if is_home else (aws,hs)
        raw=event.get("date") or event.get("dateUTC")
        dt=pd.to_datetime(raw,utc=True,errors="coerce") if raw else pd.NaT
        if pd.notna(dt) and dt>pd.Timestamp.now(tz="UTC"): continue
        rows.append({"Date UTC":dt,"Adversaire":opponent,"Lieu":"Domicile" if is_home else "Extérieur","Buts marqués":gf,"Buts encaissés":ga,"Résultat":"V" if gf>ga else "N" if gf==ga else "D"})
    rows.sort(key=lambda x:x["Date UTC"] if pd.notna(x["Date UTC"]) else pd.Timestamp("1900-01-01",tz="UTC"),reverse=True)
    return rows[:10]

@st.cache_data(ttl=1800, show_spinner=False)
def team_recent(team_id, slug, team_name):
    d=get_json(f"{ESPN}/{slug}/teams/{quote(str(team_id))}/schedule")
    return parse_schedule(d,team_name)

def stats(rows):
    if not rows:return None
    weights=list(range(len(rows),0,-1)); den=sum(weights)
    points=sum((3 if r["Résultat"]=="V" else 1 if r["Résultat"]=="N" else 0)*w for r,w in zip(rows,weights))
    return {"forme":100*points/(3*den),"pour":sum(r["Buts marqués"]*w for r,w in zip(rows,weights))/den,"contre":sum(r["Buts encaissés"]*w for r,w in zip(rows,weights))/den,"n":len(rows)}

def predict(home_rows, away_rows):
    h,a=stats(home_rows),stats(away_rows)
    if not h or not a:return None
    lh=max(.15,min(3.5,.65*h["pour"]+.35*a["contre"]+.12))
    la=max(.15,min(3.5,.65*a["pour"]+.35*h["contre"]))
    def pois(l,k):return math.exp(-l)*l**k/math.factorial(k)
    matrix=[(pois(lh,i)*pois(la,j),i,j) for i in range(8) for j in range(8)]
    matrix.sort(reverse=True)
    ph=sum(p for p,i,j in matrix if i>j); pn=sum(p for p,i,j in matrix if i==j); pa=sum(p for p,i,j in matrix if i<j); total=ph+pn+pa
    return h,a,matrix[:2],(ph/total*100,pn/total*100,pa/total*100)

with st.form("match"):
    c1,c2=st.columns(2)
    home=c1.text_input("Équipe à domicile",value="Paris Saint-Germain")
    away=c2.text_input("Équipe à l'extérieur",value="Olympique Lyonnais")
    c3,c4=st.columns(2); day=c3.date_input("Date du match",value=date.today()); tm=c4.time_input("Heure locale",value=time(20,30))
    go=st.form_submit_button("🔎 Rechercher et analyser",use_container_width=True)

if go:
    if not home.strip() or not away.strip(): st.error("Entre les deux équipes."); st.stop()
    if norm(home)==norm(away): st.error("Choisis deux équipes différentes."); st.stop()
    with st.spinner("Recherche des équipes et des derniers résultats sur ESPN…"):
        ht=find_team(home.strip()); at=find_team(away.strip())
        hr=team_recent(ht["id"],ht["slug"],ht["name"]) if ht else []
        ar=team_recent(at["id"],at["slug"],at["name"]) if at else []
    hn=ht["name"] if ht else home.strip(); an=at["name"] if at else away.strip()
    st.subheader(f"{hn} — {an}"); st.caption(f"Date saisie : {day:%d/%m/%Y} · Heure locale : {tm:%H:%M}")
    if ht: st.caption(f"Équipe domicile trouvée sur ESPN : {ht['league']}")
    else: st.warning(f"ESPN n'a pas reconnu « {home} » dans les compétitions prises en charge.")
    if at: st.caption(f"Équipe extérieure trouvée sur ESPN : {at['league']}")
    else: st.warning(f"ESPN n'a pas reconnu « {away} » dans les compétitions prises en charge.")
    result=predict(hr,ar)
    if result:
        h,a,scores,probs=result
        st.subheader("📊 Pronostic statistique estimé")
        st.caption("Calcul basé sur les derniers résultats récupérés, pondérés du plus récent au plus ancien. Ce n'est pas une garantie.")
        x,y,z=st.columns(3); x.metric("Victoire domicile",f"{probs[0]:.1f}%"); y.metric("Match nul",f"{probs[1]:.1f}%"); z.metric("Victoire extérieur",f"{probs[2]:.1f}%")
        x,y=st.columns(2); x.metric("Score probable 1",f"{scores[0][1]} - {scores[0][2]}"); y.metric("Score probable 2",f"{scores[1][1]} - {scores[1][2]}")
        x,y=st.columns(2)
        with x:
            st.markdown(f"**{hn}**"); st.write(f"Matchs analysés : {h['n']}/10 · Forme pondérée : {h['forme']:.1f}/100"); st.write(f"Buts marqués/match : {h['pour']:.2f} · encaissés/match : {h['contre']:.2f}")
            st.dataframe(pd.DataFrame(hr),use_container_width=True,hide_index=True)
        with y:
            st.markdown(f"**{an}**"); st.write(f"Matchs analysés : {a['n']}/10 · Forme pondérée : {a['forme']:.1f}/100"); st.write(f"Buts marqués/match : {a['pour']:.2f} · encaissés/match : {a['contre']:.2f}")
            st.dataframe(pd.DataFrame(ar),use_container_width=True,hide_index=True)
    else:
        st.error("Aucun score calculé : ESPN n'a pas fourni l'historique récent d'au moins une équipe. Aucun score fictif ne sera affiché.")
        st.write(f"Résultats récupérés : {hn} = {len(hr)} match(s), {an} = {len(ar)} match(s).")
    st.subheader("Sources et limites")
    st.markdown("- [ESPN Soccer](https://www.espn.com/soccer/) : source principale de résultats dans cette version.")
    st.markdown("- [FotMob](https://www.fotmob.com/) : source complémentaire à consulter manuellement.")
    st.markdown("- [Transfermarkt](https://www.transfermarkt.com/) : vérification manuelle des absences/blessures.")
    st.warning("La couverture d'ESPN varie selon les championnats. Les blessures, compositions et cotes ne sont pas automatiquement intégrées. Une variation de cote ne prouve pas qu'un match est truqué.")
with st.expander("Compétitions prises en charge"):
    st.write(", ".join(LEAGUES.keys()))
    st.write("Aucune clé API n'est nécessaire. Les interfaces publiques peuvent changer ou être temporairement indisponibles.")
