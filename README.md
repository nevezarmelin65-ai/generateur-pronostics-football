# Générateur de pronostics football — V1

Prototype web Python/Streamlit qui récupère des résultats via l'API-Football et calcule des probabilités 1N2 et deux scores les plus probables avec un modèle de Poisson.

## Ce que fait la V1
- Recherche les équipes par nom via l'API.
- Permet de sélectionner l'identité exacte de chaque équipe.
- Récupère les dix derniers matchs renvoyés par l'API et garde uniquement les rencontres terminées avec des scores numériques.
- Affiche forme V/N/D, points, buts marqués/encaissés et domicile/extérieur.
- Estime les buts attendus et les probabilités 1N2 via une distribution de Poisson.
- Essaie de vérifier le match demandé pour afficher les compositions/blessures si l'API renvoie des informations.
- Ne fabrique pas de résultats ou de données de joueurs absents. Si les données ne sont pas disponibles, le site l'indique.

## Limites connues
- Une clé API-Football est nécessaire. La source annonce une offre gratuite limitée (consulte la page officielle, car les limites et couvertures peuvent changer).
- La V1 ne corrige pas encore la force des adversaires, les xG, les changements d'entraîneur ni l'impact individuel des absences.
- Le facteur domicile (1,06 pour l'équipe à domicile et 0,94 pour l'équipe extérieure) est une hypothèse de départ explicite, pas une mesure propre à la compétition.
- Les probabilités ne sont pas encore calibrées sur une base historique et ne garantissent aucun résultat.
- L'heure saisie sert d'information de repère; le fuseau horaire de l'utilisateur n'est pas encore converti pour comparer précisément l'heure du coup d'envoi.
- La V1 utilise le endpoint des 10 derniers matchs retournés par l'API; la disponibilité des données dépend de la compétition, de la saison et du plan.

## Installation
Python 3.10 ou plus récent recommandé.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux/Android avec environnement Python compatible:
source .venv/bin/activate

pip install -r requirements.txt
streamlit run app.py
```

## Clé API
1. Crée un compte sur https://www.api-football.com/
2. Copie la clé dans le tableau de bord.
3. Dans l'application, colle-la dans la barre latérale.
4. Pour un déploiement, configure `APISPORTS_KEY` comme variable d'environnement ou un secret côté serveur. Ne place jamais la clé dans le JavaScript public ni dans un dépôt public.

## Déploiement web
Déploiement possible sur Streamlit Community Cloud ou un autre hébergeur Python compatible. Ajoute `APISPORTS_KEY` dans les secrets du service de déploiement. Vérifie les conditions de l'offre gratuite et les limites du fournisseur avant un usage régulier.

## Source
- API-Football: https://www.api-football.com/documentation-v3
- Tarifs / limites: https://www.api-football.com/pricing
