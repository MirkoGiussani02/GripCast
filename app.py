import os
import re
import time
import datetime
import urllib.parse
import requests
import gpxpy
import folium
from streamlit_folium import st_folium
import streamlit as st
from pydantic import BaseModel, Field
from typing import List, Literal
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ==========================================
# 1. CONFIGURAZIONE PAGINA E STILE CSS CUSTOM
# ==========================================
st.set_page_config(page_title="GripCast AI", page_icon="🏍️", layout="wide")

st.markdown("""
<style>
    /* Nascondi la sidebar e il pulsante di toggle */
    [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none !important; }
    /* Margini extra per centrare meglio il contenuto */
    .block-container { padding-top: 2rem; }
    .stage-card { background: linear-gradient(145deg, #1a1d24, #12141a); border-radius: 14px; padding: 18px 22px; margin-bottom: 14px; border: 1px solid #2b303c; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
    .stage-VERDE { border-left: 7px solid #00e676; }
    .stage-GIALLO { border-left: 7px solid #ffea00; }
    .stage-ROSSO { border-left: 7px solid #ff1744; }
    .metric-pill { background: #222630; border-radius: 10px; padding: 10px 12px; text-align: center; border: 1px solid #323846; }
    .metric-label { font-size: 0.72rem; color: #9aa0a6; text-transform: uppercase; letter-spacing: 0.8px; }
    .metric-val { font-size: 1.15rem; font-weight: 700; color: #ffffff; margin-top: 2px; }
    .gear-card { background: #1a1d24; border: 1px solid #2d3340; border-radius: 12px; padding: 14px 10px; text-align: center; height: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }
    .gear-icon { font-size: 2.2rem; margin-bottom: 6px; }
    .gear-title { font-size: 0.75rem; color: #9aa0a6; text-transform: uppercase; letter-spacing: 0.5px; }
    .gear-value { font-size: 0.92rem; font-weight: 600; color: #f1f3f4; margin-top: 4px; }
    .grip-box { background: linear-gradient(145deg, #1a1d24, #14171f); border: 1px solid #2d3340; border-radius: 14px; padding: 20px; height: 100%; }
    .grip-bar-bg { background: #2b303c; border-radius: 999px; height: 16px; width: 100%; overflow: hidden; margin: 10px 0; }
    details > summary { list-style: none; outline: none; }
    details > summary::-webkit-details-marker { display: none; }
</style>
""", unsafe_allow_html=True)

_, _col_centro, _ = st.columns([1, 2, 1])
with _col_centro:
    st.title("🏍️ GripCast AI — Telemetry & Weather")
    st.markdown("Analisi meteo multi-modello (**ICON + ECMWF**), telemetria grip asfalto e setup abbigliamento.")

# ==========================================
# 2. MODELLI DATI PER L'OUTPUT GRAFICO
# ==========================================
class TappaReport(BaseModel):
    nome: str = Field(description="Nome della località")
    orario: str = Field(description="Orario stimato (es. '14:00') oppure 'Intera Giornata'")
    altitudine_m: int = Field(description="Altitudine in metri")
    condizione: Literal["SOLE", "POCO_NUVOLOSO", "NUVOLOSO", "PIOGGIA", "TEMPORALE", "FREDDO"]
    temp_icon: float = Field(description="Temperatura °C ICON")
    temp_ecmwf: float = Field(description="Temperatura °C ECMWF")
    prob_pioggia_max: int = Field(description="Probabilità pioggia %")
    mm_pioggia_max: float = Field(description="Millimetri pioggia")
    vento_kmh: float = Field(description="Velocità vento km/h")
    accordo_modelli: Literal["ALTO", "MEDIO", "BASSO"]
    livello_rischio: Literal["VERDE", "GIALLO", "ROSSO"]
    nota_flash: str = Field(description="Frase telegrafica (max 10 parole)")
    dettaglio_discordanza: str = Field(description="Se accordo_modelli è BASSO, spiega la differenza (es. 'ICON prevede 80% pioggia alle 14, ECMWF 10%'). Altrimenti vuoto.", default="")
    allerta_pioggia_orari: str = Field(description="Se orario è 'Intera Giornata' e c'è pioggia, indica gli orari ESATTI (es. 'Pioggia tra le 14:00 e le 16:00'). Altrimenti vuoto.", default="")

