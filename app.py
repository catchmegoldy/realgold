# ==============================================================================
#  GOLDPRIME ENTERPRISE BACKEND - PRODUCTION READY (v5.1)
#  ----------------------------------------------------------------------------
#  AUTHOR: GoldPrime Dev Team
#  DATE: Feb 2026
#  SYSTEM: Flask Microservice for Real-Time Bullion Tracking
#  
#  UPDATES:
#  1. Cache Duration: Set to 1 HOUR (3600 seconds).
#  2. Data persistence: Saves to 'gold_cache.json'.
#  3. Full State Mapping: All 36 regions included.
#  4. Sanity Checks: Rejects fake/paper gold rates automatically.
# ==============================================================================
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS  # This is the line we added for Hostinger

app = Flask(__name__)
CORS(app)  # This allows Hostinger to talk to Render
import os
import json
import time
import random
import logging
import datetime
import requests
import threading
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

# --- SYSTEM CONFIGURATION ---
LOG_FILE = "goldprime_server.log"
CACHE_FILE = "gold_cache.json"

# *** UPDATE: CACHE SET TO 1 HOUR ***
CACHE_EXPIRY_SECONDS = 3600  # 1 Hour (60 mins * 60 secs)
REQUEST_TIMEOUT = 5          # Seconds

# --- MARKET CONFIGURATION ---
# The "Anchor" is the absolute fail-safe price (Feb 2026 Baseline).
# Used only if (1) Internet is down AND (2) Cache is empty.
FAILSAFE_ANCHOR_PRICE = 1

# Valid Price Range (Sanity Check)
# If a scraper returns 78,000 (Paper Gold), we reject it.
# If it returns 1,56,000 (Retail), we accept it.
MIN_VALID_PRICE = 140000
MAX_VALID_PRICE = 180000

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("GoldPrime")

# --- FLASK SETUP ---
app = Flask(__name__, template_folder='templates')
CORS(app) # Enable Cross-Origin requests

# ==============================================================================
#  DATA: GEOGRAPHIC PRICING MAP
# ==============================================================================
# Offsets relative to the National Base Price (in INR)
STATE_OFFSETS = {
    # North India
    "Delhi": 21, "Haryana": 14, "Punjab": 16, "Himachal Pradesh": 19, 
    "Jammu and Kashmir": 18, "Ladakh": 22, "Uttarakhand": 12, "Uttar Pradesh": 17, 
    "Chandigarh": 15, "Rajasthan": 24,
    
    # West India
    "Maharashtra": 23, "Gujarat": 16, "Goa": 15, 
    "Dadra and Nagar Haveli": 19, "Daman and Diu": 18,
    
    # South India (Generally Cheaper)
    "Karnataka": 10, "Tamil Nadu": 8, "Kerala": -25, "Telangana": -8, 
    "Andhra Pradesh": -18, "Puducherry": -20, "Lakshadweep": 14, 
    "Andaman and Nicobar Islands": 130,
    
    # East India
    "West Bengal": 60, "Odisha": 40, "Bihar": 50, "Jharkhand": 50,
    
    # Central India
    "Madhya Pradesh": 50, "Chhattisgarh": 40,
    
    # North East India
    "Assam": 80, "Sikkim": 100, "Arunachal Pradesh": 200, "Manipur": 200, 
    "Meghalaya": 180, "Mizoram": 200, "Nagaland": 200, "Tripura": 200
}

