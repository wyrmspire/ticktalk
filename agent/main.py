import functions_framework
import requests
from dotenv import load_dotenv
import os
from google.cloud import firestore
import datetime
import logging
from vertexai import init as vertex_init
from vertexai.generative_models import GenerativeModel
import json

logging.basicConfig(level=logging.DEBUG)

load_dotenv()

DATA_API_BASE = os.getenv("DATA_API_BASE", "https://ticktalk-caddy-956251883619.us-central1.run.app")
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "ticktalk-472521")
MODEL_NAME = "gemini-2.5-flash"
LOCATION = "us-east1"

try:
    db = firestore.Client(project=PROJECT_ID)
    vertex_init(project=PROJECT_ID, location=LOCATION)
    model = GenerativeModel(MODEL_NAME)
except Exception as e:
    logging.error(f"Init error: {str(e)}")

def get_bars_from_service(symbol, timeframe, start, end):
    url = f"{DATA_API_BASE}/api/bars?symbol={symbol}&tf={timeframe}&start={start}&end={end}&live=false"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json().get("series", [])
    except requests.RequestException as e:
        return {"error": str(e)}

@functions_framework.http
def handler(request):
    logging.debug(f"Request path: {request.path}, method: {request.method}, headers: {request.headers}")
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS, GET',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Content-Type': 'application/json'
    }

    if request.method == 'OPTIONS':
        return '', 204, headers

    if request.path == '/bars':
        symbol = request.args.get("symbol", "MES")
        timeframe = request.args.get("tf", "5m")
        start = request.args.get("start")
        end = request.args.get("end")
        if not (start and end):
            return (json.dumps({"error": "Missing start/end"}), 400, headers)
        bars = get_bars_from_service(symbol, timeframe, start, end)
        if "error" in bars:
            return (json.dumps(bars), 502, headers)
        return (json.dumps({"series": bars}), 200, headers)

    elif request.path == '/journal' and request.method == 'POST':
        try:
            trade_data = request.get_json()
            logging.debug(f"Parsed JSON: {trade_data}")
            required_fields = ["symbol", "entry", "stop", "target", "notes"]
            if not all(field in trade_data for field in required_fields):
                return (json.dumps({"error": "Missing required fields in trade data"}), 400, headers)
            trade_data['created_at'] = datetime.datetime.now(datetime.timezone.utc)
            update_time, doc_ref = db.collection('trades').add(trade_data)
            logging.info(f"Successfully added trade {doc_ref.id} at {update_time}")
            return (json.dumps({"success": True, "trade_id": doc_ref.id}), 201, headers)
        except Exception as e:
            logging.error(f"Journal error: {str(e)}")
            return (json.dumps({"error": "Could not save trade to database"}), 500, headers)

    elif request.path == '/ask' and request.method == 'POST':
        try:
            data = request.get_json()
            user_query = data.get('query')
            if not user_query:
                return (json.dumps({"response": "Error: Missing 'query' in request body."}), 400, headers)
            response = model.generate_content(user_query)
            return (json.dumps({"response": response.text}), 200, headers)
        except Exception as e:
            logging.error(f"Gemini error: {str(e)}")
            return (json.dumps({"response": f"An internal error occurred: {e}"}), 500, headers)

    return (json.dumps({"error": "Invalid path or method"}), 404, headers)