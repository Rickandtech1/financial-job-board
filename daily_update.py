#!/usr/bin/env python3
"""
Daily job board updater for Sarik Eng — Metro Vancouver financial services roles.
Scrapes LinkedIn, Indeed, Glassdoor, ZipRecruiter, Big 5 banks, and Metro Van credit unions.
Scores, deduplicates, appends to job_board.html, commits, pushes, and sends a digest email.

Usage (GitHub Actions sets env vars automatically):
  APIFY_TOKEN=...  RESEND_API_KEY=...  TO_EMAIL=...  python3 daily_update.py
"""

import json, os, re, sys, time, tempfile, subprocess, datetime, urllib.request, urllib.error

# ── Config ────────────────────────────────────────────────────────────────────
APIFY_TOKEN    = os.environ.get("APIFY_TOKEN", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
TO_EMAIL       = os.environ.get("TO_EMAIL", "ricktey02@gmail.com")
GITHUB_PAT     = os.environ.get("GITHUB_PAT", "")
REPO_DIR       = os.environ.get("REPO_DIR", os.path.dirname(os.path.abspath(__file__)))
JOB_BOARD_PATH = os.path.join(REPO_DIR, "job_board.html")
TODAY          = datetime.date.today().isoformat()

# ── Apify helpers ─────────────────────────────────────────────────────────────

def apify_post(actor: str, input_body: dict, timeout_s: int = 600) -> list:
    """Start an Apify actor run, poll until done, return dataset items."""
    token = APIFY_TOKEN
    base  = "https://api.apify.com/v2"

    # Start run
    url  = f"{base}/acts/{actor}/runs?token={token}"
    data = json.dumps(input_body).encode()
    req  = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        resp    = urllib.request.urlopen(req, timeout=30)
        run     = json.loads(resp.read())["data"]
        run_id  = run["id"]
        ds_id   = run.get("defaultDatasetId", "")
    except Exception as e:
        print(f"  [WARN] {actor}: failed to start — {e}", flush=True)
        return []

    print(f"  [INFO] {actor}: run {run_id} started", flush=True)

    # Poll
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(30)
        try:
            poll = urllib.request.urlopen(
                f"{base}/acts/{actor}/runs/{run_id}?token={token}", timeout=15)
            status = json.loads(poll.read())["data"]["status"]
            if status == "SUCCEEDED":
                break
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                print(f"  [WARN] {actor}: run ended with status {status}", flush=True)
                return []
            print(f"  [INFO] {actor}: {status} ...", flush=True)
        except Exception as e:
            print(f"  [WARN] {actor}: poll error — {e}", flush=True)
    else:
        print(f"  [WARN] {actor}: timed out after {timeout_s}s", flush=True)
        return []

    # Fetch dataset
    try:
        ds_url  = f"{base}/datasets/{ds_id}/items?token={token}&limit=200"
        resp2   = urllib.request.urlopen(ds_url, timeout=30)
        items   = json.loads(resp2.read())
        print(f"  [INFO] {actor}: {len(items)} items fetched", flush=True)
        return items
    except Exception as e:
        print(f"  [WARN] {actor}: dataset fetch failed — {e}", flush=True)
        return []


def extract_url(item: dict) -> str:
    """Pull the best job posting URL from an Apify result item."""
    for key in ("url", "jobUrl", "applyUrl", "link", "externalUrl", "applyLink",
                "jobLink", "detailUrl", "jobDetailUrl", "applicationUrl"):
        val = item.get(key)
        if val and str(val).startswith("http"):
            return str(val)
    return ""


def normalize(item: dict, source: str) -> dict:
    """Convert a raw Apify item into our standard job dict."""
    title    = (item.get("title") or item.get("positionName") or
                item.get("jobTitle") or item.get("name") or "").strip()
    company  = (item.get("company") or item.get("companyName") or
                item.get("employer") or item.get("organizationName") or "").strip()
    location = (item.get("location") or item.get("jobLocation") or
                item.get("city") or "").strip()
    salary   = (item.get("salary") or item.get("salaryRange") or
                item.get("compensation") or "").strip()
    desc     = (item.get("description") or item.get("jobDescription") or
                item.get("body") or item.get("text") or "")
    url_val  = extract_url(item)
    emp_type = (item.get("employmentType") or item.get("jobType") or "").strip()

    # Arrangement
    arrangement = "On-site"
    desc_lower  = (desc + title + location).lower()
    if any(w in desc_lower for w in ["remote", "work from home", "wfh", "telework"]):
        arrangement = "Remote"
    elif "hybrid" in desc_lower:
        arrangement = "Hybrid"

    # Employment
    employment = "Full-Time"
    if any(w in (title + emp_type).lower() for w in ["part-time", "part time"]):
        hours_m = re.search(r'(\d+)\s*(?:hours?|h)/?\s*(?:per\s*)?wk|week', desc_lower)
        employment = f"Part-Time · {hours_m.group(1)}h/wk" if hours_m else "Part-Time"

    return {
        "company":     company,
        "role":        title,
        "location":    location,
        "salary":      salary,
        "desc":        str(desc)[:2000],
        "url":         url_val,
        "employment":  employment,
        "arrangement": arrangement,
        "source":      source,
    }


# ── Scraping ──────────────────────────────────────────────────────────────────

def scrape_all() -> list:
    raw = []

    print("\n[STEP 2] Scraping all sources ...", flush=True)

    # SOURCE 1: LinkedIn
    print("  LinkedIn ...", flush=True)
    items = apify_post("apify~linkedin-jobs-scraper", {
        "queries": [
            "investment representative Vancouver BC",
            "financial services representative Vancouver",
            "associate financial advisor Vancouver",
            "investment associate Vancouver BC",
            "associate wealth advisor Vancouver",
            "customer experience associate bank Vancouver",
            "customer experience representative credit union Vancouver",
        ],
        "location": "Vancouver, British Columbia, Canada",
        "maxResults": 50,
    })
    raw += [normalize(i, "LinkedIn") for i in items]

    # SOURCE 2: Indeed Canada
    print("  Indeed Canada ...", flush=True)
    items = apify_post("apify~indeed-scraper", {
        "country": "CA",
        "location": "Vancouver, BC",
        "position": ("investment representative OR financial services representative OR "
                     "associate financial advisor OR investment associate OR "
                     "customer experience associate OR customer experience representative"),
        "maxItems": 50,
    })
    raw += [normalize(i, "Indeed") for i in items]

    # SOURCE 3: Glassdoor (4 parallel-ish runs — sequential in Python, all started before polling)
    print("  Glassdoor ...", flush=True)
    gd_inputs = [
        {"keyword": "investment representative",         "location": "Vancouver, BC, Canada", "maxItems": 20},
        {"keyword": "financial services representative", "location": "Vancouver, BC, Canada", "maxItems": 20},
        {"keyword": "associate financial advisor",       "location": "Vancouver, BC, Canada", "maxItems": 20},
        {"keyword": "customer experience associate bank","location": "Vancouver, BC, Canada", "maxItems": 20},
    ]
    for inp in gd_inputs:
        items = apify_post("bebity~glassdoor-jobs-scraper", inp)
        raw += [normalize(i, "Glassdoor") for i in items]

    # SOURCE 4: ZipRecruiter
    print("  ZipRecruiter ...", flush=True)
    zr_input = {
        "queries": [
            "investment representative Vancouver BC",
            "financial services representative Vancouver",
            "associate financial advisor Vancouver BC",
            "customer experience associate Vancouver BC",
        ],
        "location": "Vancouver, BC",
        "maxItems": 40,
    }
    items = apify_post("apify~zip-recruiter-scraper", zr_input)
    if not items:
        items = apify_post("petr_cermak~ziprecruiter-scraper", zr_input)
    if not items:
        items = apify_post("apify~website-content-crawler", {
            "startUrls": [
                {"url": "https://www.ziprecruiter.com/jobs-search?search=investment+representative&location=Vancouver%2C+BC"},
                {"url": "https://www.ziprecruiter.com/jobs-search?search=financial+services+representative&location=Vancouver%2C+BC"},
                {"url": "https://www.ziprecruiter.com/jobs-search?search=customer+experience+associate+bank&location=Vancouver%2C+BC"},
            ],
            "maxCrawlDepth": 1,
        })
    raw += [normalize(i, "ZipRecruiter") for i in items]

    # SOURCE 5: Big 5 bank career pages
    print("  Big 5 bank career pages ...", flush=True)
    bank_pages = [
        {"url": "https://jobs.rbc.com/ca/en/search-results?keywords=investment+representative&location=Vancouver"},
        {"url": "https://jobs.rbc.com/ca/en/search-results?keywords=financial+services+representative&location=Vancouver"},
        {"url": "https://jobs.rbc.com/ca/en/search-results?keywords=customer+experience+associate&location=Vancouver"},
        {"url": "https://jobs.td.com/en-CA/job-search-results/?keyword=investment+representative&location=British+Columbia"},
        {"url": "https://jobs.td.com/en-CA/job-search-results/?keyword=financial+services+representative&location=British+Columbia"},
        {"url": "https://jobs.td.com/en-CA/job-search-results/?keyword=customer+experience+associate&location=British+Columbia"},
        {"url": "https://jobs.scotiabank.com/search?q=investment+representative&l=Vancouver%2C+BC"},
        {"url": "https://jobs.scotiabank.com/search?q=financial+services+representative&l=Vancouver%2C+BC"},
        {"url": "https://jobs.scotiabank.com/search?q=customer+experience+associate&l=Vancouver%2C+BC"},
        {"url": "https://bmo.wd3.myworkdayjobs.com/External/jobs?q=investment+representative&locations=Vancouver"},
        {"url": "https://bmo.wd3.myworkdayjobs.com/External/jobs?q=customer+experience+associate&locations=Vancouver"},
        {"url": "https://careers.cibc.com/en/search-results?keywords=investment+representative&location=Vancouver"},
        {"url": "https://careers.cibc.com/en/search-results?keywords=customer+experience+associate&location=Vancouver"},
    ]
    # Map domain → source/company label
    BANK_MAP = {
        "rbc.com":        ("RBC Royal Bank",      "RBC Careers"),
        "td.com":         ("TD Canada Trust",      "TD Careers"),
        "scotiabank.com": ("Scotiabank",           "Scotiabank Careers"),
        "bmo.wd3":        ("BMO Bank of Montreal", "BMO Careers"),
        "cibc.com":       ("CIBC",                 "CIBC Careers"),
    }
    items = apify_post("apify~website-content-crawler", {
        "startUrls": bank_pages, "maxCrawlDepth": 1})
    for i in items:
        page_url = i.get("url", "")
        src = "Big 5 Bank Careers"
        for domain_key, (co, src_label) in BANK_MAP.items():
            if domain_key in page_url:
                src = src_label
                if not i.get("company"):
                    i["company"] = co
                break
        raw.append(normalize(i, src))

    # SOURCE 6: Metro Vancouver credit union career pages
    print("  Metro Vancouver credit union pages ...", flush=True)
    CU_MAP = {
        "vancity.com":              ("Vancity Credit Union",          "Vancity Careers"),
        "coastcapitalsavings.com":  ("Coast Capital Savings",         "Coast Capital Careers"),
        "firstwestcu.ca":           ("Envision Financial (First West CU)", "First West Careers"),
        "blueshorefinancial.com":   ("BlueShore Financial",           "BlueShore Careers"),
        "prosperacu.ca":            ("Prospera Credit Union",         "Prospera Careers"),
        "wscu.com":                 ("Westminster Savings Credit Union","WSCU Careers"),
        "gffg.com":                 ("G&F Financial Group",           "G&F Financial Careers"),
        "khalsacu.ca":              ("Khalsa Credit Union",           "Khalsa CU Careers"),
        "integriscu.ca":            ("Integris Credit Union",         "Integris CU Careers"),
    }
    cu_pages = [{"url": f"https://www.{domain}/about{'Vancity' if 'vancity' in domain else ''}/Careers/"
                        if "vancity" in domain
                        else {"url": f"https://www.{domain}/about-us/careers/"
                                     if domain in ("prosperacu.ca","khalsacu.ca","integriscu.ca")
                                     else {"url": f"https://www.{domain}/careers/current-opportunities"
                                                  if "coast" in domain
                                                  else {"url": f"https://www.{domain}/about/careers"}}}}
                for domain in CU_MAP]

    # Simpler: just list them directly
    cu_start_urls = [
        {"url": "https://www.vancity.com/AboutVancity/Careers/"},
        {"url": "https://www.coastcapitalsavings.com/careers/current-opportunities"},
        {"url": "https://www.firstwestcu.ca/about/careers/"},
        {"url": "https://www.blueshorefinancial.com/about/careers"},
        {"url": "https://www.prosperacu.ca/about-us/careers/"},
        {"url": "https://www.wscu.com/about/careers"},
        {"url": "https://www.gffg.com/about-gf/careers/"},
        {"url": "https://www.khalsacu.ca/about-us/careers"},
        {"url": "https://www.integriscu.ca/about/careers"},
    ]
    cu_keywords = {"investment", "financial", "representative", "advisor", "associate",
                   "wealth", "banking", "member service", "customer experience", "member advice"}

    items = apify_post("apify~website-content-crawler", {
        "startUrls": cu_start_urls, "maxCrawlDepth": 1})
    for i in items:
        page_url = i.get("url", "")
        title    = (i.get("title") or "").lower()
        if not any(kw in title for kw in cu_keywords):
            continue
        src = "Credit Union Careers"
        for domain_key, (co, src_label) in CU_MAP.items():
            if domain_key in page_url:
                src = src_label
                if not i.get("company"):
                    i["company"] = co
                break
        raw.append(normalize(i, src))

    print(f"  Total raw items: {len(raw)}", flush=True)
    return raw


# ── Scoring ───────────────────────────────────────────────────────────────────

# Metro Vancouver cities
METRO_VAN = {"vancouver", "burnaby", "richmond", "surrey", "north vancouver",
             "west vancouver", "coquitlam", "new westminster", "port moody",
             "maple ridge", "langley", "delta", "white rock", "abbotsford",
             "mission", "pitt meadows", "squamish", "metro vancouver", "bc"}

TIER1_TITLES = {
    "investment representative", "financial services representative",
    "associate financial advisor", "financial advisor", "investment associate",
    "associate wealth advisor", "fsr", "ir",
}
TIER2_TITLES = {
    "customer experience associate", "customer experience representative",
    "member advice specialist", "personal banking associate", "member advisor",
    "investment services representative", "advisory associate",
}
TIER3_TITLES = {
    "financial services", "banking associate", "member services representative",
    "client solutions advisor", "personal financial services",
}

BIG5 = {"rbc", "td", "scotiabank", "bmo", "cibc",
        "rbc royal bank", "td canada trust", "bmo bank of montreal"}
CREDIT_UNIONS = {"vancity", "coast capital", "first west", "blueshore", "prospera",
                 "westminster savings", "g&f financial", "khalsa", "integris",
                 "envision financial"}
BOUTIQUES = {"raymond james", "canaccord", "edward jones", "manulife", "ig wealth",
             "aviso", "desjardins", "national bank"}


def parse_salary(text: str) -> float:
    """Return the lower bound of a salary string in CAD, or 0."""
    if not text:
        return 0
    nums = re.findall(r"\$?([\d,]+(?:\.\d+)?)\s*[kK]?", text)
    values = []
    for n in nums:
        val = float(n.replace(",", ""))
        if val < 200:      # looks like hourly — annualize
            val *= 2080
        elif val < 1000:   # thousands abbreviation
            val *= 1000
        if val >= 20000:
            values.append(val)
    return min(values) if values else 0


def score_job(job: dict) -> tuple:
    """Return (fitScore, dealbreaker, dealbreakersTriggered, signals, whyFit)."""
    title   = (job.get("role", "") or "").lower()
    company = (job.get("company", "") or "").lower()
    loc     = (job.get("location", "") or "").lower()
    desc    = (job.get("desc", "") or "").lower()
    sal_str = job.get("salary", "") or ""
    combined = title + " " + desc

    # Dealbreaker checks
    dealbreakers = []
    if re.search(r"commission[- ]?only|100%\s*commission|no\s*base", combined):
        dealbreakers.append("Commission-only pay")
    if re.search(r"llqp|life\s*licen[sc]|insurance\s*(licen[sc]|certif)", combined) and \
       not re.search(r"financial\s*(serv|advis|plan|invest)|investment|wealth", title):
        dealbreakers.append("Insurance-only role requiring LLQP")
    if re.search(r"cold[- ]?call|outbound\s*prospect|generate\s*your\s*own\s*book|prospecting\s*required", combined) and \
       not re.search(r"warm\s*lead|existing\s*client|established\s*book", combined):
        dealbreakers.append("Pure cold-calling with zero warm leads")

    if dealbreakers:
        return 25, True, dealbreakers, [], "Dealbreaker triggered."

    score = 0
    signals = []
    notes   = []

    # 1. Role title match (0–30)
    if any(t in title for t in TIER1_TITLES):
        score += 30
        signals.append("Strong title match (IR/FSR/FA/IA/AWA)")
    elif any(t in title for t in TIER2_TITLES):
        score += 20
        signals.append("Good title match (CEA/CER/MAS/PBA)")
    elif any(t in title for t in TIER3_TITLES):
        score += 10
        signals.append("Adjacent banking role")
    else:
        signals.append("Title match unclear")

    # 2. Location (0–25)
    loc_clean = loc.lower()
    if any(city in loc_clean for city in METRO_VAN) or \
       any(city in loc_clean for city in ("metro", "greater vancouver", "lower mainland")):
        score += 25
        signals.append("Metro Vancouver location confirmed")
    elif "bc" in loc_clean or "british columbia" in loc_clean:
        score += 10
        signals.append("BC location (outside Metro Van)")
    elif not loc_clean or any(w in loc_clean for w in ("remote", "canada")):
        score += 5
        signals.append("Remote or location unclear")

    # 3. Salary (0–20)
    salary_low = parse_salary(sal_str)
    if not salary_low:
        # Try to extract from description
        sal_match = re.search(r"\$\s*([\d,]+)\s*(?:–|-|to)\s*\$?\s*([\d,]+)", combined)
        if sal_match:
            salary_low = float(sal_match.group(1).replace(",", ""))
            if salary_low < 200:
                salary_low *= 2080
    if salary_low >= 55000:
        score += 20
        signals.append("Salary ≥ $55K confirmed")
    elif salary_low >= 50000:
        score += 15
        signals.append("Salary $50–55K range")
    elif salary_low >= 45000:
        score += 8
        signals.append("Salary $45–50K (below target)")
    elif salary_low > 0:
        score += 3
    else:
        signals.append("Salary not listed")

    # 4. Company type (0–15)
    if any(b in company for b in BIG5):
        score += 15
        signals.append("Big 5 bank")
    elif any(cu in company for cu in CREDIT_UNIONS):
        score += 15
        signals.append("Metro Vancouver credit union")
    elif any(bq in company for bq in BOUTIQUES):
        score += 10
        signals.append("Boutique wealth management firm")
    else:
        score += 5

    # 5. Entry-level fit (0–10)
    if re.search(r"entry.?level|no experience|new grad|0.?1\s*year|one year|training provided", combined):
        score += 10
        signals.append("Entry-level / training provided")
    elif re.search(r"(1|2|one|two)\s*(?:\+)?\s*year[s]?\s*(of)?\s*(experience|exp)", combined):
        score += 7
        signals.append("1–2 years preferred")
    elif re.search(r"3\s*\+?\s*year[s]?|three\s*year[s]?|minimum\s*3", combined):
        score += 3

    # Cert-gap penalties
    why_parts = []

    # CSC required at hire (only Exam 1 passed)
    if re.search(r"csc\s*(?:required|mandatory|must\s*have|certification\s*required)", combined) and \
       not re.search(r"csc.{0,40}within|csc.{0,40}month|obtain.{0,30}csc", combined):
        score -= 17
        why_parts.append("⚠️ Cert gap — score adjusted: role requires full CSC at hire; "
                         "Sarik has only passed Exam 1 (Exam 2 scheduled June 2026)")

    # CPH required at hire
    if re.search(r"cph\s*(?:required|mandatory|must\s*have)", combined) and \
       not re.search(r"cph.{0,40}within|cph.{0,40}month|obtain.{0,30}cph", combined):
        score -= 10
        why_parts.append("⚠️ Cert gap — score adjusted: CPH required at hire; Sarik's CPH in progress")

    # LLQP required
    if re.search(r"llqp|life\s*licen[sc]", combined):
        dealbreakers.append("LLQP/life licence required")
        return min(score, 25), True, dealbreakers, signals, " | ".join(why_parts)

    # 2+ years direct financial services experience required
    if re.search(r"(minimum\s*)?(2|3|two|three)\s*\+?\s*year[s]?\s*(of)?\s*(direct\s*)?(financial\s*service|banking|brokerage|investment\s*dealer)", combined):
        score -= 10
        why_parts.append("⚠️ Cert gap — score adjusted: 2+ years direct FS experience required; Sarik has adjacent experience via MBA")

    score = max(0, min(100, score))

    # Build whyFit
    if not why_parts:
        role_tier = "IR/FSR/FA level" if any(t in title for t in TIER1_TITLES) else \
                    "CEA/CER/support level" if any(t in title for t in TIER2_TITLES) else "adjacent"
        co_type   = "Big 5 bank" if any(b in company for b in BIG5) else \
                    "Metro Van credit union" if any(cu in company for cu in CREDIT_UNIONS) else "firm"
        why_parts.append(
            f"This {role_tier} role at a {co_type} aligns with Sarik's CSC-in-progress profile and MBA. "
            f"Salary {'meets' if salary_low >= 50000 else 'approaches'} the $50K floor. "
            f"Entry-level framing or training program makes it achievable without full CSC at hire."
        )

    why_fit = " ".join(why_parts)
    return score, False, [], signals[:4], why_fit


def make_hook(company: str, role: str) -> str:
    company_clean = company.split("(")[0].strip()
    return (
        f"I'm currently completing my CSC (Exam 2 in June 2026) and hold an MBA with Distinction — "
        f"I'd love to discuss how I can contribute to {company_clean}'s {role.split('–')[0].strip()} team."
    )


def make_logo(company: str) -> str:
    words = re.sub(r"[^A-Za-z ]", "", company).split()
    return "".join(w[0].upper() for w in words[:2]) if words else "??"


# ── Deduplication ─────────────────────────────────────────────────────────────

def load_existing_jobs(html_path: str) -> list:
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return []

    pairs = []
    # Extract company + role pairs
    companies = re.findall(r'company:\s*"([^"]+)"', content)
    roles     = re.findall(r'role:\s*"([^"]+)"', content)
    max_id    = 0
    ids       = [int(m) for m in re.findall(r'id:\s*(\d+)', content)]
    if ids:
        max_id = max(ids)

    for c, r in zip(companies, roles):
        pairs.append((c.lower().strip(), r.lower().strip()))

    return pairs, max_id


def is_duplicate(job: dict, existing: list) -> bool:
    c = job.get("company", "").lower().strip()
    r = job.get("role", "").lower().strip()
    for ec, er in existing:
        if c and ec and c in ec or ec in c:
            if r and er and (r in er or er in r or
                             # fuzzy: first 20 chars match
                             r[:20] == er[:20]):
                return True
    return False


# ── HTML append ───────────────────────────────────────────────────────────────

SALARY_NOT_LISTED = "Not listed"


def format_salary(raw: str) -> str:
    if not raw or raw.strip() == "":
        return SALARY_NOT_LISTED
    raw = raw.strip()
    # Standardise format
    raw = re.sub(r"\s+", " ", raw)
    return raw


JS_TEMPLATE = """\
  {{
    id: {id},
    company: "{company}",
    logo: "{logo}",
    role: "{role}",
    location: "{location}",
    arrangement: "{arrangement}",
    employment: "{employment}",
    salary: "{salary}",
    source: "{source}",
    url: "{url}",
    fitScore: {fitScore},
    daysAgo: 0,
    status: "To Apply",
    dealbreaker: {dealbreaker_js},
    dealbreakersTriggered: {dealbreakers_js},
    signals: {signals_js},
    whyFit: "{whyFit}",
    hook: "{hook}",
  }},"""


def append_jobs_to_html(html_path: str, new_jobs: list, start_id: int) -> int:
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Find insertion point: before `];` that closes the JOBS array
    marker = "];"
    # Find the last occurrence that's preceded by job objects
    idx = content.rfind(marker)
    if idx == -1:
        print("[ERROR] Could not find ]; marker in HTML", flush=True)
        return 0

    blocks = []
    next_id = start_id + 1
    added   = 0

    for j in new_jobs:
        def esc(s):
            return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")

        block = JS_TEMPLATE.format(
            id=next_id,
            company=esc(j["company"]),
            logo=esc(j["logo"]),
            role=esc(j["role"]),
            location=esc(j["location"]),
            arrangement=esc(j["arrangement"]),
            employment=esc(j["employment"]),
            salary=esc(format_salary(j.get("salary", ""))),
            source=esc(j["source"]),
            url=esc(j["url"]),
            fitScore=j["fitScore"],
            dealbreaker_js="true" if j["dealbreaker"] else "false",
            dealbreakers_js=json.dumps(j["dealbreakersTriggered"]),
            signals_js=json.dumps(j["signals"]),
            whyFit=esc(j["whyFit"]),
            hook=esc(j["hook"]),
        )
        blocks.append(block)
        next_id += 1
        added   += 1

    if blocks:
        insert_text = "\n" + "\n".join(blocks) + "\n"
        content = content[:idx] + insert_text + content[idx:]
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)

    return added


