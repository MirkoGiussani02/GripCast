import os
import urllib.parse
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

HTTP_TIMEOUT = 10

# 1. Carica la API Key in modo sicuro dal file .env
load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    raise ValueError("ERRORE: Non ho trovato GEMINI_API_KEY nel file .env!")

client = genai.Client(api_key=api_key)

# ==========================================
# 2. DEFINIZIONE DEL TOOL PER L'AGENTE AI
# ==========================================
def controlla_meteo_tappa(nome_localita: str, data_giro: str, ora_passaggio: int) -> dict:
    """
    Ottiene le previsioni meteo multi-modello (ECMWF e ICON) per una specifica tappa del giro in moto.
    
    Args:
        nome_localita: Il nome della città, paese o passo di montagna (es. 'Chiavenna', 'Bormio').
        data_giro: La data del giro in formato YYYY-MM-DD (es. '2026-10-18').
        ora_passaggio: L'ora del giorno (da 0 a 23) in cui il motociclista passerà da lì (es. 11 per le 11:00).
    """
    nome_sicuro = str(nome_localita).strip()[:80]
    print(f"🏍️ [TOOL ATTIVATO] L'Agente sta controllando il meteo per: {nome_sicuro} alle ore {ora_passaggio}:00...")
    
    # Step A: Trova le coordinate GPS e l'altitudine della località (Geocoding gratuito)
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(nome_sicuro)}&count=1&language=it&format=json"
    try:
        geo_res = requests.get(geo_url, timeout=HTTP_TIMEOUT).json()
    except Exception:
        return {"errore": f"Impossibile contattare il servizio di geocoding per '{nome_sicuro}'."}
    
    if not geo_res.get("results"):
        return {"errore": f"Località '{nome_sicuro}' non trovata."}
    
    loc = geo_res["results"][0]
    lat, lon = loc["latitude"], loc["longitude"]
    altitudine = loc.get("elevation", "N/D")
    
    # Step B: Interroga 2 diversi modelli meteo (ECMWF Europeo + ICON Tedesco/Alpino) per confrontarli
    meteo_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m",
        "models": "ecmwf_ifs025,icon_seamless",
        "start_date": data_giro,
        "end_date": data_giro,
        "timezone": "Europe/Rome"
    }
    try:
        meteo_res = requests.get(meteo_url, params=params, timeout=HTTP_TIMEOUT).json()
    except Exception:
        return {"errore": f"Impossibile scaricare i dati meteo per '{nome_sicuro}'."}
    hourly = meteo_res.get("hourly", {})
    
    # Estraiamo i dati esattamente per l'ora di passaggio (indice da 0 a 23)
    idx = max(0, min(23, int(ora_passaggio)))
    
    return {
        "tappa": loc["name"],
        "regione": loc.get("admin1", ""),
        "altitudine_metri": altitudine,
        "ora_controllata": f"{idx}:00",
        "modello_ICON_Alpino": {
            "temperatura_C": hourly.get("temperature_2m_icon_seamless", [None]*24)[idx],
            "probabilita_pioggia_%": hourly.get("precipitation_probability_icon_seamless", [None]*24)[idx],
            "mm_pioggia": hourly.get("precipitation_icon_seamless", [None]*24)[idx],
            "vento_kmh": hourly.get("wind_speed_10m_icon_seamless", [None]*24)[idx],
        },
        "modello_ECMWF_Europeo": {
            "temperatura_C": hourly.get("temperature_2m_ecmwf_ifs025", [None]*24)[idx],
            "mm_pioggia": hourly.get("precipitation_ecmwf_ifs025", [None]*24)[idx],
            "vento_kmh": hourly.get("wind_speed_10m_ecmwf_ifs025", [None]*24)[idx],
        }
    }

# ==========================================
# 3. AVVIO DELL'AGENTE GRIPCAST
# ==========================================
istruzioni_sistema = """
Sei GripCast, un esperto Agente AI per motociclisti.
Quando l'utente ti dà un itinerario in moto con la data e l'orario di partenza:
1. Calcola o stima l'orario di passaggio per ogni singola tappa.
2. Usa SEMPRE lo strumento `controlla_meteo_tappa` per OGNI tappa dell'itinerario.
3. Confronta i due modelli meteo (ICON e ECMWF): segnala se sono concordi o discordi.
4. Valuta il grip dell'asfalto (sotto i 10°C le gomme sportive faticano a scaldarsi, con pioggia > 0.2mm l'asfalto è bagnato).
5. Restituisci una tabella chiara tappa per tappa e un VERDETTO FINALE (Semaforo VERDE, GIALLO o ROSSO) dicendo se conviene partire o modificare il giro.
"""

# Richiesta di prova (puoi cambiare date e tappe!)
richiesta_utente = """
Domenica 19 ottobre 2026 voglio fare un giro in moto partendo alle 09:00 da Cusano Milanino.
Le tappe sono:
1. Cusano Milanino (partenza ore 09:00)
2. Teglio (passaggio ore 11:45)
3. Aprica (passaggio ore 12:00)
4. Livigno (arrivo ore 14:00)
Dimmi che tempo trovo e se mi conviene andare!
"""

if __name__ == "__main__":
    print("Avvio di GripCast AI in corso...\n")

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=richiesta_utente,
        config=types.GenerateContentConfig(
            system_instruction=istruzioni_sistema,
            tools=[controlla_meteo_tappa],
            temperature=0.2,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )

    print("\n================ RISPOSTA DI GRIPCAST ================\n")
    print(response.text)
