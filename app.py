import streamlit as st
import requests
import json
import re
import time
import os
import random
import base64
from datetime import date
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv
import fitz  # pymupdf — rendu des pages CV en images PNG

# ============================================================
# DOSSIER DE CANDIDATURE — CONSTANTES
# ============================================================
DOSSIER_SYSTEM_PROMPT = """Tu rédiges les dossiers de présentation candidats d'Entourage Recrutement, cabinet de chasse spécialisé en finance et technologie. Tu remplis un template HTML strict sans toucher au design.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
I. RÈGLES HTML — NON NÉGOCIABLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. DESIGN INTOUCHABLE
   Ne modifie jamais le CSS, les couleurs, les polices, la structure des divs ni les dimensions de page.

2. PLACEHOLDERS OPAQUES
   - src="LOGO_PLACEHOLDER" : conserver tel quel dans toutes les balises <img>.
   - LINKEDIN_CONTACT_ITEM_PLACEHOLDER : conserver tel quel dans la .contact-bar.
   - La .contact-bar contient UNIQUEMENT email, téléphone et ce placeholder. Aucun autre champ.

3. NETTOYAGE
   Supprimer tout crochet [cite], balise de source ou mention "Source" dans le texte généré.

4. PIED DE PAGE
   Remplacer {{PIED_DE_PAGE_COMMERCIAL}} dans les deux pages par :
   - "Commercial : Warren" → Responsable de chasse : <a href="https://www.linkedin.com/in/warren-elbaz/">Warren</a> - 06 50 60 22 61
   - "Commercial : Helder" → Responsable de chasse : <a href="https://www.linkedin.com/in/helder-alturas-48010463/">Helder</a> - 06 22 30 96 11

5. OUTPUT
   Générer UNIQUEMENT les pages 1 et 2. Le CV est ajouté automatiquement après.
   Retourner UNIQUEMENT le HTML complet, sans markdown (pas de ```html), sans commentaire.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
II. CONTENU — REGISTRE ET STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REGISTRE ATTENDU
- Ton : analytique, factuel, direct. Registre conseil haut de gamme.
- Interdit : superlatifs ("excellent", "remarquable", "impressionnant", "solide", "fort profil"), formules de politesse ("nous sommes ravis"), adjectifs vagues ("bonne expérience", "profil intéressant").
- Vocabulaire : termes métier exacts issus du brief et du CV (noms de produits, marchés, réglementations, stacks techniques).
- Style : phrases courtes, présent de l'indicatif, voix active.

RÈGLE ANTI-RÉPÉTITION — ABSOLUE
Chaque section éclaire un angle distinct. Un fait mentionné dans une section ne peut pas être reformulé dans une autre.
- Notre Analyse → positionnement et trajectoire
- Points Clés → faits bruts et chiffres
- Score Card → évaluation critère par critère
- Projets Phares → réalisations concrètes avec contexte et résultat

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
III. CONTENU PAR SECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PAGE 1 — PRÉSENTATION

[A] NOTRE ANALYSE — {{ANALYSE_TEXTE_ISSUE_DU_BRIEF}}
    Objectif : justifier le choix de ce candidat pour ce poste précis.
    Contenu :
    → Cohérence du parcours avec les enjeux du poste (secteur, périmètre, niveau de responsabilité).
    → Un ou deux éléments de différenciation factuelle : type d'environnement (ETI, grand groupe, scale-up), marché couvert, compétence rare ou contexte particulier.
    → Adéquation globale avec le brief managérial si des éléments pertinents y figurent.
    Interdit : salaires, notes, reformulation des points clés, projets déjà évoqués en section D.
    Format : 5 à 7 phrases, 90 à 130 mots.

[B] POINTS CLÉS & VIGILANCE — 4 à 5 .point-card
    Objectif : informations opérationnelles à transmettre au client, non développées en [A].
    Structure imposée :
    → 1 card "Prétentions salariales" (obligatoire) : chiffre précis du brief/CV, ou "Non communiquées — à préciser."
    → 1 à 2 cards "Atout" : fait mesurable ou labelisé (ex. : certification CFA, gestion d'une équipe de X personnes, maîtrise d'un outil spécifique, scope géographique).
    → 1 à 2 cards "Point de vigilance" : élément à valider en entretien (ex. : expérience managériale limitée, secteur partiel, disponibilité, mobilité).
    Interdit : reformuler [A], anticiper le contenu du tableau [C].
    Format par card : titre court (2-4 mots) + une phrase factuelle.
    HTML : <div class="point-card"><div class="point-icon"><i class="fa-solid fa-check"></i></div><div class="point-content"><h4>Titre</h4><p>Description</p></div></div>

PAGE 2 — SCORE CARD

[C] ÉVALUATION — {{NOTE_GLOBALE}} et tableau 4 critères
    → Note globale : moyenne arithmétique des 4 notes, sur 5 (ex. : 3.8 / 5). Jamais sur 10.
    → Critères : extraire exactement les 4 critères définis dans la Score Card du poste.
    → Analyse par critère : 1 à 2 phrases factuelles, distinctes des sections [A], [B] et [D].
       Citer un élément précis du CV ou du brief pour étayer chaque note.
    → Format : <tr><td class="score-cat">Critère</td><td class="score-val">X.X / 5</td><td class="score-txt">Analyse.</td></tr>

[D] PROJETS PHARES & ADÉQUATION — {{TEXTE_PROJETS_PHARES}}
    Objectif : illustrer l'adéquation par des réalisations concrètes non mentionnées en [A] ou [B].
    Contenu : 2 à 3 missions ou projets significatifs, choisis pour leur lien direct avec les enjeux du poste.
    Structure par projet : contexte (1 proposition) → action menée → résultat chiffré si disponible.
    Interdit : répéter la trajectoire globale déjà posée en [A] ou des faits déjà cités en [B].
    Format : 4 à 6 phrases, 80 à 110 mots.
"""

_template_path = Path(__file__).parent / "dossier_template.html"
HTML_MASTER_TEMPLATE = _template_path.read_text(encoding="utf-8") if _template_path.exists() else ""

REVISION_SYSTEM_PROMPT = """Tu corriges les dossiers de présentation candidats d'Entourage Recrutement, cabinet de chasse spécialisé en finance et technologie.
Tu reçois les pages 1 et 2 d'un dossier HTML existant et des instructions de correction du chasseur.

RÈGLES HTML — NON NÉGOCIABLES
1. Ne modifie jamais le CSS, les couleurs, les polices, la structure des divs.
2. Conserver EXACTEMENT : src="LOGO_PLACEHOLDER" et LINKEDIN_CONTACT_ITEM_PLACEHOLDER.
3. Notes du tableau toujours /5 (jamais /10). Note globale = moyenne des 4 critères.
4. Retourner UNIQUEMENT le HTML complet des pages 1 et 2, sans markdown, sans explication.
5. Ne pas ajouter de page 3 ou suivante — le CV est géré séparément.

REGISTRE À MAINTENIR
- Ton factuel, analytique, direct. Registre conseil haut de gamme.
- Pas de superlatifs ni d'adjectifs vagues. Vocabulaire métier précis (finance/tech).
- Chaque section garde son rôle distinct : pas de répétition d'une rubrique à l'autre.
- Appliquer uniquement les corrections demandées. Ne pas réécrire ce qui n'est pas visé.
"""

# ============================================================
# CONFIG
# ============================================================
load_dotenv()
st.set_page_config(page_title="Leonar Scoring Tool", layout="wide")

LINKEDIN_DAILY_LIMIT = 1000  # limite LinkedIn Recruiter : ~1000 profils/jour par siège

