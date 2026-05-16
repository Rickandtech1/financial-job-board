#!/usr/bin/env python3
"""Daily job board scraper for Sarik Eng — financial services roles in Metro Vancouver."""

import requests, json, re, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

TOKEN = os.environ.get('APIFY_TOKEN', '')
if not TOKEN:
    print("ERROR: APIFY_TOKEN not set", flush=True)
    sys.exit(1)

BASE = "https://api.apify.com/v2"

TARGET_KEYWORDS = [
    'investment representative', 'financial services representative',
    'associate financial advisor', 'financial advisor', 'investment associate',
    'associate wealth advisor', 'customer experience associate',
    'customer experience representative', 'member advice specialist',
    'personal banking associate', 'member services representative',
    'financial services associate', 'wealth associate', 'banking associate',
    'member experience', 'financial planner', 'investment specialist',
]

METRO_VAN_CITIES = [
    'vancouver', 'burnaby', 'surrey', 'richmond', 'coquitlam', 'langley',
    'abbotsford', 'delta', 'north vancouver', 'west vancouver', 'new westminster',
    'maple ridge', 'port moody', 'port coquitlam', 'white rock', 'pitt meadows',
    'mission', 'chilliwack', 'squamish', 'metro vancouver', 'lower mainland',
]

BIG5 = ['rbc', 'royal bank', 'td bank', 'td canada', 'toronto dominion',
        'scotiabank', 'bank of nova scotia', 'bmo', 'bank of montreal',
        'cibc', 'canadian imperial']
CREDIT_UNIONS = ['vancity', 'coast capital', 'first west', 'blueshore', 'prospera',
                 'wscu', 'westminster savings', 'g&f financial', 'gulf & fraser',
                 'khalsa credit union', 'integris', 'envision financial']
BOUTIQUE_WEALTH = ['manulife', 'sun life', 'great-west', 'canaccord', 'raymond james',
                   'ia financial', 'edward jones', 'investors group', 'ig wealth',
                   'national bank', 'desjardins', 'aviso wealth', 'wellington-altus',
                   'mandeville', 'freedom 55', 'assante', 'global maxfin']


def start_run(actor, inp):
    r = requests.post(f"{BASE}/acts/{actor}/runs", params={"token": TOKEN}, json=inp, timeout=30)
    r.raise_for_status()
    d = r.json()['data']
    return d['id'], d['defaultDatasetId']


def poll_run(actor, run_id, label, timeout=600):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{BASE}/acts/{actor}/runs/{run_id}", params={"token": TOKEN}, timeout=30)
            status = r.json()['data']['status']
            print(f"  [{label}] {status}", flush=True)
            if status in ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'):
                return status
        except Exception as e:
            print(f"  [{label}] poll error: {e}", flush=True)
        time.sleep(30)
    return 'TIMEOUT'


def fetch_items(dataset_id):
    r = requests.get(
        f"{BASE}/datasets/{dataset_id}/items",
        params={"token": TOKEN, "limit": 1000},
        timeout=60
    )
    r.raise_for_status()
    return r.json()


def run_actor(actor, inp, label):
    try:
        run_id, ds_id = start_run(actor, inp)
        print(f"Started [{label}]: run={run_id}", flush=True)
        status = poll_run(actor, run_id, label)
        if status == 'SUCCEEDED':
            items = fetch_items(ds_id)
            print(f"[{label}] => {len(items)} items", flush=True)
            return [(item, label) for item in items]
        print(f"[{label}] FAILED ({status})", flush=True)
        return []
    except Exception as e:
        print(f"[{label}] ERROR: {e}", flush=True)
        return []


