import os
import json
import datetime
import logging
from typing import Dict
from io import BytesIO
import matplotlib.pyplot as plt
from telegram import Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown
from telegram.constants import ParseMode
from utils import restricted
from firebase_file import save_stats, load_stats, load_group_users
# Configura il logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Definizioni dei nomi in italiano (invariato)
giorni_settimana = {
    0: "Lunedì", 1: "Martedì", 2: "Mercoledì", 3: "Giovedì",
    4: "Venerdì", 5: "Sabato", 6: "Domenica"
}
mesi = {
    1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile", 5: "Maggio", 6: "Giugno",
    7: "Luglio", 8: "Agosto", 9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
}

# Funzione per formattare la data (invariato)
def format_data_italiano(dt):
    giorno = giorni_settimana[dt.weekday()]
    mese = mesi[dt.month]
    return f"{giorno}, {dt.day} {mese} {dt.year} alle ore {dt.hour:02d}:{dt.minute:02d}"


# Le seguenti funzioni rimangono invariate nella loro logica interna
def ensure_user_stats(stats: Dict[int, dict], user_id: int, username: str) -> dict:
    if user_id not in stats:
        stats[user_id] = {
            "feedback_fatti": {
                "count": 0,
                "daily_count": 0,
                "daily_date": None,
                "last": None
            },
            "feedback_ricevuti": {
                "count": 0,
                "daily_count": 0,
                "daily_date": None,
                "last": None
            },
            "proporzione": 0,
            "history": {}
        }
    return stats[user_id]

from datetime import datetime

def update_feedback_stats(stats, sender_id, sender_username, target_id, target_username):
    today = datetime.now().strftime("%Y-%m-%d")

    # Inizializza struttura sender se non esiste
    if sender_id not in stats:
        stats[sender_id] = {
            "username": sender_username,
            "feedback_fatti": {
                "daily_date": today,
                "daily_count": 0,
                "total": 0
            }
        }

    sender_stats = stats[sender_id]
    feedback_fatti = sender_stats.setdefault("feedback_fatti", {})

    # Controlla e resetta il contatore giornaliero se la data è cambiata
    if feedback_fatti.get("daily_date") != today:
        feedback_fatti["daily_date"] = today
        feedback_fatti["daily_count"] = 0

    feedback_fatti["daily_count"] = feedback_fatti.get("daily_count", 0) + 1
    feedback_fatti["total"] = feedback_fatti.get("total", 0) + 1

    # Inizializza struttura target se non esiste
    if target_id not in stats:
        stats[target_id] = {
            "username": target_username,
            "feedback_ricevuti": {
                "total": 0
            }
        }

    target_stats = stats[target_id]
    feedback_ricevuti = target_stats.setdefault("feedback_ricevuti", {})
    feedback_ricevuti["total"] = feedback_ricevuti.get("total", 0) + 1


