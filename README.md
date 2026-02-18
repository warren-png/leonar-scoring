# 🎯 Leonar Scoring Tool

Outil de recherche automatisée + scoring intelligent des profils candidats via Leonar et Claude API.

## Installation

```bash
pip install -r requirements.txt
```

## Lancement

```bash
streamlit run app.py
```

## Prérequis

- **Clé API Leonar** avec les scopes : `sourcing:read`, `sourcing:write`, `projects:read`, `projects:write`, `contacts:read`, `contacts:write`, `notes:write`, `tags:read`, `tags:write`
- **Clé API Claude** (Anthropic)

## Workflow

1. Configure tes clés API dans la sidebar
2. Saisis le descriptif de poste + retranscription manager
3. Claude extrait les critères de recherche (modifiables)
4. Sélectionne ou crée un projet Leonar
5. Lance la recherche + scoring
6. Choisis le score minimum et pousse dans Leonar
7. Retrouve tout dans ton CRM : profils + notes de score