def normalize_job(item, source_label):
    title = (item.get('title') or item.get('jobTitle') or item.get('position') or
             item.get('name') or item.get('roleName') or '').strip()
    company = (item.get('company') or item.get('companyName') or item.get('employer') or
               item.get('organizationName') or item.get('hiringOrganization', {}).get('name', '') or '').strip()
    location = (item.get('location') or item.get('jobLocation') or item.get('city') or
                item.get('locationName') or '').strip()
    if isinstance(location, dict):
        location = location.get('address', {}).get('addressLocality', '') or str(location)
    salary = (item.get('salary') or item.get('salaryRange') or item.get('compensation') or
              item.get('salaryInfo') or item.get('pay') or item.get('baseSalary', '') or '').strip()
    if isinstance(salary, dict):
        salary = f"${salary.get('minValue','')}-${salary.get('maxValue','')}" if salary.get('minValue') else ''
    description = (item.get('description') or item.get('jobDescription') or
                   item.get('descriptionText') or item.get('text') or
                   item.get('snippet') or '').strip()
    url = (item.get('url') or item.get('jobUrl') or item.get('applyUrl') or
           item.get('link') or item.get('externalUrl') or item.get('applyLink') or
           item.get('jobApplyUrl') or item.get('postingUrl') or item.get('jobPostingUrl') or '').strip()
    employment_type = (item.get('employmentType') or item.get('jobType') or
                       item.get('type') or item.get('workType') or '').strip()
    if isinstance(employment_type, list):
        employment_type = ' '.join(employment_type)

    source_category = source_label.split(':')[0].split('-')[0]
    return {
        'title': title,
        'company': company,
        'location': location,
        'salary': salary,
        'description': description,
        'url': url,
        'employmentType': employment_type,
        'source_label': source_label,
        'source_category': source_category,
    }


def is_metro_van(location, description=''):
    text = (location + ' ' + description[:500]).lower()
    if any(city in text for city in METRO_VAN_CITIES):
        return True
    if ', bc' in text or 'british columbia' in text:
        return True
    return False


def is_target_role(title, description=''):
    text = (title + ' ' + description[:400]).lower()
    return any(kw in text for kw in TARGET_KEYWORDS)


def parse_salary_numbers(salary_text, description=''):
    combined = salary_text + ' ' + description[:600]
    nums = re.findall(r'\$?([\d,]+)(?:\.?\d*)?(?:k\b)?', combined, re.IGNORECASE)
    vals = []
    for n in nums:
        try:
            v = int(n.replace(',', ''))
            if n.lower().endswith('k'):
                v *= 1000
            if 20000 < v < 300000:
                vals.append(v)
        except:
            pass
    # Also catch "XXk" pattern
    k_nums = re.findall(r'\$?([\d.]+)k\b', combined, re.IGNORECASE)
    for n in k_nums:
        try:
            v = int(float(n) * 1000)
            if 20000 < v < 300000:
                vals.append(v)
        except:
            pass
    return vals


