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

leonar_api_key = os.getenv("LEONAR_API_KEY")
claude_api_key = os.getenv("CLAUDE_API_KEY")

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.title("⚙️ Paramètres")
    source_type = st.selectbox(
        "Source de recherche",
        ["leonar_source", "linkedin", "base_crm"],
        format_func=lambda x: {
            "leonar_source": "🔍 Leonar Source (base scrapée)",
            "linkedin": "🔗 LinkedIn (recherche live)",
            "base_crm": "📂 Base CRM Leonar (tes contacts)"
        }[x]
    )
    max_profiles = st.slider("Nombre max de profils à analyser", 25, 1000, 100, step=25)
    score_threshold = st.slider("Score minimum à afficher", 0, 10, 6)
    
    st.divider()
    st.caption(f"💰 Coût estimé : ~{max_profiles * 0.002:.2f}€")

LEONAR_BASE = "https://app.leonar.app/api/v1"

def leonar_headers():
    return {"Authorization": f"Bearer {leonar_api_key}", "Content-Type": "application/json"}

# ============================================================
# FONCTIONS LEONAR API
# ============================================================
def sourcing_search(project_id, filters, source, page=1, page_size=25):
    """Recherche sourcing (leonar_source ou linkedin)"""
    payload = {
        "project_id": project_id,
        "source_type": source,
        "filters": filters,
        "page_size": page_size,
    }
    if page > 1:
        payload["page"] = page
    resp = requests.post(f"{LEONAR_BASE}/sourcing/search", headers=leonar_headers(), json=payload)
    resp.raise_for_status()
    return resp.json()["data"]

def crm_search(query, location=None, contact_types=None, limit=25, offset=0):
    """Recherche dans la base CRM Leonar (contacts existants)"""
    payload = {
        "query": query,
        "limit": limit,
        "offset": offset,
        "sort_by": "updated_at",
        "sort_direction": "desc"
    }
    if location:
        payload["location"] = location
    if contact_types:
        payload["contact_types"] = contact_types
    resp = requests.post(f"{LEONAR_BASE}/contacts/search", headers=leonar_headers(), json=payload)
    resp.raise_for_status()
    return resp.json()

def add_profiles_to_project(project_id, profiles):
    """Ajoute des profils sourcés à un projet"""
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

def add_note_to_contact(contact_id, content):
    """Ajoute une note à un contact"""
    resp = requests.post(
        f"{LEONAR_BASE}/contacts/{contact_id}/notes",
        headers=leonar_headers(),
        json={"content": content}
    )
    resp.raise_for_status()
    return resp.json()["data"]

def add_tag_to_contact(contact_id, tag_id):
    """Ajoute un tag à un contact"""
    resp = requests.post(
        f"{LEONAR_BASE}/contacts/{contact_id}/tags",
        headers=leonar_headers(),
        json={"tag_id": tag_id}
    )
    resp.raise_for_status()
    return resp.json()["data"]

def get_tags():
    """Récupère les tags existants"""
    resp = requests.get(f"{LEONAR_BASE}/tags", headers=leonar_headers())
    resp.raise_for_status()
    return resp.json()["data"]

def create_tag(name, color, scope="contact"):
    """Crée un tag"""
    resp = requests.post(
        f"{LEONAR_BASE}/tags",
        headers=leonar_headers(),
        json={"name": name, "color": color, "scope": scope}
    )
    resp.raise_for_status()
    return resp.json()["data"]

# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================
def deduplicate_profiles(profiles):
    """Dédoublonne par linkedin_url puis par nom complet"""
    seen_urls = set()
    seen_names = set()
    unique = []
    for p in profiles:
        url = p.get("linkedin_url") or p.get("linkedin_profile") or ""
        name = f"{p.get('first_name', '').lower().strip()} {p.get('last_name', '').lower().strip()}"
        
        if url and url in seen_urls:
            continue
        if name and name in seen_names:
            continue
        
        if url:
            seen_urls.add(url)
        if name:
            seen_names.add(name)
        unique.append(p)
    return unique

def filter_by_location(profiles, region):
    """Filtre les profils par localisation"""
    if not region:
        return profiles, []
    
    region_lower = region.lower().strip()
    region_terms = [t.strip() for t in region_lower.replace(",", " ").split() if len(t.strip()) > 2]
    
    matched = []
    excluded = []
    for p in profiles:
        loc = (p.get("location") or "").lower()
        if any(term in loc for term in region_terms):
            matched.append(p)
        elif not loc:
            matched.append(p)
        else:
            excluded.append(p)
    
    return matched, excluded

