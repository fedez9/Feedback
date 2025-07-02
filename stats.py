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
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


giorni_settimana = {
    0: "Lunedì", 1: "Martedì", 2: "Mercoledì", 3: "Giovedì",
    4: "Venerdì", 5: "Sabato", 6: "Domenica"
}
mesi = {
    1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile", 5: "Maggio", 6: "Giugno",
    7: "Luglio", 8: "Agosto", 9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
}

def format_data_italiano(dt):
    giorno = giorni_settimana[dt.weekday()]
    mese = mesi[dt.month]
    return f"{giorno}, {dt.day} {mese} {dt.year} alle ore {dt.hour:02d}:{dt.minute:02d}"


def ensure_user_stats(stats: Dict[int, dict], user_id: int, username: str) -> dict:
    """
    Ensures a user's stats object is fully initialized, including all nested structures,
    for both new and existing users.
    """
    user_stats = stats.setdefault(user_id, {})

    # Ensure 'feedback_fatti' and its sub-keys exist
    fatti_stats = user_stats.setdefault("feedback_fatti", {})
    fatti_stats.setdefault("count", 0)
    fatti_stats.setdefault("daily_count", 0)
    fatti_stats.setdefault("daily_date", None)
    fatti_stats.setdefault("last", None)

    # Ensure 'feedback_ricevuti' and its sub-keys exist
    ricevuti_stats = user_stats.setdefault("feedback_ricevuti", {})
    ricevuti_stats.setdefault("count", 0)
    ricevuti_stats.setdefault("daily_count", 0)
    ricevuti_stats.setdefault("daily_date", None)
    ricevuti_stats.setdefault("last", None)

    # Ensure other top-level keys exist
    user_stats.setdefault("proporzione", 0)
    user_stats.setdefault("history", {})
    user_stats.setdefault("username", username)

    return user_stats

def update_feedback_stats(stats: Dict[int, dict], sender_id: int, sender_username: str, target_id: int, target_username: str) -> None:
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat()

    sender_stats = ensure_user_stats(stats, sender_id, sender_username)
    target_stats = ensure_user_stats(stats, target_id, target_username)

    if sender_stats["feedback_fatti"].get("daily_date") != today:
        if sender_stats.get("history") is None:
            sender_stats["history"] = {}
        if sender_stats["feedback_fatti"]["daily_date"]:
            sender_stats["history"][sender_stats["feedback_fatti"]["daily_date"]] = {
                "feedback_fatti": sender_stats["feedback_fatti"]["daily_count"],
                "feedback_ricevuti": sender_stats["feedback_ricevuti"]["daily_count"]
            }
        sender_stats["feedback_fatti"]["daily_count"] = 0
        sender_stats["feedback_fatti"]["daily_date"] = today
    sender_stats["feedback_fatti"]["count"] += 1
    sender_stats["feedback_fatti"]["daily_count"] += 1
    sender_stats["feedback_fatti"]["last"] = {
        "target_id": target_id,
        "target_username": target_username,
        "timestamp": now
    }

    if target_stats["feedback_ricevuti"].get("daily_date") != today:
        if target_stats.get("history") is None:
            target_stats["history"] = {}
        if target_stats["feedback_ricevuti"]["daily_date"]:
            target_stats["history"][target_stats["feedback_ricevuti"]["daily_date"]] = {
                "feedback_fatti": target_stats["feedback_fatti"]["daily_count"],
                "feedback_ricevuti": target_stats["feedback_ricevuti"]["daily_count"]
            }
        target_stats["feedback_ricevuti"]["daily_count"] = 0
        target_stats["feedback_ricevuti"]["daily_date"] = today
    target_stats["feedback_ricevuti"]["count"] += 1
    target_stats["feedback_ricevuti"]["daily_count"] += 1
    target_stats["feedback_ricevuti"]["last"] = {
        "sender_id": sender_id,
        "sender_username": sender_username,
        "timestamp": now
    }

    total_feedback = sum(u["feedback_ricevuti"]["count"] for u in stats.values())
    if total_feedback > 0:
        for user_data in stats.values():
            user_data["proporzione"] = (user_data["feedback_ricevuti"]["count"] / total_feedback) * 100
    else:
        for user_data in stats.values():
            user_data["proporzione"] = 0

    save_stats(stats)


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
        sent_info = f"_📤 Hai mandato l'ultimo feedback {data_inviato} a @{target}\\._\n"

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
    stats_users = load_stats()
    
    group_stats = load_group_users()
    
    all_dates = set()
    for user_data in stats_users.values():
        if "history" in user_data:
            all_dates.update(user_data["history"].keys())
    
    all_dates = sorted(list(all_dates))
    
    if not all_dates:
        await update.message.reply_text("Non ci sono dati storici disponibili per generare il grafico\\.")
        return
    
    total_feedback_per_day = []
    for date in all_dates:
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

    all_users_data = {}
    
    for chat_id, users_in_chat in group_stats.items():
        if isinstance(users_in_chat, dict): 
            for user_id_str, user_info in users_in_chat.items():
                if isinstance(user_info, dict): 
                    user_id_int = int(user_id_str) 
                    all_users_data[user_id_int] = {
                        "username": user_info.get("username", f"UnknownUser_{user_id_int}"),
                        "feedback_fatti": 0, 
                        "feedback_ricevuti": 0  
                    }
    
    for user_id, user_stats_data in stats_users.items():
        if user_id in all_users_data:
            all_users_data[user_id]["feedback_fatti"] = user_stats_data["feedback_fatti"]["count"]
            all_users_data[user_id]["feedback_ricevuti"] = user_stats_data["feedback_ricevuti"]["count"]
        else:
            all_users_data[user_id] = {
                "username": user_stats_data.get("username", f"UnknownUser_{user_id}"),
                "feedback_fatti": user_stats_data["feedback_fatti"]["count"],
                "feedback_ricevuti": user_stats_data["feedback_ricevuti"]["count"]
            }
            
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
