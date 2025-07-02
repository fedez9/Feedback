import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown

from firebase_file import save_group_users
from utils import restricted

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

GRUPPO_SCAMBI = os.getenv("GRUPPO_SCAMBI")
GRUPPO_FEEDBACK_DA_ACCETTARE = os.getenv("GRUPPO_FEEDBACK_DA_ACCETTARE")
GRUPPO_FEEDBACK = os.getenv("GRUPPO_FEEDBACK")
GRUPPO_STAFF = os.getenv("GRUPPO_STAFF")


async def get_user_details(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        chat = await context.bot.get_chat(user_id)
        return chat.username or chat.first_name or str(user_id)
    except Exception as e:
        logger.error(f"Errore nel recupero dei dettagli per l'utente {user_id}: {e}")
        return str(user_id)


async def info_utente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {}) 
    
    args = context.args
    identifier = args[0] if args else str(update.effective_user.id)

    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.get(chat_id, {})

    target_user = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = users_in_chat.get(uid)
    else:
        username = identifier.lstrip("@").lower()
        for user in users_in_chat.values():
            if user.get("username", "").lower() == username:
                target_user = user
                break

    if not target_user:
        await update.message.reply_text("Utente non trovato nel database.")
        return

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

    keyboard = [[InlineKeyboardButton("➕ Maggiori info", callback_data=f"menu_{target_user['id']}")]]
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))


