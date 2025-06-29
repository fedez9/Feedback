from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, ContextTypes
from typing import Dict
from utils import send_paginated_message
import logging
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from utils import restricted
import os
from firebase_file import load_group_users, save_group_users
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

group_users: Dict[int, Dict[int, dict]] = {}
GRUPPO_SCAMBI = os.getenv("GRUPPO_SCAMBI")
GRUPPO_FEEDBACK_DA_ACCETTARE = os.getenv("GRUPPO_FEEDBACK_DA_ACCETTARE")
GRUPPO_FEEDBACK = os.getenv("GRUPPO_FEEDBACK")
GRUPPO_STAFF = os.getenv("GRUPPO_STAFF")

async def info_utente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()

    args = context.args
    if args:
        identifier = args[0]
    else:
        identifier = str(update.effective_user.id)

    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    # Trova l'utente target
    target_user = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if not target_user:
        await update.message.reply_text("Utente non trovato nel database.")
        return

    # Costruisci il messaggio
    nome = escape_markdown(target_user.get('username', 'N/A'), version=2)
    verified_status = "✅" if target_user.get("verified") else "❌"
    limited_status = "🔕" if target_user.get("limited") else "🔔"
    msg = (
        f"_ℹ️ Informazioni relative all'utente_\n\n"
        f"*🔢 ID\\:* `{target_user['id']}`\n"
        f"*🌐 Username\\:* @{nome}\n"
        f"*📥 Feedback ricevuti\\:* {target_user.get('feedback_ricevuti', 0)}\n"
        f"*📤 Feedback inviati\\:* {target_user.get('feedback_fatti', 0)}\n"
        f"*🛃 Verificato\\:* {verified_status}\n"
        f"*🔍 Limitato\\:* {limited_status}"
    )

    # Aggiungi un pulsante “Maggiori info”
    keyboard = [
        [InlineKeyboardButton("➕ Maggiori info", callback_data=f"menu_{target_user['id']}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)

@restricted
async def add_invio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()
    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato!*\n\nUsa: /addinv @username|id [numero] [stelle]",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    amount = 1
    stars = 0

    if len(args) >= 2:
        try:
            amount = int(args[1])
        except ValueError:
            await update.message.reply_text("Il numero deve essere un intero.")
            return
    if len(args) >= 3:
        try:
            stars = int(args[2])
            if not 0 <= stars <= 5:
                await update.message.reply_text("Il numero di stelle deve essere compreso tra 0 e 5.")
                return
        except ValueError:
            await update.message.reply_text("Il numero di stelle deve essere un intero.")
            return

    # Trova l'utente o crealo se non esiste
    target_user = None
    uid = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if not target_user:
        if uid:
            await update.message.reply_text(f"Utente con ID {uid} non trovato. Verrà creato un nuovo profilo.")
            target_user = {
                "id": uid, "username": f"utente_{uid}", "feedback_ricevuti": 0,
                "feedback_fatti": 0, "verified": False, "limited": False,
                "cards_donate": {s: 0 for s in range(6)},
                "cards_ricevute": {s: 0 for s in range(6)}
            }
            group_users[chat_id][uid] = target_user
        else:
            await update.message.reply_text("Utente non trovato. Per creare un nuovo utente, usa il suo ID numerico.")
            return

    target_user["feedback_fatti"] = target_user.get("feedback_fatti", 0) + amount
    cards_key = "cards_ricevute"
    if isinstance(target_user.get(cards_key), list):
        target_user[cards_key] = {i: v for i, v in enumerate(target_user.get(cards_key, []))}
    target_user.setdefault(cards_key, {s: 0 for s in range(0, 6)})
    target_user[cards_key][stars] = target_user[cards_key].get(stars, 0) + amount

    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    response = f"_✅ Feedback inviati aggiornati, @{nome} è ora a {target_user['feedback_fatti']}_"
    if stars != 0:
        response += f"\n\n_{'Aggiunta' if amount == 1 else 'Aggiunte'} {amount} {'carta' if amount == 1 else 'carte'} {'ricevuta' if amount == 1 else 'ricevute'} da {stars}🌟\\._"
    else:
        response += f"\n\n_{'Aggiunta' if amount == 1 else 'Aggiunte'} {amount} {'carta' if amount == 1 else 'carte'} {'ricevuta' if amount == 1 else 'ricevute'}\\._"
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN_V2)
    await check_limit_condition(update, context, target_user)


@restricted
async def add_feed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()
    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato!*\n\nUsa: /addfeed @username|id [numero] [stelle]",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    amount = 1
    stars = 0
    if len(args) >= 2:
        try:
            amount = int(args[1])
        except ValueError:
            await update.message.reply_text("Il numero deve essere un intero.")
            return
    if len(args) >= 3:
        try:
            stars = int(args[2])
            if not 0 <= stars <= 5:
                await update.message.reply_text("Il numero di stelle deve essere compreso tra 0 e 5.")
                return
        except ValueError:
            await update.message.reply_text("Il numero di stelle deve essere un intero.")
            return

    # Trova l'utente o crealo se non esiste
    target_user = None
    uid = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if not target_user:
        if uid:
            await update.message.reply_text(f"Utente con ID {uid} non trovato. Verrà creato un nuovo profilo.")
            target_user = {
                "id": uid, "username": f"utente_{uid}", "feedback_ricevuti": 0,
                "feedback_fatti": 0, "verified": False, "limited": False,
                "cards_donate": {s: 0 for s in range(6)},
                "cards_ricevute": {s: 0 for s in range(6)}
            }
            group_users[chat_id][uid] = target_user
        else:
            await update.message.reply_text("Utente non trovato. Per creare un nuovo utente, usa il suo ID numerico.")
            return

    # Aggiorna feedback_ricevuti
    target_user["feedback_ricevuti"] = target_user.get("feedback_ricevuti", 0) + amount
    # Aggiorna carte_donate
    cards_key = "cards_donate"
    if isinstance(target_user.get(cards_key), list):
        target_user[cards_key] = {i: v for i, v in enumerate(target_user.get(cards_key, []))}
    target_user.setdefault(cards_key, {s: 0 for s in range(0, 6)})
    target_user[cards_key][stars] = target_user[cards_key].get(stars, 0) + amount

    # Verifica automatico
    if target_user["feedback_ricevuti"] >= 25 and not target_user.get("verified", False):
        target_user["verified"] = True
        nome = escape_markdown(target_user['username'], version=2)
        msg = f"_🎉 L'utente @{nome} ha raggiunto i 25 feedback ed è stato verificato\\!_"
        await context.bot.send_message(chat_id=GRUPPO_STAFF, text=msg, parse_mode=ParseMode.MARKDOWN_V2)

    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    response = f"_✅ Feedback ricevuti aggiornati, @{nome} è ora a {target_user['feedback_ricevuti']}_"
    if stars != 0:
        response += f"\n\n_{'Aggiunta' if amount == 1 else 'Aggiunte'} {amount} {'carta' if amount == 1 else 'carte'} {'donata' if amount == 1 else 'donate'} da {stars}🌟\\._"
    else:
        response += f"\n\n_{'Aggiunta' if amount == 1 else 'Aggiunte'} {amount} {'carta' if amount == 1 else 'carte'} {'donata' if amount == 1 else 'donate'}\\._"
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN_V2)
    await check_limit_condition(update, context, target_user)


@restricted
async def rem_invio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()
    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato!*\n\nUsa: /reminv @username|id [numero] [stelle]",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    amount = 1
    stars = 0
    if len(args) >= 2:
        try:
            amount = int(args[1])
        except ValueError:
            await update.message.reply_text("Il numero deve essere un intero.")
            return
    if len(args) >= 3:
        try:
            stars = int(args[2])
            if not 0 <= stars <= 5:
                await update.message.reply_text("Il numero di stelle deve essere compreso tra 0 e 5.")
                return
        except ValueError:
            await update.message.reply_text("Il numero di stelle deve essere un intero.")
            return

    # Trova l'utente o crealo se non esiste
    target_user = None
    uid = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if not target_user:
        if uid:
            await update.message.reply_text(f"Utente con ID {uid} non trovato. Verrà creato un nuovo profilo.")
            target_user = {
                "id": uid, "username": f"utente_{uid}", "feedback_ricevuti": 0,
                "feedback_fatti": 0, "verified": False, "limited": False,
                "cards_donate": {s: 0 for s in range(6)},
                "cards_ricevute": {s: 0 for s in range(6)}
            }
            group_users[chat_id][uid] = target_user
        else:
            await update.message.reply_text("Utente non trovato. Per creare un nuovo utente, usa il suo ID numerico.")
            return

    # Decrementa feedback_fatti
    target_user["feedback_fatti"] = max(0, target_user.get("feedback_fatti", 0) - amount)
    # Decrementa carte_ricevute
    cards_key = "cards_ricevute"
    if isinstance(target_user.get(cards_key), list):
        target_user[cards_key] = {i: v for i, v in enumerate(target_user.get(cards_key, []))}
    target_user.setdefault(cards_key, {s: 0 for s in range(0, 6)})
    target_user[cards_key][stars] = max(0, target_user[cards_key].get(stars, 0) - amount)

    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    response = f"_✅ Feedback inviati aggiornati, @{nome} è ora a {target_user['feedback_fatti']}_"
    if stars != 0:
        response += f"\n\n_{'Rimossa' if amount == 1 else 'Rimosse'} {amount} {'carta' if amount == 1 else 'carte'} {'ricevuta' if amount == 1 else 'ricevute'} da {stars}🌟\\._"
    else:
        response += f"\n\n_{'Rimossa' if amount == 1 else 'Rimosse'} {amount} {'carta' if amount == 1 else 'carte'} {'ricevuta' if amount == 1 else 'ricevute'}\\._"
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN_V2)
    await check_limit_condition(update, context, target_user)