def score_job(job):
    title = job['title'].lower()
    desc = job['description'].lower()
    location = job['location'].lower()
    salary_text = job['salary'].lower()
    company = job['company'].lower()
    emp_type = job['employmentType'].lower()

    score = 0
    dealbreaker = False
    dealbreakers_triggered = []
    signals = []
    penalty_notes = []

    # DEALBREAKERS
    if re.search(r'commission.?only|straight commission|100%\s*commission|fully commission', desc + ' ' + salary_text):
        dealbreaker = True
        dealbreakers_triggered.append('Commission-only pay')

    if re.search(r'\bllqp\b|life insurance licen|insurance licen', desc):
        if not re.search(r'not required|preferred|asset|bonus|willing to obtain|within \d+ month', desc):
            dealbreaker = True
            dealbreakers_triggered.append('LLQP required')

    if re.search(r'cold.?call|pure outbound|100%\s*outbound', desc):
        if not re.search(r'warm lead|warm referral|existing client|book of business|inbound', desc):
            dealbreaker = True
            dealbreakers_triggered.append('Pure cold-calling')

    if dealbreaker:
        why = f"Dealbreaker triggered: {'; '.join(dealbreakers_triggered)}. Not suitable for Sarik's search criteria."
        hook = f"Research role requirements thoroughly before considering this position."
        return 15, True, dealbreakers_triggered, ['Dealbreaker triggered'], why, hook

    # 1. ROLE TITLE (0-30)
    ir_titles = ['investment representative', 'financial services representative',
                 'financial advisor', 'investment associate', 'associate financial advisor',
                 'associate wealth advisor', 'wealth advisor', 'investment advisor',
                 'investment specialist', 'financial planner', 'financial specialist']
    cea_titles = ['customer experience associate', 'customer experience representative',
                  'member advice specialist', 'personal banking associate',
                  'member services representative', 'financial services associate',
                  'banking associate', 'wealth associate', 'member experience']

    if any(t in title for t in ir_titles):
        score += 30
        signals.append(f'Strong title match: {job["title"]}')
    elif any(t in title for t in cea_titles):
        score += 20
        signals.append(f'Good title match: {job["title"]}')
    elif any(kw in title for kw in ['financial', 'investment', 'banking', 'wealth', 'advisor',
                                     'representative', 'associate', 'advisor']):
        score += 10
        signals.append(f'Adjacent role: {job["title"]}')

    # 2. LOCATION (0-25)
    loc_text = location + ' ' + desc[:400]
    if any(city in loc_text for city in METRO_VAN_CITIES):
        score += 25
        signals.append('Metro Vancouver confirmed')
    elif 'british columbia' in loc_text or ', bc' in loc_text:
        score += 10
        signals.append('BC location')
    elif 'remote' in loc_text or 'hybrid' in loc_text:
        score += 5
        signals.append('Remote/hybrid')

    # 3. SALARY (0-20)
    full_time = 'part' not in emp_type and 'part' not in title and \
                not re.search(r'part.?time', desc[:200])
    salary_vals = parse_salary_numbers(salary_text, desc)

    if salary_vals:
        min_sal = min(salary_vals)
        if full_time:
            if min_sal >= 55000:
                score += 20
                signals.append(f'Salary ≥$55K')
            elif min_sal >= 50000:
                score += 15
                signals.append(f'Salary $50-55K')
            elif min_sal >= 45000:
                score += 8
                signals.append(f'Salary $45-50K')
            else:
                score += 0
        else:
            score += 5 if min_sal >= 40000 else 0
    else:
        score += 8
        signals.append('Salary not listed (assumed competitive)')

    # 4. COMPANY TYPE (0-15)
    if any(b in company for b in BIG5):
        score += 15
        signals.append('Big 5 bank')
    elif any(cu in company for cu in CREDIT_UNIONS):
        score += 15
        signals.append('Metro Vancouver credit union')
    elif any(bw in company for bw in BOUTIQUE_WEALTH):
        score += 10
        signals.append('Wealth management firm')
    else:
        score += 5

    # 5. ENTRY-LEVEL FIT (0-10)
    yr_matches = re.findall(r'(\d+)\+?\s*(?:to\s*\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp)', desc)
    if yr_matches:
        max_yr = max(int(y) for y in yr_matches)
        if max_yr >= 3:
            score += 3
        elif max_yr >= 2:
            score += 6
        else:
            score += 8
    elif re.search(r'entry.?level|no experience required|new graduate|recent grad|0.?1 year', desc):
        score += 10
        signals.append('Entry-level')
    else:
        score += 7

    # CERT-GAP PENALTIES
    if re.search(r'\bcsc\b.*required|\bcanadian securities course\b.*required|securities.*license.*required', desc):
        if not re.search(r'within \d+ month|upon hire.*obtain|preferred|asset|willing to obtain', desc):
            score -= 17
            penalty_notes.append('CSC required at hire (Sarik holds Exam 1 only — Exam 2 June 2026)')

    if re.search(r'\bcph\b.*required|conduct and practices handbook.*required', desc):
        if not re.search(r'within \d+ month|preferred|asset|willing to obtain', desc):
            score -= 10
            penalty_notes.append('CPH required at hire (Sarik in progress)')

    direct_exp = re.findall(r'(\d+)\+?\s*years?\s*(?:of\s*)?(?:direct\s*)?(?:financial\s*services|banking|investment|brokerage)\s*(?:industry\s*)?experience', desc)
    if direct_exp:
        max_direct = max(int(y) for y in direct_exp)
        if max_direct >= 2:
            score -= 10
            penalty_notes.append(f'{max_direct}+ years direct financial services experience required')

    score = max(0, min(100, score))

    # Build whyFit
    why_parts = []
    if penalty_notes:
        why_parts.append('⚠️ Cert gap — score adjusted: ' + '; '.join(penalty_notes) + '.')

    tier = 'Strong' if score >= 70 else 'Good' if score >= 55 else 'Moderate'
    co_type = 'Big 5 bank' if any(b in company for b in BIG5) else \
              'credit union' if any(cu in company for cu in CREDIT_UNIONS) else 'firm'
    why_parts.append(
        f'{tier} match — the {job["title"]} role at {job["company"]} aligns with Sarik\'s '
        f'target roles. MBA (Distinction) from UCW and CSC Exam 1 passage provide a strong foundation.'
    )
    if job['salary']:
        why_parts.append(f'Compensation listed: {job["salary"]}.')
    why_fit = ' '.join(why_parts)

    hook = (
        f'Sarik\'s MBA (Distinction) from UCW and CSC Exam 1 completion — with Exam 2 scheduled June 2026 — '
        f'make him a ready-now candidate for the {job["title"]} role at {job["company"]}; '
        f'his financial services academic background directly maps to your team\'s needs.'
    )

    return score, False, [], signals, why_fit, hook


