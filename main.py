import logging
import os
import asyncio
from aiohttp import web
from typing import Dict, Optional
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, Message, MessageEntity
)
from telegram.ext import (
    Application, MessageHandler, CallbackQueryHandler, filters,
    ContextTypes, CommandHandler
)
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from firebase_admin import db
from firebase_file import load_stats, save_group_users, load_group_users
from stats import update_feedback_stats, start, genera_grafico_totale, save_stats
from telegram.error import BadRequest

# Importa i comandi personalizzati
from comandi import (
    info_utente,
    add_invio,
    add_feed,
    rem_invio,
    rem_feed,
    verify_user,
    limit_user,
    unlimit_user,
    check_limit_condition,
    unverify_user,
    show_commands
)
from utils import add_auth, remove_auth, list_admins, list_verified_users, list_feedback_received, list_feedback_sent, handle_pagination_callback, load_admin_ids, list_limited_users

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversione degli ID dei gruppi in interi
TOKEN = os.getenv("TOKEN")
GRUPPO_SCAMBI = int(os.getenv("GRUPPO_SCAMBI"))
GRUPPO_FEEDBACK_DA_ACCETTARE = int(os.getenv("GRUPPO_FEEDBACK_DA_ACCETTARE"))
GRUPPO_FEEDBACK = int(os.getenv("GRUPPO_FEEDBACK"))
GRUPPO_STAFF = os.getenv("GRUPPO_STAFF")

pending_feedback: Dict[str, dict] = {}
group_users: dict = {}
feedback_messages: Dict[int, int] = {}

stats = load_stats()

