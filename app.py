import streamlit as st
import requests, pandas as pd, math, re
from datetime import date, time
from urllib.parse import quote
from bs4 import BeautifulSoup

st.set_page_config(page_title="Générateur de pronostics football", page_icon="⚽", layout="wide")
st.title("⚽ Générateur de pronostics football")
st.write("Entre les équipes, la date et l'heure. Les données publiques peuvent être indisponibles; aucune statistique n'est inventée.")
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Referer":"https://www.fotmob.com/","Accept-Language":"fr-FR,fr;q=0.9,en;q=0.8"}
API="https://www.fotmob.com/api"
def norm(x): return re.sub(r"[^a-z0-9]", "", str(x or "").lower())
def get_json(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=12); r.raise_for_status(); return r.json()
    except Exception: return None
@st.cache_data(ttl=1800,show_spinner=False)
def search_team(name):
    for url in [f"{API}/search/suggest?term={quote(name)}",f"https://www.fotmob.com/api/search/suggest?term={quote(name)}"]:
        d=get_json(url)
        if not isinstance(d,dict): continue
        for key in ("teams","suggestions","results","items"):
            for x in d.get(key,[]) if isinstance(d.get(key),list) else []:
                if isinstance(x,dict) and (x.get("id") or x.get("primaryId")):
                    x=dict(x); x["id"]=x.get("id") or x.get("primaryId"); x["name"]=x.get("name") or x.get("title") or name; return x
    return None
@st.cache_data(ttl=1800,show_spinner=False)
def team_details(tid):
    if not tid:return None
    return get_json(f"{API}/teams?id={quote(str(tid))}")
def fixtures(data):
    x=(data or {}).get("fixtures",{}) if isinstance(data,dict) else {}
    if isinstance(x,dict): return x.get("allFixtures") or x.get("fixtures") or []
    return x if isinstance(x,list) else []
def names(f):
    h=f.get("home") or {}; a=f.get("away") or {}
    if not isinstance(h,dict): h={"name":h}
    if not isinstance(a,dict): a={"name":a}
    return h.get("name") or f.get("homeTeamName") or "", a.get("name") or f.get("awayTeamName") or ""
def recent(data,team):
    out=[]
    for f in fixtures(data):
        h=f.get("home") or {}; a=f.get("away") or {}
        if not isinstance(h,dict): h={"name":h}
        if not isinstance(a,dict): a={"name":a}
        hn,an=names(f)
        def score(obj, fallback):
            v=obj.get("score",fallback) if isinstance(obj,dict) else fallback
            try: return int(v)
            except (TypeError,ValueError): return None
        hs=score(h,f.get("homeScore")); aws=score(a,f.get("awayScore"))
        if hs is None or aws is None: continue
        ih=norm(hn)==norm(team); ia=norm(an)==norm(team)
        if not ih and not ia: continue
        gf,ga=(hs,aws) if ih else (aws,hs)
        raw=f.get("utcTime") or f.get("time") or f.get("date")
        dt=pd.to_datetime(raw,utc=True,errors="coerce") if raw else pd.NaT
        if pd.notna(dt) and dt>pd.Timestamp.now(tz="UTC"): continue
        out.append({"Date UTC":dt,"Adversaire":an if ih else hn,"Lieu":"Domicile" if ih else "Extérieur","Buts marqués":gf,"Buts encaissés":ga,"Résultat":"V" if gf>ga else "N" if gf==ga else "D"})
    out.sort(key=lambda x:x["Date UTC"] if pd.notna(x["Date UTC"]) else pd.Timestamp("1900-01-01",tz="UTC"),reverse=True)
    return out[:10]
def stats(rows):
    if not rows:return None
    w=list(range(len(rows),0,-1)); den=sum(w)
    pts=sum((3 if r["Résultat"]=="V" else 1 if r["Résultat"]=="N" else 0)*z for r,z in zip(rows,w))
    return {"forme":100*pts/(3*den),"pour":sum(r["Buts marqués"]*z for r,z in zip(rows,w))/den,"contre":sum(r["Buts encaissés"]*z for r,z in zip(rows,w))/den,"n":len(rows)}