def parse_salary_display(salary_text, description=''):
    if salary_text and salary_text.strip():
        # Clean up the salary text
        s = salary_text.strip()
        if not s.startswith('$') and re.search(r'\d', s):
            s = '$' + s if re.match(r'[\d,]', s) else s
        return s[:60]
    match = re.search(r'\$[\d,]+\s*[-–]\s*\$[\d,]+', description[:600])
    if match:
        return match.group()
    match = re.search(r'\$[\d,]+(?:,\d{3})+(?:\s*(?:CAD|per year|annually))?', description[:600])
    if match:
        return match.group()
    return "Not listed"


def format_location(loc, desc=''):
    if not loc:
        for city in ['Vancouver', 'Burnaby', 'Surrey', 'Richmond', 'Coquitlam',
                     'Langley', 'North Vancouver', 'West Vancouver', 'New Westminster',
                     'Delta', 'Maple Ridge', 'Port Moody', 'White Rock']:
            if city.lower() in desc[:400].lower():
                return f"{city}, BC"
        return "Vancouver, BC"
    loc = loc.strip()
    if 'british columbia' in loc.lower():
        loc = re.sub(r',?\s*british columbia', ', BC', loc, flags=re.IGNORECASE)
    if re.search(r'(?:vancouver|burnaby|surrey|richmond|coquitlam|langley|north van|west van|new westminster|delta|maple ridge|port moody|white rock)', loc, re.IGNORECASE):
        if not re.search(r',\s*BC', loc, re.IGNORECASE):
            loc = loc.rstrip(',') + ', BC'
    return loc[:60]


def get_arrangement(desc, emp_type=''):
    text = (desc[:600] + ' ' + emp_type).lower()
    if 'fully remote' in text or 'work from home' in text or 'wfh' in text:
        return 'Remote'
    if 'hybrid' in text:
        return 'Hybrid'
    return 'On-site'


def get_employment(emp_type, title='', desc=''):
    text = (emp_type + ' ' + title + ' ' + desc[:300]).lower()
    hours_match = re.search(r'(\d+)\s*hours?\s*(?:per|/)\s*week', text)
    if hours_match:
        return f'Part-Time · {hours_match.group(1)}h/wk'
    if 'part-time' in text or 'part time' in text:
        hours2 = re.search(r'(\d+)\s*h(?:rs?)?(?:/wk|/week| per week)', text)
        if hours2:
            return f'Part-Time · {hours2.group(1)}h/wk'
        return 'Part-Time'
    return 'Full-Time'


def get_logo(company):
    words = company.split()
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return company[:2].upper() if len(company) >= 2 else 'XX'


def get_source_display(source_label, company=''):
    co = company.lower()
    if 'rbc' in co or 'royal bank' in co:
        return 'RBC Careers'
    if 'td bank' in co or 'toronto-dominion' in co or 'toronto dominion' in co:
        return 'TD Careers'
    if 'scotiabank' in co or 'bank of nova scotia' in co:
        return 'Scotiabank Careers'
    if 'bmo' in co or 'bank of montreal' in co:
        return 'BMO Careers'
    if 'cibc' in co or 'canadian imperial' in co:
        return 'CIBC Careers'
    if 'vancity' in co:
        return 'Vancity Careers'
    if 'coast capital' in co:
        return 'Coast Capital Careers'
    if 'first west' in co:
        return 'First West Careers'
    if 'blueshore' in co or 'blue shore' in co:
        return 'BlueShore Careers'
    if 'prospera' in co:
        return 'Prospera Careers'
    if 'wscu' in co or 'westminster savings' in co:
        return 'WSCU Careers'
    if 'g&f' in co or 'gulf' in co or 'fraser' in co:
        return 'G&F Financial Careers'
    cat = source_label.split(':')[0].split('-')[0]
    return cat


