import os
import json
import urllib.parse
from dotenv import load_dotenv
from breeze_connect import BreezeConnect

# Load variables from .env
load_dotenv()

# Read secrets
api_key = os.getenv("API_KEY")
api_secret = os.getenv("API_SECRET")
apisession = os.getenv("API_SESSION")

# Make sure all secrets are available
if not api_key:
    raise ValueError("API_KEY is missing from .env")

if not api_secret:
    raise ValueError("API_SECRET is missing from .env")

if not apisession:
    raise ValueError("API_SESSION is missing from .env")


OUTPUT_FILE = "data.json"

# Initialize SDK
breeze = BreezeConnect(api_key=api_key)

# Generate login URL
login_url = (
    "https://api.icicidirect.com/apiuser/login?api_key="
    + urllib.parse.quote_plus(api_key)
)

print("Login URL:")
print(login_url)

# Generate session
breeze.generate_session(
    api_secret=api_secret,
    session_token=apisession
)

# Get historical data
data = breeze.get_historical_data_v2(
    interval="1minute",
    from_date="2026-01-01T09:00:00.000Z",
    to_date="2026-01-01T09:45:30.000Z",
    stock_code="RELIND",
    exchange_code="NSE",
    product_type="cash"
)

# Save response
with open(OUTPUT_FILE, "w", encoding="utf-8") as json_file:
    json.dump(data, json_file, indent=4, ensure_ascii=False)

print("DONE: Data saved to data.json")