def get_feedback_trend_image(stats: Dict[int, dict], user_id: int, days: int = 7) -> BytesIO:
    if user_id not in stats:
        raise ValueError("Utente non presente nelle statistiche\\.")
    user_stats = stats[user_id]
    if "history" not in user_stats or not user_stats["history"]:
        raise ValueError("*Non ho abbastanza informazioni per generare il grafico, ci rivediamo quando avrai donato altre carte\\.*")

    dates = sorted(user_stats["history"].keys())
    dates_to_plot = dates[-days:]
    feedback_fatti = [user_stats["history"].get(date, {}).get("feedback_fatti", 0) for date in dates_to_plot]
    feedback_ricevuti = [user_stats["history"].get(date, {}).get("feedback_ricevuti", 0) for date in dates_to_plot]

    plt.figure(figsize=(10, 6))
    plt.plot(dates_to_plot, feedback_fatti, label="Feedback fatti", marker="o", linestyle="-")
    plt.plot(dates_to_plot, feedback_ricevuti, label="Feedback ricevuti", marker="o", linestyle="-")
    plt.xlabel("Date")
    plt.ylabel("Numero di feedback")
    plt.title("Andamento dei feedback negli ultimi giorni")
    plt.legend()
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return buf


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    nickname = escape_markdown(update.effective_user.full_name or username, version=2)
    escaped_username = escape_markdown(username, version=2)

    stats = load_stats()
    user_stats = ensure_user_stats(stats, user_id, username)

    last_sent = user_stats["feedback_fatti"].get("last")
    last_received = user_stats["feedback_ricevuti"].get("last")

    sent_info = "_🥲 Non hai ancora effettuato feedback\\._\n"
    if last_sent:
        dt_sent = datetime.datetime.fromisoformat(last_sent['timestamp'])
        sent_date_str = format_data_italiano(dt_sent)
        data_inviato = escape_markdown(sent_date_str, version=2) 
        target = escape_markdown(last_sent['target_username'], version=2)
        sent_info = f"_📤 Hai fatto l'ultimo feedback {data_inviato} a @{target}\\._\n"

    received_info = "_😢 Non hai ancora ricevuto feedback\\._\n"
    if last_received:
        dt_received = datetime.datetime.fromisoformat(last_received['timestamp'])
        received_date_str = format_data_italiano(dt_received)
        data_ricevuto = escape_markdown(received_date_str, version=2)
        sender = escape_markdown(last_received['sender_username'], version=2)
        received_info = f"_📥 Hai ricevuto l'ultimo feedback {data_ricevuto} da @{sender}\\._\n"

    group_link = "https://t.me/addlist/R1OCGDs37tY1ODY0"
    welcome_text = (
        f"*👋 Benvenuto [{nickname}](https://t.me/{escaped_username})\\!*\n\n"
        f"Questo è il bot ufficiale del gruppo [MonopolyGo]({group_link}), qui avrai accesso "
        "a tutte le statistiche dei feedback che hai fatto e ricevuto\\.\n\n"
        f"{sent_info}\n"
        f"{received_info}\n"
    )

    try:
        image_buffer = get_feedback_trend_image(stats, user_id, days=7)
        await update.message.reply_photo(photo=image_buffer, caption=welcome_text, parse_mode=ParseMode.MARKDOWN_V2)
    except ValueError as e:
        await update.message.reply_text(welcome_text + "\n\n", parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Errore durante la generazione del grafico: {e}")
        await update.message.reply_text(welcome_text + "\n\nErrore nella generazione del grafico\\.", parse_mode=ParseMode.MARKDOWN_V2)


@restricted
async def genera_grafico_totale(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Carica le statistiche degli utenti dal bin di jsonbin.io
    stats_users = load_stats()
    
    # Carica i dati degli utenti del gruppo dal bin di jsonbin.io tramite utils.load_group_users()
    # Questa è la funzione che legge dal bin di group_users
    group_stats = load_group_users()
    
    all_dates = set()
    for user_data in stats_users.values():
        if "history" in user_data:
            all_dates.update(user_data["history"].keys())
    
    all_dates = sorted(list(all_dates))
    
    if not all_dates:
        await update.message.reply_text("Non ci sono dati storici disponibili per generare il grafic\\.")
        return
    
    total_feedback_per_day = []
    for date in all_dates:
        # Calcola il totale dei feedback fatti per ogni giorno
        daily_total = sum(user_data["history"][date].get("feedback_fatti", 0) for user_data in stats_users.values() if "history" in user_data and date in user_data["history"])
        total_feedback_per_day.append(daily_total)
    
    # Genera il grafico
    plt.figure(figsize=(12, 7))
    plt.plot(all_dates, total_feedback_per_day, label="Feedback totali nel gruppo", marker="o", linestyle="-", color="blue")
    plt.xlabel("Date")
    plt.ylabel("Numero di feedback")
    plt.title("Andamento giornaliero dei feedback totali nel gruppo")
    plt.legend()
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    top_sender = {"username": "N/A", "count": 0}
    top_receiver = {"username": "N/A", "count": 0}

    # Combina i dati da stats_users e group_stats per ottenere informazioni complete sugli utenti
    # e trovare i top users.
    all_users_data = {}
    
    # Inizializza gli utenti con i dati di group_stats per ottenere gli username aggiornati
    # group_stats è una Dict[int, Dict[int, dict]] -> chat_id: { user_id: { user_info } }
    for chat_id, users_in_chat in group_stats.items():
        if isinstance(users_in_chat, dict): # Assicurati che sia un dizionario di utenti
            for user_id_str, user_info in users_in_chat.items():
                if isinstance(user_info, dict): # Assicurati che sia un dizionario di info utente
                    user_id_int = int(user_id_str) # Converte a int per consistenza con stats_users
                    all_users_data[user_id_int] = {
                        "username": user_info.get("username", f"UnknownUser_{user_id_int}"),
                        "feedback_fatti": 0, # Inizializza a 0, verranno sovrascritti da stats_users
                        "feedback_ricevuti": 0  # Inizializza a 0, verranno sovrascritti da stats_users
                    }
    
    # Aggiorna i conteggi di feedback usando i dati da stats_users, che sono i più aggiornati
    for user_id, user_stats_data in stats_users.items():
        if user_id in all_users_data:
            # L'utente esiste già in all_users_data (da group_stats), aggiorna i conteggi
            all_users_data[user_id]["feedback_fatti"] = user_stats_data["feedback_fatti"]["count"]
            all_users_data[user_id]["feedback_ricevuti"] = user_stats_data["feedback_ricevuti"]["count"]
        else:
            # L'utente è presente solo in stats_users (es. nuovo utente, o dati non ancora in group_users)
            # Aggiungilo con un username di fallback se non disponibile qui
            all_users_data[user_id] = {
                "username": user_stats_data.get("username", f"UnknownUser_{user_id}"), # Fallback per username
                "feedback_fatti": user_stats_data["feedback_fatti"]["count"],
                "feedback_ricevuti": user_stats_data["feedback_ricevuti"]["count"]
            }
            
    # Trova il top sender e receiver dai dati combinati e aggiornati
    for user_id, user_info in all_users_data.items():
        if user_info["feedback_fatti"] > top_sender["count"]:
            top_sender = {"username": user_info["username"], "count": user_info["feedback_fatti"]}
        if user_info["feedback_ricevuti"] > top_receiver["count"]:
            top_receiver = {"username": user_info["username"], "count": user_info["feedback_ricevuti"]}
            
    buf = BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    
    caption_text = (
        f"*📊 Statistiche dei feedback totali nel gruppo*\n\n"
        f"_🎁 Utente con più feedback inviati\\: *@{escape_markdown(str(top_sender['username']), version=2)}* "
        f"con {top_sender['count']} feedback_\n\n"
        f"_🏆 Utente con più feedback ricevuti\\: *@{escape_markdown(str(top_receiver['username']), version=2)}* "
        f"con {top_receiver['count']} feedback_"
    )
    
    await update.message.reply_photo(
        photo=buf,
        caption=caption_text,
        parse_mode=ParseMode.MARKDOWN_V2
    )