def normalize_crm_to_sourcing(contacts):
    """Convertit le format CRM vers le format profil sourcing"""
    profiles = []
    for c in contacts:
        profile = {
            "profile_id": c.get("id", ""),
            "first_name": c.get("first_name", ""),
            "last_name": c.get("last_name", ""),
            "headline": c.get("title", ""),
            "location": c.get("location", ""),
            "linkedin_url": c.get("linkedin_profile", ""),
            "existing_contact_id": c.get("id"),
            "experiences": [],
            "source": "crm"
        }
        if c.get("title") and c.get("current_company"):
            profile["experiences"] = [{
                "title": c["title"],
                "company_name": c["current_company"],
                "is_current": True
            }]
        profiles.append(profile)
    return profiles

# ============================================================
# FONCTIONS CLAUDE API
# ============================================================
def extract_search_criteria(claude_client, job_desc, transcript, region, seniority):
    """Claude extrait les critères de recherche structurés depuis le brief"""
    prompt = f"""Tu es un recruteur expert en finance. À partir du brief ci-dessous, extrais les critères de recherche structurés pour une recherche de candidats.

DESCRIPTIF DE POSTE :
{job_desc}

RETRANSCRIPTION BRIEF MANAGER :
{transcript}

RÉGION : {region}
SÉNIORITÉ : {seniority}

Réponds UNIQUEMENT en JSON valide avec cette structure :
{{
    "job_titles": ["titre1", "titre2", "titre3"],
    "locations": {{
        "countries": ["pays"],
        "regions": ["région si pertinent"]
    }},
    "years_experience": {{
        "min": X,
        "max": Y
    }},
    "keywords": ["mot-clé1", "mot-clé2"],
    "industries": ["secteur1", "secteur2"],
    "summary": "Résumé en 2 lignes du profil recherché"
}}

Sois précis sur les titres de poste — inclus les variantes courantes (FR et EN)."""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    text = response.content[0].text
    text = text.strip()
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
            for exp in p["experiences"][:3]:
                current = " (actuel)" if exp.get("is_current") else ""
                experiences += f"  - {exp.get('title', 'N/A')} @ {exp.get('company_name', 'N/A')}{current}\n"
        
        profiles_text += f"""
--- PROFIL {i+1} (ID: {p.get('profile_id', 'N/A')}) ---
Nom: {p.get('first_name', '')} {p.get('last_name', '')}
Titre: {p.get('headline', 'N/A')}
Localisation: {p.get('location', 'N/A')}
Expériences:
{experiences}
"""

    exclusions_text = ""
    if exclusions:
        exclusions_text = f"""
MOTS-CLÉS D'EXCLUSION (profils à pénaliser fortement si ces termes apparaissent) :
{', '.join(exclusions)}
"""

    prompt = f"""Tu es un recruteur expert en finance. Score chaque profil de 0 à 10 par rapport au poste.

DESCRIPTIF DE POSTE :
{job_desc}

BRIEF MANAGER :
{transcript}

RÉSUMÉ DES CRITÈRES : {criteria_summary}

RÉGION CIBLE : {region}
{exclusions_text}
PROFILS À SCORER :
{profiles_text}

Pour chaque profil, réponds UNIQUEMENT en JSON (array) :
[
    {{
        "profile_id": "id du profil",
        "score": X,
        "justification": "Explication en 1-2 lignes max"
    }}
]

RÈGLES DE SCORING STRICTES :
- Si le profil contient un mot-clé d'exclusion (titre, entreprise, secteur), score MAX 2/10
- 8-10 : Match excellent, correspond parfaitement au brief
- 6-7 : Bon match, quelques écarts mineurs
- 4-5 : Match partiel, profil intéressant mais des lacunes
- 0-3 : Peu pertinent ou mot-clé exclu

Sois exigeant et différenciant dans tes scores."""

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
# INTERFACE PRINCIPALE
# ============================================================
st.title("🎯 Leonar Scoring Tool")
st.caption("Recherche automatisée + scoring intelligent des profils candidats")

if not leonar_api_key or not claude_api_key:
    st.error("⚠️ Clés API manquantes. Remplis le fichier .env avec LEONAR_API_KEY et CLAUDE_API_KEY.")
    st.stop()