# ── Git helpers ───────────────────────────────────────────────────────────────

def git_commit_push(repo_dir: str, count: int):
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"]     = "Rickandtech1"
    env["GIT_AUTHOR_EMAIL"]    = "rickandtech1@users.noreply.github.com"
    env["GIT_COMMITTER_NAME"]  = "Rickandtech1"
    env["GIT_COMMITTER_EMAIL"] = "rickandtech1@users.noreply.github.com"

    subprocess.run(["git", "add", "job_board.html"], cwd=repo_dir, env=env, check=True)
    subprocess.run(["git", "commit", "-m", f"Daily update {TODAY}: {count} new roles"],
                   cwd=repo_dir, env=env, check=True)

    pat   = GITHUB_PAT or os.environ.get("GITHUB_TOKEN", "")
    remote = f"https://Rickandtech1:{pat}@github.com/Rickandtech1/financial-job-board.git"

    for attempt in range(4):
        result = subprocess.run(["git", "push", remote, "main"],
                                cwd=repo_dir, env=env, capture_output=True, text=True)
        if result.returncode == 0:
            print("  [INFO] git push succeeded", flush=True)
            return
        wait = 2 ** (attempt + 1)
        print(f"  [WARN] push failed (attempt {attempt+1}): {result.stderr.strip()[:200]} — retrying in {wait}s", flush=True)
        time.sleep(wait)
    print("  [ERROR] git push failed after 4 attempts", flush=True)


