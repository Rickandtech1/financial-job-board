#!/usr/bin/env python3
"""
Daily job board updater for Sarik Eng — Metro Vancouver financial services roles.
Scrapes Job Bank Canada, Big 5 bank Workday APIs, and Metro Van credit union pages.
Scores, deduplicates, appends to index.html, commits, pushes, and sends a digest email.

Usage:
  RESEND_API_KEY=...  TO_EMAIL=...  python3 daily_update.py
"""

import json, os, re, sys, time, tempfile, subprocess, datetime, urllib.request, urllib.error, base64
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

# ── Config ────────────────────────────────────────────────────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
TO_EMAIL       = os.environ.get("TO_EMAIL", "ricktey02@gmail.com")
GITHUB_PAT     = os.environ.get("GITHUB_PAT", "")
REPO_DIR       = os.environ.get("REPO_DIR", os.path.dirname(os.path.abspath(__file__)))
JOB_BOARD_PATH = os.path.join(REPO_DIR, "index.html")
TODAY          = datetime.date.today().isoformat()
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

# ── HTTP session ──────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer":    "https://www.jobbank.gc.ca/jobsearch/",
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

# ── Scraping constants ────────────────────────────────────────────────────────

JOBBANK_SEARCHES = [
    "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=financial+services+representative&locationstring=Vancouver%2C+BC&mid=39070&fltr2=48&sort=M",
    "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=investment+representative&locationstring=Vancouver%2C+BC&mid=39070&sort=M",
    "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=customer+experience+associate+bank&locationstring=Vancouver%2C+BC&mid=39070&sort=M",
    "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=financial+advisor&locationstring=Vancouver%2C+BC&mid=39070&sort=M",
]

# Only TD and BMO confirmed to work via CXS API; others (RBC, Scotia, CIBC) use
# different ATSes (Phenom, SuccessFactors) without accessible JSON endpoints.
WORKDAY_BANKS = [
    {
        "company":  "TD Bank",
        "cxs_url":  "https://td.wd3.myworkdayjobs.com/wday/cxs/td/TD_Bank_Careers/jobs",
        "base_url": "https://td.wd3.myworkdayjobs.com/en-US/TD_Bank_Careers",
        "source":   "TD Careers",
    },
    {
        "company":  "BMO Bank of Montreal",
        "cxs_url":  "https://bmo.wd3.myworkdayjobs.com/wday/cxs/bmo/External/jobs",
        "base_url": "https://bmo.wd3.myworkdayjobs.com/en-US/External",
        "source":   "BMO Careers",
    },
    {
        "company":  "Canaccord Genuity",
        "cxs_url":  "https://cgf.wd10.myworkdayjobs.com/wday/cxs/cgf/CG/jobs",
        "base_url": "https://cgf.wd10.myworkdayjobs.com/en-US/CG",
        "source":   "Canaccord Careers",
    },
]

WORKDAY_SEARCH = (
    "financial services representative OR investment representative OR "
    "customer experience associate OR associate financial advisor OR investment associate"
)

CU_CAREERS = [
    {"company": "Vancity Credit Union",    "url": "https://www.vancity.com/careers/"},
    {"company": "Coast Capital Savings",   "url": "https://careers.coastcapitalsavings.com/"},
    {"company": "BlueShore Financial",     "url": "https://www.blueshorefinancial.com/about-blueshore/careers"},
    {"company": "First West Credit Union", "url": "https://careers.firstwestcu.ca/"},
    {"company": "Khalsa Credit Union",     "url": "https://www.khalsacreditunion.ca/about/careers/"},
    {"company": "Sunrise Credit Union",    "url": "https://www.sunrisecu.mb.ca/about/careers"},
]

WEALTH_FIRMS = [
    {"company": "Odlum Brown Limited",       "url": "https://www.odlumbrown.com/about/join-us"},
    {"company": "RGF Integrated Wealth",     "url": "https://www.rgfwealth.com/about/careers/"},
    {"company": "PWL Capital",               "url": "https://pwlcapital.com/about/careers/"},
    {"company": "Nicola Wealth",             "url": "https://nicolawealth.com/careers"},
    {"company": "IG Wealth Management",      "url": "https://www.ig.ca/en/careers/financial-advisor-careers"},
    {"company": "Wellington-Altus",          "url": "https://wellington-altus.ca/careers/"},
    {"company": "Richardson Wealth",         "url": "https://richardsonwealth.com/careers/career-opportunities"},
    {"company": "Raymond James Ltd",         "url": "https://www.raymondjames.ca/careers"},
    {"company": "ZLC Wealth",                "url": "https://www.zlc.net/careers/"},
    {"company": "Spring Planning",           "url": "https://springplans.ca/careers/"},
    {"company": "Assante Wealth Management", "url": "https://www.assante.com/careers"},
]

JOB_KEYWORDS = {
    "financial", "representative", "advisor", "associate", "banking",
    "member service", "customer experience", "member advice", "investment", "wealth",
}