@restricted
async def add_invio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {})
    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.setdefault(chat_id, {})

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato!*\n\nUsa: /addinv @username|id [numero] [stelle]",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    amount = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
    stars  = int(args[2]) if len(args) >= 3 and args[2].isdigit() else 0

    # Trova o crea target_user
    uid = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = users_in_chat.get(uid)
    else:
        username = identifier.lstrip("@").lower()
        target_user = next(
            (u for u in users_in_chat.values() if u.get("username", "").lower() == username),
            None
        )

    if not target_user:
        if not uid:
            await update.message.reply_text("Utente non trovato. Usa l'ID numerico per crearlo.")
            return
        real_username = await get_user_details(uid, context)
        target_user = {
            "id": uid,
            "username": real_username,
            "feedback_ricevuti": 0,
            "feedback_fatti": 0,
            "verified": False,
            "limited": False,
            "cards_donate": [0]*6,
            "cards_ricevute": [0]*6
        }
        users_in_chat[uid] = target_user

    # Aggiorna
    target_user["feedback_fatti"] = target_user.get("feedback_fatti", 0) + amount
    target_user.setdefault("cards_ricevute", [0]*6)
    target_user["cards_ricevute"][stars] += amount

    save_group_users(group_users)

    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(
        f"_✅ Feedback inviati aggiornati per @{nome}, ora a quota {target_user['feedback_fatti']}_",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await check_limit_condition(update, context, target_user)


@restricted
async def add_feed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {})
    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.setdefault(chat_id, {})

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato!*\n\nUsa: /addfeed @username|id [numero] [stelle]",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    amount = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
    stars  = int(args[2]) if len(args) >= 3 and args[2].isdigit() else 0

    uid = None
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = users_in_chat.get(uid)
    else:
        username = identifier.lstrip("@").lower()
        target_user = next(
            (u for u in users_in_chat.values() if u.get("username", "").lower() == username),
            None
        )

    if not target_user:
        if not uid:
            await update.message.reply_text("Utente non trovato. Usa l'ID numerico per crearlo.")
            return
        real_username = await get_user_details(uid, context)
        target_user = {
            "id": uid,
            "username": real_username,
            "feedback_ricevuti": 0,
            "feedback_fatti": 0,
            "verified": False,
            "limited": False,
            "cards_donate": [0]*6,
            "cards_ricevute": [0]*6
        }
        users_in_chat[uid] = target_user

    target_user["feedback_ricevuti"] = target_user.get("feedback_ricevuti", 0) + amount
    target_user.setdefault("cards_donate", [0]*6)
    target_user["cards_donate"][stars] += amount

    # Verifica badge a 25 feedback
    if target_user["feedback_ricevuti"] >= 25 and not target_user.get("verified", False):
        target_user["verified"] = True
        nome_verificato = escape_markdown(target_user['username'], version=2)
        await context.bot.send_message(
            chat_id=GRUPPO_STAFF,
            text=(
                f"_➕ L'utente @{nome_verificato} ha raggiunto i 25 feedback\\._\n\n"
                "*🔝 È stato verificato\\.*"
            ),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    save_group_users(group_users)

    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(
        f"_✅ Feedback ricevuti aggiornati per @{nome}, ora a quota {target_user['feedback_ricevuti']}_",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await check_limit_condition(update, context, target_user)


@restricted
async def rem_invio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {})
    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.get(chat_id, {})

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato!*\n\nUsa: /reminv @username|id [numero] [stelle]",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    amount = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
    stars  = int(args[2]) if len(args) >= 3 and args[2].isdigit() else 0

    # Trova target_user
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = users_in_chat.get(uid)
    else:
        username = identifier.lstrip("@").lower()
        target_user = next(
            (u for u in users_in_chat.values() if u.get("username", "").lower() == username),
            None
        )

    if not target_user:
        await update.message.reply_text("Utente non trovato.")
        return

    target_user["feedback_fatti"] = max(0, target_user.get("feedback_fatti", 0) - amount)
    target_user.setdefault("cards_ricevute", [0]*6)
    target_user["cards_ricevute"][stars] = max(0, target_user["cards_ricevute"][stars] - amount)

    save_group_users(group_users)

    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(
        f"_✅ Feedback inviati aggiornati per @{nome}, ora a quota {target_user['feedback_fatti']}_",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await check_limit_condition(update, context, target_user)


@restricted
async def rem_feed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {})
    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.get(chat_id, {})

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato!*\n\nUsa: /remfeed @username|id [numero] [stelle]",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    identifier = args[0]
    amount = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
    stars  = int(args[2]) if len(args) >= 3 and args[2].isdigit() else 0

    # Trova target_user
    if identifier.lstrip("-").isdigit():
        uid = int(identifier)
        target_user = users_in_chat.get(uid)
    else:
        username = identifier.lstrip("@").lower()
        target_user = next(
            (u for u in users_in_chat.values() if u.get("username", "").lower() == username),
            None
        )

    if not target_user:
        await update.message.reply_text("Utente non trovato.")
        return

    current = target_user.get("feedback_ricevuti", 0)
    target_user["feedback_ricevuti"] = max(0, current - amount)
    target_user.setdefault("cards_donate", [0]*6)
    target_user["cards_donate"][stars] = max(0, target_user["cards_donate"][stars] - amount)

    # Rimuovi badge se sotto soglia
    if current >= 25 and target_user["feedback_ricevuti"] < 25:
        target_user["verified"] = False
        nome = escape_markdown(target_user['username'], version=2)
        await update.message.reply_text(
            f"_➖ L'utente @{nome} ha meno di 25 feedback\\._\n\n*🚮 Non è più verificato\\.*",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    save_group_users(group_users)

    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(
        f"_✅ Feedback ricevuti aggiornati per @{nome}, ora a quota {target_user['feedback_ricevuti']}_",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await check_limit_condition(update, context, target_user)


async def find_target_user(identifier: str, users_in_chat: dict) -> dict | None:
    if identifier.lstrip("-").isdigit():
        return users_in_chat.get(int(identifier))
    else:
        username = identifier.lstrip("@").lower()
        for user in users_in_chat.values():
            if user.get("username", "").lower() == username:
                return user
    return None


@restricted
async def verify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {})
    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.get(chat_id, {})
    
    if not context.args:
        await update.message.reply_text("*🆘 Comando errato\\!*\n\nUsa: /verifica @username|id", parse_mode=ParseMode.MARKDOWN_V2)
        return

    target_user = await find_target_user(context.args[0], users_in_chat)
    if not target_user:
        await update.message.reply_text("Utente non trovato.")
        return

    if target_user.get("verified"):
        await update.message.reply_text("L'utente è già verificato.")
        return

    target_user["verified"] = True
    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(f"_✅ L'utente @{nome} è stato verificato\\!_", parse_mode=ParseMode.MARKDOWN_V2)


@restricted
async def unverify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {})
    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.get(chat_id, {})

    if not context.args:
        await update.message.reply_text("*🆘 Comando errato\\!*\n\nUsa: /sverifica @username|id", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    target_user = await find_target_user(context.args[0], users_in_chat)
    if not target_user:
        await update.message.reply_text("Utente non trovato.")
        return

    if not target_user.get("verified"):
        await update.message.reply_text("L'utente non è attualmente verificato.")
        return

    target_user["verified"] = False
    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(f"_✅ La verifica per @{nome} è stata rimossa\\._", parse_mode=ParseMode.MARKDOWN_V2)


@restricted
async def limit_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {})
    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.get(chat_id, {})

    if not context.args:
        await update.message.reply_text("*🆘 Comando errato\\!*\n\nUsa: /limita @username|id", parse_mode=ParseMode.MARKDOWN_V2)
        return

    target_user = await find_target_user(context.args[0], users_in_chat)
    if not target_user:
        await update.message.reply_text("Utente non trovato.")
        return
        
    if target_user.get("limited"):
        await update.message.reply_text("L'utente è già limitato.")
        return

    target_user["limited"] = True
    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(f"_✅ L'utente @{nome} è stato limitato_", parse_mode=ParseMode.MARKDOWN_V2)


@restricted
async def unlimit_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_users = context.bot_data.get('group_users', {})
    chat_id = int(GRUPPO_SCAMBI)
    users_in_chat = group_users.get(chat_id, {})

    if not context.args:
        await update.message.reply_text("*🆘 Comando errato\\!*\n\nUsa: /unlimita @username|id", parse_mode=ParseMode.MARKDOWN_V2)
        return

    target_user = await find_target_user(context.args[0], users_in_chat)
    if not target_user:
        await update.message.reply_text("Utente non trovato.")
        return

    if not target_user.get("limited"):
        await update.message.reply_text("L'utente non risulta limitato.")
        return

    target_user["limited"] = False
    save_group_users(group_users)
    nome = escape_markdown(target_user['username'], version=2)
    await update.message.reply_text(f"_✅ L'utente @{nome} non è più limitato_", parse_mode=ParseMode.MARKDOWN_V2)


async def check_limit_condition(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict):
    diff = user.get("feedback_ricevuti", 0) - user.get("feedback_fatti", 0)
    if user.get("limited") and diff >= 0:
        nome = escape_markdown(user.get("username", "Sconosciuto"), version=2)
        msg = f"_🟰 L'utente @{nome} ha pareggiato i feedback\\._\n\n*Ora ha un divario di {diff}\\.*"
        await context.bot.send_message(chat_id=GRUPPO_STAFF, text=msg, parse_mode=ParseMode.MARKDOWN_V2)


async def show_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    commands_text = (
        "*_⚙️ Lista Comandi\\:_*\n\n"
        "*👥 Comandi Utente\\:*\n"
        "*\\.inf \\[ID\\|@username\\]* \\- _Ottieni info su un utente\\._\n"
        "*\\.statistiche* \\- _Visualizza le statistiche generali del gruppo\\._\n"
        "*\\.verificati* \\- _Mostra la lista degli utenti verificati\\._\n"
        "*\\.ricevuti* \\- _Mostra la classifica dei feedback ricevuti\\._\n"
        "*\\.inviati* \\- _Mostra la classifica dei feedback inviati\\._\n"
        "*\\.limitati* \\- _Mostra la lista degli utenti limitati\\._\n"
        "*\\.comandi* \\- _Mostra questa lista di comandi\\._\n\n"
        "*👮‍♀️ Comandi Staff\\:*\n"
        "*\\.addinv \\[ID\\|@username\\] \\[num\\] \\[stelle\\]* \\- _Aggiungi invii e carte\\._\n"
        "*\\.addfeed \\[ID\\|@username\\] \\[num\\] \\[stelle\\]* \\- _Aggiungi feedback e carte\\._\n"
        "*\\.reminv \\[ID\\|@username\\] \\[num\\] \\[stelle\\]* \\- _Rimuovi invii e carte\\._\n"
        "*\\.remfeed \\[ID\\|@username\\] \\[num\\] \\[stelle\\]* \\- _Rimuovi feedback e carte\\._\n"
        "*\\.verifica \\[ID\\|@username\\]* \\- _Verifica un utente\\._\n"
        "*\\.sverifica \\[ID\\|@username\\]* \\- _Rimuovi la verifica\\._\n"
        "*\\.limita \\[ID\\|@username\\]* \\- _Limita un utente\\._\n"
        "*\\.unlimita \\[ID\\|@username\\]* \\- _Rimuovi il limite\\._\n"
        "*\\.admin \\[ID\\|@username\\]* \\- _Aggiungi un admin del bot\\._\n"
        "*\\.remadmin \\[ID\\|@username\\]* \\- _Rimuovi un admin del bot\\._\n"
    )
    await update.message.reply_text(commands_text, parse_mode=ParseMode.MARKDOWN_V2)
