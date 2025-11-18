import os
import csv
import random
import json
import re
import PyPDF2
import docx
import requests
from flask import Flask, request, render_template, redirect, url_for
from werkzeug.utils import secure_filename
import spacy
from difflib import SequenceMatcher

app = Flask(__name__, template_folder='templates', static_folder='static')

UPLOAD_DIR = 'uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CRED_FILE = os.path.join(BASE_DIR, "cred.csv")
PARSED_JSON = os.path.join(BASE_DIR, "parsed_resumes.json")

nlp = spacy.load("en_core_web_sm")

APP_ID = "14b53f83"
APP_KEY = "6685c7d0768db2715c56805048ef559e"


def normalize_text(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s\-\_\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fuzzy_ratio(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def delete_old_user_resumes(uid):
    for f in os.listdir(UPLOAD_DIR):
        if f.startswith(uid + "_"):
            try:
                os.remove(os.path.join(UPLOAD_DIR, f))
            except:
                pass


def extract_text_from_resume(file_path):
    ext = os.path.splitext(file_path)[-1].lower()

    if ext == ".pdf":
        try:
            reader = PyPDF2.PdfReader(open(file_path, "rb"))
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
        except:
            return ""

    elif ext in [".docx", ".doc"]:
        try:
            docf = docx.Document(file_path)
            return "\n".join([p.text for p in docf.paragraphs])
        except:
            return ""

    else:
        try:
            return open(file_path, "r", encoding="utf-8").read()
        except:
            return ""


ROLE_SKILL_MAP = {
    "data scientist": ["pandas", "numpy", "statistics", "ml", "model", "prediction", "scikit", "regression"],
    "ml engineer": ["machine learning", "training", "pipeline", "deployment", "mlops", "model"],
    "deep learning engineer": ["deep learning", "cnn", "rnn", "lstm", "transformer", "pytorch", "tensorflow"],
    "nlp engineer": ["nlp", "bert", "token", "ner", "text analysis"],
    "llm engineer": ["llm", "langchain", "rag", "vector", "prompt"],
    "software developer": ["react", "node", "django", "java", "spring", "api", "frontend", "backend"],
    "data analyst": ["tableau", "power bi", "sql", "dashboard", "bi", "data viz"],

    "hotel": [
        "guest", "front desk", "housekeeping", "reservation", "check in", "check out",
        "room service", "f&b", "food", "beverage", "pms", "opera", "hotel", "event",
        "supervision", "hospitality"
    ],

    "teacher": ["classroom", "lesson", "curriculum", "student", "ctet", "teaching"],

    "doctor": [
        "mbbs", "medical", "medicine", "clinical", "hospital", "doctor", "physician",
        "medical officer", "resident doctor", "emergency", "emergency care", "treatment",
        "diagnosis", "patient examination", "case sheet", "ipd", "opd", "patient care",
        "vital signs", "ecg", "bls", "acls", "prescription", "medical record",
        "ward rounds", "monitoring", "infection control"
    ],

    "devops engineer": ["docker", "kubernetes", "aws", "gcp", "azure", "terraform"],
    "frontend developer": ["html", "css", "javascript", "react", "angular", "vue"],
    "backend developer": ["express", "django", "spring", "database", "sql"]
}

RELATED_ROLE_MAP = {
    "data scientist": ["ml engineer", "deep learning engineer"],
    "ml engineer": ["data scientist"],
    "deep learning engineer": ["ml engineer"],
    "teacher": ["curriculum designer"]
}


def detect_roles_from_skills(skills, min_keyword_matches=2):
    if not skills:
        return ["general"]

    skills_norm = [normalize_text(s) for s in skills if s]
    joined = " ".join(skills_norm)

    scores = {}
    for role, keywords in ROLE_SKILL_MAP.items():
        count = 0
        for kw in keywords:
            k = normalize_text(kw)
            if re.search(r'\b' + re.escape(k) + r'\b', joined):
                count += 1
            else:
                for s in skills_norm:
                    if fuzzy_ratio(k, s) >= 0.75:
                        count += 1
                        break
        scores[role] = count

    valid = [r for r, c in scores.items() if c >= min_keyword_matches]

    if not valid:
        valid = [r for r, c in scores.items() if c >= 1]

    valid_sorted = sorted(valid, key=lambda r: scores[r], reverse=True)

    final_roles = []
    for r in valid_sorted:
        if r not in final_roles:
            final_roles.append(r)
        for rr in RELATED_ROLE_MAP.get(r, []):
            if rr not in final_roles:
                final_roles.append(rr)

    return final_roles[:6] if final_roles else ["general"]


def simple_parse_resume_spacy(text):
    try:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [l.rstrip() for l in text.split("\n")]

        section_map = {
            "education": ["education", "academic", "qualifications"],
            "skills": ["skills", "technical skills", "skillset"],
            "experience": ["experience", "work experience", "employment"],
            "projects": ["projects", "personal projects"]
        }

        current = None
        sections = {"education": [], "skills": [], "experience": [], "projects": []}

        for line in lines:
            low = line.strip().lower()
            header_found = False

            for sec, keys in section_map.items():
                if any(k in low for k in keys):
                    current = sec
                    header_found = True
                    break
            if header_found:
                continue

            if line.strip().isupper() and len(line.strip()) < 60:
                if "skill" in line.lower():
                    current = "skills"
                elif "experience" in line.lower() or "work" in line.lower():
                    current = "experience"
                elif "education" in line.lower():
                    current = "education"
                elif "project" in line.lower():
                    current = "projects"
                else:
                    current = None
                continue

            if line.strip().endswith(":"):
                lbl = line.strip()[:-1].lower()
                if "skill" in lbl:
                    current = "skills"
                elif "experience" in lbl:
                    current = "experience"
                elif "education" in lbl:
                    current = "education"
                elif "project" in lbl:
                    current = "projects"
                else:
                    current = None
                continue

            if current and line.strip():
                sections[current].append(line.strip())

        def clean(arr):
            out = []
            for t in arr:
                parts = re.split(r"[;,•\-\n]+", t)
                for p in parts:
                    p = p.strip()
                    if p and len(p) > 1:
                        out.append(p)
            return out

        skills = clean(sections["skills"])
        education = clean(sections["education"])
        experience = clean(sections["experience"])
        projects = clean(sections["projects"])

        if not skills:
            m = re.search(r"skills?:\s*([^\n]{10,500})", text, flags=re.I)
            if m:
                found = [s.strip() for s in re.split(r"[;,•\n]+", m.group(1)) if s.strip()]
                skills.extend(found)

        if not skills:
            doc = nlp(text)
            for span in re.findall(r"([A-Za-z0-9\-\+\.\/ ]{2,60}(?:,|\s·\s|\s•\s))+", text):
                for part in re.split(r"[,•\s·]+", span):
                    p = part.strip()
                    if p:
                        skills.append(p)

            for chunk in doc.noun_chunks:
                ch = chunk.text.strip()
                if 2 <= len(ch) <= 60:
                    skills.append(ch)

            caps = re.findall(r"(?:[A-Z][a-z]{1,}\s){0,3}[A-Z][a-z]{1,}", text)
            for c in caps:
                if len(c.split()) <= 4:
                    skills.append(c.strip())

        cleaned_skills = []
        seen = set()
        for s in skills:
            s_clean = re.sub(r"^[\-\•\–\*]+\s*", "", s).strip()
            s_clean = re.sub(r"\s{2,}", " ", s_clean)
            if s_clean and s_clean.lower() not in seen:
                seen.add(s_clean.lower())
                cleaned_skills.append(s_clean)

        doc2 = nlp(text)
        name = None
        for ent in doc2.ents:
            if ent.label_ == "PERSON":
                name = ent.text.strip()
                break

        email = None
        m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
        if m:
            email = m.group(0)

        phone = None
        m = re.search(r"(\+?\d[\d\-\(\) ]{7,}\d)", text)
        if m:
            phone = m.group(1).strip()

        detected_roles = detect_roles_from_skills(cleaned_skills)

        return {
            "name": name,
            "email": email,
            "phone": phone,
            "education": education,
            "skills": cleaned_skills,
            "experience": experience,
            "projects": projects,
            "profession": detected_roles,
            "job_titles": detected_roles
        }

    except:
        return {
            "name": None,
            "email": None,
            "phone": None,
            "education": [],
            "skills": [],
            "experience": [],
            "projects": [],
            "profession": ["general"],
            "job_titles": []
        }


def store_resume_json(uid, parsed):
    # Load old JSON data safely
    if os.path.exists(PARSED_JSON):
        try:
            with open(PARSED_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = {}
    else:
        data = {}

    # Always store valid parsed resume, never empty
    if parsed and isinstance(parsed, dict) and len(parsed.keys()) > 0:
        data[str(uid)] = parsed
    else:
        data[str(uid)] = {"error": "Parsing failed"}

    # Now write safely back to file
    with open(PARSED_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)



def fetch_jobs(keyword):
    url = "https://api.adzuna.com/v1/api/jobs/gb/search/1"
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "results_per_page": 50,
        "what": keyword
    }
    try:
        r = requests.get(url, params=params)
        if r.status_code == 200:
            return r.json().get("results", [])
    except:
        pass

    return []



def simple_match_jobs(jobs, parsed):
    skills = [normalize_text(s) for s in parsed.get("skills", [])]
    roles = [normalize_text(r) for r in parsed.get("job_titles", [])]

    keywords = list(dict.fromkeys(roles + skills))

    matched = []
    for job in jobs:
        title = normalize_text(job.get("title", ""))
        desc = normalize_text(job.get("description", ""))

        score = 0
        for kw in keywords:
            if kw in title:
                score += 10
            if kw in desc:
                score += 5

        matched.append({
            "title": job.get("title", "N/A"),
            "company": job.get("company", {}).get("display_name", "N/A"),
            "location": job.get("location", {}).get("display_name", "N/A"),
            "salary": job.get("salary_min", "N/A"),
            "url": job.get("redirect_url", "#"),
            "match_pct": score
        })

    matched.sort(key=lambda x: x["match_pct"], reverse=True)
    return matched


@app.route('/')
def home():
    return render_template("index.html")


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == "POST":
        name = request.form.get("name")
        password = request.form.get("password")

        if not os.path.exists(CRED_FILE):
            with open(CRED_FILE, "w", newline="") as f:
                csv.writer(f).writerow(["id", "name", "password"])

        existing = set()
        with open(CRED_FILE, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing.add(row["id"])

        while True:
            uid = "C" + str(random.randint(1000, 9999))
            if uid not in existing:
                break

        with open(CRED_FILE, "a", newline="") as f:
            csv.writer(f).writerow([uid, name.lower(), password])

        return redirect(url_for("dashboard", user_id=uid))

    return render_template("signup.html")


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == "POST":
        uid = request.form.get("user_id")
        pwd = request.form.get("password")

        with open(CRED_FILE, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["id"] == uid and row["password"] == pwd:
                    return redirect(url_for("dashboard", user_id=uid))

        return "Invalid Login"

    return render_template("login.html")


@app.route('/dashboard')
def dashboard():
    uid = request.args.get("user_id")
    name = "Unknown"

    with open(CRED_FILE, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["id"] == uid:
                name = row["name"].title()

    return render_template("dashboard.html", user_id=uid, name=name)


@app.route('/upload')
def upload_page():
    uid = request.args.get("user_id")
    return render_template("upload.html", user_id=uid)


@app.route('/upload_resume', methods=['POST'])
def upload_resume():
    uid = request.form.get("user_id")
    file = request.files.get("resume")

    delete_old_user_resumes(uid)

    filename = secure_filename(f"{uid}_{file.filename}")
    file_path = os.path.join(UPLOAD_DIR, filename)
    file.save(file_path)

    text = extract_text_from_resume(file_path)
    parsed = simple_parse_resume_spacy(text)
    store_resume_json(uid, parsed)

    roles = parsed.get("job_titles", ["general"])

    jobs = []
    for r in roles:
        jobs.extend(fetch_jobs(r))

    matched = simple_match_jobs(jobs, parsed)
    top_jobs = matched[:20]

    return render_template("table.html", user_id=uid, jobs=top_jobs, parsed=parsed)


@app.route('/companies')
def companies():
    uid = request.args.get("user_id")
    role = request.args.get("role")
    jobs = fetch_jobs(role)
    return render_template("companies.html", user_id=uid, role=role, jobs=jobs)



if __name__ == "__main__":
    app.run(debug=True)