def extract_url(item: dict) -> str:
    """Pull the best job posting URL from an Apify result item."""
    for key in ("url", "jobUrl", "applyUrl", "link", "externalUrl", "applyLink",
                "jobLink", "detailUrl", "jobDetailUrl", "applicationUrl"):
        val = item.get(key)
        if val and str(val).startswith("http"):
            return str(val)
    return ""


def is_job_active(item: dict) -> bool:
    """Return False if the raw Apify item shows the job is expired, closed, or older than 30 days."""
    if item.get('expired') is True:    return False
    if item.get('isExpired') is True:  return False
    if item.get('isClosed') is True:   return False
    if item.get('isActive') is False:  return False

    status = str(item.get('jobStatus', item.get('status', ''))).lower()
    if status in ('expired', 'closed', 'filled', 'inactive', 'removed'):
        return False

    today = datetime.date.today()
    for field in ('closingDate', 'expiryDate', 'validThrough', 'deadline', 'applicationDeadline'):
        val = item.get(field)
        if val:
            try:
                d = datetime.date.fromisoformat(str(val)[:10])
                if d < today:
                    return False
            except Exception:
                pass

    for field in ('datePosted', 'postedAt', 'publishedAt', 'date', 'scrapedAt'):
        val = item.get(field)
        if val:
            try:
                d = datetime.date.fromisoformat(str(val)[:10])
                if (today - d).days > 30:
                    return False
            except Exception:
                pass

    return True


def _s(val) -> str:
    """Safely coerce any Apify field value (str, list, dict, None) to a plain string."""
    if val is None:
        return ""
    if isinstance(val, list):
        return ", ".join(str(v) for v in val if v)
    if isinstance(val, dict):
        return val.get("text") or val.get("label") or val.get("value") or str(val)
    return str(val)


def normalize(item: dict, source: str) -> dict:
    """Convert a raw Apify item into our standard job dict."""
    title    = _s(item.get("title") or item.get("positionName") or
                  item.get("jobTitle") or item.get("name")).strip()
    company  = _s(item.get("company") or item.get("companyName") or
                  item.get("employer") or item.get("organizationName")).strip()
    location = _s(item.get("location") or item.get("jobLocation") or
                  item.get("city")).strip()
    salary   = _s(item.get("salary") or item.get("salaryRange") or
                  item.get("compensation")).strip()
    desc     = _s(item.get("description") or item.get("jobDescription") or
                  item.get("body") or item.get("text"))
    url_val  = extract_url(item)
    emp_type = _s(item.get("employmentType") or item.get("jobType")).strip()

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

def scrape_jobbank() -> list:
    items = []
    seen_urls = set()

    for search_url in JOBBANK_SEARCHES:
        try:
            time.sleep(1)
            resp = SESSION.get(search_url, timeout=30)
            if resp.status_code != 200:
                print(f"  [WARN] Job Bank: HTTP {resp.status_code}", flush=True)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            # Each job card is <article class="action-buttons">; the clickable
            # link inside has class="resultJobItem"
            articles = soup.select("article.action-buttons")
            if not articles:
                print(f"  [INFO] Job Bank: no results for {search_url.split('searchstring=')[1].split('&')[0]}", flush=True)
                continue

            for article in articles:
                try:
                    link = article.select_one("a.resultJobItem")
                    if not link:
                        continue
                    href = link.get("href", "")
                    # Strip jsessionid from URL
                    href = re.sub(r';jsessionid=[^?]*', '', href)
                    job_url = ("https://www.jobbank.gc.ca" + href
                               if href.startswith("/") else href)
                    if job_url in seen_urls:
                        continue
                    seen_urls.add(job_url)

                    title    = (link.select_one("span.noctitle") or
                                link.select_one("h3.title")).get_text(strip=True)
                    company  = getattr(article.select_one("li.business"), "get_text", lambda **_: "")(strip=True)
                    loc_li   = article.select_one("li.location")
                    # Remove the hidden "Location" label span before extracting text
                    if loc_li:
                        for hidden in loc_li.select("span.wb-inv"):
                            hidden.decompose()
                    location = loc_li.get_text(strip=True) if loc_li else ""
                    date_str = getattr(article.select_one("li.date"), "get_text", lambda **_: "")(strip=True)
                    # Salary is already on the card
                    sal_li   = article.select_one("li.salary")
                    salary   = sal_li.get_text(strip=True).replace("Salary", "").strip() if sal_li else ""

                    # Fetch detail page for full description
                    description = ""
                    time.sleep(1)
                    try:
                        detail = SESSION.get(job_url, timeout=30)
                        if detail.status_code == 200:
                            dsoup   = BeautifulSoup(detail.text, "lxml")
                            desc_el = (dsoup.select_one("#job-detail-section") or
                                       dsoup.select_one(".job-posting-details") or
                                       dsoup.select_one("article.job-posting") or
                                       dsoup.select_one("main"))
                            if desc_el:
                                description = desc_el.get_text(separator=" ", strip=True)[:2000]
                    except Exception as e:
                        print(f"  [WARN] Job Bank detail fetch: {e}", flush=True)

                    items.append({
                        "title":       title,
                        "company":     company,
                        "location":    location or "Vancouver, BC",
                        "url":         job_url,
                        "salary":      salary,
                        "description": description,
                        "datePosted":  date_str,
                    })
                except Exception as e:
                    print(f"  [WARN] Job Bank item parse: {e}", flush=True)

        except Exception as e:
            print(f"  [WARN] Job Bank search failed: {e}", flush=True)

    return items


