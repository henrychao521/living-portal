import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

TDX_CLIENT_ID = os.getenv('TDX_CLIENT_ID', '')
TDX_CLIENT_SECRET = os.getenv('TDX_CLIENT_SECRET', '')
CWA_API_KEY = os.getenv('CWA_API_KEY', '')

TDX_TOKEN_URL = 'https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token'
TDX_BASE = 'https://tdx.transportdata.tw/api/basic'