class GripReport(BaseModel):
    percentuale_grip: int = Field(description="Indice di tenuta da 10 a 100")
    stato_gomme: Literal["OTTIMALE", "TIEPIDE", "FREDDE", "SCIVOLOSO_BAGNATO"]
    condizione_asfalto: str = Field(description="Condizione asfalto (max 4 parole)")
    consiglio_guida: str = Field(description="Consiglio pratico telegrafico (max 12 parole)")

class KitAbbigliamento(BaseModel):
    casco_visiera: str = Field(description="Es. Visiera scura")
    giacca: str = Field(description="Es. Pelle, Traforata, Membrana")
    strato_termico: str = Field(description="Es. Non necessario")
    guanti: str = Field(description="Es. Estivi")
    antipioggia: Literal["NON_SERVE", "SOTTOSELLA_PRONTO", "DA_INDOSSARE"]

class AnalisiGiroCompleta(BaseModel):
    semaforo: Literal["VERDE", "GIALLO", "ROSSO"]
    titolo_verdetto: str = Field(description="Titolo d'impatto di max 7 parole")
    sintesi_flash: str = Field(description="Riassunto diretto di max 20 parole")
    grip: GripReport
    abbigliamento: KitAbbigliamento
    tappe: List[TappaReport]

# ==========================================
# 3. GESTIONE CHIAVE API
# ==========================================
load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")

_, _col_api, _ = st.columns([1, 2, 1])
with _col_api:
    if not api_key:
        api_key = st.text_input("🔑 Inserisci Gemini API Key:", type="password")

    if not api_key:
        st.warning("🔑 Inserisci una Gemini API Key qui sopra per attivare la telemetria.")
        st.stop()

client = genai.Client(api_key=api_key)

# ==========================================
# 4. FUNZIONI GEOCODING E METEO
# ==========================================
def estrai_ora_da_testo(testo: str):
    match = re.search(r"\b(\d{1,2})[:.]\d{2}\b", testo)
    return int(match.group(1)) if match else None

def cerca_coordinate_localita(riga_testo: str):
    nome_pulito = re.sub(r"\(.*?\)", "", riga_testo)
    nome_pulito = re.sub(r"\b\d{1,2}[:.]\d{2}\b", "", nome_pulito).strip(" -•,\t")
    if not nome_pulito: return None
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(nome_pulito)}&count=1&language=it&format=json"
    try:
        res = requests.get(geo_url, timeout=10).json()
        if res.get("results"):
            loc = res["results"][0]
            return {"nome": f"{loc['name']} ({loc.get('admin1', '')})", "lat": loc["latitude"], "lon": loc["longitude"], "altitudine": int(loc.get("elevation", 0))}
    except Exception:
        pass
    return None

def estrai_dati_meteo(lat: float, lon: float, data_giro: str, ora_opt, nome_tappa: str, altitudine=0) -> str:
    meteo_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m",
        "models": "ecmwf_ifs025,icon_seamless",
        "start_date": data_giro, "end_date": data_giro,
        "timezone": "Europe/Rome"
    }
    
    hourly = {}
    try:
        res = requests.get(meteo_url, params=params, timeout=10).json()
        if "error" in res:
            st.session_state.avviso_meteo_incompleto = True
        else:
            hourly = res.get("hourly", {})
            if not altitudine: altitudine = int(res.get("elevation", 0))
    except Exception:
        st.session_state.avviso_meteo_incompleto = True

    def safe_get(chiave):
        arr = hourly.get(chiave, [])
        if len(arr) < 24:
            st.session_state.avviso_meteo_incompleto = True
            return [0.0] * 24
        return arr

    t_icon = safe_get('temperature_2m_icon_seamless')
    p_prob_icon = safe_get('precipitation_probability_icon_seamless')
    p_mm_icon = safe_get('precipitation_icon_seamless')
    v_icon = safe_get('wind_speed_10m_icon_seamless')
    
    t_ecmwf = safe_get('temperature_2m_ecmwf_ifs025')
    p_mm_ecmwf = safe_get('precipitation_ecmwf_ifs025')
    v_ecmwf = safe_get('wind_speed_10m_ecmwf_ifs025')

    testo = f"\n📍 Tappa: {nome_tappa} (Altitudine: {altitudine}m)\n"
    
    if ora_opt is not None:
        idx = max(0, min(23, ora_opt))
        testo += f"Ora analizzata: {ora_opt:02d}:00\n"
        testo += f"ICON -> Temp: {t_icon[idx]}°C | Pioggia: {p_prob_icon[idx]}%, {p_mm_icon[idx]}mm | Vento: {v_icon[idx]}km/h\n"
        testo += f"ECMWF -> Temp: {t_ecmwf[idx]}°C | Pioggia: {p_mm_ecmwf[idx]}mm | Vento: {v_ecmwf[idx]}km/h\n"
    else:
        testo += "Ora analizzata: INTERA GIORNATA (fascia 08:00 - 20:00)\n"
        for h in range(8, 21):
            testo += f"- Ore {h:02d}:00 -> ICON (Temp: {t_icon[h]}°C, Piogg: {p_prob_icon[h]}%, {p_mm_icon[h]}mm) | ECMWF (Temp: {t_ecmwf[h]}°C, Piogg: {p_mm_ecmwf[h]}mm)\n"
    return testo