async def get_user_from_dict_or_telegram(chat_id: int, username: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    if chat_id != GRUPPO_SCAMBI:
        return None

    global group_users
    if chat_id not in group_users:
        group_users[chat_id] = {}

    for user_id, user_data in group_users[chat_id].items():
        if user_data.get("username", "").lower() == username.lower():
            return user_data

    return None


async def traccia_utente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None or update.effective_chat.id != GRUPPO_SCAMBI:
        return
    if update.effective_user is None or update.message is None:
        logger.warning("Messaggio ricevuto senza informazioni di chat, utente o messaggio.")
        return

    global group_users
    chat = update.effective_chat
    user = update.effective_user

    if chat.id not in group_users:
        group_users[chat.id] = {}
        logger.info(f"Nuovo gruppo aggiunto: {chat.id}")

    if user.id not in group_users[chat.id]:
        # FIX: Unificata la struttura dati per le carte a una lista di 6 elementi (0-5 stelle)
        group_users[chat.id][user.id] = {
            "id": user.id,
            "username": user.username,
            "feedback_fatti": 0,
            "feedback_ricevuti": 0,
            "verified": False,
            "limited": False,
            "cards_donate":   [0] * 7,
            "cards_ricevute": [0] * 7,
        }
        logger.info(f"Nuovo utente aggiunto: {user.username} (ID: {user.id}) nel gruppo {chat.id}")
        save_group_users(group_users)

async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pending_feedback
    message = update.message
    user = update.effective_user
    chat_id = update.effective_chat.id
    if chat_id != GRUPPO_SCAMBI:
        logger.info("Messaggio feedback ignorato: non proviene dal gruppo scambi.")
        return

    await traccia_utente(update, context)

    if message.photo and message.caption:
        caption = message.caption.strip()
        parts = caption.split()
        if len(parts) >= 2 and parts[0] == "@feedback":
            target_username = parts[1].lstrip("@")
            
            # Cerca l'utente nel dizionario caricato
            target_user_info = None
            for uid, u_data in group_users.get(chat_id, {}).items():
                if u_data.get("username", "").lower() == target_username.lower():
                    target_user_info = u_data
                    break
            
            if target_user_info:
                feedback_text = " ".join(parts[2:]) if len(parts) > 2 else ""
                photo_id = message.photo[-1].file_id
                keyboard = [
                    [InlineKeyboardButton("✅ Conferma", callback_data=f"confirm_{message.message_id}"),
                     InlineKeyboardButton("❌ Annulla", callback_data=f"cancel_{message.message_id}")]
                ]
                ricevente = escape_markdown(target_user_info['username'], version=2)
                reply_markup = InlineKeyboardMarkup(keyboard)
                await message.reply_text(
                    f"_📥 Confermi il feedback per @{ricevente}\\?_",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                feedback_data = {
                    "photo_id": message.photo[-1].file_id,
                    "feedback_text": " ".join(parts[2:]) if len(parts) > 2 else "",
                    "target_user_id": target_user_info["id"],
                    "target_username": target_user_info["username"],
                    "user_id": user.id,
                    "sender_username": user.username,
                    "origin_chat_id": chat_id,
                }
                try:
                    ref = db.reference(f'pending_feedback/{message.message_id}')
                    ref.set(feedback_data)
                    logger.info(f"Feedback pendente salvato su Firebase per il messaggio {message.message_id}")
                except Exception as e:
                    logger.error(f"Errore nel salvare il feedback pendente su Firebase: {e}")
                    await message.reply_text("*Si è verificato un errore, riprova\\.*", parse_mode=ParseMode.MARKDOWN_V2)
                    return

                logger.info(f"Feedback pendente salvato per il messaggio {message.message_id}")
            else:
                await message.reply_text("*⚠️ Utente non trovato\\.*", parse_mode=ParseMode.MARKDOWN_V2)
                logger.warning(f"Utente target @{target_username} non trovato.")
        else:
            await message.reply_text(
                "*⚠️ Formato feedback non valido\\.*\n\nUsa: @feedback @username \\+ testo facoltativo",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.warning(f"Feedback con formato errato da {user.username}")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data

    # Gestione del menu di info utente (logica separata)
    if data.startswith("menu_") or data.startswith("back_"):
        group_users = context.bot_data.get('group_users', {})
        target_id = int(data.split("_", 1)[1])
        chat_id = int(GRUPPO_SCAMBI)
        
        if data.startswith("menu_"):
            user_data = group_users.get(chat_id, {}).get(target_id)
            if not user_data:
                await query.edit_message_text("Utente non più disponibile.")
                return

            username = escape_markdown(user_data.get("username", "N/A"), 2)
            cards = user_data.get("cards_donate", [0] * 7)
            received = user_data.get("cards_ricevute", [0] * 7)

            cards = cards if isinstance(cards, list) else [0] * 7
            received = received if isinstance(received, list) else [0] * 7
            cards += [0] * (7 - len(cards))
            received += [0] * (7 - len(received))

            lines = [f"_⏫ Carte donate da @{username}:_"]
            for star in range(7):
                lines.append(f"{'Generico' if star == 0 else f'{star}🌟'}: {cards[star]}")

            lines.append(f"\n_⏬ Carte ricevute da @{username}:_")
            for star in range(7):
                lines.append(f"{'Generico' if star == 0 else f'{star}🌟'}: {received[star]}")

            keyboard = [[InlineKeyboardButton("🔙 Indietro", callback_data=f"back_{target_id}")]]
            await query.edit_message_text(
                text="\n".join(lines),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data.startswith("back_"):
            target_user = group_users.get(chat_id, {}).get(target_id)
            if not target_user:
                await query.edit_message_text("Utente non più disponibile.")
                return

            nome = escape_markdown(target_user.get('username', 'N/A'), version=2)
            verified_status = "✅" if target_user.get("verified") else "❌"
            limited_status = "⛔️" if target_user.get("limited") else "🆓"
            base_msg = (
                f"_ℹ️ Informazioni relative all'utente_\n\n"
                f"*🔢 ID\\:* `{target_user['id']}`\n"
                f"*🌐 Username\\:* @{nome}\n"
                f"*📥 Feedback ricevuti\\:* {target_user.get('feedback_ricevuti', 0)}\n"
                f"*📤 Feedback inviati\\:* {target_user.get('feedback_fatti', 0)}\n"
                f"*🛃 Verificato\\:* {verified_status}\n"
                f"*🔍 Limitato\\:* {limited_status}"
            )
            keyboard = [[InlineKeyboardButton("➕ Maggiori info", callback_data=f"menu_{target_id}")]]
            await query.edit_message_text(
                text=base_msg,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    # Parsing robusto per le callback dei feedback
    parts = data.split("_")
    action = parts[0]
    
    if len(parts) < 2:
        await query.edit_message_text("Errore: callback non valida.")
        return
        
    request_id = parts[1]
    
    # Recupera i dati del feedback pendente da Firebase
    pending_ref = db.reference(f'pending_feedback/{request_id}')
    pending = pending_ref.get()

    if not pending:
        await query.edit_message_caption(
            caption="*🚫 Feedback già elaborato o scaduto*",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Gestione delle azioni di feedback
    if action == "confirm":
        if pending["user_id"] != user.id:
            await query.answer("Non puoi confermare questo feedback.", show_alert=True)
            return

        mittente = escape_markdown(pending['sender_username'], version=2)
        destinatario = escape_markdown(pending['target_username'], version=2)
        mex = escape_markdown(pending['feedback_text'], version=2)
        caption = (f"_🆕 Feedback ricevuto\\!_\n\n"
                   f"*Da\\:* @{mittente} \\[`{pending['user_id']}`\\]\n"
                   f"*Per\\:* @{destinatario} \\[`{pending['target_user_id']}`\\]\n"
                   f"*Messaggio\\:* {mex}")
        keyboard = [[InlineKeyboardButton("👍 Accetta", callback_data=f"accept_{request_id}"),
                     InlineKeyboardButton("👎 Rifiuta", callback_data=f"reject_{request_id}")]]

        sent = await context.bot.send_photo(
            chat_id=GRUPPO_FEEDBACK_DA_ACCETTARE, photo=pending["photo_id"],
            caption=caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        pending_ref.update({"feedback_group_message_id": sent.message_id})
        
        # Gestisce i clic ripetuti intercettando l'errore
        try:
            await query.edit_message_text("_🏹 Feedback inviato\\!_", parse_mode=ParseMode.MARKDOWN_V2)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                # L'utente ha premuto di nuovo il bottone. Ignoriamo l'errore.
                pass
            else:
                # Se l'errore è diverso, lo registriamo per debug.
                logger.error(f"Errore imprevisto durante l'edit del messaggio: {e}")

    elif action == "cancel":
        if pending["user_id"] != user.id:
            await query.answer("Non puoi annullare questo feedback.", show_alert=True)
            return
        pending_ref.delete()
        await query.edit_message_text("_🪃 Feedback annullato\\!_", parse_mode=ParseMode.MARKDOWN_V2)

    elif action == "reject":
        mittente = escape_markdown(pending['sender_username'], version=2)
        destinatario = escape_markdown(pending['target_username'], version=2)
        mex = escape_markdown(pending['feedback_text'], version=2)
        caption = (f"_🆕 Feedback ricevuto\\!_\n\n"
                   f"*Da\\:* @{mittente} \\[`{pending['user_id']}`\\]\n"
                   f"*Per\\:* @{destinatario} \\[`{pending['target_user_id']}`\\]\n"
                   f"*Messaggio\\:* {mex}\n\n*🤌 Feedback rifiutato\\.*")
        await query.edit_message_caption(caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
        pending_ref.delete()

    elif action == "accept":
        mittente = escape_markdown(pending["sender_username"], version=2)
        destinatario = escape_markdown(pending["target_username"], version=2)
        mex = escape_markdown(pending["feedback_text"], version=2)
        caption = (f"_🆕 Feedback ricevuto\\!_\n\n"
                   f"*Da\\:* @{mittente} \\[`{pending['user_id']}`\\]\n"
                   f"*Per\\:* @{destinatario} \\[`{pending['target_user_id']}`\\]\n"
                   f"*Messaggio\\:* {mex}\n\n*Quante stelle vuoi assegnare\\?*")
        star_buttons = [InlineKeyboardButton(f"{i} ⭐", callback_data=f"star_{request_id}_{i}") for i in range(1, 7)]
        star_buttons.append(InlineKeyboardButton("Generico", callback_data=f"star_{request_id}_0"))
        
        keyboard = [star_buttons[:3], star_buttons[3:6], [star_buttons[6]]]
        await query.edit_message_caption(
            caption=caption, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif action == "star":
        if len(parts) < 3:
            await query.edit_message_text("Errore: callback delle stelle non valida.")
            return
        
        stars = int(parts[2])
        group_users = context.bot_data['group_users']
        stats = context.bot_data['stats']
        origin_chat = pending["origin_chat_id"]
        sender = group_users[origin_chat].get(pending["user_id"])
        target = group_users[origin_chat].get(pending["target_user_id"])

        if not sender or not target:
            await query.edit_message_caption(caption="*Errore: utente non trovato nel database.*", parse_mode=ParseMode.MARKDOWN_V2)
            return

        sender["feedback_fatti"] = sender.get("feedback_fatti", 0) + 1
        target["feedback_ricevuti"] = target.get("feedback_ricevuti", 0) + 1
        sender.setdefault("cards_ricevute", [0]*7)[stars] += 1
        target.setdefault("cards_donate", [0]*7)[stars] += 1

        if target["feedback_ricevuti"] >= 25 and not target.get("verified"):
            target["verified"] = True
            nome_verificato = escape_markdown(target["username"], version=2)
            await context.bot.send_message(
                chat_id=GRUPPO_STAFF,
                text=f"_➕ L'utente @{nome_verificato} ha raggiunto i 25 feedback\\._\n\n*🔝 È stato verificato\\!*",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        
        save_group_users(group_users)
        update_feedback_stats(stats, pending["user_id"], pending["sender_username"], pending["target_user_id"], pending["target_username"])
        save_stats(stats)

        stelle_text = "Generico" if stars == 0 else f"{stars} ⭐" 
        mittente = escape_markdown(pending['sender_username'], version=2)
        destinatario = escape_markdown(pending['target_username'], version=2)
        mex = escape_markdown(pending['feedback_text'], version=2)
        final_caption = (f"_🆕 Feedback ricevuto\\!_\n\n"
                         f"*Da\\:* @{mittente} \\[`{pending['user_id']}`\\]\n"
                         f"*Per\\:* @{destinatario} \\[`{pending['target_user_id']}`\\]\n"
                         f"*Messaggio\\:* {mex}\n"
                         f"*Stelle\\:* {stelle_text}\n\n"
                         f"*🤙 Feedback accettato\\.*")

        await context.bot.send_photo(
            chat_id=GRUPPO_FEEDBACK, photo=pending["photo_id"],
            caption=final_caption, parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.edit_message_caption(caption=final_caption, parse_mode=ParseMode.MARKDOWN_V2)
        
        pending_ref.delete()

COMMAND_MAP = {
    "inf": info_utente,
    "addinv": add_invio,
    "addfeed": add_feed,
    "reminv": rem_invio,
    "remfeed": rem_feed,
    "verifica": verify_user,
    "sverifica": unverify_user,
    "limita": limit_user,
    "unlimita": unlimit_user,
    "limitati": list_limited_users,
    "admin": add_auth,
    "remadmin": remove_auth,
    "statistiche": genera_grafico_totale,
    "listadmin": list_admins,
    "verificati": list_verified_users,
    "inviati": list_feedback_sent,
    "ricevuti": list_feedback_received,
    "comandi": show_commands
}


async def dot_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.message.text and update.message.text.startswith('.'):
        command_text = update.message.text[1:]
        parts = command_text.split()
        if not parts:
            return
            
        command_name = parts[0]
        context.args = parts[1:]
        
        if command_name in COMMAND_MAP:
            # Crea un nuovo oggetto messaggio per non interferire con altri handler
            new_message = update.message.to_dict()
            new_message['text'] = '/' + command_text
            new_update = Update.de_json(data={'update_id': update.update_id, 'message': new_message}, bot=context.bot)
            await COMMAND_MAP[command_name](new_update, context)

async def on_startup(app: Application):
    global ALLOWED_USER_IDS
    ALLOWED_USER_IDS = load_admin_ids()
    logger.info(f"Admin inizializzati: {ALLOWED_USER_IDS}")

async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def handle_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Errore nel parse del JSON: {e}")
        return web.Response(status=400, text="Invalid JSON")

    update = Update.de_json(data, application.bot)

    asyncio.create_task(application.process_update(update))

    return web.Response(text="OK")


async def start_webserver() -> None:
    load_dotenv()
    PORT = int(os.getenv('PORT', '8443'))

    webapp = web.Application()
    webapp.router.add_get('/', health_check)       
    webapp.router.add_get('/health', health_check)  
    webapp.router.add_post('/webhook', handle_webhook)

    runner = web.AppRunner(webapp)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

    logger.info(f"Webserver avviato su 0.0.0.0:{PORT}")


async def main() -> None:
    global group_users, stats
    group_users = load_group_users()
    stats = load_stats()
    WEBHOOK_URL = os.getenv('WEBHOOK_URL')  

    if not TOKEN or not WEBHOOK_URL:
        logger.error("Le variabili d'ambiente TOKEN e WEBHOOK_URL devono essere definite.")
        return

    global application
    application = Application.builder().token(TOKEN).build()
    application.bot_data['group_users'] = load_group_users()
    application.bot_data['stats'] = load_stats()
    logger.info("Dati utenti e statistiche caricati in memoria.")


    # Comandi standard
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("inf", info_utente))
    application.add_handler(CommandHandler("addinv", add_invio))
    application.add_handler(CommandHandler("addfeed", add_feed))
    application.add_handler(CommandHandler("reminv", rem_invio))
    application.add_handler(CommandHandler("remfeed", rem_feed))
    application.add_handler(CommandHandler("verifica", verify_user))
    application.add_handler(CommandHandler("sverifica", unverify_user))
    application.add_handler(CommandHandler("limita", limit_user))
    application.add_handler(CommandHandler("unlimita", unlimit_user))
    application.add_handler(CommandHandler("limitati", list_limited_users))
    application.add_handler(CommandHandler("admin", add_auth))
    application.add_handler(CommandHandler("remadmin", remove_auth))
    application.add_handler(CommandHandler("statistiche", genera_grafico_totale))
    application.add_handler(CommandHandler("listadmin", list_admins))
    application.add_handler(CommandHandler("verificati", list_verified_users)) 
    application.add_handler(CommandHandler("inviati", list_feedback_sent))
    application.add_handler(CommandHandler("ricevuti", list_feedback_received))
    application.add_handler(CommandHandler("comandi", show_commands))

    # Handler per i comandi che iniziano con '.'
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^\."), dot_command_handler))
    application.add_handler(CallbackQueryHandler(handle_pagination_callback, pattern=r"^(pagina_verificati|pagina_ricevuti|pagina_inviati|pagina_limitati|pagina_admin)_(\d+)$"))

    # Handler principali
    application.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^@feedback"), feedback))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, traccia_utente))

    await application.initialize()

    await application.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook impostato su: {WEBHOOK_URL}")

    await start_webserver()

    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