# Compteur quotidien LinkedIn — persistant dans un fichier (survit aux rechargements et multi-onglets)
_USAGE_FILE = Path.home() / ".leonar_tool" / "linkedin_usage.json"

def _load_usage() -> dict:
    today = date.today().isoformat()
    if _USAGE_FILE.exists():
        try:
            data = json.loads(_USAGE_FILE.read_text())
            if data.get("date") == today:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
    return {"date": today, "count": 0}

def _save_usage(data: dict) -> None:
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USAGE_FILE.write_text(json.dumps(data))
    except (OSError, PermissionError):
        pass  # Streamlit Cloud : filesystem read-only, dégradation silencieuse

def get_linkedin_count() -> int:
    return _load_usage()["count"]

def add_linkedin_count(n: int) -> None:
    data = _load_usage()
    data["count"] += n
    _save_usage(data)

def get_secret(key):
    val = os.getenv(key)
    if val:
        return val
    try:
        return st.secrets[key]
    except Exception:
        return None

leonar_api_key = get_secret("LEONAR_API_KEY")
claude_api_key = get_secret("CLAUDE_API_KEY")

LEONAR_BASE = "https://app.leonar.app/api/v1"

def leonar_headers():
    return {"Authorization": f"Bearer {leonar_api_key}", "Content-Type": "application/json"}

# Scopes minimum requis pour cet outil
REQUIRED_SCOPES = "sourcing:read, sourcing:write, contacts:read, projects:read"

def leonar_request(method, url, **kwargs):
    """Exécute une requête Leonar avec retry exponentiel sur 429 et gestion d'erreurs par code."""
    for attempt in range(5):
        resp = requests.request(method, url, headers=leonar_headers(), **kwargs)

        # Pause si on approche la limite API (1000 req/h)
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) < 10:
            time.sleep(2)

        if resp.status_code == 429:
            wait = 2 ** (attempt + 1)  # séquence : 2s, 4s, 8s, 16s, 32s
            time.sleep(wait)
            continue

        if not resp.ok:
            try:
                error = resp.json().get("error", {})
                code = error.get("code", "unknown")
                message = error.get("message", resp.text)
            except Exception:
                code, message = "unknown", resp.text

            if code == "insufficient_scope":
                raise Exception(f"🔑 Permissions insuffisantes. Scopes requis pour cet outil : {REQUIRED_SCOPES}\n{message}")
            elif code == "invalid_api_key":
                raise Exception("🔑 Clé API invalide ou révoquée. Vérifie ta LEONAR_API_KEY.")
            elif code == "billing_required":
                raise Exception("💳 Abonnement Leonar requis pour cette fonctionnalité.")
            elif code == "plan_upgrade_required":
                raise Exception("📦 Fonctionnalité non disponible sur le plan actuel.")
            elif code == "validation_error":
                raise Exception(f"⚠️ Paramètres invalides : {message}")
            elif code == "not_found":
                raise Exception(f"❌ Ressource introuvable : {message}")
            else:
                raise Exception(f"{resp.status_code} [{code}] : {message}")

        return resp

    raise Exception("🚫 Rate limit API dépassé après 5 tentatives. Réessaie dans quelques minutes.")

def sanitize_boolean_query(q: str) -> str:
    """Corrige les erreurs courantes de syntaxe boolean LinkedIn avant envoi."""
    q = q.strip()
    # NOT seul → AND NOT (LinkedIn exige AND NOT)
    q = re.sub(r'\)\s*NOT\s*\(', ') AND NOT (', q)
    # Dédoublonner AND AND NOT si déjà corrigé
    q = re.sub(r'\bAND\s+AND\s+NOT\b', 'AND NOT', q)
    # Supprimer le caractère & (non supporté par le parser LinkedIn)
    q = q.replace('&', 'and')
    # Supprimer les articles français avec apostrophe (d', l', j', etc.)
    # "compagnie d'assurance" → "compagnie assurance" (l'apostrophe casse le parser LinkedIn)
    q = re.sub(r"\b[a-zA-ZÀ-ÿ]+'\s*", " ", q)
    # Normaliser tous les whitespace (newlines, tabs, espaces multiples) en un seul espace
    q = re.sub(r'\s+', ' ', q)
    return q

# ============================================================
# LEONAR API
# ============================================================
def get_connected_accounts():
    """Récupère les comptes LinkedIn connectés"""
    resp = leonar_request("GET", f"{LEONAR_BASE}/connected-accounts")
    return resp.json()["data"]

def linkedin_lookup_locations(query, account_id, api_type="recruiter"):
    """Cherche les IDs de localisation LinkedIn"""
    resp = leonar_request(
        "GET",
        f"{LEONAR_BASE}/sourcing/linkedin/locations",
        params={"q": query, "account_id": account_id, "api_type": api_type}
    )
    return resp.json().get("data", [])

def linkedin_search(project_id, account_id, job_titles, location_ids=None, years_experience=None, boolean_query=None, page=1, page_size=25):
    """Recherche LinkedIn via endpoint dédié"""
    payload = {
        "project_id": project_id,
        "account_id": account_id,
        "page": page,
        "page_size": page_size,
    }
    if job_titles:
        payload["job_titles"] = job_titles
    if location_ids:
        payload["location_ids"] = location_ids
    if years_experience and (years_experience.get("min", 0) > 0 and years_experience.get("max", 0) > 0):
        payload["years_experience"] = years_experience
    if boolean_query:
        payload["boolean_query"] = boolean_query

    resp = leonar_request("POST", f"{LEONAR_BASE}/sourcing/linkedin/search", json=payload)
    return resp.json()["data"]

def sourcing_search(project_id, filters, source_type, page=1, page_size=25):
    """Recherche Leonar Source ou Contacts CRM"""
    payload = {
        "project_id": project_id,
        "source_type": source_type,
        "filters": filters,
        "page": page,
        "page_size": page_size,
    }
    resp = leonar_request("POST", f"{LEONAR_BASE}/sourcing/search", json=payload)
    return resp.json()["data"]

def add_profiles_to_project(project_id, profiles):
    """Ajoute des profils sourcés à un projet (max 100 par requête)"""
    payload = {"project_id": project_id, "profiles": profiles}
    resp = requests.post(f"{LEONAR_BASE}/sourcing/add-to-project", headers=leonar_headers(), json=payload)
    resp.raise_for_status()
    return resp.json()["data"]

def get_project_entries(project_id):
    """Récupère tous les profils déjà dans le projet"""
    all_entries = []
    offset = 0
    while True:
        resp = requests.get(
            f"{LEONAR_BASE}/projects/{project_id}/entries?limit=50&offset={offset}",
            headers=leonar_headers()
        )
        if not resp.ok:
            break
        data = resp.json()
        entries = data.get("data", [])
        if not entries:
            break
        all_entries.extend(entries)
        if not data.get("meta", {}).get("has_more", False):
            break
        offset += 50
        time.sleep(0.3)
    return all_entries

def add_note_to_contact(contact_id, content):
    """Ajoute une note à un contact"""
    resp = requests.post(
        f"{LEONAR_BASE}/contacts/{contact_id}/notes",
        headers=leonar_headers(),
        json={"content": content}
    )
    resp.raise_for_status()
    return resp.json()["data"]

# ============================================================
# UTILITAIRES
# ============================================================
def deduplicate_profiles(profiles):
    """Dédoublonne par linkedin_url puis par nom complet"""
    seen_urls = set()
    seen_names = set()
    unique = []
    for p in profiles:
        url = p.get("linkedin_url") or ""
        fn = (p.get("first_name") or "").lower().strip()
        ln = (p.get("last_name") or "").lower().strip()
        name = f"{fn} {ln}"
        
        if url and url in seen_urls:
            continue
        if name.strip() and name in seen_names:
            continue
        
        if url:
            seen_urls.add(url)
        if name.strip():
            seen_names.add(name)
        unique.append(p)
    return unique