def scrape_workday_banks() -> list:
    items = []

    for bank in WORKDAY_BANKS:
        try:
            time.sleep(1)
            resp = SESSION.post(
                bank["cxs_url"],
                json={"appliedFacets": {}, "limit": 20, "offset": 0,
                      "searchText": WORKDAY_SEARCH},
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            if resp.status_code != 200:
                print(f"  [WARN] {bank['company']} Workday: HTTP {resp.status_code}", flush=True)
                continue

            postings = resp.json().get("jobPostings", [])
            found = 0
            for p in postings:
                loc = p.get("locationsText", "")
                loc_l = loc.lower()
                if loc and not any(kw in loc_l for kw in
                                   ("vancouver", "british columbia", " bc", "remote")):
                    continue
                ext = p.get("externalPath", "")
                items.append({
                    "title":      p.get("title", ""),
                    "company":    bank["company"],
                    "location":   loc or "Vancouver, BC",
                    "url":        bank["base_url"] + ext if ext else "",
                    "salary":     "",
                    "description": "",
                    "datePosted": p.get("postedOn", ""),
                })
                found += 1
            print(f"  [INFO] {bank['company']}: {found} Vancouver/BC postings", flush=True)

        except Exception as e:
            print(f"  [WARN] {bank['company']} Workday failed: {e}", flush=True)

    return items


def scrape_credit_unions() -> list:
    items = []

    for cu in CU_CAREERS:
        company = cu["company"]
        cu_url  = cu["url"]
        try:
            time.sleep(1)
            resp = SESSION.get(cu_url, timeout=15)
            if resp.status_code != 200:
                print(f"  [INFO] {company}: HTTP {resp.status_code}", flush=True)
                continue

            soup  = BeautifulSoup(resp.text, "lxml")
            found = 0
            for link in soup.find_all("a", href=True):
                text = link.get_text(strip=True)
                href = link["href"]
                if not text or len(text) < 5 or len(text) > 150:
                    continue
                if not any(kw in text.lower() for kw in JOB_KEYWORDS):
                    continue
                job_url = (href if href.startswith("http")
                           else re.sub(r'/[^/]*$', '', cu_url.rstrip("/")) + "/" + href.lstrip("/"))
                items.append({
                    "title":      text,
                    "company":    company,
                    "location":   "Metro Vancouver, BC",
                    "url":        job_url,
                    "salary":     "",
                    "description": "",
                })
                found += 1

            if found == 0:
                print(f"  [INFO] {company} — no jobs found (may be JS-rendered)", flush=True)

        except Exception as e:
            print(f"  [WARN] {company} failed: {e}", flush=True)

    return items


def scrape_wealth_firms() -> list:
    items = []

    for firm in WEALTH_FIRMS:
        company = firm["company"]
        firm_url = firm["url"]
        try:
            time.sleep(1)
            resp = SESSION.get(firm_url, timeout=15)
            if resp.status_code != 200:
                print(f"  [INFO] {company}: HTTP {resp.status_code}", flush=True)
                continue

            soup  = BeautifulSoup(resp.text, "lxml")
            found = 0
            for link in soup.find_all("a", href=True):
                text = link.get_text(strip=True)
                href = link["href"]
                if not text or len(text) < 5 or len(text) > 150:
                    continue
                if not any(kw in text.lower() for kw in JOB_KEYWORDS):
                    continue
                job_url = (href if href.startswith("http")
                           else re.sub(r'/[^/]*$', '', firm_url.rstrip("/")) + "/" + href.lstrip("/"))
                items.append({
                    "title":       text,
                    "company":     company,
                    "location":    "Metro Vancouver, BC",
                    "url":         job_url,
                    "salary":      "",
                    "description": "",
                })
                found += 1

            if found == 0:
                print(f"  [INFO] {company} — no jobs found (may be JS-rendered)", flush=True)

        except Exception as e:
            print(f"  [WARN] {company} failed: {e}", flush=True)

    return items


def scrape_all() -> list:
    raw = []
    print("\n[STEP 2] Scraping all sources ...", flush=True)

    print("  Job Bank Canada ...", flush=True)
    jb_items = scrape_jobbank()
    print(f"  Job Bank: {len(jb_items)} items", flush=True)
    raw += [normalize(i, "Job Bank Canada") for i in jb_items]

    print("  Big 5 bank Workday APIs (TD, BMO) ...", flush=True)
    wd_items = scrape_workday_banks()
    print(f"  Workday banks: {len(wd_items)} items", flush=True)
    raw += [normalize(i, "Bank Careers") for i in wd_items]

    print("  Credit union career pages ...", flush=True)
    cu_items = scrape_credit_unions()
    print(f"  Credit unions: {len(cu_items)} links found", flush=True)
    raw += [normalize(i, "Credit Union Careers") for i in cu_items]

    print("  Wealth management firm career pages ...", flush=True)
    wf_items = scrape_wealth_firms()
    print(f"  Wealth firms: {len(wf_items)} links found", flush=True)
    raw += [normalize(i, "Wealth Firm Careers") for i in wf_items]

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
             "aviso", "desjardins", "national bank", "odlum brown", "rgf", "pwl capital",
             "nicola wealth", "wellington-altus", "richardson wealth", "zlc", "spring planning",
             "assante"}

