import csv
import hashlib
import os
import random
import re
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import requests

# URL directe de recherche ciblée sur l'Agentur für Arbeit pour l'Ausbildung Kaufmann/-frau
TARGET_URL = "https://www.arbeitsagentur.de/jobsuche/suche?suchbereich=ausbildung&was=Kaufmann%2F-frau"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (Version/17.4 Safari/605.1.15)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.arbeitsagentur.de/"
    }

def scrape_arbeitsagentur():
    print("[+] Connexion à l'Agentur für Arbeit...")
    jobs = []
    
    try:
        time.sleep(random.uniform(1.0, 2.5))
        response = requests.get(TARGET_URL, headers=get_headers(), timeout=10)
        
        if response.status_code == 403:
            print("[!] Erreur 403 : Le site bloque l'accès automatisé. Essayez d'exécuter le script depuis GitHub Actions ou via une connexion résidentielle.")
            return jobs
            
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Recherche des éléments d'annonces sur la page de l'Agentur für Arbeit
            # (Note: l'Agentur charge parfois ses données via des API internes, ce bloc extrait les liens d'offres structurels)
            offer_elements = soup.find_all("a", href=True)
            
            for tag in offer_elements:
                href = tag['href']
                if "/jobsuche/jobdetail/" in href or "jobdetail" in href:
                    link = urljoin("https://www.arbeitsagentur.de", href)
                    title = tag.get_text(strip=True) or "Ausbildung Kaufmann/-frau"
                    
                    job_id = f"aa_{hashlib.md5(link.encode()).hexdigest()[:8]}"
                    
                    jobs.append({
                        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
                        "statut": "NOUVEAU",
                        "role_cible": "Ausbildung Kaufmann/-frau",
                        "intitule": title,
                        "entreprise": "Entreprise Allemande (Agentur)",
                        "lieu": "Deutschland",
                        "emails_rh": "Non détecté (Postuler via lien)",
                        "source": "Agentur für Arbeit",
                        "lien": link,
                        "id": job_id
                    })
        else:
            print(f"[!] Code HTTP inattendu : {response.status_code}")
            
    except Exception as e:
        print(f"[!] Erreur technique lors du scraping : {e}")
        
    return jobs

if __name__ == "__main__":
    extracted_jobs = scrape_arbeitsagentur()
    print(f"[*] Total offres brutes trouvées : {len(extracted_jobs)}")
    
    def send_to_google_sheet_webhook(jobs):
  webhook_url = os.environ.get('GOOGLE_SHEET_WEBHOOK_URL', '').strip()

  if not webhook_url:
    print('[!] Erreur : Secret GOOGLE_SHEET_WEBHOOK_URL introuvable.')
    return

  if not jobs:
    print('[i] Aucune offre à envoyer.')
    return

  try:
    headers = {'Content-Type': 'application/json'}
    response = requests.post(
        webhook_url,
        data=json.dumps(jobs),
        headers=headers,
        timeout=30,
    )
    print(f'[+] Réponse Webhook ({response.status_code}) : {response.text}')
  except Exception as e:
    print(f'[!] Échec envoi Webhook : {e}')
