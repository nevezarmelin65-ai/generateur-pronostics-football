import streamlit as st
import requests, re, math, unicodedata
from datetime import date, time, datetime, timedelta

st.set_page_config(page_title='Générateur de pronostics football', page_icon='⚽', layout='wide')
st.title('⚽ Générateur de pronostics football')
st.write('Recherche les matchs terminés disponibles sur ESPN. Aucun résultat ni score n’est inventé.')

BASE='https://site.api.espn.com/apis/site/v2/sports/soccer'
HEADERS={'User-Agent':'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36','Accept':'application/json'}
LEAGUES={
 'Ligue 1 (France)':'fra.1', 'Premier League (Angleterre)':'eng.1', 'Liga (Espagne)':'esp.1',
 'Serie A (Italie)':'ita.1', 'Bundesliga (Allemagne)':'ger.1', 'Ligue des champions':'uefa.champions',
 'Ligue Europa':'uefa.europa', 'MLS (États-Unis)':'usa.1', 'Saudi Pro League':'ksa.1'
}
ALIASES={'psg':'parissaintgermain','parissaintgermain':'parissaintgermain','parissaintgermainfc':'parissaintgermain','ol':'olympiquelyonnais','lyon':'lyon','olympiquelyonnais':'lyon','sevilla':'sevilla','sevillafc':'sevilla','osasuna':'osasuna'}
def norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    s=re.sub(r'\b(fc|cf|afc|club|football|the)\b',' ',s)
    s=re.sub(r'[^a-z0-9]','',s)
    return ALIASES.get(s,s)
def team_names(comp):
    t=comp.get('team') or {}
    return [str(t.get(k,'')) for k in ('displayName','name','shortDisplayName','abbreviation') if t.get(k)]
def match_name(target, names):
    a=norm(target)
    for name in names:
        b=norm(name)
        if a and b and (a==b or (len(a)>3 and (a in b or b in a))): return True
    return False

@st.cache_data(ttl=900, show_spinner=False)
def fetch_league(code, start_str, end_str):
    url=f'{BASE}/{code}/scoreboard'
    r=requests.get(url,params={'dates':f'{start_str}-{end_str}','limit':500},headers=HEADERS,timeout=18)
    r.raise_for_status()
    data=r.json()
    found=[]
    for ev in data.get('events',[]):
        comps=(ev.get('competitions') or [])
        if not comps: continue
        competition=comps[0]
        competitors=competition.get('competitors') or []
        if len(competitors)<2: continue
        status=((competition.get('status') or {}).get('type') or {}).get('state','')
        if status!='post': continue
        parsed=[]
        for c in competitors:
            try: score=float(c.get('score'))
            except (TypeError,ValueError): score=None
            parsed.append({'names':team_names(c),'score':score,'home':c.get('homeAway')=='home'})
        if any(x['score'] is None for x in parsed): continue
        found.append({'date':ev.get('date',''),'teams':parsed,'league':code,'event':ev.get('name','')})
    return found

def find_results(target, all_games):
    out=[]
    for g in all_games:
        own=next((x for x in g['teams'] if match_name(target,x['names'])),None)
        if own:
            opp=next((x for x in g['teams'] if x is not own),None)
            if opp:
                out.append({'date':g['date'],'gf':own['score'],'ga':opp['score'],'home':own['home'],'opponent':', '.join(opp['names'][:2]),'league':g['league']})
    out.sort(key=lambda x:x['date'],reverse=True)
    return out[:10]

def poisson(lam,k):
    return math.exp(-lam)*lam**k/math.factorial(k)

def predict(home, away):
    # Recent matches are the only input; small home advantage is explicit.
    hgf=sum(x['gf'] for x in home)/len(home); hga=sum(x['ga'] for x in home)/len(home)
    agf=sum(x['gf'] for x in away)/len(away); aga=sum(x['ga'] for x in away)/len(away)
    lh=max(.15,min(4.0,0.55*hgf+0.45*aga+0.18))
    la=max(.15,min(4.0,0.55*agf+0.45*hga))
    scores=[]
    for i in range(6):
        for j in range(6): scores.append((poisson(lh,i)*poisson(la,j),i,j))
    scores.sort(reverse=True)
    ph=sum(p for p,i,j in scores if i>j); pd=sum(p for p,i,j in scores if i==j); pa=sum(p for p,i,j in scores if i<j)
    return (lh,la,scores[:2],ph,pd,pa,hgf,hga,agf,aga)