def genera_link_google_maps(punti_tappe: list) -> str:
    if len(punti_tappe) < 2: return ""
    origin = f"{punti_tappe[0]['lat']},{punti_tappe[0]['lon']}"
    destination = f"{punti_tappe[-1]['lat']},{punti_tappe[-1]['lon']}"
    url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={destination}&travelmode=driving"
    if len(punti_tappe) > 2:
        waypoints = "|".join([f"{p['lat']},{p['lon']}" for p in punti_tappe[1:-1]])
        url += f"&waypoints={urllib.parse.quote(waypoints)}"
    return url

def disegna_mappa(punti_tappe: list, traccia_completa: list = None, report_ai: AnalisiGiroCompleta = None):
    lat_centro = sum(p["lat"] for p in punti_tappe) / len(punti_tappe)
    lon_centro = sum(p["lon"] for p in punti_tappe) / len(punti_tappe)
    m = folium.Map(location=[lat_centro, lon_centro], zoom_start=9, tiles="https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}", attr="Google Maps")
    coordinate_linea = traccia_completa if traccia_completa else [[p["lat"], p["lon"]] for p in punti_tappe]
    folium.PolyLine(coordinate_linea, color="#FF4B4B", weight=5, opacity=0.85).add_to(m)
    
    for i, p in enumerate(punti_tappe):
        colore, extra_info = "blue", ""
        if report_ai and i < len(report_ai.tappe):
            t_rep = report_ai.tappe[i]
            colore = {"VERDE": "green", "GIALLO": "orange", "ROSSO": "red"}.get(t_rep.livello_rischio, "blue")
            extra_info = f"<br>🌡️ {t_rep.temp_icon}°C | 🌧️ {t_rep.prob_pioggia_max}%"
        elif i == 0: colore = "green"
        elif i == len(punti_tappe) - 1: colore = "red"
            
        icona = "play" if i == 0 else ("flag-checkered" if i == len(punti_tappe) - 1 else "info-sign")
        folium.Marker(
            location=[p["lat"], p["lon"]], popup=folium.Popup(f"<b>{i+1}. {p['nome']}</b>{extra_info}", max_width=260),
            tooltip=f"{i+1}. {p['nome']}", icon=folium.Icon(color=colore, icon=icona)
        ).add_to(m)
    m.fit_bounds([[p["lat"], p["lon"]] for p in punti_tappe])
    return m

# ==========================================
# 5. INPUT UTENTE E STATI
# ==========================================
if "report_strutturato" not in st.session_state: st.session_state.report_strutturato = None
if "punti_mappa" not in st.session_state: st.session_state.punti_mappa = []
if "traccia_mappa" not in st.session_state: st.session_state.traccia_mappa = None
if "avviso_meteo_incompleto" not in st.session_state: st.session_state.avviso_meteo_incompleto = False