# ============================================================
# ÉTAPE 1 — SAISIE DU BRIEF
# ============================================================
st.header("1️⃣ Brief du poste")

col1, col2 = st.columns(2)

with col1:
    job_desc = st.text_area(
        "Descriptif de poste",
        height=250,
        placeholder="Colle ici le descriptif du poste (missions, compétences, formation...)"
    )

with col2:
    transcript = st.text_area(
        "Retranscription brief manager",
        height=250,
        placeholder="Colle ici la retranscription de ton échange avec le manager..."
    )

col3, col4 = st.columns(2)
with col3:
    region = st.text_input("Région / Localisation", placeholder="Ex: Paris, Île-de-France, Lyon...")
with col4:
    seniority = st.text_input("Séniorité (années d'expérience)", placeholder="Ex: 5-10 ans")

exclusion_keywords = st.text_area(
    "🚫 Mots-clés à exclure (NOT)",
    height=80,
    placeholder="Un mot-clé par ligne. Ex:\ncabinet d'audit\nconseil\nintérim"
)

# ============================================================
# ÉTAPE 2 — EXTRACTION DES CRITÈRES
# ============================================================
st.header("2️⃣ Extraction des critères de recherche")

if st.button("🔍 Analyser le brief et extraire les critères", type="primary"):
    if not job_desc:
        st.error("Le descriptif de poste est obligatoire.")
    else:
        with st.spinner("Claude analyse le brief..."):
            try:
                claude_client = Anthropic(api_key=claude_api_key)
                criteria = extract_search_criteria(claude_client, job_desc, transcript, region, seniority)
                st.session_state["criteria"] = criteria
                st.session_state["scoring_done"] = False  # Reset si on relance
                st.success("Critères extraits !")
            except Exception as e:
                st.error(f"Erreur : {e}")

