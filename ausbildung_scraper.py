import os
import re
import json
import uuid
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from urllib.parse import urljoin, urlparse

# Configuration
SHEET_ID = os.environ.get("SHEET_ID")
GCP_CRED = os.environ.get("GCP_CREDENTIALS")
KEYWORDS = ["Ausbildung Büromanagement", "Ausbildung E-Commerce", "Ausbildung Groß- und Außenhandel", "Ausbildung Spedition", "Ausbildung Tourismus"]
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
BAD_EMAILS = ['sentry', 'example', 'wix', 'png', 'jpg', 'jpeg', 'gif', 'tracker', 'noreply', 'no-reply', 'test']

def get_sheet():
    creds_dict = json.loads(GCP_CRED)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1

def extract_emails(text):
    # Gestion de l'obfuscation et extraction stricte
    text = text.replace('[at]', '@').replace('(at)', '@')
    regex = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = set(re.findall(regex, text))
    clean_emails = []
    for email in emails:
        email = email.lower()
        if not any(bad in email for bad in BAD_EMAILS):
            clean_emails.append(email)
    return clean_emails

def deep_scrape(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        emails = extract_emails(res.text)
        if emails: return emails
        
        # Deep scraping des pages de contact
        soup = BeautifulSoup(res.text, 'html.parser')
        base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        targets = ['/kontakt', '/impressum', '/karriere', '/ausbildung']
        
        for link in soup.find_all('a', href=True):
            href = link['href'].lower()
            if any(t in href for t in targets):
                full_url = urljoin(base_url, link['href'])
                sub_res = requests.get(full_url, headers=HEADERS, timeout=10)
                sub_emails = extract_emails(sub_res.text)
                if sub_emails:
                    emails.extend(sub_emails)
        return list(set(emails))
    except Exception as e:
        return []

def scrape_duckduckgo(keyword):
    results = []
    url = "https://html.duckduckgo.com/html/"
    data = {'q': f"{keyword} site:.de", 'b': ''}
    
    while True:
        try:
            time.sleep(2) # Anti-ban
            res = requests.post(url, headers=HEADERS, data=data, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            links = soup.find_all('a', class_='result__url')
            
            if not links: break
            
            for link in links:
                company_url = link.get('href')
                if company_url.startswith('//'):
                    company_url = 'https:' + company_url
                    
                role_cible = keyword.replace("Ausbildung ", "").strip()
                emails = deep_scrape(company_url)
                
                if emails:
                    results.append([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "NOUVEAU",
                        role_cible,
                        f"Ausbildung {role_cible}",
                        urlparse(company_url).netloc.replace('www.', ''),
                        "Deutschland",
                        ", ".join(emails),
                        "DuckDuckGo",
                        company_url,
                        str(uuid.uuid4())
                    ])
            
            # Pagination
            next_btn = soup.find('div', class_='nav-step')
            if next_btn and next_btn.find('form'):
                data = {i['name']: i.get('value', '') for i in next_btn.find('form').find_all('input')}
            else:
                break
        except Exception as e:
            break
    return results

def main():
    sheet = get_sheet()
    existing_ids = sheet.col_values(10) # Colonne J (ID)
    
    all_leads = []
    for kw in KEYWORDS:
        all_leads.extend(scrape_duckduckgo(kw))
        
    new_rows = []
    for lead in all_leads:
        if lead[9] not in existing_ids: # Eviter les doublons stricts
            new_rows.append(lead)
            existing_ids.append(lead[9])
            
    if new_rows:
        sheet.append_rows(new_rows)
        print(f"{len(new_rows)} nouvelles offres ajoutées.")
    else:
        print("Aucune nouvelle offre trouvée.")

if __name__ == "__main__":
    main()
