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

# Definizioni dei nomi in italiano
giorni_settimana = {
    0: "Lunedì", 1: "Martedì", 2: "Mercoledì", 3: "Giovedì",
    4: "Venerdì", 5: "Sabato", 6: "Domenica"
}
mesi = {
    1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile", 5: "Maggio", 6: "Giugno",
    7: "Luglio", 8: "Agosto", 9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
}

# Formatta la data in italiano
def format_data_italiano(dt: datetime.datetime) -> str:
    giorno = giorni_settimana.get(dt.weekday(), "")
    mese = mesi.get(dt.month, "")
    return f"{giorno}, {dt.day} {mese} {dt.year} alle ore {dt.hour:02d}:{dt.minute:02d}"

# Inizializza le statistiche di un utente se non esistono
def ensure_user_stats(stats: Dict[int, dict], user_id: int, username: str) -> dict:
    user = stats.setdefault(user_id, {})
    user["username"] = username
    # Feedback fatti
    fatti = user.setdefault("feedback_fatti", {})
    fatti.setdefault("count", 0)
    fatti.setdefault("daily_count", 0)
    fatti.setdefault("daily_date", None)
    fatti.setdefault("last", None)
    # Feedback ricevuti
    ricevuti = user.setdefault("feedback_ricevuti", {})
    ricevuti.setdefault("count", 0)
    ricevuti.setdefault("daily_count", 0)
    ricevuti.setdefault("daily_date", None)
    ricevuti.setdefault("last", None)
    # Proporzione e storico
    user.setdefault("proporzione", 0)
    user.setdefault("history", {})
    return user

# Aggiorna le statistiche di feedback tra sender e target
from datetime import datetime

def update_feedback_stats(stats: Dict[int, dict], sender_id: int, sender_username: str,
                          target_id: int, target_username: str) -> None:
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Sender
    sender = stats.setdefault(sender_id, {})
    sender["username"] = sender_username
    fatti = sender.setdefault("feedback_fatti", {})
    fatti.setdefault("daily_date", today_str)
    fatti.setdefault("daily_count", 0)
    fatti.setdefault("total", fatti.get("count", 0))

    # Reset giornaliero se cambia data
    if fatti.get("daily_date") != today_str:
        fatti["daily_date"] = today_str
        fatti["daily_count"] = 0

    fatti["daily_count"] += 1
    fatti["total"] = fatti.get("total", 0) + 1
    fatti["count"] = fatti["total"]

    # Storico sender
    hist_s = sender.setdefault("history", {})
    day_hist_s = hist_s.setdefault(today_str, {})
    day_hist_s["feedback_fatti"] = day_hist_s.get("feedback_fatti", 0) + 1

    # Timestamp ultimo invio
    timestamp = datetime.now().isoformat()
    fatti["last"] = {"timestamp": timestamp, "target_username": target_username}

    # Target
    target = stats.setdefault(target_id, {})
    target["username"] = target_username
    ricevuti = target.setdefault("feedback_ricevuti", {})
    ricevuti.setdefault("total", ricevuti.get("count", 0))

    ricevuti["total"] += 1
    ricevuti["count"] = ricevuti["total"]

    # Storico target
    hist_t = target.setdefault("history", {})
    day_hist_t = hist_t.setdefault(today_str, {})
    day_hist_t["feedback_ricevuti"] = day_hist_t.get("feedback_ricevuti", 0) + 1

    # Timestamp ultima ricezione
    ricevuti["last"] = {"timestamp": timestamp, "sender_username": sender_username}

    # Salva le statistiche
    try:
        save_stats(stats)
    except Exception as e:
        logger.error(f"Errore nel salvataggio delle stats: {e}")

# Genera l'immagine della tendenza di feedback