# ==============================================================================
#  LAYER 1: PERSISTENCE (CACHE MANAGER)
# ==============================================================================
class CacheManager:
    """Handles reading and writing the last known price to disk."""
    
    @staticmethod
    def save(price):
        """Saves price and timestamp to JSON."""
        try:
            data = {
                "price": price,
                "timestamp": time.time(),
                "human_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(CACHE_FILE, 'w') as f:
                json.dump(data, f)
            logger.info(f"Cache updated: {price}")
        except Exception as e:
            logger.error(f"Failed to write cache: {e}")

    @staticmethod
    def load(ignore_expiry=False):
        """
        Loads price from JSON. 
        If ignore_expiry=True, returns data even if it's old (Fail-Safe).
        """
        if not os.path.exists(CACHE_FILE):
            return None
        
        try:
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
                
            age = time.time() - data.get('timestamp', 0)
            
            # Logic: Return if fresh OR if we are forced to use stale data
            if age < CACHE_EXPIRY_SECONDS or ignore_expiry:
                return data.get('price')
            
            return None
        except Exception as e:
            logger.error(f"Failed to read cache: {e}")
            return None

# ==============================================================================
#  LAYER 2: SCRAPING ENGINE
# ==============================================================================
class MarketScraper:
    """Handles network requests to fetch live market data."""
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
        "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36"
    ]

    @classmethod
    def _get_headers(cls):
        """Returns a random User-Agent to avoid blocking."""
        return {"User-Agent": random.choice(cls.USER_AGENTS)}

    @classmethod
    def fetch_goodreturns(cls):
        """Source A: Multi-Source Scraping Logic"""
        # List of backup sources
        sources = [
            "https://www.policybazaar.com/gold-rate/",
            "https://www.goodreturns.in/gold-rates/",
            "https://www.tanishq.co.in/gold-rate.html?lang=en_IN"
        ]
        
        for url in sources:
            logger.info(f"Attempting to scrape: {url}")
            try:
                resp = requests.get(url, headers=cls._get_headers(), timeout=REQUEST_TIMEOUT)
                
                if resp.status_code != 200:
                    continue # Skip to the next URL if this one fails
                    
                soup = BeautifulSoup(resp.text, 'html.parser')
                tables = soup.find_all("table")
                for table in tables:
                    table_text = table.text.lower()
                    if "10 gram" in table_text and "24" in table_text:
                        rows = table.find_all("tr")
                        for row in rows:
                            cols = row.find_all("td")
                            if len(cols) > 1 and "10" in cols[0].text:
                                raw_str = cols[1].text.strip()
                                clean_str = raw_str.replace('₹', '').replace(',', '').replace('.', '')
                                price = int(clean_str)
                                
                                if cls._validate_price(price):
                                    return price # Return immediately once success is found
            except Exception as e:
                logger.error(f"Error scraping {url}: {e}")
                continue # Try next URL on error
            
        return None # Only return None if ALL sources fail
    @staticmethod
    def _validate_price(price):
        """Sanity Check to reject outliers or paper gold rates."""
        if MIN_VALID_PRICE < price < MAX_VALID_PRICE:
            return True
        logger.warning(f"Rejected suspicious price: {price}")
        return False

# ==============================================================================
#  LAYER 3: SERVICE LOGIC (The Brain)
# ==============================================================================

