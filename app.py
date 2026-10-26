import streamlit as st
import requests, pandas as pd, math, re
from datetime import date, time
from urllib.parse import quote

st.set_page_config(page_title="Générateur de pronostics football", page_icon="⚽", layout="wide")
st.title("⚽ Générateur de pronostics football")
st.write("Analyse les résultats récents, la forme et les absences publiques disponibles. Aucune donnée manquante n’est inventée.")
st.info("Les absences passent en priorité dans l’analyse lorsqu’une source publique les fournit. Les sources gratuites ne couvrent pas tous les clubs : une absence non trouvée ne signifie pas que l’équipe est au complet.")
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Accept-Language":"fr-FR,fr;q=0.9,en;q=0.8"}
ESPN="https://site.api.espn.com/apis/site/v2/sports/soccer"
# ESPN public endpoints, no API key. Availability and coverage vary by competition.
LEAGUES={"Ligue 1 (France)":"fra.1","Premier League (Angleterre)":"eng.1","Liga (Espagne)":"esp.1","Serie A (Italie)":"ita.1","Bundesliga (Allemagne)":"ger.1","Ligue des champions":"uefa.champions","Ligue Europa":"uefa.europa","MLS (États-Unis)":"usa.1","Saudi Pro League":"ksa.1","World Cup":"fifa.world"}

ALIASES = {
    "psg": "parissaintgermain",
    "parissaintgermain": "parissaintgermain",
    "parissaintgermainfc": "parissaintgermain",
    "parissaintgermainfootballclub": "parissaintgermain",
    "ol": "lyon",
    "olympiquelyonnais": "lyon",
    "lyonnais": "lyon",
    "lyon": "lyon",
}

def norm(s):
    s=str(s or "").lower().replace("saint-germain", "saint germain")
    cleaned=re.sub(r"[^a-z0-9]", "", s)
    # Apply aliases before stripping generic words so club names remain matchable.
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    cleaned=re.sub(r"(footballclub|fc|cf|sc|afc|club|olympique)$", "", cleaned)
    return ALIASES.get(cleaned, cleaned)

def get_json(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=12); r.raise_for_status()
        d=r.json(); return d if isinstance(d,dict) else None
    except Exception:
        return None

@st.cache_data(ttl=1800, show_spinner=False)
def league_teams(slug):
    d=get_json(f"{ESPN}/{slug}/teams?limit=1000") or {}
    out=[]; seen=set()
    def walk(obj):
        if isinstance(obj, dict):
            # Team objects can be nested at several levels in ESPN responses.
            if obj.get("id") and (obj.get("displayName") or obj.get("name")):
                name=obj.get("displayName") or obj.get("shortDisplayName") or obj.get("name")
                abbreviation=obj.get("abbreviation") or ""
                key=(str(obj["id"]), name)
                if name and key not in seen:
                    seen.add(key); out.append({"id":str(obj["id"]),"name":name,"abbreviation":abbreviation,"slug":slug})
            for value in obj.values(): walk(value)
        elif isinstance(obj, list):
            for value in obj: walk(value)
    walk(d)
    return out

@st.cache_data(ttl=1800, show_spinner=False)
def find_team(name):
    wanted=norm(name); candidates=[]
    for league_name,slug in LEAGUES.items():
        for t in league_teams(slug):
            n=norm(t["name"])
            ab=norm(t.get("abbreviation", ""))
            if n==wanted or ab==wanted or (wanted and (wanted in n or n in wanted)):
                candidates.append({**t,"league":league_name,"match": 4 if ab==wanted else 3 if n==wanted else 1})
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
    urls = [
        f"{ESPN}/{slug}/teams/{quote(str(team_id))}/schedule",
        f"{ESPN}/{slug}/teams/{quote(str(team_id))}/schedule?season={date.today().year}",
    ]
    for url in urls:
        d=get_json(url)
        rows=parse_schedule(d,team_name)
        if rows:
            return rows
    return []

@st.cache_data(ttl=1800, show_spinner=False)
def team_injuries(team_id, slug):
    """Récupère les signalements ESPN disponibles, sans transformer l'absence de données en zéro absence."""
    data = get_json(f"{ESPN}/{slug}/teams/{quote(str(team_id))}/injuries")
    if not data:
        return None
    found=[]
    def walk(obj):
        if isinstance(obj, dict):
            name = obj.get("displayName") or obj.get("fullName") or obj.get("athleteName") or obj.get("name")
            injury = obj.get("injury") or obj.get("type") or obj.get("details") or obj.get("status")
            if name and (injury or obj.get("status") or obj.get("date")):
                parts=[]
                for k in ("status", "type", "details", "description", "date"):
                    v=obj.get(k)
                    if isinstance(v, (str,int,float)) and str(v).strip(): parts.append(str(v).strip())
                label=" — ".join(dict.fromkeys(parts))
                if label and not any(x["Joueur"]==str(name) for x in found): found.append({"Joueur":str(name),"Information publiée":label})
            for v in obj.values(): walk(v)
        elif isinstance(obj, list):
            for v in obj: walk(v)
    walk(data)
    return found