with st.form('match_form'):
    c1,c2=st.columns(2)
    with c1: home_name=st.text_input('Équipe à domicile',value='PSG')
    with c2: away_name=st.text_input('Équipe à l’extérieur',value='Lyon')
    c3,c4=st.columns(2)
    with c3: match_date=st.date_input('Date du match',value=date.today())
    with c4: match_time=st.time_input('Heure locale',value=time(20,30))
    lookback=st.slider('Période de recherche des résultats',14,90,45,step=7)
    submitted=st.form_submit_button('Analyser le match',type='primary')

if submitted:
    if not home_name.strip() or not away_name.strip():
        st.error('Entre les deux noms d’équipes.')
    else:
        end=date.today()
        start=end-timedelta(days=lookback)
        start_s=start.strftime('%Y%m%d'); end_s=end.strftime('%Y%m%d')
        all_games=[]; errors=[]; league_counts={}
        with st.spinner(f'Recherche des résultats du {start.strftime("%d/%m/%Y")} au {end.strftime("%d/%m/%Y")}…'):
            for league,label in [(v,k) for k,v in LEAGUES.items()]:
                try:
                    games=fetch_league(league,start_s,end_s)
                    all_games.extend(games); league_counts[label]=len(games)
                except Exception as e:
                    errors.append(f'{label}: {type(e).__name__} — {str(e)[:180]}')
        home_results=find_results(home_name,all_games)
        away_results=find_results(away_name,all_games)
        st.subheader(f'{home_name.strip()} — {away_name.strip()}')
        st.caption(f'Match demandé : {match_date.strftime("%d/%m/%Y")} à {match_time.strftime("%H:%M")} · Recherche des résultats terminés jusqu’au {end.strftime("%d/%m/%Y")}')
        st.write(f'**Résultats récupérés :** {home_name} = {len(home_results)} match(s) ; {away_name} = {len(away_results)} match(s).')
        if home_results:
            with st.expander(f'Derniers résultats de {home_name}',expanded=False):
                for x in home_results: st.write(f'{x["date"][:10]} · {x["gf"]:g}–{x["ga"]:g} contre {x["opponent"]}')
        if away_results:
            with st.expander(f'Derniers résultats de {away_name}',expanded=False):
                for x in away_results: st.write(f'{x["date"][:10]} · {x["gf"]:g}–{x["ga"]:g} contre {x["opponent"]}')
        if len(home_results)>=3 and len(away_results)>=3:
            lh,la,top,ph,pd,pa,hgf,hga,agf,aga=predict(home_results,away_results)
            st.subheader('Pronostic estimatif')
            cols=st.columns(3)
            cols[0].metric(f'Victoire {home_name}',f'{ph*100:.1f}%')
            cols[1].metric('Match nul',f'{pd*100:.1f}%')
            cols[2].metric(f'Victoire {away_name}',f'{pa*100:.1f}%')
            st.write('**Deux scores les plus probables selon le modèle :**')
            for p,i,j in top: st.write(f'⚽ **{i}–{j}** — probabilité estimée {p*100:.1f}%')
            st.caption(f'Moyennes observées : {home_name} {hgf:.2f} buts marqués / {hga:.2f} encaissés par match ; {away_name} {agf:.2f} marqués / {aga:.2f} encaissés. Modèle simple basé sur les derniers résultats récupérés, pas sur les blessures, les titulaires ou les cotes.')
        else:
            st.warning('Pronostic non calculé : il faut au moins 3 résultats terminés pour chacune des deux équipes. Aucun score fictif ne sera affiché.')
        with st.expander('Diagnostic de la source'):
            st.write('Matchs terminés récupérés par compétition :')
            st.json(league_counts)
            if errors:
                st.write('Erreurs de récupération :')
                for err in errors: st.write('• '+err)
            else: st.write('Les requêtes aux compétitions ont répondu sans erreur HTTP. Si une équipe reste à zéro, son nom peut différer du nom exposé par ESPN ou ses matchs peuvent être hors des compétitions couvertes.')
        st.caption('Les données publiques peuvent être limitées ou indisponibles. Les probabilités et scores sont des estimations, jamais des garanties. Une variation de cote ne prouve pas qu’un match est truqué.')
else:
    st.info('Entre les équipes et appuie sur « Analyser le match ». La recherche porte sur les derniers matchs terminés disponibles, pas sur le score du match futur.')