TAB_MANUALE = "✍️ Inserimento Manuale Tappe"
TAB_GPX = "📂 Carica File .GPX"
prompt_agente = None
_, _col_form, _ = st.columns([1, 2, 1])
with _col_form:
    # L'ora e' disabilitata solo nel tab GPX quando la stima orari non e' attiva
    ora_disabilitata = (
        st.session_state.get("tab_attivo") == TAB_GPX
        and not st.session_state.get("applica_stima", False)
    )
    col_dt1, col_dt2 = st.columns(2)
    with col_dt1: 
        data_partenza = st.date_input("📅 Data del giro", datetime.date.today())
    with col_dt2: 
        ora_partenza = st.time_input("⏰ Ora di partenza", datetime.time(9, 0), disabled=ora_disabilitata)
    
    tab_manuale, tab_gpx = st.tabs([TAB_MANUALE, TAB_GPX], key="tab_attivo", on_change="rerun")
    
    with tab_manuale:
        testo_tappe = st.text_area(
            "Scrivi le tappe (una per riga). Se inserisci l'orario a lato, analizzerà l'ora esatta, altrimenti controllerà l'intera giornata:", 
            placeholder="Cusano Milanino\nTeglio 11:30\nAprica\nBormio\nLivigno 16:00", 
            height=130
        )
        if st.button("🚀 Analizza Tappe Manuali", type="primary", use_container_width=True):
            righe = [r.strip() for r in testo_tappe.split("\n") if r.strip()]
            if not righe:
                st.warning("Inserisci almeno una tappa!")
            else:
                st.session_state.avviso_meteo_incompleto = False 
                with st.spinner("🗺️ Download dati meteo in corso..."):
                    punti_trovati = []
                    dati_meteo_testo = ""
                    for r in righe:
                        info = cerca_coordinate_localita(r)
                        if info:
                            ora_trovata = estrai_ora_da_testo(r)
                            info['ora'] = ora_trovata
                            punti_trovati.append(info)
                            dati_meteo_testo += estrai_dati_meteo(info['lat'], info['lon'], str(data_partenza), ora_trovata, info['nome'], info['altitudine'])
                    
                    st.session_state.punti_mappa = punti_trovati
                    st.session_state.traccia_mappa = None
                prompt_agente = f"Data: {data_partenza}\nDati meteo grezzi estratti:\n{dati_meteo_testo}"
    
    with tab_gpx:
        applica_stima = st.checkbox("⏱️ Calcola l'orario di passaggio di ogni tappa in base all'ora di partenza (altrimenti valuta l'intera giornata)", value=False, key="applica_stima")
        
        if applica_stima:
            durata_stimata = st.slider(
                "Durata totale stimata del giro (ore):", 1, 12, 4,
                help="I file GPX contengono solo il tracciato geografico (GPS), non gli orari. GripCast userà questa durata per calcolare a che ora ti troverai in ogni punto stradale."
            )
        else:
            durata_stimata = 4
    
        file_gpx = st.file_uploader("Trascina il file .GPX", type=["gpx"])
        if st.button("🚀 Analizza Tracciato GPX", type="primary", use_container_width=True):
            if file_gpx:
                st.session_state.avviso_meteo_incompleto = False 
                gpx = gpxpy.parse(file_gpx)
                tappe_gpx, traccia_completa = [], []
                for track in gpx.tracks:
                    for segment in track.segments:
                        for idx_pt, pt in enumerate(segment.points):
                            if idx_pt % 5 == 0: traccia_completa.append([pt.latitude, pt.longitude])
                
                if gpx.waypoints:
                    for i, wp in enumerate(gpx.waypoints):
                        nome = wp.name if wp.name else f"Tappa {i+1}"
                        ora = int((ora_partenza.hour + round((i / max(1, len(gpx.waypoints) - 1)) * durata_stimata)) % 24) if applica_stima else None
                        tappe_gpx.append({"nome": nome, "lat": wp.latitude, "lon": wp.longitude, "ora": ora})
                elif gpx.tracks and gpx.tracks[0].segments:
                    punti = gpx.tracks[0].segments[0].points
                    num_campioni = min(5, len(punti))
                    etichette = ["Partenza", "Tappa 2", "Metà Giro", "Tappa 4", "Arrivo"]
                    for i in range(num_campioni):
                        idx = int(i * (len(punti) - 1) / max(1, num_campioni - 1))
                        ora = int((ora_partenza.hour + round((i / max(1, num_campioni - 1)) * durata_stimata)) % 24) if applica_stima else None
                        tappe_gpx.append({"nome": etichette[i], "lat": punti[idx].latitude, "lon": punti[idx].longitude, "ora": ora})
                
                if tappe_gpx:
                    with st.spinner("☁️ Download dati meteo del tracciato..."):
                        st.session_state.punti_mappa = tappe_gpx
                        st.session_state.traccia_mappa = traccia_completa if traccia_completa else None
                        dati_meteo_testo = ""
                        for t in tappe_gpx:
                            dati_meteo_testo += estrai_dati_meteo(t['lat'], t['lon'], str(data_partenza), t['ora'], t['nome'])
                            
                    prompt_agente = f"Data: {data_partenza}\nDati meteo grezzi estratti:\n{dati_meteo_testo}"
    