def stats(rows):
    if not rows:return None
    weights=list(range(len(rows),0,-1)); den=sum(weights)
    points=sum((3 if r["Résultat"]=="V" else 1 if r["Résultat"]=="N" else 0)*w for r,w in zip(rows,weights))
    return {"forme":100*points/(3*den),"pour":sum(r["Buts marqués"]*w for r,w in zip(rows,weights))/den,"contre":sum(r["Buts encaissés"]*w for r,w in zip(rows,weights))/den,"n":len(rows)}

def confirmed_absences(items):
    """Ne compte comme absence confirmée que les statuts explicitement clairs."""
    if not items:
        return []
    confirmed=[]
    for item in items:
        label=(item.get("Information publiée", "") or "").lower()
        # Ne pas confondre un rapport de blessure, une incertitude ou une date avec une absence confirmée.
        if any(term in label for term in ("out", "suspended", "suspension", "ruled out", "indisponible", "absent confirmé")):
            if not any(term in label for term in ("doubtful", "questionable", "probable", "day-to-day", "incertain")):
                confirmed.append(item)
    return confirmed

def predict(home_rows, away_rows, home_absences=None, away_absences=None):
    h,a=stats(home_rows),stats(away_rows)
    if not h or not a:return None
    # Le modèle commence par les buts réellement observés dans les matchs récents.
    lh=max(.15,min(3.5,.65*h["pour"]+.35*a["contre"]+.12))
    la=max(.15,min(3.5,.65*a["pour"]+.35*h["contre"]))
    # Ajustement prudent seulement si la source indique clairement une indisponibilité.
    # Sans information fiable sur l'importance du joueur, aucun statut de titulaire n'est inventé.
    h_confirmed=confirmed_absences(home_absences)
    a_confirmed=confirmed_absences(away_absences)
    lh *= max(.70, 1 - .04*min(len(h_confirmed), 7))
    la *= max(.70, 1 - .04*min(len(a_confirmed), 7))
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
    st.markdown("**Cotes actuelles uniquement (facultatif)** — ordre 1-N-2")
    o1,oN,o2=st.columns(3)
    odd_home=o1.number_input("Cote actuelle 1",min_value=0.0,value=0.0,step=0.05,format="%.2f",help="Laisse 0 si tu ne l'as pas.")
    odd_draw=oN.number_input("Cote actuelle N",min_value=0.0,value=0.0,step=0.05,format="%.2f",help="Laisse 0 si tu ne l'as pas.")
    odd_away=o2.number_input("Cote actuelle 2",min_value=0.0,value=0.0,step=0.05,format="%.2f",help="Laisse 0 si tu ne l'as pas.")
    go=st.form_submit_button("🔎 Analyser le match",use_container_width=True)