def get_feedback_trend_image(stats: Dict[int, dict], user_id: int, days: int = 7) -> BytesIO:
    user = stats.get(user_id)
    if not user or not user.get("history"):
        raise ValueError("*Non ho abbastanza informazioni per generare il grafico.*")

    dates = sorted(user["history"].keys())[-days:]
    fatti = [user["history"].get(d, {}).get("feedback_fatti", 0) for d in dates]
    ricevuti = [user["history"].get(d, {}).get("feedback_ricevuti", 0) for d in dates]

    plt.figure(figsize=(10, 6))
    plt.plot(dates, fatti, label="Feedback fatti", marker="o", linestyle="-")
    plt.plot(dates, ricevuti, label="Feedback ricevuti", marker="o", linestyle="-")
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

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    nickname = escape_markdown(update.effective_user.full_name or username, version=2)
    escaped_username = escape_markdown(username, version=2)

    stats = load_stats()
    user_stats = ensure_user_stats(stats, user_id, username)

    last_sent = user_stats.get("feedback_fatti", {}).get("last")
    last_recv = user_stats.get("feedback_ricevuti", {}).get("last")

    sent_info = "_🥲 Non hai ancora effettuato feedback._\n"
    if last_sent:
        dt = datetime.datetime.fromisoformat(last_sent["timestamp"])
        sent_info = f"_📤 Hai fatto l'ultimo feedback {escape_markdown(format_data_italiano(dt),2)} a @{escape_markdown(last_sent['target_username'],2)}._\n"

    recv_info = "_😢 Non hai ancora ricevuto feedback._\n"
    if last_recv:
        dt_r = datetime.datetime.fromisoformat(last_recv["timestamp"])
        recv_info = f"_📥 Hai ricevuto l'ultimo feedback {escape_markdown(format_data_italiano(dt_r),2)} da @{escape_markdown(last_recv['sender_username'],2)}._\n"

    group_link = "https://t.me/addlist/R1OCGDs37tY1ODY0"
    welcome = (
        f"*👋 Benvenuto [{nickname}](https://t.me/{escaped_username})!*\n\n"
        "Questo è il bot ufficiale del gruppo [MonopolyGo]({group_link}), qui avrai accesso "
        "a tutte le statistiche dei feedback che hai fatto e ricevuto.\n\n"
        f"{sent_info}\n{recv_info}"
    )

    try:
        buf = get_feedback_trend_image(stats, user_id, days=7)
        await update.message.reply_photo(photo=buf, caption=welcome, parse_mode=ParseMode.MARKDOWN_V2)
    except ValueError:
        await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Errore nella generazione del grafico: {e}")
        await update.message.reply_text(welcome + "\nErrore nella generazione del grafico.", parse_mode=ParseMode.MARKDOWN_V2)

# Comando genera_grafico_totale
@restricted
async def genera_grafico_totale(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats_users = load_stats()
    group_users = load_group_users()

    # Raccogli tutte le date
    all_dates = set()
    for u in stats_users.values():
        hist = u.get("history", {})
        all_dates.update(hist.keys())
    all_dates = sorted(all_dates)

    if not all_dates:
        await update.message.reply_text("Non ci sono dati storici disponibili.")
        return

    totals = []
    for date in all_dates:
        daily = 0
        for u in stats_users.values():
            daily += u.get("history", {}).get(date, {}).get("feedback_fatti", 0)
        totals.append(daily)

    plt.figure(figsize=(12, 7))
    plt.plot(all_dates, totals, label="Feedback totali nel gruppo", marker="o", linestyle="-")
    plt.xlabel("Date")
    plt.ylabel("Numero di feedback")
    plt.title("Andamento giornaliero dei feedback totali nel gruppo")
    plt.legend()
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.tight_layout()

    # Trova top sender e receiver
    all_data = {}
    for chat_id, users in group_users.items():
        if isinstance(users, dict):
            for uid_str, info in users.items():
                try:
                    uid = int(uid_str)
                except ValueError:
                    continue
                all_data.setdefault(uid, {"username": info.get("username", f"User_{uid}"),
                                           "fatti": 0, "ricevuti": 0})

    for uid, u in stats_users.items():
        entry = all_data.setdefault(uid, {"username": u.get("username", f"User_{uid}"), "fatti": 0, "ricevuti": 0})
        entry["fatti"] = u.get("feedback_fatti", {}).get("count", 0)
        entry["ricevuti"] = u.get("feedback_ricevuti", {}).get("count", 0)

    top_s = max(all_data.values(), key=lambda x: x["fatti"], default={"username":"N/A","fatti":0})
    top_r = max(all_data.values(), key=lambda x: x["ricevuti"], default={"username":"N/A","ricevuti":0})

    buf = BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)

    caption = (
        f"*📊 Statistiche totali gruppo*\n"
        f"🎁 Top sender: *@{escape_markdown(top_s['username'],2)}* con {top_s['fatti']} feedback\n"
        f"🏆 Top receiver: *@{escape_markdown(top_r['username'],2)}* con {top_r['ricevuti']} feedback"
    )
    await update.message.reply_photo(photo=buf, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)