# ==========================================
# 6. ESECUZIONE AGENTE JSON
# ==========================================
istruzioni_sistema = """
Sei GripCast, ingegnere di pista e meteorologo per motociclisti. Riceverai i dati meteo grezzi estratti.
REGOLE FONDAMENTALI:
1. Se l'orario è "INTERA GIORNATA", analizza i dati della fascia 08:00-20:00. Compila il campo 'allerta_pioggia_orari' indicando le fasce orarie ESATTE in cui è prevista pioggia (es. "Forti rovesci tra le 14:00 e le 16:00, pioviggine alle 18:00"). NON scrivere genericamente "dalle 08:00 alle 20:00". Se non piove, lascia vuoto.
2. DISCORDANZA MODELLI: Se la probabilità di pioggia differisce di OLTRE IL 50% tra ICON ed ECMWF (oppure c'è una netta discrepanza sui mm), imposta accordo_modelli su "BASSO" e compila 'dettaglio_discordanza' spiegando la differenza tecnica.
3. Sotto i 10-12°C segnala che il grip cala.
"""

MODELLI_FALLBACK = ["gemini-2.5-flash", "gemini-2.0-flash"]
MAX_TENTATIVI = 3

if prompt_agente:
    with st.spinner("🏍️ Telemetria in corso: calcolo grip asfalto e discrepanze satellitari..."):
        riuscito = False
        for modello in MODELLI_FALLBACK:
            for tentativo in range(1, MAX_TENTATIVI + 1):
                try:
                    response = client.models.generate_content(
                        model=modello,
                        contents=prompt_agente,
                        config=types.GenerateContentConfig(
                            system_instruction=istruzioni_sistema,
                            response_mime_type="application/json",
                            response_schema=AnalisiGiroCompleta,
                            temperature=0.1,
                        ),
                    )
                    st.session_state.report_strutturato = AnalisiGiroCompleta.model_validate_json(response.text)
                    riuscito = True
                    break
                except Exception as e:
                    errore_msg = str(e)
                    if "503" in errore_msg or "UNAVAILABLE" in errore_msg or "429" in errore_msg or "RESOURCE_EXHAUSTED" in errore_msg:
                        if tentativo < MAX_TENTATIVI:
                            attesa = 2 ** tentativo
                            st.toast(f"⏳ Server sovraccarico ({modello}), ritento tra {attesa}s... (tentativo {tentativo}/{MAX_TENTATIVI})")
                            time.sleep(attesa)
                        continue
                    else:
                        st.error(f"Errore durante l'elaborazione telemetrica: {e}")
                        riuscito = True  # Non riprovare per errori non-transitori
                        break
            if riuscito:
                break
        if not riuscito:
            st.error("⚠️ Tutti i server Gemini sono momentaneamente sovraccarichi. Riprova tra qualche minuto.")

# ==========================================
# 7. DASHBOARD GRAFICA TELEMETRICA
# ==========================================
rep: AnalisiGiroCompleta = st.session_state.report_strutturato