EXPIRED_PHRASES = [
    'no longer accepting applications',
    'this position has been filled',
    'this opportunity is currently not available',
    'this job has expired',
    'job has expired',
    'job is no longer available',
    'position is no longer available',
    'this job is no longer available',
    'posting has expired',
    'requisition is no longer active',
    'this job posting has closed',
]

_REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
}


def _is_direct_job_url(url: str) -> bool:
    """Return True if the URL looks like a specific job posting (worth checking for expiry)."""
    if not url:
        return False
    u = url.lower()
    if re.search(r'/(careers|jobs|job-search|current-opportunities|open-positions|work-with-us)/?(\?[^/]*)?$', u):
        return False
    if re.search(r'/search[-_]results?', u):
        return False
    if re.search(r'/jobs?/\d{4,}', u):                    return True
    if re.search(r'[?&](jk|jid|jobid|job_id|job-id|req_id|requisition_id|posting_id|opportunityid)=\w+', u): return True
    if re.search(r'/posting/[a-z0-9-]{6,}', u):           return True
    if re.search(r'/opportunity/[a-z0-9-]{6,}', u):       return True
    if re.search(r'/viewjob\?', u):                        return True
    if re.search(r'linkedin\.com/jobs/view/', u):          return True
    if re.search(r'myworkdayjobs\.com.*/job/', u):         return True
    if re.search(r'(lever|greenhouse|workable|breezy|talent|jobs\.ca)\.', u): return True
    if re.search(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', u): return True  # UUID paths
    if re.search(r'/\d{5,}', u):                           return True
    return False


def _check_url_active(url: str) -> tuple:
    """Return (is_active, reason). False means the job is expired or gone."""
    try:
        resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=10, allow_redirects=True)

        if resp.status_code in (404, 410):
            return False, f"HTTP {resp.status_code}"

        if resp.status_code >= 400:
            return True, f"HTTP {resp.status_code} (keeping)"

        # Redirected to a generic page?
        if resp.url.rstrip('/') != url.rstrip('/'):
            final = resp.url.lower().rstrip('/')
            if re.search(r'/(careers|jobs|search|404|not.found|home|error)$', final):
                return False, f"Redirected to generic page: {resp.url}"

        body = resp.text[:5000].lower()
        for phrase in EXPIRED_PHRASES:
            if phrase in body:
                return False, f"Expired phrase: '{phrase}'"

        return True, "OK"
    except requests.exceptions.Timeout:
        return True, "Timeout (keeping)"
    except Exception as e:
        return True, f"Check failed ({e}), keeping"


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


def verify_job_urls(jobs: list) -> list:
    """HTTP-check each new job's URL; drop jobs whose page shows expiry signals."""
    if not jobs:
        return jobs
    verified = []
    for job in jobs:
        url = job.get('url', '')
        if not url or not _is_direct_job_url(url):
            verified.append(job)
            continue
        active, reason = _check_url_active(url)
        if active:
            verified.append(job)
        else:
            print(f"  [SKIP] Expired: {job['company']} — {job['role']} ({reason})", flush=True)
        time.sleep(0.5)
    return verified


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


# ── Fix 3: Increment daysAgo ──────────────────────────────────────────────────

def increment_days_ago(html_path: str) -> bool:
    """Increment daysAgo for all jobs with daysAgo > 0 (new jobs stay at 0). Returns True if changed."""
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    original = content

    def _inc(m):
        return f"daysAgo: {int(m.group(1)) + 1},"

    # Only touches values >= 1; new jobs at 0 are untouched
    content = re.sub(r"daysAgo:\s*([1-9]\d*),", _inc, content)

    if content != original:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)
        changed = len(re.findall(r"daysAgo:\s*([1-9]\d*),", original))
        print(f"  [INFO] daysAgo incremented for {changed} jobs", flush=True)
        return True
    print("  [INFO] No daysAgo values to increment", flush=True)
    return False


# ── Fix 2: Apply pipeline status ──────────────────────────────────────────────

