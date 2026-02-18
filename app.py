import streamlit as st
import requests
import json
import time
import os
from anthropic import Anthropic
from dotenv import load_dotenv

# ============================================================
# CONFIG
# ============================================================
load_dotenv()
st.set_page_config(page_title="Leonar Scoring Tool", layout="wide")

# Support both .env (local) and st.secrets (Streamlit Cloud)
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

# ============================================================
# LEONAR API
# ============================================================
def get_connected_accounts():
    """Récupère les comptes LinkedIn connectés"""
    resp = requests.get(f"{LEONAR_BASE}/connected-accounts", headers=leonar_headers())
    resp.raise_for_status()
    return resp.json()["data"]

def sourcing_search(project_id, filters, source_type, page=1, page_size=25, account_id=None, linkedin_api_type=None):
    """Recherche sourcing unifiée"""
    payload = {
        "project_id": project_id,
        "source_type": source_type,
        "filters": filters,
        "page": page,
        "page_size": page_size,
    }
    if account_id:
        payload["account_id"] = account_id
    if linkedin_api_type:
        payload["linkedin_api_type"] = linkedin_api_type
    
    resp = requests.post(f"{LEONAR_BASE}/sourcing/search", headers=leonar_headers(), json=payload)
    resp.raise_for_status()
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
        resp.raise_for_status()
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
        name = f"{p.get('first_name', '').lower().strip()} {p.get('last_name', '').lower().strip()}"
        
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
        name = f"{contact.get('first_name', '').lower().strip()} {contact.get('last_name', '').lower().strip()}"
        url = contact.get("linkedin_profile", "") or ""
        if name.strip():
            existing_names.add(name)
        if url:
            existing_urls.add(url)
    
    new_profiles = []
    skipped = 0
    for p in profiles:
        name = f"{p.get('first_name', '').lower().strip()} {p.get('last_name', '').lower().strip()}"
        url = p.get("linkedin_url", "") or ""
        
        if (url and url in existing_urls) or (name.strip() and name in existing_names):
            skipped += 1
            continue
        new_profiles.append(p)
    
    return new_profiles, skipped

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
        "cities": ["ville1", "ville2"]
    }},
    "years_experience": {{
        "min": X,
        "max": Y
    }},
    "skills": {{
        "include": ["compétence1", "compétence2"],
        "require_all": false
    }},
    "keywords": {{
        "include": ["mot-clé1"],
        "exclude": ["mot-clé à exclure"]
    }},
    "industries": ["secteur1", "secteur2"],
    "summary": "Résumé en 2 lignes du profil recherché"
}}

Sois précis sur les titres de poste — inclus les variantes FR et EN.
Pour les villes, mets les villes de la région mentionnée (ex: Île-de-France → Paris, Nanterre, Boulogne-Billancourt, La Défense, etc.)
Pour les skills, extrais les compétences techniques et métier mentionnées."""

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
    """Claude score un lot de profils contre le brief — version enrichie"""
    profiles_text = ""
    for i, p in enumerate(profiles):
        # Expériences
        experiences = ""
        if p.get("experiences"):
            for exp in p["experiences"][:4]:
                current = " (actuel)" if exp.get("is_current") else ""
                period = ""
                if exp.get("start_date"):
                    period = f" [{exp.get('start_date', '')} → {exp.get('end_date', 'présent')}]"
                experiences += f"  - {exp.get('title', 'N/A')} @ {exp.get('company_name', 'N/A')}{current}{period}\n"
        
        # Formation
        education = ""
        if p.get("educations"):
            for edu in p["educations"][:2]:
                education += f"  - {edu.get('diploma', '')} {edu.get('specialization', '')} @ {edu.get('educational_establishment', '')}\n"
        
        # Skills
        skills = ", ".join(p.get("skills", [])[:10]) if p.get("skills") else "N/A"
        
        profiles_text += f"""