def exclude_existing_profiles(profiles, existing_entries):
    """Retire les profils déjà présents dans le projet"""
    existing_names = set()
    existing_urls = set()
    
    for entry in existing_entries:
        contact = entry.get("contact", {})
        fn = (contact.get("first_name") or "").lower().strip()
        ln = (contact.get("last_name") or "").lower().strip()
        name = f"{fn} {ln}"
        url = contact.get("linkedin_profile", "") or ""
        if name.strip():
            existing_names.add(name)
        if url:
            existing_urls.add(url)
    
    new_profiles = []
    skipped = 0
    for p in profiles:
        fn = (p.get("first_name") or "").lower().strip()
        ln = (p.get("last_name") or "").lower().strip()
        name = f"{fn} {ln}"
        url = p.get("linkedin_url", "") or ""
        
        if (url and url in existing_urls) or (name.strip() and name in existing_names):
            skipped += 1
            continue
        new_profiles.append(p)
    
    return new_profiles, skipped

def filter_by_location(profiles, region):
    """Filtre post-recherche par localisation (filet de sécurité pour Leonar Source)"""
    if not region:
        return profiles, []
    
    region_lower = region.lower().strip()
    region_terms = [t.strip() for t in region_lower.replace(",", " ").split() if len(t.strip()) > 2]
    
    matched = []
    excluded = []
    for p in profiles:
        loc = ((p.get("location") or "")).lower()
        if not loc:
            matched.append(p)  # Pas de loc → on garde
        elif any(term in loc for term in region_terms):
            matched.append(p)
        else:
            excluded.append(p)
    
    return matched, excluded

# ============================================================
# CLAUDE API
# ============================================================
def extract_search_criteria(claude_client, job_desc, transcript, region, seniority):
    """Claude extrait les critères de recherche structurés depuis le brief"""
    prompt = f"""Tu es un recruteur expert en finance. À partir du brief ci-dessous, extrais les critères de recherche structurés.

DESCRIPTIF DE POSTE :
{job_desc}

RETRANSCRIPTION BRIEF MANAGER :
{transcript}

RÉGION : {region}
SÉNIORITÉ : {seniority}

Réponds UNIQUEMENT en JSON valide :
{{
    "job_titles": {{
        "include": ["titre1", "titre2"],
        "exclude": ["titre à exclure"]
    }},
    "companies": {{
        "include": [],
        "exclude": ["entreprise à exclure"]
    }},
    "locations": {{
        "countries": ["France"],
        "regions": ["région1"]
    }},
    "years_experience": {{
        "min": X,
        "max": Y
    }},
    "boolean_query": "expression booléenne LinkedIn complète",
    "keywords": {{
        "include": ["mot-clé1", "mot-clé2"],
        "exclude": ["mot-clé à exclure"]
    }},
    "summary": "Résumé en 2 lignes du profil recherché"
}}

Sois précis sur les titres de poste — inclus les variantes FR et EN.
Pour les régions, mets le nom exact (ex: Île-de-France, Auvergne-Rhône-Alpes).
Pour les mots-clés (keywords), extrais les termes simples : compétences, outils, secteurs (un terme par item, pas d'opérateurs booléens).
Pour years_experience, déduis-le de la séniorité indiquée.

Pour boolean_query : construis une expression booléenne LinkedIn complète et valide, prête à l'emploi.
- Regroupe les variantes de titres essentielles ET les mots-clés sectoriels clés
- Opérateurs AND, OR, NOT obligatoirement en MAJUSCULES
- Toujours "AND NOT" pour les exclusions, jamais "NOT" seul
- Guillemets autour de chaque expression multi-mots (ex: "directeur commercial")
- Ne pas inclure les lieux (gérés par le filtre location séparé)
- Viser moins de 800 caractères — être concis, garder uniquement les termes discriminants
- Exemple : ("directeur commercial" OR "sales director") AND (assurance OR IARD) AND NOT (junior OR stagiaire)
- boolean_query doit être une STRING sur une seule ligne, jamais un tableau.
- Évite les apostrophes dans les expressions entre guillemets : écris "compagnie assurance" plutôt que "compagnie d'assurance", "groupe assurance" plutôt que "groupe d'assurance" — l'apostrophe casse le parser LinkedIn."""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    
    return json.loads(text.strip())

def score_profiles(claude_client, profiles, job_desc, transcript, criteria_summary, region, exclusions):
    """Claude score un lot de profils contre le brief"""
    profiles_text = ""
    for i, p in enumerate(profiles):
        experiences = ""
        if p.get("experiences"):
            for exp in p["experiences"][:4]:
                current = " (actuel)" if exp.get("is_current") else ""
                period = ""
                if exp.get("start_date"):
                    period = f" [{exp.get('start_date', '')} → {exp.get('end_date', 'présent')}]"
                experiences += f"  - {exp.get('title', 'N/A')} @ {exp.get('company_name', 'N/A')}{current}{period}\n"
        
        education = ""
        if p.get("educations"):
            for edu in p["educations"][:2]:
                education += f"  - {edu.get('diploma', '')} {edu.get('specialization', '')} @ {edu.get('educational_establishment', '')}\n"
        
        skills = ", ".join(p.get("skills", [])[:10]) if p.get("skills") else "N/A"
        
        profiles_text += f"""
--- PROFIL {i+1} (ID: {p.get('profile_id', 'N/A')}) ---
Nom: {(p.get('first_name') or '')} {(p.get('last_name') or '')}
Titre: {p.get('headline', 'N/A')}
Localisation: {p.get('location', 'N/A')}
Années d'expérience: {p.get('total_years_experience', 'N/A')}
Résumé: {((p.get('summary') or 'N/A'))[:200]}
Compétences: {skills}
Expériences:
{experiences}Formation:
{education}"""

    exclusions_text = ""
    if exclusions:
        exclusions_text = f"\nMOTS-CLÉS D'EXCLUSION SUPPLÉMENTAIRES : {', '.join(exclusions)}\n"

    prompt = f"""Tu es un recruteur expert en finance. Score chaque profil de 0 à 10.

DESCRIPTIF DE POSTE :
{job_desc}

BRIEF MANAGER :
{transcript}

RÉSUMÉ CRITÈRES : {criteria_summary}
RÉGION CIBLE : {region}
{exclusions_text}
PROFILS :
{profiles_text}

Réponds UNIQUEMENT en JSON (array) :
[
    {{
        "profile_id": "id",
        "score": X,
        "justification": "1-2 lignes max"
    }}
]

BARÈME :
- 8-10 : Match excellent (expérience, compétences, secteur, formation alignés)
- 6-7 : Bon match, écarts mineurs
- 4-5 : Match partiel
- 0-3 : Peu pertinent

Utilise TOUTES les données (skills, formation, parcours, résumé). Sois exigeant et différenciant."""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    
    return json.loads(text.strip())

# ============================================================
# INTERFACE
# ============================================================
st.title("🎯 Leonar Scoring Tool")
st.caption("Recherche automatisée + scoring intelligent des profils candidats")