def apply_pipeline_status(html_path: str, pipeline_path: str) -> bool:
    """Read pipeline.json and update each job's status field in index.html. Returns True if changed."""
    if not os.path.exists(pipeline_path):
        print("  [INFO] pipeline.json not found — skipping status sync", flush=True)
        return False
    try:
        with open(pipeline_path, "r", encoding="utf-8") as f:
            pipeline = json.load(f)
    except Exception as e:
        print(f"  [WARN] pipeline.json read error: {e}", flush=True)
        return False

    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    original = content

    for status in ("Applied", "Interview", "Offer"):
        for entry in pipeline.get(status, []):
            job_id = entry.get("id")
            if job_id is None:
                continue
            # Match the id field, then within 600 chars find and replace the status field
            pattern = rf'(id:\s*{job_id}\s*,[\s\S]{{0,600}}?status:\s*)"[^"]*"'
            replacement = rf'\g<1>"{status}"'
            new_content = re.sub(pattern, replacement, content)
            if new_content != content:
                print(f"  [INFO] pipeline: job {job_id} → {status}", flush=True)
                content = new_content

    if content != original:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    print("  [INFO] No pipeline status changes needed", flush=True)
    return False


# ── Git helpers ───────────────────────────────────────────────────────────────

def git_commit_push(repo_dir: str, count: int):
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"]     = "Rickandtech1"
    env["GIT_AUTHOR_EMAIL"]    = "rickandtech1@users.noreply.github.com"
    env["GIT_COMMITTER_NAME"]  = "Rickandtech1"
    env["GIT_COMMITTER_EMAIL"] = "rickandtech1@users.noreply.github.com"

    subprocess.run(["git", "add", "index.html", "pipeline.json"], cwd=repo_dir, env=env, check=True)
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
  <li><strong>Sources checked:</strong> Job Bank Canada, TD &amp; BMO Workday APIs, 6 Metro Vancouver credit unions (Vancity, Coast Capital, BlueShore, First West, Khalsa, Sunrise), 12 wealth management firms (Odlum Brown, RGF, PWL Capital, Nicola Wealth, IG Wealth, Wellington-Altus, Richardson Wealth, Canaccord, Raymond James, ZLC, Spring Planning, Assante)</li>
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


def send_email(subject: str, html_body: str, attachments: list = None):
    """attachments: optional list of local file paths to attach."""
    payload = {
        "from":    "Job Board <onboarding@resend.dev>",
        "to":      [TO_EMAIL],
        "subject": subject,
        "html":    html_body,
    }

    if attachments:
        att_list = []
        for fpath in attachments:
            try:
                with open(fpath, "rb") as f:
                    att_list.append({
                        "filename": os.path.basename(fpath),
                        "content":  base64.b64encode(f.read()).decode("utf-8"),
                    })
            except Exception as e:
                print(f"  [WARN] Could not attach {fpath}: {e}", flush=True)
        if att_list:
            payload["attachments"] = att_list

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


# ── Resume package ────────────────────────────────────────────────────────────

RESUME_TEXT = """\
SARIK ENG
Richmond, BC  |  236-513-1896  |  sarikc2@gmail.com  |  Permanent Resident of Canada

PROFESSIONAL SUMMARY
Client-focused banking and financial services professional with 7+ years of front-line experience building customer relationships, explaining complex financial processes, and delivering solutions across regulated environments. Currently completing the Canadian Securities Course (CSC) — Exam 1 passed, Exam 2 scheduled June 2026; CPH in progress. Certified in Federal Income Tax (H&R Block, 2025). Hands-on investor across equities, ETFs, and crypto. MBA (Distinction). Trilingual: English, Khmer, French.

CORE COMPETENCIES
Client Relationship Building & Needs Assessment | Canadian Securities Course (CSC) — Exam 1 Passed
Banking Solutions & Financial Products Awareness | Federal Income Tax — H&R Block Certified (2025)
Multi-Channel Customer Service (Phone, Email, In-Person) | MS Office Suite: Word, Excel, Outlook
Regulatory Compliance & Confidentiality | Problem Resolution & Issue Escalation
Cash & Non-Cash Transaction Support | Trilingual: English, Khmer, French

PROFESSIONAL EXPERIENCE
Tax Preparer & Customer Representative (Contract Full-time)  |  Dhiman & Company Inc., Richmond, BC
Jan 2026 – May 2026
- Guided clients through a complex regulated filing process via phone and email, explaining requirements and timelines from intake to completion
- Applied CRA legislation to assess client eligibility, explain financial obligations, and prepare accurate T1/T2 returns and GST/HST reconciliations
- Identified discrepancies in client financial records and resolved them before filing; maintained audit-ready documentation under strict confidentiality
- Provided direct administrative and financial support to management, adapting to shifting priorities without compromising accuracy

Sales Associate (Part-Time)  |  Running Room, Richmond, BC
Oct 2024 – Dec 2025
- Operated store independently; assessed individual customer needs through targeted questions and delivered confident, tailored recommendations
- Built repeat customer relationships through consistent, knowledgeable, and personalized service interactions

Customer Service Representative (Full-time)  |  TLC Healthcare Services Inc., Parksville, BC
Oct 2023 – Dec 2025
- Managed 50+ daily client interactions via phone and in-person in a regulated environment; resolved issues in real time and escalated complex cases
- Maintained patient records across two regulated platforms (Kroll, ImmsBC); performed data entry and ensured file integrity
- Exercised good judgement in confidential matters and applied patience and tact consistently with clients from diverse backgrounds

Sales Associate (Part-Time)  |  The Home Depot, West Vancouver & Nanaimo, BC
Mar 2022 – Oct 2025
- Provided in-person, phone, and online customer support in a high-volume retail environment; used internal systems for order management and inventory tracking

Store Manager (Seasonal)  |  Two Roads Retail Specialist Inc., West Vancouver, BC
Aug – Oct 2023
- Led day-to-day operations including staff scheduling, performance reviews, and sales reporting with minimal supervision
- Promoted digital data capture at point of sale above company average; trained team on customer engagement standards

Sales & Marketing Manager (Full-time)  |  Vattanac Properties (The Atom), Phnom Penh, Cambodia
Jan – Dec 2021
- Managed B2B and B2C client accounts through structured follow-up and relationship-building
- Prepared budget proposals and collaborated cross-functionally with leadership

Business Development Manager (Full-time)  |  Naki Group (Cira Arthika Tourism), Cambodia
Jun 2019 – Jan 2021
- Built client and vendor portfolio from launch; negotiated multi-region contracts
- Trained internal team on client service standards

Sales Manager (Full-time)  |  AboutAsia Travel, Siem Reap, Cambodia
Jan 2014 – Oct 2016
- Sold customized packages to international clients via phone and email
- Resolved complaints and refund requests with discretion to protect client relationships and revenue

EDUCATION & TRAINING
Master of Business Administration (Distinction)  |  University Canada West, Vancouver, BC  |  Jan 2022 – Jul 2023
Web Development Bootcamp  |  BrainStation, Vancouver, BC  |  Apr – Jun 2023
Master of Tourism Management  |  Victoria University of Wellington, New Zealand  |  Mar 2017 – Apr 2019  (NZ Government Scholarship)
Bachelor of Business Administration  |  National University of Management, Phnom Penh, Cambodia  |  Sep 2007 – Oct 2011  (Full Royal Government Scholarship)

CERTIFICATIONS & FINANCIAL KNOWLEDGE
Canadian Securities Course (CSC): Exam 1 passed; Exam 2 scheduled June 2026
Federal Income Tax Level 1 — H&R Block (Completed December 2025)
Hands-on investor: self-directed trading across equities, ETFs, and crypto
MS Office Suite: Word, Excel (Advanced), Outlook — daily professional use
CRM & Platforms: Zoho CRM, Kroll, ImmsBC; Google Analytics, Tableau

LANGUAGES & VOLUNTEER
Languages: English (Full Professional) · Khmer (Native) · French (Classroom Study)
President, Cambodian Student Association of Wellington (2018–2019)
Volunteer, SEALNet anti-trafficking awareness program (2016)
"""


