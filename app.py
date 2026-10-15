import os
import datetime
import requests
import gpxpy
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ==========================================
# 1. CONFIGURAZIONE PAGINA WEB (Streamlit)
# ==========================================
st.set_page_config(page_title="GripCast AI", page_icon="🏍️", layout="centered")
st.title("🏍️ GripCast AI")
st.markdown("Analisi meteo multi-modello (**ICON + ECMWF**) e valutazione grip per il tuo itinerario in moto.")

# ==========================================
# 2. GESTIONE CHIAVE API (Locale + Pubblica)
# ==========================================
load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")

# Barra laterale: permette anche ad altri utenti (se pubblichi il sito) di inserire la propria chiave
with st.sidebar:
    st.header("⚙️ Impostazioni")
    if not api_key:
        api_key = st.text_input("Inserisci Gemini API Key:", type="password")
        st.caption("Ottieni una chiave gratuita su [Google AI Studio](https://aistudio.google.com/)")
    else:
        st.success("API Key caricata dal file `.env` locale 🔒")
    
    st.divider()
    st.markdown("**Modelli Meteo Interrogati:**\n- 🏔️ **ICON (DWD):** Alta risoluzione Alpi/Italia\n- 🌍 **ECMWF:** Modello globale europeo")

if not api_key:
    st.warning("👈 Inserisci una Gemini API Key nella barra laterale (o nel file `.env`) per usare l'app.")
    st.stop()

client = genai.Client(api_key=api_key)