if rep:
    st.divider()
    
    if st.session_state.get("avviso_meteo_incompleto", False):
        st.warning("⚠️ **Avviso di Rete:** I server meteo (Open-Meteo) hanno restituito dati parziali o sono temporaneamente irraggiungibili per alcuni parametri. I valori mancanti sono stati impostati a 0 per garantirti comunque l'uso dell'app.")

    colori_sem = {
        "VERDE": ("#00e676", "rgba(0, 230, 118, 0.12)", "🟢 VIA LIBERA — CONDIZIONI OTTIMALI"),
        "GIALLO": ("#ffea00", "rgba(255, 234, 0, 0.12)", "🟡 ATTENZIONE — CONDIZIONI VARIABILI O FREDDE"),
        "ROSSO": ("#ff1744", "rgba(255, 23, 68, 0.12)", "🔴 SCONSIGLIATO — CRITICITÀ METEO / ASFALTO")
    }
    col_bordo, col_bg, badge_sem = colori_sem.get(rep.semaforo, colori_sem["GIALLO"])
    
    st.markdown(f'<div style="background: {col_bg}; border: 2px solid {col_bordo}; border-radius: 16px; padding: 22px; margin-bottom: 24px;"><div style="font-size: 0.85rem; font-weight: 800; color: {col_bordo}; letter-spacing: 1.5px; text-transform: uppercase;">{badge_sem}</div><div style="font-size: 1.7rem; font-weight: 800; color: #ffffff; margin: 6px 0;">{rep.titolo_verdetto}</div><div style="font-size: 1.05rem; color: #d1d5db;">{rep.sintesi_flash}</div></div>', unsafe_allow_html=True)

    col_grip, col_kit = st.columns([1, 1.35])
    with col_grip:
        st.markdown("#### 🛞 Telemetria Grip & Pneumatici")
        col_barra = "#00e676" if rep.grip.percentuale_grip >= 75 else ("#ffea00" if rep.grip.percentuale_grip >= 50 else "#ff1744")
        icona_gomma = {"OTTIMALE": "🔥", "TIEPIDE": "🌤️", "FREDDE": "❄️", "SCIVOLOSO_BAGNATO": "⚠️"}.get(rep.grip.stato_gomme, "🛞")
        
        st.markdown(f'<div class="grip-box"><div style="display:flex; justify-content:space-between; align-items:center;"><span style="color:#9aa0a6; font-size:0.85rem; text-transform:uppercase;">Indice Tenuta Asfalto</span><span style="font-size:1.5rem; font-weight:800; color:{col_barra};">{rep.grip.percentuale_grip}%</span></div><div class="grip-bar-bg"><div style="width:{rep.grip.percentuale_grip}%; background:{col_barra}; height:100%; border-radius:999px;"></div></div><div style="margin-top: 14px; display:flex; gap:10px; flex-wrap:wrap;"><span style="background:#222630; padding:6px 12px; border-radius:8px; font-size:0.85rem;">{icona_gomma} <b>Mescola:</b> {rep.grip.stato_gomme.replace("_", " ")}</span><span style="background:#222630; padding:6px 12px; border-radius:8px; font-size:0.85rem;">🛣️ <b>Fondo:</b> {rep.grip.condizione_asfalto}</span></div><div style="margin-top: 14px; font-size:0.92rem; color:#e5e7eb; border-top:1px solid #2d3340; padding-top:10px;">💡 <i>{rep.grip.consiglio_guida}</i></div></div>', unsafe_allow_html=True)

    with col_kit:
        st.markdown("#### 🧥 Setup Abbigliamento Consigliato")
        g1, g2, g3, g4, g5 = st.columns(5)
        badge_rain = {"NON_SERVE": ("☀️", "Non serve"), "SOTTOSELLA_PRONTO": ("🎒", "Nel sottosella"), "DA_INDOSSARE": ("🌧️", "Indossare!")}.get(rep.abbigliamento.antipioggia, ("🎒", "Pronto"))
        
        items_gear = [(g1, "🪖", "Casco", rep.abbigliamento.casco_visiera), (g2, "🧥", "Giacca", rep.abbigliamento.giacca), (g3, "🌡️", "Termico", rep.abbigliamento.strato_termico), (g4, "🧤", "Guanti", rep.abbigliamento.guanti), (g5, badge_rain[0], "Antipioggia", badge_rain[1])]
        for col_g, ico, tit, val in items_gear:
            with col_g:
                st.markdown(f'<div class="gear-card"><div class="gear-icon">{ico}</div><div class="gear-title">{tit}</div><div class="gear-value">{val}</div></div>', unsafe_allow_html=True)

    st.write("")
    
    col_mappa, col_tappe = st.columns([1.05, 1.25])
    with col_mappa:
        st.markdown("#### 🗺️ Tracciato GPS & Rischio Tappe")
        if st.session_state.punti_mappa:
            mappa = disegna_mappa(st.session_state.punti_mappa, st.session_state.traccia_mappa, rep)
            st_folium(mappa, use_container_width=True, height=480, returned_objects=[])
            gmaps_url = genera_link_google_maps(st.session_state.punti_mappa)
            if gmaps_url: st.link_button("📍 Apri Navigazione su Google Maps", gmaps_url, use_container_width=True)

    with col_tappe:
        st.markdown("#### 🏁 Telemetria Tappa per Tappa")
        icone_meteo = {"SOLE": "☀️", "POCO_NUVOLOSO": "🌤️", "NUVOLOSO": "☁️", "PIOGGIA": "🌧️", "TEMPORALE": "⛈️", "FREDDO": "❄️"}
        badge_accordo = {"ALTO": ("#00e676", "Modelli Concordi"), "MEDIO": ("#ffea00", "Incertezza Media"), "BASSO": ("#ff1744", "Modelli Discordi")}
        
        for idx, t in enumerate(rep.tappe):
            ico_m = icone_meteo.get(t.condizione, "🌤️")
            col_acc, txt_acc = badge_accordo.get(t.accordo_modelli, ("#9aa0a6", ""))
            
            html_card = f'<div class="stage-card stage-{t.livello_rischio}"><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;"><div><span style="font-size:1.25rem; font-weight:800; color:#ffffff;">{ico_m} {idx+1}. {t.nome}</span><span style="background:#2b303c; color:#d1d5db; padding:3px 9px; border-radius:6px; font-size:0.78rem; margin-left:8px;">⛰️ {t.altitudine_m} m</span></div><div style="text-align:right;"><span style="background:#222630; border:1px solid #3c4252; padding:4px 10px; border-radius:8px; font-weight:700; color:#60a5fa;">🕒 {t.orario}</span></div></div><div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:8px; margin-bottom:10px;"><div class="metric-pill"><div class="metric-label">🌡️ ICON/ECMWF</div><div class="metric-val">{t.temp_icon:.1f}° / {t.temp_ecmwf:.1f}°</div></div><div class="metric-pill"><div class="metric-label">🌧️ Prob. Pioggia</div><div class="metric-val">{t.prob_pioggia_max}%</div></div><div class="metric-pill"><div class="metric-label">💧 Precipitazioni</div><div class="metric-val">{t.mm_pioggia_max:.1f} mm</div></div><div class="metric-pill"><div class="metric-label">💨 Vento</div><div class="metric-val">{t.vento_kmh:.0f} km/h</div></div></div><div style="display:flex; justify-content:space-between; align-items:center; font-size:0.86rem; color:#d1d5db; padding-top:4px;"><span>💬 <i>{t.nota_flash}</i></span><span style="color:{col_acc}; font-weight:600; font-size:0.78rem;">● {txt_acc}</span></div>'
            
            if t.allerta_pioggia_orari:
                html_card += f'<div style="margin-top: 12px; background: rgba(0, 191, 255, 0.1); padding: 8px 12px; border-radius: 8px; border: 1px solid rgba(0, 191, 255, 0.3); font-size: 0.85rem; color: #87cefa;">☔ <b>Allerta Pioggia:</b> {t.allerta_pioggia_orari}</div>'
                
            if t.accordo_modelli == "BASSO" and t.dettaglio_discordanza:
                html_card += f'<details style="margin-top: 12px; background: #222630; padding: 10px 12px; border-radius: 8px; border: 1px solid #3c4252;"><summary style="cursor: pointer; color: #ffea00; font-weight: 600; font-size: 0.85rem;">⚠️ Modelli Discordi (Clicca per espandere i dettagli)</summary><div style="margin-top: 8px; font-size: 0.85rem; color: #d1d5db; line-height: 1.4;">{t.dettaglio_discordanza}</div></details>'

            html_card += '</div>'
            st.markdown(html_card, unsafe_allow_html=True)