def claude_tailor(job: dict, api_key: str) -> dict | None:
    """Call Claude to tailor resume + write cover letter for a job. Returns parsed dict or None."""
    prompt = f"""You are a professional resume writer. Tailor the following resume for this specific job and return ONLY a valid JSON object — no markdown, no preamble.

JSON schema (all fields required):
{{
  "summary": "2-3 sentence tailored professional summary for this role",
  "competencies": ["item 1", "item 2", ... up to 10 items most relevant to this job],
  "experience_blocks": [
    {{
      "title_line": "Job Title (Full-time/Part-time)  |  Company, Location",
      "date_range": "Mon Year – Mon Year",
      "bullets": ["rewritten bullet 1 mirroring job language", "bullet 2", "bullet 3"]
    }}
  ],
  "education": ["degree line 1", "degree line 2", "degree line 3", "degree line 4"],
  "certifications": ["cert 1", "cert 2", "cert 3", "cert 4", "cert 5"],
  "cover_letter": "Full cover letter body paragraphs separated by double newlines. Do NOT include salutation, date, or sign-off — those are added automatically."
}}

Rules:
- Select the 3–5 most relevant experience blocks for this role; rewrite bullets to mirror the job's language
- Emphasize CSC Exam 1 passed, Exam 2 June 2026, MBA Distinction, and trilingual fluency where relevant
- Cover letter: 3–4 paragraphs, confident and specific, reference the company by name
- Never fabricate credentials, dates, or experience
- Return ONLY the JSON object

JOB POSTING:
Company: {job['company']}
Role: {job['role']}
Location: {job.get('location', '')}
Salary: {job.get('salary', 'Not listed')}
Description: {(job.get('desc') or '')[:2000]}

CANDIDATE RESUME:
{RESUME_TEXT}"""

    payload = json.dumps({
        "model":      "claude-sonnet-4-6",
        "max_tokens": 4096,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload, method="POST")
    req.add_header("Content-Type",       "application/json")
    req.add_header("x-api-key",          api_key)
    req.add_header("anthropic-version",  "2023-06-01")

    try:
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read())
        text = data["content"][0]["text"].strip()
        # Strip markdown code fence if Claude wrapped the JSON
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text).strip()
        return json.loads(text)
    except Exception as e:
        print(f"  [WARN] Claude tailor error for {job.get('company','?')}: {e}", flush=True)
        return None