def predict(hr,ar):
    h,a=stats(hr),stats(ar)
    if not h or not a:return None
    lh=max(.2,min(3.2,.65*h["pour"]+.35*a["contre"]+.18)); la=max(.2,min(3.2,.65*a["pour"]+.35*h["contre"]))
    def pois(l,k):return math.exp(-l)*l**k/math.factorial(k)
    mat=sorted([(pois(lh,i)*pois(la,j),i,j) for i in range(7) for j in range(7)],reverse=True)
    ph=sum(p for p,i,j in mat if i>j); pn=sum(p for p,i,j in mat if i==j); pa=sum(p for p,i,j in mat if i<j); t=ph+pn+pa
    return h,a,mat[:2],(ph/t*100,pn/t*100,pa/t*100)
with st.form("match"):
    c1,c2=st.columns(2); home=c1.text_input("Équipe à domicile",placeholder="Paris Saint-Germain"); away=c2.text_input("Équipe à l'extérieur",placeholder="Olympique Lyonnais")
    c3,c4=st.columns(2); day=c3.date_input("Date du match",value=date.today()); tm=c4.time_input("Heure locale",value=time(18,0))
    go=st.form_submit_button("🔎 Rechercher et analyser",use_container_width=True)
if go:
    if not home.strip() or not away.strip(): st.error("Entre les deux équipes."); st.stop()
    if norm(home)==norm(away): st.error("Choisis deux équipes différentes."); st.stop()
    with st.spinner("Recherche des équipes et résultats sur FotMob…"):
        hi=search_team(home.strip()); ai=search_team(away.strip())
        hd=team_details((hi or {}).get("id")); ad=team_details((ai or {}).get("id"))
    hn=(hi or {}).get("name") or home.strip(); an=(ai or {}).get("name") or away.strip()
    st.subheader(f"{hn} — {an}"); st.caption(f"Date saisie : {day:%d/%m/%Y} · Heure locale : {tm:%H:%M}")
    if not hi or not ai: st.warning("FotMob n'a pas reconnu une équipe. Essaie son nom complet.")
    if not hd or not ad: st.warning("FotMob n'a pas fourni les détails d'une équipe. Le pronostic ne peut être calculé que si les deux historiques sont disponibles.")
    hr=recent(hd,hn); ar=recent(ad,an)
    result=predict(hr,ar)
    if result:
        h,a,scores,probs=result
        st.subheader("📊 Pronostic basé sur les résultats récupérés")
        st.caption("Pas de bonus de classement : la forme récente et les buts marqués/encaissés sont utilisés.")
        x,y,z=st.columns(3); x.metric("Victoire domicile",f"{probs[0]:.1f}%"); y.metric("Match nul",f"{probs[1]:.1f}%"); z.metric("Victoire extérieur",f"{probs[2]:.1f}%")
        x,y=st.columns(2); x.metric("Score probable 1",f"{scores[0][1]} - {scores[0][2]}"); y.metric("Score probable 2",f"{scores[1][1]} - {scores[1][2]}")
        x,y=st.columns(2)
        with x:
            st.markdown(f"**{hn}**"); st.write(f"Matchs analysés : {h['n']}/10 · Forme : {h['forme']:.1f}/100"); st.write(f"Buts marqués/match : {h['pour']:.2f} · encaissés/match : {h['contre']:.2f}")
            if hr: st.dataframe(pd.DataFrame(hr),use_container_width=True,hide_index=True)
        with y:
            st.markdown(f"**{an}**"); st.write(f"Matchs analysés : {a['n']}/10 · Forme : {a['forme']:.1f}/100"); st.write(f"Buts marqués/match : {a['pour']:.2f} · encaissés/match : {a['contre']:.2f}")
            if ar: st.dataframe(pd.DataFrame(ar),use_container_width=True,hide_index=True)
    else:
        st.error("Aucun score calculé : les résultats récents d'au moins une équipe sont indisponibles. FotMob peut limiter ses accès publics. Aucun score fictif ne sera affiché.")
    st.subheader("🔍 Sources complémentaires")
    st.markdown("- [FotMob](https://www.fotmob.com/) : résultats et fiches selon disponibilité.")
    st.markdown("- [Transfermarkt](https://www.transfermarkt.com/) : consulte les blessures/absences directement si elles ne sont pas récupérées ici.")
    st.markdown("- [Open-Matchday](https://github.com/open-matchday) : dépôt public; ses données ne sont pas intégrées automatiquement à ce calcul.")
    st.warning("Une variation de cote ou un signal statistique ne prouve pas qu'un match est truqué. Les scores restent des estimations, pas des garanties.")
with st.expander("Sources et limites"):
    st.write("Aucune clé API n'est nécessaire. FotMob peut modifier ou limiter ses interfaces publiques. Les compositions, absences et cotes ne sont pas garanties.")
    
