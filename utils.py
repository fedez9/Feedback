import logging
import os
import json
import requests
from typing import Dict, Set, List
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from dotenv import load_dotenv
from firebase_file import load_admin_ids, save_admin_ids, load_group_users
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
GROUP_USERS_BIN_ID = os.getenv("GROUP_USERS_BIN_ID")
ADMIN_IDS_BIN_ID = os.getenv("ADMIN_IDS_BIN_ID")
GRUPPO_STAFF = os.getenv("GRUPPO_STAFF")
GRUPPO_SCAMBI = os.getenv("GRUPPO_SCAMBI")
ALLOWED_USER_IDS: Set[int] = set()  # Inizializzazione temporanea, verrà sovrascritta
ITEMS_PER_PAGE = 25
PAGINATION_DATA_STORE: Dict[int, Dict[str, any]] = {}
ALLOWED_USER_IDS = load_admin_ids()

def restricted(func):
    @wraps(func)
    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USER_IDS:
            logger.warning(f"Tentativo di accesso non autorizzato: {user_id} ha tentato di usare {func.__name__}")
            if update.message:
                await update.message.reply_text(
                    "*⛔ Accesso negato\\!*",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            return
        return await func(update, context, *args, **kwargs)

    return wrapped

@restricted
async def add_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ALLOWED_USER_IDS
    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato\\!*\\n\\nUsa: /admin ID o @username",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    param = args[0]
    new_user_id = await get_user_id(update, param, context)
    if new_user_id is None:
        return  # Messaggio di errore già inviato in get_user_id

    ALLOWED_USER_IDS = load_admin_ids()

    if new_user_id in ALLOWED_USER_IDS:
        await update.message.reply_text(
            "_Questo utente è già autorizzato\\._", parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    ALLOWED_USER_IDS.add(new_user_id)
    save_admin_ids(ALLOWED_USER_IDS)  # Salva su Firebase

    username = await get_username(new_user_id, context)

    await update.message.reply_text(
        f"_👮‍♂️ Utente @{username} aggiunto tra gli admin del bot\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


@restricted
async def remove_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ALLOWED_USER_IDS
    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "*🆘 Comando errato\\!*\\n\\nUsa: /remadmin ID o @username",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    param = args[0]
    rem_user_id = await get_user_id(update, param, context)
    if rem_user_id is None:
        return  # Messaggio di errore già inviato in get_user_id

    ALLOWED_USER_IDS = load_admin_ids()

    if rem_user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text(
            "_Questo utente non risulta nella lista degli autorizzati\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    ALLOWED_USER_IDS.remove(rem_user_id)
    save_admin_ids(ALLOWED_USER_IDS)  # Salva su Firebase

    username = await get_username(rem_user_id, context)

    await update.message.reply_text(
        f"_✈️ Utente @{username} rimosso dagli admin del bot\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

async def get_user_id(update: Update, param: str, context: ContextTypes.DEFAULT_TYPE):
    """
    Recupera l'ID di un utente. Prova prima a convertirlo in un intero.
    Se fallisce, lo tratta come uno username e cerca nel database locale (caricato da Firebase).
    """
    try:
        # Prova a convertire direttamente il parametro in un ID numerico
        return int(param)
    except ValueError:
        # Se non è un ID, trattalo come uno username
        username_to_find = param.lstrip("@").lower()
        
        # Carica i dati degli utenti dal database (simulato dal tuo file.json)
        group_users = load_group_users()
        
        # Itera su tutti i gruppi e gli utenti nel database per trovare una corrispondenza
        for chat_id, users in group_users.items():
            if isinstance(users, dict):
                for user_id, user_data in users.items():
                    if isinstance(user_data, dict) and user_data.get("username", "").lower() == username_to_find:
                        return user_data.get("id")  # Restituisce l'ID trovato

        # Se l'utente non è stato trovato nel database locale
        await update.message.reply_text(
            f"_Utente @{escape_markdown(param.lstrip('@'), version=2)} non trovato nel database del gruppo\\._\n"
            "_Assicurati che l'utente abbia interagito con il gruppo e che il suo username sia corretto\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return None


async def get_username(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat = await context.bot.get_chat(user_id)
        return escape_markdown(chat.username or chat.first_name or str(user_id), version=2)
    except Exception as e:
        logger.error(
            "Errore nel recupero dei dettagli per l'utente %s: %s", user_id, e
        )
        return str(user_id)

@restricted
async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Stampa la lista degli amministratori,
    con paginazione e bottoni per navigare le pagine.
    """
    admin_ids = list(load_admin_ids())
    if not admin_ids:
        await update.message.reply_text(
            "_Nessun amministratore trovato\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    admin_data = []
    # Recupera username di ogni admin
    for uid in admin_ids:
        try:
            member = await context.bot.get_chat_member(int(GRUPPO_STAFF or GRUPPO_SCAMBI), uid)
            username = member.user.username or "N/A"
        except Exception:
            username = "N/A"
        admin_data.append({"id": uid, "username": username})

    await send_paginated_message(
        update,
        context,
        admin_data,
        'admin',
        '*👮‍♂️ Lista Admin Bot*'
    )

@restricted
async def list_verified_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Stampa la lista di tutti gli utenti verificati nel gruppo,
    ordinati per numero crescente di feedback ricevuti, con paginazione.
    """
    global group_users
    group_users = load_group_users() # Ricarica per avere i dati più aggiornati

    chat_id = int(GRUPPO_SCAMBI)

    if chat_id not in group_users or not group_users[chat_id]:
        await update.message.reply_text(
            "_Nessun utente trovato o verificato in questo gruppo\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    verified_users_data = []
    for user_id, user_data in group_users[chat_id].items():
        if user_data.get("verified"):
            username = user_data.get("username", "N/A")
            feedback_ricevuti = user_data.get("feedback_ricevuti", 0)
            verified_users_data.append({
                "id": user_id,
                "username": username,
                "feedback_ricevuti": feedback_ricevuti
            })
    
    if not verified_users_data:
        await update.message.reply_text(
            "_Nessun utente verificato trovato in questo gruppo\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    verified_users_data.sort(key=lambda x: (x["feedback_ricevuti"], x["username"].lower()))

    await send_paginated_message(
        update, context, verified_users_data, 'verificati', '*✅ Utenti Verificati*'
    )

@restricted
async def list_feedback_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stampa la lista di tutti gli utenti con il numero di feedback ricevuti, ordinati decrescentemente, con paginazione."""
    global group_users
    group_users = load_group_users()

    chat_id = int(GRUPPO_SCAMBI)

    if chat_id not in group_users or not group_users[chat_id]:
        await update.message.reply_text(
            "_Nessun utente trovato con feedback ricevuti in questo gruppo\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    users_with_feedback = []
    for user_id, user_data in group_users[chat_id].items():
        feedback_ricevuti = user_data.get("feedback_ricevuti", 0)
        if feedback_ricevuti > 0:
            username = user_data.get("username", "N/A")
            users_with_feedback.append({
                "id": user_id,
                "username": username,
                "feedback_ricevuti": feedback_ricevuti
            })
    
    if not users_with_feedback:
        await update.message.reply_text(
            "_Nessun utente ha ancora ricevuto feedback in questo gruppo\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    users_with_feedback.sort(key=lambda x: x["feedback_ricevuti"], reverse=True)

    await send_paginated_message(
        update, context, users_with_feedback, 'ricevuti', '*🏆 Classifica Feedback Ricevuti*'
    )

@restricted
async def list_feedback_sent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global group_users
    group_users = load_group_users()

    chat_id = int(GRUPPO_SCAMBI)

    if chat_id not in group_users or not group_users[chat_id]:
        await update.message.reply_text(
            "_Nessun utente trovato con feedback inviati in questo gruppo\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    users_with_feedback = []
    for user_id, user_data in group_users[chat_id].items():
        feedback_fatti = user_data.get("feedback_fatti", 0)
        if feedback_fatti > 0:
            username = user_data.get("username", "N/A")
            users_with_feedback.append({
                "id": user_id,
                "username": username,
                "feedback_fatti": feedback_fatti
            })
    
    if not users_with_feedback:
        await update.message.reply_text(
            "_Nessun utente ha ancora inviato feedback in questo gruppo\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    users_with_feedback.sort(key=lambda x: x["feedback_fatti"], reverse=True)

    await send_paginated_message(
        update, context, users_with_feedback, 'inviati', '*📊 Classifica Feedback Inviati*'
    )


@restricted
async def list_limited_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Stampa la lista di tutti gli utenti limitati nel gruppo,
    con paginazione e bottoni per navigare le pagine.
    """
    global group_users
    group_users = load_group_users()

    chat_id = int(GRUPPO_SCAMBI)
    if chat_id not in group_users or not group_users[chat_id]:
        await update.message.reply_text(
            "_Nessun utente trovato o limitato in questo gruppo\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    limited_users_data = []
    for user_id, user_data in group_users[chat_id].items():
        if user_data.get("limited"):
            username = user_data.get("username", "N/A")
            feedback_ricevuti = user_data.get("feedback_ricevuti", 0)
            feedback_fatti = user_data.get("feedback_fatti", 0)
            diff = feedback_ricevuti - feedback_fatti
            limited_users_data.append({
                "id": user_id,
                "username": username,
                "divario": diff,
                "feedback_ricevuti": feedback_ricevuti,
                "feedback_fatti": feedback_fatti
            })

    if not limited_users_data:
        await update.message.reply_text(
            "_Nessun utente limitato trovato\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Ordina per divario e username
    limited_users_data.sort(key=lambda x: (x["divario"], x["username"].lower()))

    # Usa la funzione generica di paginazione con key 'limitati'
    await send_paginated_message(
        update,
        context,
        limited_users_data,
        'limitati',
        '*🚫 Utenti Limitati*'
    )

async def send_paginated_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data_list: List[Dict], command_key: str, title: str, current_page: int = 0, message_id: int = None) -> None:
    total_items = len(data_list)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    if total_items == 0:
        text = f"_{title.strip('*').strip()} non trovati\\._"
        if message_id:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        return

    # Se c'è una sola pagina, non mostriamo i bottoni di paginazione
    if total_pages <= 1:
        message_lines = [f"*{title}*\n"]
        for i, item in enumerate(data_list, start=1):
            username_esc = escape_markdown(item['username'], version=2)
            if command_key == 'verificati':
                message_lines.append(f"{i}\\. @{username_esc} \\[`{item['id']}`\\]\\: `{item['feedback_ricevuti']}`")
            elif command_key == 'ricevuti':
                message_lines.append(f"{i}\\. @{username_esc} \\[`{item['id']}`\\]\\: `{item['feedback_ricevuti']}`")
            elif command_key == 'inviati':
                message_lines.append(f"{i}\\. @{username_esc} \\[`{item['id']}`\\]\\: `{item['feedback_fatti']}`")
            elif command_key == 'limitati':
                divario_esc = escape_markdown(str(item['divario']), version=2)
                message_lines.append(f"{i}\\. @{username_esc} \\[`{item['id']}`\\]\\: `{divario_esc}`")
            elif command_key == 'admin':
                message_lines.append(f"{i}\\. @{username_esc} \\[`{item['id']}`\\]")

            else:
                message_lines.append(f"{i}\\. {str(item)}")

        full_text = "\n".join(message_lines)
        if message_id:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=message_id,
                text=full_text,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.reply_text(
                full_text,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        return

    # Calcola start/end per pagina
    start_index = current_page * ITEMS_PER_PAGE
    end_index = min(start_index + ITEMS_PER_PAGE, total_items)
    current_page_items = data_list[start_index:end_index]

    message_lines = [f"*{title}* \\(Pagina {current_page + 1}/{total_pages}\\)\n"]
    offset = start_index + 1
    for i, item in enumerate(current_page_items):
        index_number = offset + i
        username_esc = escape_markdown(item['username'], version=2)
        if command_key == 'verificati':
            message_lines.append(f"{index_number}\\. @{username_esc} \\[`{item['id']}`\\]\\: `{item['feedback_ricevuti']}`")
        elif command_key == 'ricevuti':
            message_lines.append(f"{index_number}\\. @{username_esc} \\[`{item['id']}`\\]\\: `{item['feedback_ricevuti']}`")
        elif command_key == 'inviati':
            message_lines.append(f"{index_number}\\. @{username_esc} \\[`{item['id']}`\\]\\: `{item['feedback_fatti']}`")
        elif command_key == 'limitati':
            divario_esc = escape_markdown(str(item['divario']), version=2)
            message_lines.append(f"{index_number}\\. @{username_esc} \\[`{item['id']}`\\]\\: `{divario_esc}`")
        elif command_key == 'admin':
            message_lines.append(f"{index_number}\\. @{username_esc} \\[`{item['id']}`\\]")
        else:
            message_lines.append(f"{index_number}\\. {str(item)}")

    keyboard = [[InlineKeyboardButton("⏪", callback_data=f"pagina_{command_key}_0"), 
                  InlineKeyboardButton("◀️", callback_data=f"pagina_{command_key}_{max(0, current_page-1)}"),
                  InlineKeyboardButton(f"{current_page+1}/{total_pages}", callback_data="ignore_page_number"),
                  InlineKeyboardButton("▶️", callback_data=f"pagina_{command_key}_{min(total_pages-1, current_page+1)}"),
                  InlineKeyboardButton("⏩", callback_data=f"pagina_{command_key}_{total_pages-1}")]]
    # Flatten keyboard rows
    reply_markup = InlineKeyboardMarkup([row for row in keyboard])
    full_text = "\n".join(message_lines)

    if message_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=message_id,
            text=full_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await update.message.reply_text(
            full_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Salva stato paginazione
    user_id = update.effective_user.id
    if user_id not in PAGINATION_DATA_STORE:
        PAGINATION_DATA_STORE[user_id] = {}
    PAGINATION_DATA_STORE[user_id][command_key] = {
        'data_list': data_list,
        'total_pages': total_pages,
        'current_page': current_page
    }

async def handle_pagination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "ignore_page_number":
        return
    parts = query.data.split('_')
    command_key = parts[1]
    new_page = int(parts[2])
    user_id = query.from_user.id
    store = PAGINATION_DATA_STORE.get(user_id, {}).get(command_key)
    if not store:
        await query.edit_message_text(
            "_Errore: Dati di paginazione non disponibili\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    total_pages = store['total_pages']
    
    if 0 <= new_page < total_pages and new_page != store['current_page']:
        title_map = {
            'verificati': '✅ Utenti Verificati',
            'ricevuti': '🏆 Classifica Feedback Ricevuti',
            'inviati': '📊 Classifica Feedback Inviati',
            'limitati': '🚫 Utenti Limitati',
            'admin': '👮‍♂️ Lista Admin Bot'
        }
        await send_paginated_message(
            update=update,
            context=context,
            data_list=store['data_list'],
            command_key=command_key,
            title=f"*{title_map.get(command_key, command_key)}*",
            current_page=new_page,
            message_id=query.message.message_id
        )
