# Générateur de pronostics football — Ney-Nevez

Application de pronostics football utilisant des sources publiques, sans clé API.

## Fonctionnalités
- Recherche des résultats disponibles.
- Analyse des derniers matchs des deux équipes.
- Comparaison des performances à domicile et à l'extérieur.
- Estimation des probabilités : victoire à domicile, match nul et victoire à l'extérieur.
- Proposition de deux scores probables.
- Comparaison facultative avec les cotes du bookmaker.
- Avertissement lorsque les données sont insuffisantes.

## Installation
Installer les dépendances :

```bash
pip install -r requirements.txt
```

Lancer l'application :

```bash
streamlit run app.py
```

## Sources et limites
Les données proviennent de sources publiques telles qu'ESPN et OpenFootball, selon leur disponibilité.

Les résultats peuvent être incomplets. Les blessures et compositions officielles ne sont pas toujours disponibles. Les probabilités sont des estimations et ne garantissent aucun résultat.