--- PROFIL {i+1} (ID: {p.get('profile_id', 'N/A')}) ---
Nom: {p.get('first_name', '')} {p.get('last_name', '')}
Titre: {p.get('headline', 'N/A')}
Localisation: {p.get('location', 'N/A')}
Années d'expérience: {p.get('total_years_experience', 'N/A')}
Résumé: {(p.get('summary') or 'N/A')[:200]}
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

Utilise TOUTES les données disponibles (skills, formation, parcours complet, résumé) pour scorer précisément. Sois exigeant et différenciant."""

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
        ["leonar_source", "linkedin_unipile", "contacts"],
        format_func=lambda x: {
            "leonar_source": "🔍 Leonar Source",
            "linkedin_unipile": "🔗 LinkedIn Recruiter",
            "contacts": "📂 Contacts CRM"
        }[x]
    )
    
    # Si LinkedIn, charger les comptes connectés
    linkedin_account_id = None
    if source_type == "linkedin_unipile":
        try:
            accounts = get_connected_accounts()
            if accounts:
                account_names = {f"{a['name']} ({a.get('license_type', '')})": a["id"] for a in accounts}
                selected_account = st.selectbox("Compte LinkedIn", list(account_names.keys()))
                linkedin_account_id = account_names[selected_account]
                
                # Options LinkedIn
                st.subheader("Options LinkedIn")
                filter_open_to_work = st.checkbox("Open to Work uniquement", value=False)
                connection_degrees = st.multiselect("Degré de connexion", [1, 2, 3], default=[2, 3])
            else:
                st.warning("Aucun compte LinkedIn connecté dans Leonar.")
        except Exception as e:
            st.error(f"Erreur comptes LinkedIn : {e}")
    
    st.divider()
    max_profiles = st.slider("Profils max à analyser", 25, 1000, 100, step=25)
    score_threshold = st.slider("Score minimum à afficher", 0, 10, 6)
    
    st.divider()
    st.caption(f"💰 Coût scoring estimé : ~{max_profiles * 0.002:.2f}€")

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
    region = st.text_input("Région / Localisation", placeholder="Ex: Paris, Île-de-France, Lyon...")
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
        edited_skills = st.text_area(
            "🛠 Compétences",
            value="\n".join(criteria.get("skills", {}).get("include", [])),
            height=100
        )
        edited_cities = st.text_area(
            "📍 Villes",
            value="\n".join(criteria.get("locations", {}).get("cities", [])),
            height=80
        )
    with col_c:
        edited_companies_exclude = st.text_area(
            "🚫 Entreprises à exclure",
            value="\n".join(criteria.get("companies", {}).get("exclude", [])),
            height=100
        )
        exp_min = st.number_input("XP min", value=criteria.get("years_experience", {}).get("min", 0))
        exp_max = st.number_input("XP max", value=criteria.get("years_experience", {}).get("max", 15))
    
    exclusion_list = [k.strip() for k in exclusion_keywords.split("\n") if k.strip()]
    
    st.info(f"📋 {criteria.get('summary', '')}")
    
    # Construire les filtres mis à jour
    def build_filters():
        filters = {}
        
        # Titres
        titles_inc = [t.strip() for t in edited_titles_include.split("\n") if t.strip()]
        titles_exc = [t.strip() for t in edited_titles_exclude.split("\n") if t.strip()]
        if titles_inc or titles_exc:
            filters["job_titles"] = {}
            if titles_inc:
                filters["job_titles"]["include"] = titles_inc
                filters["job_titles"]["include_current_only"] = True
            if titles_exc:
                filters["job_titles"]["exclude"] = titles_exc
        
        # Compétences
        skills_list = [s.strip() for s in edited_skills.split("\n") if s.strip()]
        if skills_list:
            filters["skills"] = {"include": skills_list, "require_all": False}
        
        # Localisation
        cities = [c.strip() for c in edited_cities.split("\n") if c.strip()]
        countries = criteria.get("locations", {}).get("countries", ["France"])
        filters["locations"] = {"countries": countries}
        if cities:
            filters["locations"]["cities"] = cities
        
        # Expérience
        filters["years_experience"] = {"min": int(exp_min), "max": int(exp_max)}
        
        # Entreprises à exclure
        companies_exc = [c.strip() for c in edited_companies_exclude.split("\n") if c.strip()]
        if companies_exc:
            filters["companies"] = {"exclude": companies_exc}
        
        # Keywords exclude (depuis champ exclusion supplémentaire)
        if exclusion_list:
            filters["keywords"] = {"exclude": exclusion_list}
        
        return filters

    # ============================================================
    # ÉTAPE 3 — RECHERCHE & SCORING
    # ============================================================
    st.header("3️⃣ Recherche & Scoring")
    
    st.subheader("Projet Leonar")
    st.caption("💡 Copie l'ID depuis l'URL Leonar : app.leonar.app/projects/**ID_ICI**")
    selected_project_id = st.text_input("ID du projet", placeholder="550e8400-e29b-41d4-a716-...")

    if selected_project_id and st.button("🚀 Lancer recherche + scoring", type="primary"):
        
        filters = build_filters()
        all_profiles = []
        
        # ---- PHASE 1 : RECHERCHE ----
        st.subheader("Phase 1 — Recherche")
        progress_bar = st.progress(0, text="Recherche en cours...")
        
        try:
            # Params LinkedIn
            extra_kwargs = {}
            if source_type == "linkedin_unipile":
                extra_kwargs["account_id"] = linkedin_account_id
                extra_kwargs["linkedin_api_type"] = "recruiter"
                
                # Filtres LinkedIn spécifiques
                linkedin_filters = {}
                if 'filter_open_to_work' in dir() and filter_open_to_work:
                    linkedin_filters["is_open_to_work"] = True
                if 'connection_degrees' in dir() and connection_degrees:
                    linkedin_filters["connection_degree"] = connection_degrees
                if linkedin_filters:
                    filters["linkedin_filters"] = linkedin_filters
            
            # Contacts : ajouter filtres spécifiques
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
                    page_size=25,
                    **extra_kwargs
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
            
            all_profiles = all_profiles[:max_profiles]
            
            if results.get("filters_too_strict"):
                st.warning("⚠️ Leonar indique que les filtres sont trop stricts. Essaie d'élargir.")
            
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
        
        if not all_profiles:
            st.warning("Tous les profils sont déjà dans le projet.")
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
    # RÉSULTATS (persiste)
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
            
            # Ligne de résumé
            skills_preview = ", ".join(p.get("skills", [])[:5]) if p.get("skills") else ""
            xp = f" | {p.get('total_years_experience', '?')} ans XP" if p.get("total_years_experience") else ""
            
            with st.expander(f"{emoji} **{score}/10** — {p.get('first_name', '')} {p.get('last_name', '')} | {p.get('headline', '')}{xp}"):
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
                
                # Préparer les profils avec toutes les données enrichies
                profiles_payload = []
                for p in profiles_to_push:
                    profile_data = {
                        "profile_id": p.get("profile_id"),
                        "first_name": p.get("first_name", ""),
                        "last_name": p.get("last_name", ""),
                        "headline": p.get("headline", ""),
                        "linkedin_url": p.get("linkedin_url", ""),
                        "location": p.get("location", ""),
                        "summary": p.get("summary", ""),
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
                
                # Ajout par lots de 50 (max API = 100)
                for i in range(0, len(profiles_payload), 50):
                    batch = profiles_payload[i:i+50]
                    result = add_profiles_to_project(project_id, batch)
                    added_total += result.get("added", 0)
                    contact_ids.extend(result.get("contact_ids", []))
                    
                    progress = min((i + 50) / len(profiles_payload), 0.5)
                    push_progress.progress(progress, text=f"{added_total} profils ajoutés...")
                    time.sleep(0.5)
                
                push_progress.progress(0.5, text=f"✅ {added_total} profils ajoutés. Notes...")
                
                # Notes de scoring
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