# ==========================================
# 3. I DUE TOOL DELL'AGENTE AI
# ==========================================
def _interroga_open_meteo(lat: float, lon: float, data_giro: str, ora_passaggio: int, nome_tappa: str, altitudine="N/D") -> dict:
    """Funzione ausiliaria che interroga i modelli ECMWF e ICON su Open-Meteo."""
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
    res = requests.get(meteo_url, params=params).json()
    hourly = res.get("hourly", {})
    idx = max(0, min(23, ora_passaggio))
    
    # Se l'altitudine non era nota dal geocoding, prendiamo quella del modello meteo
    if altitudine == "N/D":
        altitudine = res.get("elevation", "N/D")

    return {
        "tappa": nome_tappa,
        "altitudine_m": altitudine,
        "ora_controllata": f"{ora_passaggio:02d}:00",
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

def controlla_meteo_localita(nome_localita: str, data_giro: str, ora_passaggio: int) -> dict:
    """
    Usa questo tool quando conosci il NOME della città, paese o passo di montagna.
    
    Args:
        nome_localita: Nome della tappa (es. 'Chiavenna', 'Passo dello Stelvio', 'Livigno').
        data_giro: Data in formato YYYY-MM-DD.
        ora_passaggio: Ora del passaggio (intero da 0 a 23).
    """
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={nome_localita}&count=1&language=it&format=json"
    geo_res = requests.get(geo_url).json()
    
    if not geo_res.get("results"):
        return {"errore": f"Località '{nome_localita}' non trovata."}
    
    loc = geo_res["results"][0]
    return _interroga_open_meteo(
        lat=loc["latitude"],
        lon=loc["longitude"],
        data_giro=data_giro,
        ora_passaggio=ora_passaggio,
        nome_tappa=f"{loc['name']} ({loc.get('admin1', '')})",
        altitudine=loc.get("elevation", "N/D")
    )

def controlla_meteo_coordinate(lat: float, lon: float, data_giro: str, ora_passaggio: int, nome_tappa: str) -> dict:
    """
    Usa questo tool quando hai già le COORDINATE GPS esatte (latitudine e longitudine) estratte da un file GPX.
    
    Args:
        lat: Latitudine decimale.
        lon: Longitudine decimale.
        data_giro: Data in formato YYYY-MM-DD.
        ora_passaggio: Ora del passaggio (intero da 0 a 23).
        nome_tappa: Etichetta o nome della tappa.
    """
    return _interroga_open_meteo(lat, lon, data_giro, ora_passaggio, nome_tappa)

# ==========================================
# 4. INTERFACCIA UTENTE: SCELTA MODALITÀ
# ==========================================
col1, col2 = st.columns(2)
with col1:
    data_partenza = st.date_input("📅 Data del giro", datetime.date.today())
with col2:
    ora_partenza = st.time_input("⏰ Ora di partenza", datetime.time(9, 0))

# Creiamo le 2 schede per far scegliere all'utente come inserire il giro
tab_manuale, tab_gpx = st.tabs(["✍️ Inserimento Manuale Tappe", "📂 Carica File .GPX"])

prompt_agente = None

# --- TAB 1: INSERIMENTO MANUALE ---
with tab_manuale:
    st.subheader("Scrivi le tappe del tuo giro")
    st.caption("Inserisci una tappa per riga (puoi anche specificare l'orario se lo sai, altrimenti lo stimerà l'Agente partendo dall'ora di partenza).")
    
    testo_tappe = st.text_area(
        "Itinerario:",
        placeholder="Esempio:\nCusano Milanino (partenza 09:00)\nLecco\nChiavenna\nPasso dello Spluga\nMadesimo",
        height=150
    )
    
    if st.button("🚀 Analizza Tappe Manuali", type="primary", use_container_width=True):
        if not testo_tappe.strip():
            st.warning("Inserisci almeno una tappa prima di avviare l'analisi!")
        else:
            prompt_agente = f"""
            Data del giro: {data_partenza}
            Ora di partenza iniziale: {ora_partenza.strftime('%H:%M')}
            Tappe inserite dall'utente:
            {testo_tappe}
            
            Stima l'orario di passaggio per le tappe che non hanno un orario esplicito e usa lo strumento `controlla_meteo_localita` per ogni singola tappa.
            """

# --- TAB 2: CARICAMENTO FILE GPX ---
with tab_gpx:
    st.subheader("Importa tracciato GPX")
    st.caption("Carica un file `.gpx` esportato da Kurviger, Calimoto, Google MyMaps o dal tuo navigatore.")
    
    durata_stimata = st.slider("Durata totale stimata del giro (in ore):", min_value=1, max_value=12, value=4)
    file_gpx = st.file_uploader("Seleziona il file .GPX", type=["gpx"])
    
    if st.button("🚀 Analizza Tracciato GPX", type="primary", use_container_width=True):
        if not file_gpx:
            st.warning("Carica un file .gpx prima di avviare l'analisi!")
        else:
            gpx = gpxpy.parse(file_gpx)
            tappe_gpx = []
            
            # Caso A: Il GPX contiene Waypoint con nome
            if gpx.waypoints:
                tot_wp = len(gpx.waypoints)
                for i, wp in enumerate(gpx.waypoints):
                    nome = wp.name if wp.name else f"Tappa {i+1}"
                    fraz = i / max(1, tot_wp - 1)
                    ora_stimata = int((ora_partenza.hour + round(fraz * durata_stimata)) % 24)
                    tappe_gpx.append({"nome": nome, "lat": wp.latitude, "lon": wp.longitude, "ora": ora_stimata})
            
            # Caso B: Il GPX ha solo una traccia continua -> campioniamo 5 punti equidistanti lungo il giro
            elif gpx.tracks and gpx.tracks[0].segments:
                punti = gpx.tracks[0].segments[0].points
                num_campioni = min(5, len(punti))
                etichette = ["Partenza", "Tappa 2 (25%)", "Metà Giro (50%)", "Tappa 4 (75%)", "Arrivo"]
                
                for i in range(num_campioni):
                    idx = int(i * (len(punti) - 1) / max(1, num_campioni - 1))
                    pt = punti[idx]
                    fraz = i / max(1, num_campioni - 1)
                    ora_stimata = int((ora_partenza.hour + round(fraz * durata_stimata)) % 24)
                    tappe_gpx.append({
                        "nome": etichette[i] if num_campioni == 5 else f"Punto {i+1}",
                        "lat": pt.latitude,
                        "lon": pt.longitude,
                        "ora": ora_stimata
                    })
            
            if not tappe_gpx:
                st.error("Non sono riuscito a trovare coordinate valide in questo file GPX.")
            else:
                st.info(f"📍 Estratti {len(tappe_gpx)} punti chiave dal tracciato GPX!")
                prompt_agente = f"Data del giro: {data_partenza}\nCoordinate tappe estratte dal GPX:\n"
                for t in tappe_gpx:
                    prompt_agente += f"- {t['nome']}: Lat {t['lat']:.4f}, Lon {t['lon']:.4f} (Passaggio stimato ore {t['ora']}:00)\n"
                prompt_agente += "\nUsa lo strumento `controlla_meteo_coordinate` per ciascuno di questi punti."

# ==========================================
# 5. CHIAMATA ALL'AGENTE AI E RISULTATO
# ==========================================
istruzioni_sistema = """
Sei GripCast, un esperto Agente AI meteorologico per motociclisti.
Hai a disposizione due strumenti:
- `controlla_meteo_localita`: usalo quando l'utente inserisce i nomi delle città/passi manualmente.
- `controlla_meteo_coordinate`: usalo quando ricevi coordinate Lat/Lon da un file GPX.

Regole di analisi:
1. Interroga SEMPRE il tool appropriato per TUTTE le tappe prima di rispondere.
2. Confronta i due modelli (ICON Alpino e ECMWF Europeo): segnala se concordano o se c'è incertezza.
3. Valuta la sicurezza e il grip dell'asfalto: sotto i 10-12°C segnala che le gomme faticano a scaldarsi (specialmente in quota), e con pioggia > 0.2 mm segnala asfalto bagnato/scivoloso.
4. Mostra una **Tabella Riepilogativa** chiara con tutte le tappe, orario, altitudine, temperature, % pioggia e vento.
5. Concludi con un **VERDETTO FINALE (🟢 SEMAFORO VERDE / 🟡 SEMAFORO GIALLO / 🔴 SEMAFORO ROSSO)** e consigli pratici sull'abbigliamento (es. antipioggia, strato termico).
"""

if prompt_agente:
    with st.spinner("🏍️ L'Agente GripCast sta interrogando i modelli ECMWF e ICON per tutte le tappe..."):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt_agente,
                config=types.GenerateContentConfig(
                    system_instruction=istruzioni_sistema,
                    tools=[controlla_meteo_localita, controlla_meteo_coordinate],
                    temperature=0.2,
                ),
            )
            st.divider()
            st.markdown(response.text)
        except Exception as e:
            st.error(f"Errore durante l'interrogazione dell'Agente: {e}")