def generate_resume_package(new_jobs: list) -> list:
    """Generate tailored resume+cover letter .docx pairs for top 10 jobs. Returns list of file paths."""
    top_jobs = sorted(
        [j for j in new_jobs if not j.get("dealbreaker")],
        key=lambda x: x["fitScore"], reverse=True
    )[:10]

    if not top_jobs:
        print("  [INFO] No qualifying jobs for resume package", flush=True)
        return []

    if not ANTHROPIC_KEY:
        print("  [WARN] ANTHROPIC_API_KEY not set — skipping resume package", flush=True)
        return []

    generate_docs_js = os.path.join(REPO_DIR, "generate-docs.js")
    if not os.path.exists(generate_docs_js):
        print("  [WARN] generate-docs.js not found — skipping resume package", flush=True)
        return []

    output_dir = os.path.join(tempfile.gettempdir(), "resume-package")
    os.makedirs(output_dir, exist_ok=True)

    generated_files = []

    for job in top_jobs:
        print(f"  [INFO] Tailoring docs for {job['company']} — {job['role']}", flush=True)

        content = claude_tailor(job, ANTHROPIC_KEY)
        if not content:
            continue

        cover_letter   = content.pop("cover_letter", "")
        resume_content = content

        payload_path = os.path.join(output_dir, "payload_tmp.json")
        with open(payload_path, "w") as f:
            json.dump({
                "job": {
                    "company":  job["company"],
                    "role":     job["role"],
                    "location": job.get("location", ""),
                    "salary":   job.get("salary", ""),
                    "fitScore": job.get("fitScore", 0),
                },
                "resume_content": resume_content,
                "cover_letter":   cover_letter,
                "output_dir":     output_dir,
            }, f)

        result = subprocess.run(
            ["node", generate_docs_js, payload_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"  [WARN] generate-docs.js failed: {result.stderr[:200]}", flush=True)
            continue

        try:
            paths = json.loads(result.stdout.strip())
            generated_files.append(paths["resume"])
            generated_files.append(paths["cover_letter"])
            print(f"  [INFO] {os.path.basename(paths['resume'])} + cover letter created", flush=True)
        except Exception as e:
            print(f"  [WARN] Could not parse generate-docs output: {e}", flush=True)

    return generated_files


def build_resume_package_email(jobs: list) -> str:
    board_url = "https://rickandtech1.github.io/financial-job-board/"
    count = len(jobs)
    items = "".join(
        f"<li><strong>{j['company']}</strong> — {j['role']} "
        f"<span style='color:#888;font-size:12px;'>({j['fitScore']}/100)</span></li>"
        for j in jobs
    )
    return f"""
<html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#222;">
<h2 style="color:#1a237e;">📄 Tailored Resume Package — {TODAY}</h2>
<p>Here are tailored resume and cover letter files for today's top {count} role{'s' if count != 1 else ''}. Each pair is attached as a .docx file ready to review and send.</p>
<ul style="line-height:1.8;">{items}</ul>
<p>Each pair:</p>
<ul>
  <li><strong>Resume_[Company]_[Role].docx</strong> — Tailored summary + selected experience</li>
  <li><strong>CoverLetter_[Company]_[Role].docx</strong> — 3–4 paragraph targeted cover letter</li>
</ul>
<p><a href="{board_url}" style="color:#1a237e;">Open your job board</a> to manage your pipeline.</p>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
<p style="font-size:12px;color:#999;">Automated resume package · Sarik Eng Job Board · Powered by Claude</p>
</body></html>"""


# ── Stale job removal ─────────────────────────────────────────────────────────

_JOB_BLOCK_RE = re.compile(r'\n  \{\n    id: \d+,[\s\S]*?\n  \},?')


def _jobs_section(content: str):
    """Return (jobs_text, pre, post) splitting content around the JOBS array.
    Scopes all regex operations to the array only, preventing over-matching."""
    start = content.find('const JOBS = [')
    if start == -1:
        return None, None, None
    end_m = re.search(r'\n\];', content[start:])
    if not end_m:
        return None, None, None
    end = start + end_m.end()
    return content[start:end], content[:start], content[end:]


def remove_stale_jobs(html_path: str) -> int:
    """Remove jobs with daysAgo > 45 unless they're in the active pipeline."""
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    jobs, pre, post = _jobs_section(content)
    if jobs is None:
        print("  [WARN] JOBS array not found — skipping stale removal", flush=True)
        return 0

    removed = 0

    def check_block(m):
        nonlocal removed
        block = m.group(0)
        status_m = re.search(r'status:\s*"([^"]+)"', block)
        status = status_m.group(1) if status_m else 'To Apply'
        if status in ('Applied', 'Interview', 'Offer'):
            return block
        days_m = re.search(r'daysAgo:\s*(\d+)', block)
        if days_m and int(days_m.group(1)) > 45:
            removed += 1
            return ''
        return block

    new_jobs = _JOB_BLOCK_RE.sub(check_block, jobs)

    if removed > 0:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(pre + new_jobs + post)
        print(f"  [INFO] Removed {removed} stale job(s) (daysAgo > 45, status: To Apply)", flush=True)
    else:
        print("  [INFO] No stale jobs to remove", flush=True)

    return removed


def remove_expired_existing_jobs(html_path: str) -> int:
    """Check existing job URLs for expiry signals; remove expired jobs not in pipeline."""
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    jobs, pre, post = _jobs_section(content)
    if jobs is None:
        print("  [WARN] JOBS array not found — skipping expiry check", flush=True)
        return 0

    removed = 0

    def check_and_remove(m):
        nonlocal removed
        block = m.group(0)

        status_m = re.search(r'status:\s*"([^"]+)"', block)
        status = status_m.group(1) if status_m else 'To Apply'
        if status in ('Applied', 'Interview', 'Offer'):
            return block

        url_m = re.search(r'\n\s+url:\s*"([^"]*)"', block)
        url = url_m.group(1) if url_m else ''
        if not url or not _is_direct_job_url(url):
            return block

        active, reason = _check_url_active(url)
        time.sleep(0.3)

        if not active:
            co_m = re.search(r'company:\s*"([^"]+)"', block)
            ro_m = re.search(r'role:\s*"([^"]+)"', block)
            company = co_m.group(1) if co_m else '?'
            role    = ro_m.group(1) if ro_m else '?'
            print(f"  [SKIP] Expired existing: {company} — {role} ({reason})", flush=True)
            removed += 1
            return ''

        return block

    new_jobs = _JOB_BLOCK_RE.sub(check_and_remove, jobs)

    if removed > 0:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(pre + new_jobs + post)
        print(f"  [INFO] Removed {removed} expired existing job(s)", flush=True)
    else:
        print("  [INFO] All checked existing job URLs still active", flush=True)

    return removed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Daily Job Board Update — {TODAY} ===\n", flush=True)

    if not RESEND_API_KEY:
        print("[ERROR] RESEND_API_KEY not set. Aborting.", flush=True)
        sys.exit(1)

    # Step 0: Remove stale jobs (daysAgo > 45, not in pipeline)
    print("\n[STEP 0] Removing stale jobs ...", flush=True)
    remove_stale_jobs(JOB_BOARD_PATH)

    # Step 0b: HTTP-check existing job URLs and remove expired ones
    print("\n[STEP 0b] Checking existing job URLs for expiry ...", flush=True)
    remove_expired_existing_jobs(JOB_BOARD_PATH)

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

    # Step 3b: HTTP-verify new job URLs are still active
    print("\n[STEP 3b] Verifying job URLs are still active ...", flush=True)
    new_jobs = verify_job_urls(new_jobs)
    print(f"  {len(new_jobs)} jobs verified active", flush=True)

    # Step 4: Append to HTML
    print("\n[STEP 5] Updating index.html ...", flush=True)
    added = 0
    if new_jobs:
        added = append_jobs_to_html(JOB_BOARD_PATH, new_jobs, max_id)
        print(f"  Appended {added} jobs", flush=True)

    # Step 5b: Increment daysAgo for all existing jobs (new jobs stay at 0)
    print("\n[STEP 5b] Incrementing daysAgo ...", flush=True)
    days_changed = increment_days_ago(JOB_BOARD_PATH)

    # Step 5c: Sync pipeline status from pipeline.json
    pipeline_path = os.path.join(REPO_DIR, "pipeline.json")
    print("\n[STEP 5c] Syncing pipeline status ...", flush=True)
    pipeline_changed = apply_pipeline_status(JOB_BOARD_PATH, pipeline_path)

    # Step 5: Commit & push if anything changed
    any_changes = added > 0 or days_changed or pipeline_changed
    if any_changes:
        print("\n[STEP 6] Committing and pushing ...", flush=True)
        git_commit_push(REPO_DIR, added)
    else:
        print("\n[STEP 6] No changes — skipping commit", flush=True)

    # Step 7: Email digest
    print("\n[STEP 7] Sending email digest ...", flush=True)
    total_jobs = count_jobs_in_html(JOB_BOARD_PATH)
    subject, html_body = build_email_html(new_jobs, total_jobs)
    send_email(subject, html_body)

    # Step 8: Generate and email tailored resume package (new non-dealbreaker jobs only)
    non_db_new = [j for j in new_jobs if not j.get("dealbreaker")]
    if non_db_new and ANTHROPIC_KEY:
        print("\n[STEP 8] Generating tailored resume package ...", flush=True)
        doc_files = generate_resume_package(non_db_new)
        if doc_files:
            top_jobs = sorted(non_db_new, key=lambda x: x["fitScore"], reverse=True)[:len(doc_files) // 2]
            pkg_subject = f"Resume Package — {TODAY}: {len(doc_files) // 2} tailored set(s)"
            send_email(pkg_subject, build_resume_package_email(top_jobs), attachments=doc_files)
            print(f"  [INFO] Resume package sent: {len(doc_files) // 2} set(s)", flush=True)
    else:
        print("\n[STEP 8] Skipping resume package (no new non-dealbreaker jobs or no API key)", flush=True)

    print(f"\n=== Done. {added} new roles added. Email sent. ===\n", flush=True)


if __name__ == "__main__":
    main()