if not leonar_api_key or not claude_api_key:
    st.error("⚠️ Clés API manquantes. Remplis le fichier .env ou les Secrets Streamlit Cloud.")
    st.stop()

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.title("⚙️ Paramètres")
    
    source_type = st.selectbox(
        "Source de recherche",
        ["leonar_source", "linkedin", "contacts"],
        format_func=lambda x: {
            "leonar_source": "🔍 Leonar Source",
            "linkedin": "🔗 LinkedIn Recruiter",
            "contacts": "📂 Contacts CRM"
        }[x]
    )
    
    # Si LinkedIn, charger les comptes connectés
    linkedin_account_id = None
    if source_type == "linkedin":
        try:
            accounts = get_connected_accounts()
            if accounts:
                account_names = {f"{a['name']} ({a.get('license_type', 'N/A')})": a["id"] for a in accounts}
                selected_account = st.selectbox("Compte LinkedIn", list(account_names.keys()))
                linkedin_account_id = account_names[selected_account]
                
                # Debug : afficher le statut du compte sélectionné
                selected_acc = [a for a in accounts if a["id"] == linkedin_account_id][0]
                st.caption(f"Statut API : {selected_acc.get('api_status', {})}")
            else:
                st.warning("Aucun compte LinkedIn connecté dans Leonar.")
        except Exception as e:
            st.error(f"Erreur comptes LinkedIn : {e}")
    
    st.divider()
    if source_type == "linkedin":
        max_profiles = st.slider("Profils max à analyser", 25, 250, 100, step=25)
        linkedin_used = get_linkedin_count()
        remaining = LINKEDIN_DAILY_LIMIT - linkedin_used
        color = "🟢" if remaining > 500 else "🟡" if remaining > 200 else "🔴"
        st.markdown(f"{color} **LinkedIn : {linkedin_used}/{LINKEDIN_DAILY_LIMIT}** profils consultés aujourd'hui _(compteur persistant)_")
        if remaining < max_profiles:
            st.warning(f"⚠️ Il te reste {remaining} profils LinkedIn aujourd'hui")
    else:
        max_profiles = st.slider("Profils max à analyser", 25, 1000, 100, step=25)
    score_threshold = st.slider("Score minimum à afficher", 0, 10, 6)
    
    st.divider()
    st.caption(f"💰 Coût scoring estimé : ~{max_profiles * 0.002:.2f}€")


# ============================================================
# ONGLETS PRINCIPAUX
# ============================================================
tab1, tab2 = st.tabs(["🎯 Recherche & Scoring", "📄 Dossier de Candidature"])