# ── Email ─────────────────────────────────────────────────────────────────────

def count_jobs_in_html(html_path: str) -> int:
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        return len(re.findall(r'id:\s*\d+', content))
    except Exception:
        return 0


SCORE_COLOR = {
    "gold":  "#B8860B",
    "green": "#2E7D32",
    "amber": "#E65100",
    "red":   "#B71C1C",
}


def score_badge(score: int) -> str:
    if score >= 85:
        bg, label = "#FFF9C4", "gold"
    elif score >= 70:
        bg, label = "#E8F5E9", "green"
    elif score >= 50:
        bg, label = "#FFF3E0", "amber"
    else:
        bg, label = "#FFEBEE", "red"
    color = SCORE_COLOR[label]
    return (f'<span style="background:{bg};color:{color};border:1px solid {color};'
            f'border-radius:4px;padding:2px 8px;font-weight:700;font-size:13px;">'
            f'{score}/100</span>')


def build_email_html(new_jobs: list, total_jobs: int) -> tuple:
    count     = len(new_jobs)
    board_url = "https://rickandtech1.github.io/financial-job-board/"

    if count == 0:
        subject = f"Job Board — {TODAY}: No new roles today"
        body = f"""
<html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#222;">
<h2 style="color:#1a237e;">📋 Daily Job Board Update — {TODAY}</h2>
<p>Sarik, no new roles matched your criteria today. Here's a quick summary:</p>
<ul>
  <li><strong>Sources checked:</strong> LinkedIn, Indeed, Glassdoor, ZipRecruiter, Big 5 banks (RBC, TD, Scotiabank, BMO, CIBC), 9 Metro Vancouver credit unions</li>
  <li><strong>Total roles on your board:</strong> {total_jobs}</li>
  <li><strong>Your board:</strong> <a href="{board_url}">{board_url}</a></li>
</ul>
<p>Check back tomorrow — the board updates daily at 6am PDT.</p>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
<p style="font-size:12px;color:#999;">Automated daily update · Sarik Eng Job Board</p>
</body></html>"""
        return subject, body

    subject = f"Job Board Update — {TODAY}: {count} new role{'s' if count != 1 else ''} found"

    # Sort: non-dealbreaker by score desc, dealbreakers at bottom
    sorted_jobs = sorted([j for j in new_jobs if not j.get("dealbreaker")],
                         key=lambda x: x["fitScore"], reverse=True) + \
                  [j for j in new_jobs if j.get("dealbreaker")]

    cards = []
    for j in sorted_jobs:
        db_warn = ""
        if j.get("dealbreaker"):
            db_warn = '<p style="color:#B71C1C;font-weight:bold;">⛔ DEALBREAKER: ' + \
                      ", ".join(j.get("dealbreakersTriggered", [])) + "</p>"
        url_display = j.get("url", "") or board_url
        sal = j.get("salary") or "Not listed"
        cards.append(f"""
<div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px 20px;margin-bottom:20px;">
  <p style="margin:0 0 6px;">
    <strong style="font-size:16px;">{j['company']} — {j['role']}</strong>
    &nbsp;&nbsp;{score_badge(j['fitScore'])}
  </p>
  {db_warn}
  <p style="margin:4px 0;color:#555;font-size:13px;">
    {j.get('employment','Full-Time')} &nbsp;·&nbsp; {j.get('location','')} &nbsp;·&nbsp;
    {j.get('arrangement','On-site')} &nbsp;·&nbsp; {sal}
  </p>
  <p style="margin:4px 0;font-size:13px;">
    Source: <a href="{url_display}" style="color:#1a237e;">{j.get('source','')}</a>
  </p>
  <p style="margin:8px 0 4px;font-size:13px;"><strong>Why I'm a fit:</strong> {j.get('whyFit','')}</p>
  <p style="margin:4px 0;font-size:13px;font-style:italic;color:#444;">{j.get('hook','')}</p>
  {"<ul style='margin:6px 0;font-size:12px;color:#555;'>" + "".join(f"<li>{s}</li>" for s in j.get('signals',[])) + "</ul>" if j.get('signals') else ""}
</div>""")

    body = f"""
<html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#222;">
<h2 style="color:#1a237e;">📋 Daily Job Board Update — {TODAY}</h2>
<p><strong>{count} new role{'s' if count != 1 else ''} added</strong> &nbsp;|&nbsp;
   Total on board: <strong>{total_jobs}</strong> &nbsp;|&nbsp;
   <a href="{board_url}">Open board</a></p>
<hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
{"".join(cards)}
<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
<p style="font-size:12px;color:#999;">Automated daily update · Sarik Eng Job Board · Check back tomorrow!</p>
</body></html>"""

    return subject, body