if "criteria" in st.session_state:
    criteria = st.session_state["criteria"]
    
    st.subheader("Critères extraits (modifiables)")
    
    col_a, col_b = st.columns(2)
    with col_a:
        edited_titles = st.text_area(
            "Titres de poste recherchés",
            value="\n".join(criteria.get("job_titles", [])),
            height=100
        )
        edited_keywords = st.text_area(
            "Mots-clés",
            value="\n".join(criteria.get("keywords", [])),
            height=100
        )
    with col_b:
        edited_industries = st.text_area(
            "Secteurs",
            value="\n".join(criteria.get("industries", [])),
            height=100
        )
        exp_min = st.number_input("Expérience min (années)", value=criteria.get("years_experience", {}).get("min", 0))
        exp_max = st.number_input("Expérience max (années)", value=criteria.get("years_experience", {}).get("max", 15))
    
    exclusion_list = [k.strip() for k in exclusion_keywords.split("\n") if k.strip()]
    if exclusion_list:
        st.warning(f"🚫 Exclusions actives : {', '.join(exclusion_list)}")
    
    st.info(f"📋 Résumé : {criteria.get('summary', '')}")
    
    criteria["job_titles"] = [t.strip() for t in edited_titles.split("\n") if t.strip()]
    criteria["keywords"] = [k.strip() for k in edited_keywords.split("\n") if k.strip()]
    criteria["industries"] = [i.strip() for i in edited_industries.split("\n") if i.strip()]
    criteria["years_experience"] = {"min": int(exp_min), "max": int(exp_max)}

    # ============================================================
    # ÉTAPE 3 — PROJET & RECHERCHE
    # ============================================================
    st.header("3️⃣ Recherche & Scoring")
    
    st.subheader("Projet Leonar")
    st.caption("💡 Copie l'ID du projet depuis l'URL Leonar (ex: https://app.leonar.app/projects/**ID_ICI**)")
    selected_project_id = st.text_input("ID du projet", placeholder="Ex: 550e8400-e29b-41d4-a716-446655440000")

    # ============================================================
    # LANCEMENT RECHERCHE + SCORING
    # ============================================================
    if selected_project_id and st.button("🚀 Lancer la recherche et le scoring", type="primary"):
        
        all_profiles = []
        
        # ---- PHASE 1 : RECHERCHE ----
        st.subheader("Phase 1 — Recherche des profils")
        progress_bar = st.progress(0, text="Recherche en cours...")
        
        try:
            if source_type == "base_crm":
                search_query = " ".join(criteria["job_titles"][:3])
                offset = 0
                
                while len(all_profiles) < max_profiles:
                    results = crm_search(
                        query=search_query,
                        location=region,
                        contact_types=["candidate"],
                        limit=25,
                        offset=offset
                    )
                    
                    contacts = results.get("data", [])
                    if not contacts:
                        break
                    
                    profiles = normalize_crm_to_sourcing(contacts)
                    all_profiles.extend(profiles)
                    
                    total = results.get("meta", {}).get("total", len(all_profiles))
                    progress = min(len(all_profiles) / max_profiles, 1.0)
                    progress_bar.progress(progress, text=f"{len(all_profiles)} contacts récupérés sur {total} disponibles")
                    
                    if not results.get("meta", {}).get("has_more", False):
                        break
                    
                    offset += 25
                    time.sleep(0.3)
            else:
                filters = {
                    "job_titles": {"include": criteria["job_titles"]},
                    "years_experience": criteria["years_experience"]
                }
                
                locations = criteria.get("locations", {})
                if locations.get("countries"):
                    filters["locations"] = {"countries": locations["countries"]}
                
                page = 1
                while len(all_profiles) < max_profiles:
                    results = sourcing_search(
                        project_id=selected_project_id,
                        filters=filters,
                        source=source_type,
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
            
            all_profiles = all_profiles[:max_profiles]
            progress_bar.progress(1.0, text=f"✅ {len(all_profiles)} profils récupérés")
            
        except Exception as e:
            st.error(f"Erreur recherche : {e}")
            st.stop()
        
        if not all_profiles:
            st.warning("Aucun profil trouvé. Essaie d'élargir tes critères.")
            st.stop()
        
        # ---- DÉDOUBLONNAGE ----
        before_dedup = len(all_profiles)
        all_profiles = deduplicate_profiles(all_profiles)
        after_dedup = len(all_profiles)
        if before_dedup > after_dedup:
            st.info(f"🔄 {before_dedup - after_dedup} doublons supprimés ({before_dedup} → {after_dedup} profils)")
        
        # ---- EXCLUSION DES PROFILS DÉJÀ DANS LE PROJET ----
        with st.spinner("Vérification des profils déjà dans le projet..."):
            try:
                existing_entries = get_project_entries(selected_project_id)
                if existing_entries:
                    all_profiles, skipped = exclude_existing_profiles(all_profiles, existing_entries)
                    if skipped > 0:
                        st.info(f"♻️ {skipped} profils déjà dans le projet retirés")
            except Exception as e:
                st.warning(f"Impossible de vérifier les profils existants : {e}")
        
        # ---- FILTRE LOCALISATION ----
        if region and source_type != "base_crm":
            all_profiles, excluded = filter_by_location(all_profiles, region)
            if excluded:
                st.info(f"📍 {len(excluded)} profils hors {region} retirés avant scoring")
        
        if not all_profiles:
            st.warning("Aucun profil restant après filtrage. Essaie d'élargir la région.")
            st.stop()
        
        # ---- PHASE 2 : SCORING ----
        st.subheader("Phase 2 — Scoring des profils")
        
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
        
        # Fusionner profils + scores
        scores_map = {s["profile_id"]: s for s in all_scores}
        
        scored_profiles = []
        for p in all_profiles:
            pid = p.get("profile_id", "")
            score_data = scores_map.get(pid, {"score": 0, "justification": "Non scoré"})
            scored_profiles.append({
                **p,
                "score": score_data["score"],
                "justification": score_data["justification"]
            })
        
        scored_profiles.sort(key=lambda x: x["score"], reverse=True)
        
        # Persister dans session_state
        st.session_state["scored_profiles"] = scored_profiles
        st.session_state["selected_project_id"] = selected_project_id
        st.session_state["scoring_done"] = True

    # ============================================================
    # AFFICHAGE DES RÉSULTATS (persiste après clic)
    # ============================================================
    if st.session_state.get("scoring_done"):
        scored_profiles = st.session_state["scored_profiles"]
        
        visible_profiles = [p for p in scored_profiles if p["score"] >= score_threshold]
        hidden_count = len(scored_profiles) - len(visible_profiles)
        
        st.subheader(f"Résultats — {len(visible_profiles)} profils ≥ {score_threshold}/10")
        if hidden_count > 0:
            st.caption(f"({hidden_count} profils sous le seuil masqués)")
        
        for p in visible_profiles:
            score = p["score"]
            if score >= 8:
                emoji = "🟢"
            elif score >= 6:
                emoji = "🟡"
            elif score >= 4:
                emoji = "🟠"
            else:
                emoji = "🔴"
            
            with st.expander(f"{emoji} **{score}/10** — {p.get('first_name', '')} {p.get('last_name', '')} | {p.get('headline', '')}"):
                st.write(f"📍 {p.get('location', 'N/A')}")
                st.write(f"💬 {p['justification']}")
                if p.get("linkedin_url"):
                    st.write(f"🔗 [LinkedIn]({p['linkedin_url']})")
                else:
                    st.write("⚠️ Pas de profil LinkedIn")
                if p.get("experiences"):
                    for exp in p["experiences"][:3]:
                        current = " ✅" if exp.get("is_current") else ""
                        st.write(f"  • {exp.get('title', '')} @ {exp.get('company_name', '')}{current}")
                else:
                    st.write("⚠️ Aucune expérience renseignée")

        # ============================================================
        # ÉTAPE 4 — PUSH DANS LEONAR
        # ============================================================
        st.header("4️⃣ Envoyer dans Leonar")
        
        min_score_push = st.slider(
            "Score minimum pour ajouter au projet",
            min_value=0, max_value=10, value=score_threshold,
            help="Seuls les profils au-dessus de ce score seront ajoutés à Leonar"
        )
        
        profiles_to_push = [p for p in scored_profiles if p["score"] >= min_score_push]
        st.info(f"{len(profiles_to_push)} profils seront ajoutés au projet (score ≥ {min_score_push}/10)")
        
        if st.button(f"📤 Ajouter {len(profiles_to_push)} profils dans Leonar", type="primary"):
            project_id = st.session_state.get("selected_project_id")
            push_progress = st.progress(0, text="Ajout en cours...")
            
            try:
                crm_profiles = [p for p in profiles_to_push if p.get("source") == "crm"]
                sourced_profiles = [p for p in profiles_to_push if p.get("source") != "crm"]
                
                added_total = 0
                contact_ids = []
                
                # --- Profils sourcés : ajout via sourcing API ---
                if sourced_profiles:
                    profiles_payload = []
                    for p in sourced_profiles:
                        profile_data = {
                            "profile_id": p.get("profile_id"),
                            "first_name": p.get("first_name", ""),
                            "last_name": p.get("last_name", ""),
                            "linkedin_url": p.get("linkedin_url", ""),
                        }
                        if p.get("experiences"):
                            profile_data["experiences"] = p["experiences"]
                        profiles_payload.append(profile_data)
                    
                    for i in range(0, len(profiles_payload), 10):
                        batch = profiles_payload[i:i+10]
                        result = add_profiles_to_project(project_id, batch)
                        added_total += result.get("added", 0)
                        contact_ids.extend(result.get("contact_ids", []))
                        
                        progress = min((i + 10) / len(profiles_payload), 0.5)
                        push_progress.progress(progress, text=f"{added_total} profils ajoutés...")
                        time.sleep(0.5)
                
                # --- Profils CRM : déjà dans Leonar ---
                for p in crm_profiles:
                    if p.get("existing_contact_id"):
                        contact_ids.append(p["existing_contact_id"])
                        added_total += 1
                
                push_progress.progress(0.5, text=f"✅ {added_total} profils traités. Ajout des notes...")
                
                # --- Notes de scoring ---
                all_push_profiles = sourced_profiles + crm_profiles
                for idx, contact_id in enumerate(contact_ids):
                    if idx < len(all_push_profiles):
                        p = all_push_profiles[idx]
                        note_content = f"🎯 Score : {p['score']}/10\n💬 {p['justification']}"
                        try:
                            add_note_to_contact(contact_id, note_content)
                        except Exception:
                            pass
                        
                        progress = 0.5 + (0.5 * (idx + 1) / len(contact_ids))
                        push_progress.progress(min(progress, 1.0), text=f"Notes : {idx+1}/{len(contact_ids)}")
                        time.sleep(0.3)
                
                push_progress.progress(1.0, text="✅ Terminé")
                st.success(f"🎉 {added_total} profils ajoutés avec leurs scores dans Leonar.")
                st.balloons()
                
            except Exception as e:
                st.error(f"Erreur lors de l'ajout : {e}")