with tab1:
    # ============================================================
    # ÉTAPE 1 — BRIEF
    # ============================================================
    st.header("1️⃣ Brief du poste")

    col1, col2 = st.columns(2)
    with col1:
        job_desc = st.text_area("Descriptif de poste", height=250, placeholder="Missions, compétences, formation...")
    with col2:
        transcript = st.text_area("Retranscription brief manager", height=250, placeholder="Retranscription audio...")

    col3, col4 = st.columns(2)
    with col3:
        region = st.text_input("Région / Localisation", placeholder="Ex: Île-de-France, Lyon, PACA...")
    with col4:
        seniority = st.text_input("Séniorité (années d'expérience)", placeholder="Ex: 5-10 ans")

    exclusion_keywords = st.text_area(
        "🚫 Exclusions supplémentaires",
        height=80,
        placeholder="Un mot-clé par ligne. Ex:\ncabinet d'audit\nconseil\nintérim"
    )

    # ============================================================
    # ÉTAPE 2 — EXTRACTION CRITÈRES
    # ============================================================
    st.header("2️⃣ Critères de recherche")

    if st.button("🔍 Analyser le brief", type="primary"):
        if not job_desc:
            st.error("Le descriptif de poste est obligatoire.")
        else:
            with st.spinner("Claude analyse le brief..."):
                try:
                    claude_client = Anthropic(api_key=claude_api_key)
                    criteria = extract_search_criteria(claude_client, job_desc, transcript, region, seniority)
                    st.session_state["criteria"] = criteria
                    st.session_state["scoring_done"] = False
                    st.success("Critères extraits !")
                except Exception as e:
                    st.error(f"Erreur : {e}")

    if "criteria" in st.session_state:
        criteria = st.session_state["criteria"]

        st.subheader("Critères (modifiables avant recherche)")

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            edited_titles_include = st.text_area(
                "✅ Titres de poste (inclure)",
                value="\n".join(criteria.get("job_titles", {}).get("include", [])),
                height=100
            )
            edited_titles_exclude = st.text_area(
                "❌ Titres de poste (exclure)",
                value="\n".join(criteria.get("job_titles", {}).get("exclude", [])),
                height=80
            )
        with col_b:
            edited_keywords = st.text_area(
                "🔑 Mots-clés",
                value="\n".join(criteria.get("keywords", {}).get("include", [])),
                height=100
            )
            edited_regions = st.text_area(
                "📍 Régions",
                value="\n".join(criteria.get("locations", {}).get("regions", [])),
                height=80
            )
        with col_c:
            edited_companies_exclude = st.text_area(
                "🚫 Entreprises à exclure",
                value="\n".join(criteria.get("companies", {}).get("exclude", [])),
                height=100
            )
            edited_keywords_exclude = st.text_area(
                "🚫 Mots-clés à exclure",
                value="\n".join(criteria.get("keywords", {}).get("exclude", [])),
                height=80
            )

        # XP — affiché sous les colonnes
        col_xp1, col_xp2, col_xp3 = st.columns([1, 1, 2])
        with col_xp1:
            exp_min = st.number_input("XP min (années)", value=criteria.get("years_experience", {}).get("min", 0))
        with col_xp2:
            exp_max = st.number_input("XP max (années)", value=criteria.get("years_experience", {}).get("max", 0))

        exclusion_list = [k.strip() for k in exclusion_keywords.split("\n") if k.strip()]

        # Champ boolean query — visible uniquement pour LinkedIn
        edited_boolean_query = ""
        if source_type == "linkedin":
            edited_boolean_query = st.text_area(
                "🔍 Boolean Query LinkedIn",
                value=criteria.get("boolean_query", ""),
                height=80,
                help='Opérateurs AND OR NOT en MAJUSCULES. Guillemets autour des expressions multi-mots. Ex: ("directeur commercial" OR "sales director") AND (assurance OR IARD)'
            )
            bq_len = len(edited_boolean_query.strip())
            if bq_len == 0:
                st.caption("💡 Query vide — la recherche s'appuiera uniquement sur les titres et filtres")
            elif bq_len < 1000:
                st.caption(f"✅ {bq_len} caractères — longueur optimale")
            elif bq_len < 1500:
                st.caption(f"🟡 {bq_len} caractères — acceptable, mais simplifier si possible")
            else:
                st.warning(f"🔴 {bq_len} caractères — query trop longue, risque de rejet par LinkedIn (max ~1 500). Simplifiez.")
            if "'" in edited_boolean_query:
                st.warning("⚠️ La query contient des apostrophes (`'`) dans des phrases — LinkedIn peut rejeter cette syntaxe. Le sanitizer les supprimera automatiquement avant l'envoi.")

        st.info(f"📋 {criteria.get('summary', '')}")

        # ============================================================
        # ÉTAPE 3 — RECHERCHE & SCORING
        # ============================================================
        st.header("3️⃣ Recherche & Scoring")

        st.subheader("Projet Leonar")
        st.caption("💡 Copie l'ID depuis l'URL Leonar : app.leonar.app/projects/**ID_ICI**")
        selected_project_id = st.text_input("ID du projet", placeholder="550e8400-e29b-41d4-a716-...")

        if selected_project_id and st.button("🚀 Lancer recherche + scoring", type="primary"):

            all_profiles = []

            # ---- Construire les paramètres de recherche ----
            titles_inc = [t.strip() for t in edited_titles_include.split("\n") if t.strip()]
            titles_exc = [t.strip() for t in edited_titles_exclude.split("\n") if t.strip()]
            kw_inc = [k.strip() for k in edited_keywords.split("\n") if k.strip()]
            kw_exc = [k.strip() for k in edited_keywords_exclude.split("\n") if k.strip()]
            kw_exc.extend(exclusion_list)
            kw_exc = list(set(kw_exc))
            companies_exc = [c.strip() for c in edited_companies_exclude.split("\n") if c.strip()]
            regions_list = [r.strip() for r in edited_regions.split("\n") if r.strip()]
            years_exp = {"min": int(exp_min), "max": int(exp_max)}

            # ---- PHASE 1 : RECHERCHE ----
            st.subheader("Phase 1 — Recherche")
            progress_bar = st.progress(0, text="Recherche en cours...")

            try:
                if source_type == "linkedin":
                    # === LINKEDIN : endpoint dédié ===

                    # 0. Vérifier la limite quotidienne
                    linkedin_used = get_linkedin_count()
                    remaining = LINKEDIN_DAILY_LIMIT - linkedin_used
                    if remaining <= 0:
                        st.error(f"🚫 Limite LinkedIn quotidienne atteinte ({LINKEDIN_DAILY_LIMIT} profils). Réessaie demain ou utilise Leonar Source.")
                        st.stop()
                    if max_profiles > remaining:
                        st.warning(f"⚠️ Il te reste {remaining} profils LinkedIn aujourd'hui. Recherche limitée à {remaining}.")
                        max_profiles = remaining

                    # 1. Résoudre les IDs de localisation
                    location_ids = {}
                    if regions_list and linkedin_account_id:
                        with st.spinner("Résolution des localisations LinkedIn..."):
                            for region_name in regions_list:
                                results = linkedin_lookup_locations(region_name, linkedin_account_id)
                                if results:
                                    # Prendre le premier résultat
                                    loc = results[0]
                                    location_ids[loc["id"]] = loc["title"]
                                    st.caption(f"📍 {region_name} → {loc['title']} (ID: {loc['id']})")
                                else:
                                    st.warning(f"⚠️ Localisation '{region_name}' non trouvée sur LinkedIn")

                    # 2. Boolean query — directement depuis le champ UI (édité par l'utilisateur ou extrait par Claude)
                    boolean_query = sanitize_boolean_query(edited_boolean_query) if edited_boolean_query.strip() else None
                    if boolean_query:
                        st.caption(f"🔍 Boolean query envoyée : `{boolean_query}`")

                    # Debug : afficher le payload complet avant envoi (miroir exact de ce qui est envoyé)
                    with st.expander("🛠 Debug — payload envoyé à l'API"):
                        debug_payload = {
                            "project_id": selected_project_id,
                            "account_id": linkedin_account_id,
                            "boolean_query": boolean_query,
                            "location_ids": list(location_ids.keys()) if location_ids else None,
                            "job_titles": titles_inc if titles_inc else None,
                        }
                        if years_exp.get("min", 0) > 0 or years_exp.get("max", 0) > 0:
                            debug_payload["years_experience"] = years_exp
                        st.json(debug_payload)

                    # 3. Recherche paginée avec délais humains
                    page = 1
                    while len(all_profiles) < max_profiles:
                        # Vérifier la limite avant chaque page
                        if get_linkedin_count() >= LINKEDIN_DAILY_LIMIT:
                            st.warning("⚠️ Limite LinkedIn quotidienne atteinte en cours de recherche. Arrêt.")
                            break

                        results = linkedin_search(
                            project_id=selected_project_id,
                            account_id=linkedin_account_id,
                            job_titles=titles_inc,
                            location_ids=location_ids if location_ids else None,
                            years_experience=years_exp,
                            boolean_query=boolean_query,
                            page=page,
                            page_size=25
                        )

                        profiles = results.get("profiles", [])
                        if not profiles:
                            break

                        # Comptabiliser les profils consultés
                        add_linkedin_count(len(profiles))

                        # Filtrer les profils déjà dans le projet (flag LinkedIn)
                        profiles = [p for p in profiles if not p.get("already_in_project", False)]
                        all_profiles.extend(profiles)

                        total = results.get("total_count", len(all_profiles))
                        linkedin_now = get_linkedin_count()
                        progress = min(len(all_profiles) / max_profiles, 1.0)
                        progress_bar.progress(progress, text=f"{len(all_profiles)} profils récupérés sur {total} | LinkedIn: {linkedin_now}/{LINKEDIN_DAILY_LIMIT}")

                        if not results.get("has_more", False):
                            break

                        page += 1
                        # Délai aléatoire pour simuler un comportement humain
                        time.sleep(random.uniform(2.0, 4.0))

                else:
                    # === LEONAR SOURCE / CONTACTS CRM ===
                    filters = {}

                    if titles_inc or titles_exc:
                        filters["job_titles"] = {}
                        if titles_inc:
                            filters["job_titles"]["include"] = titles_inc
                        if titles_exc:
                            filters["job_titles"]["exclude"] = titles_exc

                    if kw_inc or kw_exc:
                        filters["keywords"] = {}
                        if kw_inc:
                            filters["keywords"]["include"] = kw_inc
                        if kw_exc:
                            filters["keywords"]["exclude"] = kw_exc

                    countries = criteria.get("locations", {}).get("countries", ["France"])
                    filters["locations"] = {"countries": countries}
                    if regions_list:
                        filters["locations"]["states"] = regions_list

                    filters["years_experience"] = years_exp

                    if companies_exc:
                        filters["companies"] = {"exclude": companies_exc}

                    if source_type == "contacts":
                        if "contacts_filters" not in filters:
                            filters["contacts_filters"] = {}
                        filters["contacts_filters"]["contact_types"] = ["candidate"]

                    page = 1
                    while len(all_profiles) < max_profiles:
                        results = sourcing_search(
                            project_id=selected_project_id,
                            filters=filters,
                            source_type=source_type,
                            page=page,
                            page_size=25
                        )

                        profiles = results.get("profiles", [])
                        if not profiles:
                            break

                        all_profiles.extend(profiles)
                        total = results.get("total_count", len(all_profiles))
                        progress = min(len(all_profiles) / max_profiles, 1.0)
                        progress_bar.progress(progress, text=f"{len(all_profiles)} profils récupérés sur {total} disponibles")

                        if not results.get("has_more", False):
                            break

                        page += 1
                        time.sleep(0.5)

                    if results.get("filters_too_strict"):
                        st.warning("⚠️ Leonar indique que les filtres sont trop stricts.")

                all_profiles = all_profiles[:max_profiles]
                progress_bar.progress(1.0, text=f"✅ {len(all_profiles)} profils récupérés")

            except Exception as e:
                st.error(f"Erreur recherche : {e}")
                st.stop()

            if not all_profiles:
                st.warning("Aucun profil trouvé. Élargis tes critères.")
                st.stop()

            # ---- DÉDOUBLONNAGE ----
            before = len(all_profiles)
            all_profiles = deduplicate_profiles(all_profiles)
            if before > len(all_profiles):
                st.info(f"🔄 {before - len(all_profiles)} doublons supprimés")

            # ---- EXCLUSION PROFILS EXISTANTS ----
            with st.spinner("Vérification des profils déjà dans le projet..."):
                try:
                    existing = get_project_entries(selected_project_id)
                    if existing:
                        all_profiles, skipped = exclude_existing_profiles(all_profiles, existing)
                        if skipped > 0:
                            st.info(f"♻️ {skipped} profils déjà dans le projet retirés")
                except Exception as e:
                    st.warning(f"Impossible de vérifier les existants : {e}")

            # ---- FILTRE LOCALISATION POST-RECHERCHE (filet de sécurité) ----
            if region and source_type != "linkedin":
                all_profiles, excluded_loc = filter_by_location(all_profiles, region)
                if excluded_loc:
                    st.info(f"📍 {len(excluded_loc)} profils hors {region} retirés")

            if not all_profiles:
                st.warning("Aucun profil restant après filtrage.")
                st.stop()

            # ---- PHASE 2 : SCORING ----
            st.subheader(f"Phase 2 — Scoring de {len(all_profiles)} profils")

            claude_client = Anthropic(api_key=claude_api_key)
            all_scores = []
            batch_size = 10
            scoring_progress = st.progress(0, text="Scoring en cours...")

            try:
                for i in range(0, len(all_profiles), batch_size):
                    batch = all_profiles[i:i+batch_size]
                    scores = score_profiles(
                        claude_client, batch, job_desc, transcript,
                        criteria.get("summary", ""), region, exclusion_list
                    )
                    all_scores.extend(scores)

                    progress = min((i + batch_size) / len(all_profiles), 1.0)
                    scoring_progress.progress(progress, text=f"{min(i+batch_size, len(all_profiles))}/{len(all_profiles)} profils scorés")
                    time.sleep(0.3)

                scoring_progress.progress(1.0, text=f"✅ {len(all_scores)} profils scorés")
            except Exception as e:
                st.error(f"Erreur scoring : {e}")
                st.stop()

            # Fusionner
            scores_map = {s["profile_id"]: s for s in all_scores}
            scored_profiles = []
            for p in all_profiles:
                pid = p.get("profile_id", "")
                score_data = scores_map.get(pid, {"score": 0, "justification": "Non scoré"})
                scored_profiles.append({**p, "score": score_data["score"], "justification": score_data["justification"]})

            scored_profiles.sort(key=lambda x: x["score"], reverse=True)

            st.session_state["scored_profiles"] = scored_profiles
            st.session_state["selected_project_id"] = selected_project_id
            st.session_state["scoring_done"] = True

        # ============================================================
        # RÉSULTATS
        # ============================================================
        if st.session_state.get("scoring_done"):
            scored_profiles = st.session_state["scored_profiles"]

            visible = [p for p in scored_profiles if p["score"] >= score_threshold]
            hidden = len(scored_profiles) - len(visible)

            st.subheader(f"Résultats — {len(visible)} profils ≥ {score_threshold}/10")
            if hidden > 0:
                st.caption(f"({hidden} profils sous le seuil masqués)")

            for p in visible:
                score = p["score"]
                emoji = "🟢" if score >= 8 else "🟡" if score >= 6 else "🟠" if score >= 4 else "🔴"

                skills_preview = ", ".join(p.get("skills", [])[:5]) if p.get("skills") else ""
                xp = f" | {p.get('total_years_experience', '?')} ans XP" if p.get("total_years_experience") else ""

                with st.expander(f"{emoji} **{score}/10** — {(p.get('first_name') or '')} {(p.get('last_name') or '')} | {p.get('headline', '')}{xp}"):
                    col_l, col_r = st.columns([2, 1])
                    with col_l:
                        st.write(f"💬 {p['justification']}")
                        st.write(f"📍 {p.get('location', 'N/A')}")
                        if p.get("experiences"):
                            for exp in p["experiences"][:3]:
                                current = " ✅" if exp.get("is_current") else ""
                                period = f" ({exp.get('start_date', '')} → {exp.get('end_date', 'présent')})" if exp.get("start_date") else ""
                                st.write(f"  • {exp.get('title', '')} @ {exp.get('company_name', '')}{current}{period}")
                        if p.get("educations"):
                            for edu in p["educations"][:2]:
                                st.write(f"  🎓 {edu.get('diploma', '')} {edu.get('specialization', '')} — {edu.get('educational_establishment', '')}")
                    with col_r:
                        if skills_preview:
                            st.write(f"🛠 {skills_preview}")
                        if p.get("linkedin_url"):
                            st.write(f"🔗 [LinkedIn]({p['linkedin_url']})")
                        else:
                            st.write("⚠️ Pas de LinkedIn")

            # ============================================================
            # ÉTAPE 4 — PUSH LEONAR
            # ============================================================
            st.header("4️⃣ Envoyer dans Leonar")

            min_score_push = st.slider("Score minimum pour push", 0, 10, score_threshold)
            profiles_to_push = [p for p in scored_profiles if p["score"] >= min_score_push]
            st.info(f"{len(profiles_to_push)} profils seront ajoutés (score ≥ {min_score_push}/10)")

            if st.button(f"📤 Ajouter {len(profiles_to_push)} profils dans Leonar", type="primary"):
                project_id = st.session_state.get("selected_project_id")
                push_progress = st.progress(0, text="Ajout en cours...")

                try:
                    added_total = 0
                    contact_ids = []

                    profiles_payload = []
                    for p in profiles_to_push:
                        profile_data = {
                            "profile_id": p.get("profile_id"),
                            "first_name": (p.get("first_name") or ""),
                            "last_name": (p.get("last_name") or ""),
                            "headline": p.get("headline", ""),
                            "linkedin_url": p.get("linkedin_url", ""),
                            "location": p.get("location", ""),
                        }
                        if p.get("current_job"):
                            profile_data["current_job"] = p["current_job"]
                        if p.get("experiences"):
                            profile_data["experiences"] = p["experiences"]
                        if p.get("educations"):
                            profile_data["educations"] = p["educations"]
                        if p.get("skills"):
                            profile_data["skills"] = p["skills"]
                        if p.get("total_years_experience"):
                            profile_data["total_years_experience"] = p["total_years_experience"]
                        if p.get("picture_url"):
                            profile_data["picture_url"] = p["picture_url"]
                        profiles_payload.append(profile_data)

                    for i in range(0, len(profiles_payload), 50):
                        batch = profiles_payload[i:i+50]
                        result = add_profiles_to_project(project_id, batch)
                        added_total += result.get("added", 0)
                        contact_ids.extend(result.get("contact_ids", []))

                        progress = min((i + 50) / len(profiles_payload), 0.5)
                        push_progress.progress(progress, text=f"{added_total} profils ajoutés...")
                        time.sleep(0.5)

                    push_progress.progress(0.5, text=f"✅ {added_total} profils ajoutés. Notes...")

                    for idx, contact_id in enumerate(contact_ids):
                        if idx < len(profiles_to_push):
                            p = profiles_to_push[idx]
                            note = f"🎯 Score : {p['score']}/10\n💬 {p['justification']}"
                            try:
                                add_note_to_contact(contact_id, note)
                            except Exception:
                                pass

                            progress = 0.5 + (0.5 * (idx + 1) / len(contact_ids))
                            push_progress.progress(min(progress, 1.0), text=f"Notes : {idx+1}/{len(contact_ids)}")
                            time.sleep(0.2)

                    push_progress.progress(1.0, text="✅ Terminé")
                    st.success(f"🎉 {added_total} profils ajoutés avec scores dans Leonar !")
                    st.balloons()

                except Exception as e:
                    st.error(f"Erreur push : {e}")