def send_email(subject: str, html_body: str):
    payload = {
        "from":    "Job Board <onboarding@resend.dev>",
        "to":      [TO_EMAIL],
        "subject": subject,
        "html":    html_body,
    }
    tmp_path = os.path.join(tempfile.gettempdir(), "resend_payload.json")
    with open(tmp_path, "w") as f:
        json.dump(payload, f)

    result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://api.resend.com/emails",
         "-H", f"Authorization: Bearer {RESEND_API_KEY}",
         "-H", "Content-Type: application/json",
         "--data-binary", f"@{tmp_path}"],
        capture_output=True, text=True)

    print("Resend response:", result.stdout, flush=True)
    if result.returncode != 0:
        print("Resend error:", result.stderr, flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Daily Job Board Update — {TODAY} ===\n", flush=True)

    if not APIFY_TOKEN:
        print("[ERROR] APIFY_TOKEN not set. Aborting.", flush=True)
        sys.exit(1)
    if not RESEND_API_KEY:
        print("[ERROR] RESEND_API_KEY not set. Aborting.", flush=True)
        sys.exit(1)

    # Step 1: Scrape
    raw_jobs = scrape_all()

    # Step 2: Load existing (for dedup)
    print("\n[STEP 4] Loading existing jobs for deduplication ...", flush=True)
    existing_pairs, max_id = load_existing_jobs(JOB_BOARD_PATH)
    print(f"  Existing jobs: {len(existing_pairs)}, highest ID: {max_id}", flush=True)

    # Step 3: Filter, score, dedup
    print("\n[STEP 3+4] Scoring & deduplicating ...", flush=True)
    new_jobs = []
    seen_in_batch = set()

    for raw in raw_jobs:
        if not raw.get("role") or not raw.get("company"):
            continue
        # Confirm Metro Vancouver location filter
        loc_lower = raw.get("location", "").lower()
        if loc_lower and not any(city in loc_lower for city in METRO_VAN) and \
           "bc" not in loc_lower and "british columbia" not in loc_lower:
            continue

        fit, dealbreaker, db_triggered, signals, why_fit = score_job(raw)

        # Only include fit ≥ 40 or dealbreakers
        if fit < 40 and not dealbreaker:
            continue

        # Dedup
        if is_duplicate(raw, existing_pairs):
            continue

        batch_key = (raw["company"].lower()[:30], raw["role"].lower()[:30])
        if batch_key in seen_in_batch:
            continue
        seen_in_batch.add(batch_key)

        new_jobs.append({
            **raw,
            "logo":                make_logo(raw["company"]),
            "fitScore":            fit,
            "dealbreaker":         dealbreaker,
            "dealbreakersTriggered": db_triggered,
            "signals":             signals,
            "whyFit":              why_fit,
            "hook":                make_hook(raw["company"], raw["role"]),
        })

    print(f"  New qualifying jobs: {len(new_jobs)}", flush=True)

    # Step 4: Append to HTML
    print("\n[STEP 5] Updating job_board.html ...", flush=True)
    added = 0
    if new_jobs:
        added = append_jobs_to_html(JOB_BOARD_PATH, new_jobs, max_id)
        print(f"  Appended {added} jobs", flush=True)

    # Step 5: Commit & push
    if added > 0:
        print("\n[STEP 6] Committing and pushing ...", flush=True)
        git_commit_push(REPO_DIR, added)
    else:
        print("\n[STEP 6] No new jobs — skipping commit", flush=True)

    # Step 6: Email digest
    print("\n[STEP 7] Sending email digest ...", flush=True)
    total_jobs = count_jobs_in_html(JOB_BOARD_PATH)
    subject, html_body = build_email_html(new_jobs, total_jobs)
    send_email(subject, html_body)

    print(f"\n=== Done. {added} new roles added. Email sent. ===\n", flush=True)


if __name__ == "__main__":
    main()