if go:
    if not home.strip() or not away.strip(): st.error("Entre les deux équipes."); st.stop()
    if norm(home)==norm(away): st.error("Choisis deux équipes différentes."); st.stop()
    progress=st.progress(0, text="0 % — Préparation de l’analyse")
    status=st.empty()
    status.write("Étape 1/4 : recherche des équipes…")
    ht=find_team(home.strip()); at=find_team(away.strip())
    progress.progress(20, text="20 % — Équipes recherchées")
    status.write("Étape 2/4 : récupération des dix derniers résultats disponibles…")
    hr=team_recent(ht["id"],ht["slug"],ht["name"]) if ht else []
    ar=team_recent(at["id"],at["slug"],at["name"]) if at else []
    progress.progress(50, text="50 % — Forme et buts analysés")
    status.write("Étape 3/4 : vérification des blessures/absences publiées par ESPN…")
    hi=team_injuries(ht["id"],ht["slug"]) if ht else None
    ai=team_injuries(at["id"],at["slug"]) if at else None
    progress.progress(75, text="75 % — Données d’absences vérifiées")
    status.write("Étape 4/4 : calcul des probabilités et scores possibles…")
    hn=ht["name"] if ht else home.strip(); an=at["name"] if at else away.strip()
    st.subheader(f"{hn} — {an}"); st.caption(f"Date saisie : {day:%d/%m/%Y} · Heure locale : {tm:%H:%M}")
    if ht: st.caption(f"Équipe domicile trouvée sur ESPN : {ht['league']}")
    else: st.warning(f"ESPN n'a pas reconnu « {home} » dans les compétitions prises en charge.")
    if at: st.caption(f"Équipe extérieure trouvée sur ESPN : {at['league']}")
    else: st.warning(f"ESPN n'a pas reconnu « {away} » dans les compétitions prises en charge.")
    result=predict(hr,ar,hi,ai)
    if result:
        h,a,scores,probs=result
        progress.progress(100, text="100 % — Analyse terminée")
        status.success("Analyse terminée. Les résultats dépendent des données réellement récupérées.")
        st.subheader("📊 Pronostic statistique estimé")
        st.caption("Calcul basé sur les derniers résultats récupérés, pondérés du plus récent au plus ancien. Ce n'est pas une garantie.")
        x,y,z=st.columns(3); x.metric("Victoire domicile",f"{probs[0]:.1f}%"); y.metric("Match nul",f"{probs[1]:.1f}%"); z.metric("Victoire extérieur",f"{probs[2]:.1f}%")
        x,y=st.columns(2); x.metric("Score probable 1",f"{scores[0][1]} - {scores[0][2]}"); y.metric("Score probable 2",f"{scores[1][1]} - {scores[1][2]}")
        st.subheader("🚑 Absences et blessures — priorité de vérification")
        for team_label, injury_data in ((hn,hi),(an,ai)):
            st.markdown(f"**{team_label}**")
            if injury_data is None:
                st.warning("Aucun rapport d’absences exploitable reçu. Cela ne signifie pas qu’il n’y a aucun absent.")
            elif injury_data:
                confirmed=confirmed_absences(injury_data)
                st.write(f"Entrées de blessure trouvées : {len(injury_data)} · indisponibilités explicitement confirmées par le statut : {len(confirmed)}")
                st.dataframe(pd.DataFrame(injury_data),use_container_width=True,hide_index=True)
                st.caption("Le modèle n'ajuste les buts attendus que pour les statuts clairement indisponibles. Il ne devine pas si un joueur est titulaire ou important.")
            else:
                st.info("Aucune entrée d’absence retournée par ESPN. Ce n’est pas une preuve que l’équipe est au complet.")
        if all(v>1 for v in (odd_home,odd_draw,odd_away)):
            raw=[1/odd_home,1/odd_draw,1/odd_away]; margin=sum(raw)
            market=[v/margin*100 for v in raw]
            st.subheader("📈 Comparaison avec les cotes actuelles")
            c1,c2,c3=st.columns(3)
            for col,label,mp,pp in zip((c1,c2,c3),("1 domicile","N nul","2 extérieur"),market,probs):
                col.metric(label,f"Modèle {pp:.1f} %",f"Cotes ajustées {mp:.1f} %",delta_color="off")
            st.caption("Les probabilités des cotes sont normalisées pour retirer approximativement la marge du bookmaker. Les cotes d’ouverture ne sont pas utilisées.")
        else:
            st.caption("Cotes actuelles non renseignées ou incomplètes : la comparaison au marché est ignorée.")
        x,y=st.columns(2)
        with x:
            h_home=[r for r in hr if r["Lieu"]=="Domicile"]
            h_away=[r for r in hr if r["Lieu"]=="Extérieur"]
            st.markdown(f"**{hn}**"); st.write(f"Matchs analysés : {h['n']}/10 · Forme pondérée : {h['forme']:.1f}/100"); st.write(f"Buts marqués/match : {h['pour']:.2f} · encaissés/match : {h['contre']:.2f}")
            st.write(f"Dans ces 10 matchs : {len(h_home)} à domicile · {len(h_away)} à l’extérieur")
            st.dataframe(pd.DataFrame(hr),use_container_width=True,hide_index=True)
        with y:
            a_home=[r for r in ar if r["Lieu"]=="Domicile"]
            a_away=[r for r in ar if r["Lieu"]=="Extérieur"]
            st.markdown(f"**{an}**"); st.write(f"Matchs analysés : {a['n']}/10 · Forme pondérée : {a['forme']:.1f}/100"); st.write(f"Buts marqués/match : {a['pour']:.2f} · encaissés/match : {a['contre']:.2f}")
            st.write(f"Dans ces 10 matchs : {len(a_home)} à domicile · {len(a_away)} à l’extérieur")
            st.dataframe(pd.DataFrame(ar),use_container_width=True,hide_index=True)
    else:
        progress.progress(100, text="100 % — Analyse terminée avec données insuffisantes")
        status.warning("Analyse terminée, mais les données sont insuffisantes pour calculer un score fiable.")
        st.error("Aucun score calculé : ESPN n'a pas fourni l'historique récent d'au moins une équipe. Aucun score fictif ne sera affiché.")
        st.write(f"Résultats récupérés : {hn} = {len(hr)} match(s), {an} = {len(ar)} match(s).")
    st.subheader("Sources et limites")
    st.markdown("- [ESPN Soccer](https://www.espn.com/soccer/) : source principale de résultats dans cette version.")
    st.markdown("- [FotMob](https://www.fotmob.com/) : source complémentaire à consulter manuellement.")
    st.markdown("- [Transfermarkt](https://www.transfermarkt.com/) : vérification manuelle des absences/blessures.")
    st.warning("La couverture d’ESPN varie selon les championnats. Les compositions officielles et les titulaires importants absents ne sont pas toujours disponibles. Les probabilités et scores restent des estimations, jamais des certitudes.")
with st.expander("Compétitions prises en charge"):
    st.write(", ".join(LEAGUES.keys()))
    st.write("Aucune clé API n'est nécessaire. Les interfaces publiques peuvent changer ou être temporairement indisponibles.")