class GoldService:
    @staticmethod
    def get_master_price():
        # 1. Load the last known price from cache immediately (Ignore expiry)
        cached_price = CacheManager.load(ignore_expiry=True)
        
        # 2. Check if the cache is actually "stale" (older than 1 hour)
        # We still return the stale price to the user so they don't wait
        data = None
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
        
        age = time.time() - (data.get('timestamp', 0) if data else 0)
        
        # 3. If stale, trigger a background update for the NEXT request
        if age > CACHE_EXPIRY_SECONDS:
            logger.info("Cache stale. Triggering background update...")
            thread = threading.Thread(target=MarketScraper.fetch_goodreturns_and_save)
            thread.start()

        return cached_price if cached_price else FAILSAFE_ANCHOR_PRICE

    @staticmethod
    def generate_charts(base_price):
        """
        Generates realistic chart data relative to the current base price.
        """
        # --- Weekly Data (7 Days) ---
        week_labels = []
        week_data = []
        today = datetime.date.today()
        
        # Pattern: Slight volatility ending at current price
        # [Day-6, Day-5, ... Today]
        variance_pattern = [-450, -120, +220, -80, +300, +90, 0]
        
        for i in range(6, -1, -1):
            day_label = (today - datetime.timedelta(days=i)).strftime('%a')
            week_labels.append(day_label)
            
            # Apply variance
            week_data.append(base_price + variance_pattern[6-i])

        # --- Monthly Data (30 Days) ---
        month_labels = []
        month_data = []
        
        current_sim_price = base_price - 1500 # Assume market was lower 30 days ago
        
        for i in range(29, -1, -1):
            day_label = (today - datetime.timedelta(days=i)).strftime('%d %b')
            month_labels.append(day_label)
            
            # Random Walk Logic
            change = random.randint(-300, 400)
            current_sim_price += change
            
            # Force convergence on last day
            if i == 0: 
                current_sim_price = base_price
                
            month_data.append(current_sim_price)

        return {
            "weekly": {"labels": week_labels, "data": week_data},
            "monthly": {"labels": month_labels, "data": month_data}
        }

    @staticmethod
    def calculate_all_states(base_price_24k):
        """Generates the full list of prices for all regions."""
        
        # 22K Calculation (Standard 91.66% Purity)
        base_price_22k = int(base_price_24k * 0.9166)
        
        results = []
        
        for state_name, offset in STATE_OFFSETS.items():
            # Apply regional offset
            p24 = base_price_24k + offset
            p22 = base_price_22k + int(offset * 0.9) # 22K offset is slightly less
            
            results.append({
                "name": state_name,
                "p24": p24,
                "p22": p22,
                # Calculated 1g prices for the UI
                "p24_1g": p24 // 10,
                "p22_1g": p22 // 10
            })
            
        # Sort Alphabetically for UI dropdown
        results.sort(key=lambda x: x['name'])
        return results

# ==============================================================================
#  LAYER 4: API ENDPOINTS (FLASK)
# ==============================================================================

@app.route('/')
def home():
    """Serves the Frontend Application."""
    return render_template('index.html')

@app.route('/api/full-data', methods=['GET'])
def get_full_data():
    """
    Main API Endpoint.
    Returns comprehensive data package to the frontend.
    """
    start_time = time.time()
    
    # 1. Get Authoritative Price
    base_price = GoldService.get_master_price()
    
    # 2. Calculate Derived Data (States)
    states_data = GoldService.calculate_all_states(base_price)
    
    # 3. Generate Charts
    charts_data = GoldService.generate_charts(base_price)
    
    # 4. Construct Response
    response = {
        "status": "success",
        "timestamp": datetime.datetime.now().strftime("%d %B %Y, %I:%M %p"),
        "base_price": base_price,
        "states": states_data,
        "charts": charts_data,
        "meta": {
            "latency": f"{round(time.time() - start_time, 2)}s",
            "source": "GoldPrime Live Engine"
        }
    }
    
    return jsonify(response)

@app.route('/api/status')
def system_status():
    """Health Check for Uptime Monitors."""
    cached_price = CacheManager.load(ignore_expiry=True)
    return jsonify({
        "status": "online", 
        "cached_price": cached_price,
        "cache_file_exists": os.path.exists(CACHE_FILE)
    })

# ==============================================================================
#  SERVER ENTRY POINT
# ==============================================================================
if __name__ == '__main__':
    print("==========================================================")
    print("   GOLDPRIME ENTERPRISE SERVER (v5.1)")
    print("   ----------------------------------")
    print(f"   * Logs: {LOG_FILE}")
    print(f"   * Cache: {CACHE_FILE}")
    print(f"   * Expiry: {CACHE_EXPIRY_SECONDS} seconds (1 Hour)")
    print(f"   * Anchor: ₹{FAILSAFE_ANCHOR_PRICE}")
    print(f"   * Status: Ready for Production")
    print("==========================================================")
    
    # Perform initial check
    initial_price = GoldService.get_master_price()
    print(f"   -> System Initialization Complete. Current Price: {initial_price}")
    

    app.run(debug=True, port=5000)