def esc(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ').replace('\r', ' ').strip()


def job_to_js(j, job_id):
    signals_js = ', '.join(f'"{esc(s)}"' for s in j['signals'][:4])
    db_js = ', '.join(f'"{esc(d)}"' for d in j['dealbreakersTriggered'])
    return (
        f'  {{\n'
        f'    id: {job_id},\n'
        f'    company: "{esc(j["company"])}",\n'
        f'    logo: "{esc(j["logo"])}",\n'
        f'    role: "{esc(j["role"])}",\n'
        f'    location: "{esc(j["location"])}",\n'
        f'    arrangement: "{esc(j["arrangement"])}",\n'
        f'    employment: "{esc(j["employment"])}",\n'
        f'    salary: "{esc(j["salary"])}",\n'
        f'    source: "{esc(j["source"])}",\n'
        f'    url: "{esc(j["url"])}",\n'
        f'    fitScore: {j["fitScore"]},\n'
        f'    daysAgo: 0,\n'
        f'    status: "To Apply",\n'
        f'    dealbreaker: {str(j["dealbreaker"]).lower()},\n'
        f'    dealbreakersTriggered: [{db_js}],\n'
        f'    signals: [{signals_js}],\n'
        f'    whyFit: "{esc(j["whyFit"])}",\n'
        f'    hook: "{esc(j["hook"])}",\n'
        f'  }},'
    )


def load_existing(index_html):
    match = re.search(r'const JOBS\s*=\s*\[(.*?)\];', index_html, re.DOTALL)
    if not match:
        return set(), 0
    jobs_text = match.group(1)
    companies = re.findall(r'company:\s*"([^"]*)"', jobs_text)
    roles = re.findall(r'role:\s*"([^"]*)"', jobs_text)
    ids = [int(i) for i in re.findall(r'\bid:\s*(\d+)', jobs_text)]
    existing = {(c.lower().strip(), r.lower().strip()) for c, r in zip(companies, roles)}
    return existing, max(ids) if ids else 0


def main():
    print("=== Financial Job Board Scraper — Metro Vancouver ===", flush=True)
    print(f"Token: {TOKEN[:12]}...", flush=True)

    tasks = [
        ('apify~linkedin-jobs-scraper', {
            "queries": [
                "investment representative Vancouver BC",
                "financial services representative Vancouver",
                "associate financial advisor Vancouver",
                "investment associate Vancouver BC",
                "associate wealth advisor Vancouver",
                "customer experience associate bank Vancouver",
                "customer experience representative credit union Vancouver"
            ],
            "location": "Vancouver, British Columbia, Canada",
            "maxResults": 50
        }, 'LinkedIn'),
        ('apify~indeed-scraper', {
            "country": "CA",
            "location": "Vancouver, BC",
            "position": "investment representative OR financial services representative OR associate financial advisor OR investment associate OR customer experience associate OR customer experience representative",
            "maxItems": 50
        }, 'Indeed'),
    ]

    for kw in ['investment representative', 'financial services representative',
               'associate financial advisor', 'customer experience associate bank']:
        tasks.append(('bebity~glassdoor-jobs-scraper', {
            "keyword": kw,
            "location": "Vancouver, BC, Canada",
            "maxItems": 20
        }, f'Glassdoor:{kw[:25]}'))

    tasks.append(('apify~zip-recruiter-scraper', {
        "queries": [
            "investment representative Vancouver BC",
            "financial services representative Vancouver",
            "associate financial advisor Vancouver BC",
            "customer experience associate Vancouver BC"
        ],
        "location": "Vancouver, BC",
        "maxItems": 40
    }, 'ZipRecruiter'))

    bank_urls = [
        "https://jobs.rbc.com/ca/en/search-results?keywords=investment+representative&location=Vancouver",
        "https://jobs.rbc.com/ca/en/search-results?keywords=financial+services+representative&location=Vancouver",
        "https://jobs.rbc.com/ca/en/search-results?keywords=customer+experience+associate&location=Vancouver",
        "https://jobs.td.com/en-CA/job-search-results/?keyword=investment+representative&location=British+Columbia",
        "https://jobs.td.com/en-CA/job-search-results/?keyword=financial+services+representative&location=British+Columbia",
        "https://jobs.td.com/en-CA/job-search-results/?keyword=customer+experience+associate&location=British+Columbia",
        "https://jobs.scotiabank.com/search?q=investment+representative&l=Vancouver%2C+BC",
        "https://jobs.scotiabank.com/search?q=financial+services+representative&l=Vancouver%2C+BC",
        "https://jobs.scotiabank.com/search?q=customer+experience+associate&l=Vancouver%2C+BC",
        "https://bmo.wd3.myworkdayjobs.com/External/jobs?q=investment+representative&locations=Vancouver",
        "https://bmo.wd3.myworkdayjobs.com/External/jobs?q=customer+experience+associate&locations=Vancouver",
        "https://careers.cibc.com/en/search-results?keywords=investment+representative&location=Vancouver",
        "https://careers.cibc.com/en/search-results?keywords=customer+experience+associate&location=Vancouver",
    ]
    cu_urls = [
        "https://www.vancity.com/AboutVancity/Careers/",
        "https://www.coastcapitalsavings.com/careers/current-opportunities",
        "https://www.firstwestcu.ca/about/careers/",
        "https://www.blueshorefinancial.com/about/careers",
        "https://www.prosperacu.ca/about-us/careers/",
        "https://www.wscu.com/about/careers",
        "https://www.gffg.com/about-gf/careers/",
        "https://www.khalsacu.ca/about-us/careers",
        "https://www.integriscu.ca/about/careers",
    ]

    all_urls = bank_urls + cu_urls
    for i in range(0, len(all_urls), 5):
        batch = all_urls[i:i+5]
        tasks.append(('apify~website-content-crawler', {
            "startUrls": [{"url": u} for u in batch],
            "maxCrawlDepth": 1,
            "maxResults": 50
        }, f'WebCrawler-{i//5+1}'))

    print(f"\nLaunching {len(tasks)} actor tasks...", flush=True)
    all_raw = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(run_actor, t[0], t[1], t[2]): t[2] for t in tasks}
        for fut in as_completed(futures):
            results = fut.result()
            all_raw.extend(results)

    print(f"\nTotal raw items: {len(all_raw)}", flush=True)

    normalized = []
    for item, label in all_raw:
        job = normalize_job(item, label)
        if job['title']:
            normalized.append(job)

    print(f"Items with titles: {len(normalized)}", flush=True)

    filtered = [j for j in normalized
                if is_target_role(j['title'], j['description'])
                and is_metro_van(j['location'], j['description'])]
    print(f"Matching target roles in Metro Van: {len(filtered)}", flush=True)

    scored = []
    for job in filtered:
        fit_score, db, db_triggered, sigs, why, hook = score_job(job)
        scored.append({
            'company': job['company'] or 'Unknown',
            'logo': get_logo(job['company'] or 'XX'),
            'role': job['title'],
            'location': format_location(job['location'], job['description']),
            'arrangement': get_arrangement(job['description'], job['employmentType']),
            'employment': get_employment(job['employmentType'], job['title'], job['description']),
            'salary': parse_salary_display(job['salary'], job['description']),
            'source': get_source_display(job['source_label'], job['company']),
            'url': job['url'],
            'fitScore': fit_score,
            'dealbreaker': db,
            'dealbreakersTriggered': db_triggered,
            'signals': sigs,
            'whyFit': why,
            'hook': hook,
        })

    with open('index.html', 'r') as f:
        index_html = f.read()

    existing, max_id = load_existing(index_html)
    print(f"Existing jobs: {len(existing)}, max ID: {max_id}", flush=True)

    new_jobs = []
    seen = set()
    for job in scored:
        key = (job['company'].lower().strip(), job['role'].lower().strip())
        if key in existing or key in seen:
            continue
        if job['fitScore'] >= 40 or job['dealbreaker']:
            seen.add(key)
            new_jobs.append(job)

    new_jobs.sort(key=lambda j: (j['dealbreaker'], -j['fitScore']))
    print(f"New qualifying jobs to add: {len(new_jobs)}", flush=True)

    if not new_jobs:
        print("Nothing new — no commit needed.", flush=True)
        sys.exit(0)

    current_id = max_id
    entries = []
    for job in new_jobs:
        current_id += 1
        entries.append(job_to_js(job, current_id))
        print(f"  [{job['fitScore']:3d}{'⛔' if job['dealbreaker'] else '  '}] {job['company']} — {job['role']}", flush=True)

    insert_block = '\n' + '\n'.join(entries) + '\n'
    updated = re.sub(
        r'(const JOBS\s*=\s*\[)(.*?)(\];)',
        lambda m: m.group(1) + m.group(2) + insert_block + m.group(3),
        index_html, flags=re.DOTALL, count=1
    )

    with open('index.html', 'w') as f:
        f.write(updated)

    print(f"\nDone — added {len(new_jobs)} new jobs.", flush=True)


if __name__ == '__main__':
    main()