@restricted
async def rem_feed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()
    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato!*\n\nUsa: /remfeed @username|id [numero] [stelle]",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    amount = 1
    stars = 0
    if len(args) >= 2:
        try:
            amount = int(args[1])
        except ValueError:
            await update.message.reply_text("Il numero deve essere un intero.")
            return
    if len(args) >= 3:
        try:
            stars = int(args[2])
            if not 0 <= stars <= 5:
                await update.message.reply_text("Il numero di stelle deve essere compreso tra 0 e 5.")
                return
        except ValueError:
            await update.message.reply_text("Il numero di stelle deve essere un intero.")
            return

    # Trova l'utente o crealo se non esiste
    target_user = None
    uid = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if not target_user:
        if uid:
            await update.message.reply_text(f"Utente con ID {uid} non trovato. Verrà creato un nuovo profilo.")
            target_user = {
                "id": uid, "username": f"utente_{uid}", "feedback_ricevuti": 0,
                "feedback_fatti": 0, "verified": False, "limited": False,
                "cards_donate": {s: 0 for s in range(6)},
                "cards_ricevute": {s: 0 for s in range(6)}
            }
            group_users[chat_id][uid] = target_user
        else:
            await update.message.reply_text("Utente non trovato. Per creare un nuovo utente, usa il suo ID numerico.")
            return

    current_feed = target_user.get("feedback_ricevuti", 0)
    target_user["feedback_ricevuti"] = max(0, current_feed - amount)
    cards_key = "cards_donate"
    if isinstance(target_user.get(cards_key), list):
        target_user[cards_key] = {i: v for i, v in enumerate(target_user.get(cards_key, []))}
    target_user.setdefault(cards_key, {s: 0 for s in range(0, 6)})
    target_user[cards_key][stars] = max(0, target_user[cards_key].get(stars, 0) - amount)

    if current_feed >= 25 and target_user["feedback_ricevuti"] < 25:
        target_user["verified"] = False
        nome = escape_markdown(target_user['username'], version=2)
        await update.message.reply_text(
            f"_⚠️ L'utente @{nome} ha meno di 25 feedback e non è più verificato\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    response = f"_✅ Feedback ricevuti aggiornati, @{nome} è ora a {target_user['feedback_ricevuti']}_"
    if stars != 0:
        response += f"\n\n_{'Rimossa' if amount == 1 else 'Rimosse'} {amount} {'carta' if amount == 1 else 'carte'} {'donata' if amount == 1 else 'donate'} da {stars}🌟\\._"
    else:
        response += f"\n\n_{'Rimossa' if amount == 1 else 'Rimosse'} {amount} {'carta' if amount == 1 else 'carte'} {'donata' if amount == 1 else 'donate'}\\._"
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN_V2)
    await check_limit_condition(update, context, target_user)