# ============================================================
# ONGLET 2 — DOSSIER DE CANDIDATURE
# ============================================================
# ============================================================
# ONGLET 2 — DOSSIER DE CANDIDATURE
# ============================================================
with tab2:
    st.header("📄 Générateur de Dossier de Candidature")
    st.caption("Crée un dossier Entourage à partir du CV PDF + Score Card + brief entretien.")

    if not HTML_MASTER_TEMPLATE:
        st.error("⚠️ Fichier `dossier_template.html` introuvable. Place-le dans le même dossier que app.py.")
        st.stop()

    # --- LOGO (persistant en session) ---
    with st.expander(
        "🖼 Logo Entourage" + (" ✓" if st.session_state.get("dossier_logo_b64") else " — à uploader une fois"),
        expanded=not st.session_state.get("dossier_logo_b64"),
    ):
        logo_file = st.file_uploader(
            "Logo Entourage Recrutement (.png / .jpg)",
            type=["png", "jpg", "jpeg"],
            key="dossier_logo_upload",
        )
        if logo_file:
            st.session_state["dossier_logo_b64"] = base64.b64encode(logo_file.read()).decode()
            st.success("Logo chargé et conservé pour la session ✓")
        elif st.session_state.get("dossier_logo_b64"):
            st.info("Logo déjà chargé en session ✓")

    st.divider()

    # --- INPUTS PRINCIPAUX ---
    col_left, col_right = st.columns(2)

    with col_left:
        cv_file = st.file_uploader(
            "📎 CV du candidat (PDF)",
            type=["pdf"],
            key="dossier_cv",
        )
        brief_text = st.text_area(
            "📝 Brief / Compte-rendu entretien",
            height=180,
            placeholder="Colle ici le brief IA issu de la retranscription visio...",
            key="dossier_brief",
        )
        linkedin_url = st.text_input(
            "🔗 LinkedIn du candidat",
            placeholder="https://www.linkedin.com/in/prenom-nom/",
            key="dossier_linkedin",
        )
        commercial = st.radio(
            "👤 Responsable de chasse",
            ["Warren", "Helder"],
            horizontal=True,
            key="dossier_commercial",
        )

    with col_right:
        st.markdown("**📊 Score Card du poste**")
        st.caption(
            "Upload la score card HTML générée par l'outil Entourage. "
            "L'IA en extraira automatiquement les critères, notes et analyses."
        )
        scorecard_file = st.file_uploader(
            "Score Card (.html ou .pdf)",
            type=["html", "htm", "pdf"],
            key="dossier_scorecard",
        )
        if scorecard_file:
            st.success(f"Score card chargée : {scorecard_file.name} ✓")

    st.divider()

    # --- BOUTON GÉNÉRER ---
    if st.button("✨ Générer le Dossier", type="primary", key="dossier_generate"):
        errors = []
        if not st.session_state.get("dossier_logo_b64"):
            errors.append("Upload le logo Entourage (section en haut)")
        if not cv_file:
            errors.append("Upload le CV PDF du candidat")
        if not brief_text.strip():
            errors.append("Le brief / compte-rendu est obligatoire")
        if not scorecard_file:
            errors.append("Upload la Score Card du poste")

        if errors:
            for err in errors:
                st.error(f"❌ {err}")
        else:
            with st.status("Génération du dossier en cours…", expanded=True) as status:
                try:
                    # ÉTAPE 1 — Lecture des fichiers
                    st.write("📄 Lecture du CV et de la Score Card…")
                    pdf_bytes = cv_file.read()
                    pdf_b64 = base64.b64encode(pdf_bytes).decode()
                    scorecard_bytes = scorecard_file.read()
                    scorecard_ext = scorecard_file.name.rsplit(".", 1)[-1].lower()

                    # ÉTAPE 2 — Construction du message Claude
                    st.write("🧠 Envoi à Claude pour analyse et génération…")
                    content_blocks = [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                            "title": "CV du candidat",
                        },
                    ]

                    if scorecard_ext in ("html", "htm"):
                        scorecard_text = scorecard_bytes.decode("utf-8", errors="ignore")
                        content_blocks.append({
                            "type": "text",
                            "text": f"SCORE CARD DU POSTE (HTML) :\n{scorecard_text}",
                        })
                    else:
                        sc_b64 = base64.b64encode(scorecard_bytes).decode()
                        content_blocks.append({
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": sc_b64,
                            },
                            "title": "Score Card du poste",
                        })

                    user_prompt = (
                        f"BRIEF / COMPTE-RENDU ENTRETIEN :\n{brief_text.strip()}\n\n"
                        f"COMMERCIAL : {commercial}\n\n"
                        "INSTRUCTIONS SCORE CARD :\n"
                        "Lis la Score Card du poste ci-dessus. "
                        "Extrais les 4 critères, notes (/5) et analyses. "
                        "Utilise-les pour remplir le tableau page 2.\n\n"
                        "RAPPEL : génère UNIQUEMENT les pages 1 et 2. "
                        "Le CV original sera ajouté automatiquement après.\n\n"
                        f"VOICI LE CODE HTML MAÎTRE À REMPLIR :\n{HTML_MASTER_TEMPLATE}"
                    )
                    content_blocks.append({"type": "text", "text": user_prompt})

                    # ÉTAPE 3 — Appel Claude (timeout 3 min)
                    claude_client = Anthropic(api_key=claude_api_key)
                    response = claude_client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=8000,
                        system=DOSSIER_SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": content_blocks}],
                        timeout=240.0,
                    )
                    generated_html = response.content[0].text

                    # Vérifier si Claude a été coupé par la limite de tokens
                    if response.stop_reason == "max_tokens":
                        st.warning(
                            "⚠️ Génération interrompue (limite de tokens atteinte). "
                            "Les pages 1 et 2 peuvent être incomplètes."
                        )

                    # ÉTAPE 4 — Nettoyage, injection logo + LinkedIn
                    st.write("🖼 Injection du logo et finalisation…")
                    generated_html = re.sub(r"^```[^\n]*\n", "", generated_html)
                    generated_html = re.sub(r"\n```\s*$", "", generated_html.strip())

                    # Injection logo (APRÈS génération Claude)
                    logo_b64 = st.session_state["dossier_logo_b64"]
                    if 'LOGO_PLACEHOLDER' not in generated_html:
                        st.warning("⚠️ Logo : le placeholder n'a pas été conservé par Claude — le logo n'apparaîtra pas dans le header.")
                    final_html = generated_html.replace(
                        'src="LOGO_PLACEHOLDER"',
                        f'src="data:image/png;base64,{logo_b64}"',
                    )

                    # Injection LinkedIn via placeholder opaque — 100% fiable
                    # Claude ne peut pas modifier une chaîne non-HTML, donc le placeholder
                    # LINKEDIN_CONTACT_ITEM_PLACEHOLDER arrive intact jusqu'ici
                    if linkedin_url.strip():
                        li_html = (
                            '<div class="contact-item">'
                            '<i class="fa-brands fa-linkedin-in"></i> '
                            f'<a href="{linkedin_url.strip()}" target="_blank">Profil LinkedIn</a>'
                            '</div>'
                        )
                    else:
                        li_html = ""
                    final_html = final_html.replace("LINKEDIN_CONTACT_ITEM_PLACEHOLDER", li_html)
                    # Nettoyage résiduel (placeholders ancienne version)
                    final_html = final_html.replace('href="{{LIEN_LINKEDIN}}"', f'href="{linkedin_url.strip() or "#"}"')
                    final_html = final_html.replace('{{LIEN_LINKEDIN}}', linkedin_url.strip() or "#")

                    # Sauvegarde pages 1+2 et PDF pour les révisions ultérieures
                    # On sauvegarde generated_html (placeholders LOGO/LinkedIn intacts, sans base64 logo)
                    # pour éviter un payload gigantesque lors des révisions → timeout Anthropic
                    st.session_state["dossier_html_pages12"] = generated_html
                    st.session_state["dossier_pdf_bytes"] = pdf_bytes

                    # ÉTAPE 5 — Appendre les pages du CV original comme images PNG
                    st.write("📄 Conversion du CV en images…")
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    cv_pages_html = ""
                    mat = fitz.Matrix(150 / 72, 150 / 72)  # 150 DPI
                    for page_num in range(len(pdf_doc)):
                        pix = pdf_doc[page_num].get_pixmap(matrix=mat)
                        img_b64 = base64.b64encode(pix.tobytes("png")).decode()
                        cv_pages_html += (
                            '<div class="page" style="padding:0;overflow:hidden;">'
                            f'<img src="data:image/png;base64,{img_b64}" '
                            'style="width:210mm;height:297mm;object-fit:contain;display:block;margin:0;" />'
                            '</div>\n'
                        )
                    pdf_doc.close()
                    # Insérer les pages CV juste avant </body>
                    final_html = final_html.replace("</body>", f"{cv_pages_html}</body>", 1)

                    # ÉTAPE 6 — Injection du bouton "Enregistrer en PDF"
                    # Ce bouton appelle window.print() du navigateur = PDF parfait, natif, gratuit
                    print_button_html = """
<div class="no-print" style="position:fixed;top:20px;right:20px;z-index:9999;background:#FFD700;border-radius:8px;box-shadow:0 4px 15px rgba(0,0,0,0.3);">
  <button onclick="window.print()" style="background:#FFD700;color:#000;border:none;padding:12px 24px;font-size:14px;font-weight:800;cursor:pointer;border-radius:8px;font-family:sans-serif;letter-spacing:0.5px;">
    🖨️ Enregistrer en PDF
  </button>
</div>
<style>@media print { .no-print { display:none!important; } }</style>
"""
                    # Sauvegarder pour la révision (le bouton doit être re-injecté)
                    st.session_state["_print_button_html"] = print_button_html
                    # Insérer juste après <body>
                    final_html = final_html.replace("<body>", f"<body>\n{print_button_html}", 1)

                    st.session_state["dossier_html"] = final_html
                    status.update(label="✅ Dossier généré !", state="complete")

                except Exception as e:
                    status.update(label="❌ Erreur", state="error")
                    st.error(f"Erreur : {e}")

    # --- RÉSULTAT : TÉLÉCHARGEMENT + APERÇU ---
    if st.session_state.get("dossier_html"):
        html_content = st.session_state["dossier_html"]

        name_match = re.search(r'class="candidate-name">([^<]+)<', html_content)
        candidate_name = (
            name_match.group(1).strip().replace(" ", "_") if name_match else "candidat"
        )

        st.info(
            "**Comment obtenir le PDF :**  \n"
            "1. Télécharge le fichier HTML ci-dessous  \n"
            "2. Ouvre-le dans **Chrome**  \n"
            "3. Clique le bouton **🖨️ Enregistrer en PDF** en haut à droite de la page  \n"
            "4. Dans la boîte de dialogue : format A4, sans marges → Enregistrer"
        )

        st.download_button(
            label="⬇️ Télécharger le Dossier (.html → PDF via Chrome)",
            data=html_content,
            file_name=f"dossier_{candidate_name}.html",
            mime="text/html",
            type="primary",
            key="dossier_download_html",
        )

        with st.expander("👁 Aperçu du dossier"):
            st.components.v1.html(html_content, height=900, scrolling=True)

        st.divider()

        # --- CORRECTIONS PAR COMMENTAIRES ---
        with st.expander("✏️ Corrections — décrire et régénérer"):
            st.caption(
                "Décris ce que tu veux modifier (ton, scores, analyse, points clés…). "
                "Claude régénère les pages 1 et 2 en intégrant tes corrections. Le CV reste inchangé."
            )
            user_corrections = st.text_area(
                "Tes corrections",
                placeholder=(
                    "Exemples :\n"
                    "— L'analyse manque de conviction, rends-la plus assertive\n"
                    "— Note Expertise Technique trop haute, mettre 3.0/5\n"
                    "— Ajouter un point de vigilance sur la mobilité géographique\n"
                    "— Prétentions : 70k€ fixe + 15k€ variable"
                ),
                height=160,
                key="fix_comments",
            )

            if st.button("🔄 Régénérer avec les corrections", type="primary", key="fix_regenerate"):
                if not user_corrections.strip():
                    st.warning("Écris tes corrections avant de régénérer.")
                elif not st.session_state.get("dossier_html_pages12"):
                    st.error("Génère d'abord un dossier.")
                else:
                    with st.status("Révision en cours…", expanded=True) as rev_status:
                        try:
                            html_p12 = st.session_state["dossier_html_pages12"]
                            pdf_bytes_rev = st.session_state.get("dossier_pdf_bytes", b"")

                            revision_user_prompt = (
                                f"CORRECTIONS DEMANDÉES :\n{user_corrections.strip()}\n\n"
                                "PAGES 1 ET 2 ACTUELLES (HTML à corriger) :\n"
                                f"{html_p12}"
                            )

                            claude_client_rev = Anthropic(api_key=claude_api_key)
                            rev_response = claude_client_rev.messages.create(
                                model="claude-sonnet-4-20250514",
                                max_tokens=8000,
                                system=REVISION_SYSTEM_PROMPT,
                                messages=[{"role": "user", "content": revision_user_prompt}],
                                timeout=240.0,
                            )
                            revised = rev_response.content[0].text
                            revised = re.sub(r"^```[^\n]*\n", "", revised)
                            revised = re.sub(r"\n```\s*$", "", revised.strip())

                            # Sauvegarde pages 1+2 révisées pour révisions futures
                            # AVANT injection logo/LinkedIn pour garder les placeholders intacts
                            # et éviter le timeout lié au payload base64 logo
                            st.session_state["dossier_html_pages12"] = revised

                            # Re-injection logo
                            logo_b64_rev = st.session_state["dossier_logo_b64"]
                            revised = revised.replace(
                                'src="LOGO_PLACEHOLDER"',
                                f'src="data:image/png;base64,{logo_b64_rev}"',
                            )

                            # Re-injection LinkedIn
                            li_url = st.session_state.get("dossier_linkedin", "")
                            if li_url.strip():
                                li_div = (
                                    '<div class="contact-item">'
                                    '<i class="fa-brands fa-linkedin-in"></i> '
                                    f'<a href="{li_url.strip()}" target="_blank">Profil LinkedIn</a>'
                                    '</div>'
                                )
                            else:
                                li_div = ""
                            revised = revised.replace("LINKEDIN_CONTACT_ITEM_PLACEHOLDER", li_div)

                            # Re-append pages CV
                            if pdf_bytes_rev:
                                pdf_doc_rev = fitz.open(stream=pdf_bytes_rev, filetype="pdf")
                                cv_imgs = ""
                                mat_rev = fitz.Matrix(150 / 72, 150 / 72)
                                for pn in range(len(pdf_doc_rev)):
                                    pix_rev = pdf_doc_rev[pn].get_pixmap(matrix=mat_rev)
                                    i64 = base64.b64encode(pix_rev.tobytes("png")).decode()
                                    cv_imgs += (
                                        '<div class="page" style="padding:0;overflow:hidden;">'
                                        f'<img src="data:image/png;base64,{i64}" '
                                        'style="width:210mm;height:297mm;object-fit:contain;display:block;margin:0;" />'
                                        '</div>\n'
                                    )
                                pdf_doc_rev.close()
                                revised = revised.replace("</body>", f"{cv_imgs}</body>", 1)

                            # Re-injection bouton print
                            print_btn = st.session_state.get("_print_button_html", "")
                            if print_btn:
                                revised = revised.replace("<body>", f"<body>\n{print_btn}", 1)

                            st.session_state["dossier_html"] = revised
                            rev_status.update(label="✅ Dossier révisé !", state="complete")
                            st.rerun()

                        except Exception as rev_e:
                            rev_status.update(label="❌ Erreur", state="error")
                            st.error(f"Erreur : {rev_e}")