@restricted
async def verify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()
    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("*🆘 Comando errato\\!*\n\nUsa:/verfica @username o id", parse_mode=ParseMode.MARKDOWN_V2)
        return

    identifier = args[0]
    target_user = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if target_user is None:
        await update.message.reply_text("Utente non trovato per la verifica.")
        return

    target_user["verified"] = True
    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(
        f"_✅ L'utente @{nome} è stato verificato\\!_",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@restricted
async def unverify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()
    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato\\!*\n\nUsa: /sverifica @username o id",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    target_user = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if target_user is None:
        await update.message.reply_text("Utente non trovato per la rimozione della verifica.")
        return

    target_user["verified"] = False
    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(
        f"_✅ L'utente @{nome} non risulta più verificato\\._",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@restricted
async def limit_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()
    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    args = context.args
    if not args:
        await update.message.reply_text(
            "*🆘 Comando errato\\!* \n\nUsa: /limit @username o id",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    target_user = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if target_user is None:
        await update.message.reply_text("Utente non trovato per la limitazione.")
        return

    target_user["limited"] = True
    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(
        f"_✅ L'utente @{nome} è stato limitato_",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@restricted
async def unlimit_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global group_users
    group_users = load_group_users()
    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users:
        group_users[chat_id] = {}

    args = context.args
    if not args:
        await update.message.reply_text(
            "*🆘 Comando errato\\!* \n\nUsa: /unlimit @username o id",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    target_user = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = group_users[chat_id].get(uid)
    else:
        username = identifier.lstrip("@")
        for user in group_users[chat_id].values():
            if user.get("username", "").lower() == username.lower():
                target_user = user
                break

    if target_user is None:
        await update.message.reply_text("Utente non trovato.")
        return

    if not target_user.get("limited"):
        await update.message.reply_text("L'utente non risulta limitato.")
        return

    target_user["limited"] = False
    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(
        f"_✅ L'utente @{nome} è stato rimosso dai limitati_",
        parse_mode=ParseMode.MARKDOWN_V2
    )


async def check_limit_condition(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict):
    diff = user.get("feedback_ricevuti", 0) - user.get("feedback_fatti", 0)
    if user.get("limited") and diff >= 0:
        nome = escape_markdown(user.get("username", "Unknown"), version=2)
        msg = f"⚠️ L'utente @{nome} ha pareggiato i feed ha ora un divario di {diff}\\."
        await context.bot.send_message(chat_id=GRUPPO_STAFF, text=msg, parse_mode=ParseMode.MARKDOWN_V2)

async def show_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    commands_text = (
        "*_⚙️ Lista Comandi\\:_*\n\n"
        "*👥 Comandi Utente\\:*\n"
        "*\\.inf \\[ID\\|@username\\] *\\- _Ottieni info su un utente\\._\n"
        "*\\.statistiche *\\- _Visualizza le statistiche generali del gruppo\\._\n"
        "*\\.verificati *\\- _Mostra la lista degli utenti verificati\\._\n"
        "*\\.ricevuti *\\- _Mostra la classifica dei feedback ricevuti\\._\n"
        "*\\.inviati *\\- _Mostra la classifica dei feedback inviati\\._\n"
        "*\\.limitati *\\- _Mostra la lista degli utenti limitati\\._\n"
        "*\\.comandi *\\- _Mostra questa lista di comandi\\._\n"
        "\n"
        "*👮‍♀️ Comandi Staff\\:*\n"
        "*\\.addinv \\[ID\\|@username\\] \\[numero\\] \\[1\\-5\\] *\\- _Aggiungi invii e carte a un utente\\._\n"
        "*\\.addfeed \\[ID\\|@username\\] \\[numero\\] \\[1\\-5\\] *\\- _Aggiungi feedback e carte a un utente\\._\n"
        "*\\.reminv \\[ID\\|@username\\] \\[numero\\] \\[1\\-5\\] *\\- _Rimuovi invii e carte a un utente\\._\n"
        "*\\.remfeed \\[ID\\|@username\\] \\[numero\\] \\[1\\-5\\] *\\- _Rimuovi feedback e carte a un utente\\._\n"
        "*\\.verifica \\[ID\\|@username\\] *\\- _Verifica un utente\\._\n"
        "*\\.sverifica \\[ID\\|@username\\] *\\- _Rimuovi la verifica di un utente\\._\n"
        "*\\.limita \\[ID\\|@username\\] *\\- _Limita un utente\\._\n"
        "*\\.unlimita \\[ID\\|@username\\] *\\- _Rimuovi il limite a un utente\\._\n"
        "*\\.admin \\[ID\\|@username\\] *\\- _Aggiungi un admin\\._\n"
        "*\\.remadmin \\[ID\\|@username\\] *\\- _Rimuovi un admin\\._\n"
    )


    await update.message.reply_text(
        commands_text,
        parse_mode=ParseMode.MARKDOWN_V2
    )
