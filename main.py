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

from firebase_file import load_stats, save_group_users, load_group_users
from stats import update_feedback_stats, start, genera_grafico_totale

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

    # Logica di fallback per aggiungere un utente se non esiste, non più necessaria
    # dato che i comandi ora gestiscono l'utente non trovato.
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
        # Inizializziamo le carte da 0 a 5 stelle: 0 = 'senza stelle'
        group_users[chat.id][user.id] = {
            "id": user.id,
            "username": user.username,
            "feedback_fatti": 0,
            "feedback_ricevuti": 0,
            "verified": False,
            "limited": False,
            "cards_donate":   {star: 0 for star in range(0, 6)},
            "cards_ricevute": {star: 0 for star in range(0, 6)},
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
                reply_markup = InlineKeyboardMarkup(keyboard)
                await message.reply_text(
                    f"_📥 Confermi il feedback per @{target_user_info['username']}\\?_",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                pending_feedback[str(message.message_id)] = {
                    "photo_id": photo_id,
                    "feedback_text": feedback_text,
                    "target_user_id": target_user_info["id"],
                    "target_username": target_user_info["username"],
                    "user_id": user.id,
                    "sender_username": user.username,
                    "origin_chat_id": chat_id,
                }
                logger.info(f"Feedback pendente salvato per il messaggio {message.message_id}")
            else:
                await message.reply_text("*⚠️ Utente non trovato\\.*", parse_mode=ParseMode.MARKDOWN_V2)
                logger.warning(f"Utente target @{target_username} non trovato.")
        else:
            await message.reply_text(
                "*⚠️ Formato feedback non valido\\.*\n\nUsa: @feedback @username + testo facoltativo",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.warning(f"Feedback con formato errato da {user.username}")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pending_feedback, group_users, feedback_messages, stats
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data.startswith("menu_"):
        global group_users
        group_users = load_group_users()
        target_id = int(query.data.split("_", 1)[1])
        chat_id = int(GRUPPO_SCAMBI)
        user_data = group_users.get(chat_id, {}).get(target_id)
        if not user_data:
            await query.edit_message_text("Utente non più disponibile.")
            return

        username = user_data.get("username", "N/A")
        cards = user_data.get("cards_donate", {})
        received = user_data.get("cards_ricevute", {})

        lines = [f"_⏫ Carte donate da @{escape_markdown(username, 2)}:_"]
        # da 0 a 5, dove 0 = Generico
        for star in range(0, 6):
            label = "Generico" if star == 0 else f"{star}🌟"
            donate = cards[star]
            lines.append(f"{label}: {donate}")

        lines.append("")  # separatore
        lines.append(f"_⏬ Carte ricevute da @{escape_markdown(username, 2)}:_")
        for star in range(0, 6):
            label = "Generico" if star == 0 else f"{star}🌟"
            ricevute = received[star]
            lines.append(f"{label}: {ricevute}")

        cards_text = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 Indietro", callback_data=f"back_{target_id}")]]
        await query.edit_message_text(
            text=cards_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
        
    elif query.data.startswith("back_"):
        # Torna alla schermata base delle info utente, riscrivendo il testo e il pulsante
        target_id = int(query.data.split("_", 1)[1])
        chat_id = int(GRUPPO_SCAMBI)
        user = group_users.get(chat_id, {}).get(target_id)

        if not user:
            await query.edit_message_text("Utente non più disponibile.")
            return

        # Costruisci il testo base
        nome = escape_markdown(user.get('username', 'N/A'), version=2)
        verified_status = "✅" if user.get("verified") else "❌"
        limited_status = "🔕" if user.get("limited") else "🔔"
        base_msg = (
            f"_ℹ️ Informazioni relative all'utente_\n\n"
            f"*🔢 ID:* `{user['id']}`\n"
            f"*🌐 Username:* @{nome}\n"
            f"*📥 Feedback ricevuti:* {user.get('feedback_ricevuti', 0)}\n"
            f"*📤 Feedback inviati:* {user.get('feedback_fatti', 0)}\n"
            f"*🛃 Verificato:* {verified_status}\n"
            f"*🔍 Limitato:* {limited_status}"
        )

        # Pulsante “Maggiori info”
        keyboard = [
            [InlineKeyboardButton("➕ Maggiori info", callback_data=f"menu_{target_id}")]
        ]
        markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text=base_msg,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=markup
        )
        return

    elif query.data.startswith("confirm_"):
        request_id = query.data.split("_")[1]
        pending = pending_feedback.get(request_id)
        if not pending or pending["user_id"] != user.id:
            await query.answer("Non puoi confermare questo feedback.", show_alert=True)
            return

        try:
            keyboard = [
                [InlineKeyboardButton("👍 Accetta", callback_data=f"accept_{request_id}"),
                 InlineKeyboardButton("👎 Rifiuta", callback_data=f"reject_{request_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            mittente = escape_markdown(pending['sender_username'], version=2)
            destinatario = escape_markdown(pending['target_username'], version=2)
            mex = escape_markdown(pending['feedback_text'], version=2)
            caption = (f"_🆕 Feedback ricevuto\\!_\n\n*Da\\:* @{mittente} \\[`{pending['user_id']}`\\]\n"
                       f"*Per\\:* @{destinatario} \\[`{pending['target_user_id']}`\\]\n*Messaggio\\:* {mex}")
            sent_message: Message = await context.bot.send_photo(
                chat_id=GRUPPO_FEEDBACK_DA_ACCETTARE,
                photo=pending["photo_id"],
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            pending_feedback[request_id]["feedback_group_message_id"] = sent_message.message_id
            await query.edit_message_text("_🏹 Feedback inviato\\!_", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await query.edit_message_text(f"Errore nell'invio del feedback: {e}")
            logger.error(f"Errore nell'invio del feedback: {e}")

    elif query.data.startswith("cancel_"):
        request_id = query.data.split("_")[1]
        pending = pending_feedback.get(request_id)
        if not pending or pending["user_id"] != user.id:
            await query.answer("Non puoi annullare questo feedback.", show_alert=True)
            return
        del pending_feedback[request_id]
        await query.edit_message_text("_🪃 Feedback annullato\\!_", parse_mode=ParseMode.MARKDOWN_V2)

    elif query.data.startswith("accept_"):
        request_id = query.data.split("_", 1)[1]
        pending = pending_feedback.get(request_id)
        if not pending:
            await query.edit_message_text("Feedback non più valido o già processato.")
            return

        # Ricarica i dati
        group_users = load_group_users()
        origin_chat = pending["origin_chat_id"]
        sender_id   = pending["user_id"]
        target_id   = pending["target_user_id"]

        sender = group_users[origin_chat].get(sender_id)
        target = group_users[origin_chat].get(target_id)
        if not sender or not target:
            await query.edit_message_text("Errore: utente non trovato nel database.")
            return

        # 1) Incrementa i contatori feedback
        sender["feedback_fatti"] = sender.get("feedback_fatti", 0) + 1
        target["feedback_ricevuti"] = target.get("feedback_ricevuti", 0) + 1

        # 2) Eventuale verifica autom. al 25° feedback
        if target["feedback_ricevuti"] >= 25 and not target.get("verified"):
            target["verified"] = True
            nome_verificato = escape_markdown(target["username"], version=2)
            await context.bot.send_message(
                chat_id=int(os.getenv("GRUPPO_STAFF")),
                text=f"_🎉 L'utente @{nome_verificato} ha raggiunto i 25 feedback ed è stato verificato\\!_",
                parse_mode=ParseMode.MARKDOWN_V2
            )

        save_group_users(group_users)
        update_feedback_stats(stats, sender_id, pending["sender_username"], target_id, pending["target_username"])

        # 3) Modifica caption del messaggio in gruppo di revisione
        mittente = escape_markdown(pending["sender_username"], version=2)
        destinatario = escape_markdown(pending["target_username"], version=2)
        mex = escape_markdown(pending["feedback_text"], version=2)
        new_caption = (
            f"_🆕 Feedback ricevuto_ 💪 *Accettato\\!*\n\n"
            f"*Da\\:* @{mittente} [`{sender_id}`]\n"
            f"*Per\\:* @{destinatario} [`{target_id}`]\n"
            f"*Messaggio\\:* {mex}"
        )
        await query.edit_message_caption(
            caption=new_caption,
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # 4) Chiedi le stelle **nella stessa chat** (GRUPPO_FEEDBACK_DA_ACCETTARE)
        star_buttons = [
            InlineKeyboardButton("⭐️",      callback_data=f"star_{request_id}_1"),
            InlineKeyboardButton("⭐️⭐️",     callback_data=f"star_{request_id}_2"),
            InlineKeyboardButton("⭐️⭐️⭐️",   callback_data=f"star_{request_id}_3"),
            InlineKeyboardButton("⭐️⭐️⭐️⭐️", callback_data=f"star_{request_id}_4"),
            InlineKeyboardButton("⭐️⭐️⭐️⭐️⭐️", callback_data=f"star_{request_id}_5"),
            InlineKeyboardButton("Generico", callback_data=f"star_{request_id}_0")
        ]
        keyboard = [
            star_buttons[:3],
            star_buttons[3:]
        ]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,  # stessa chat di revisione
            text=(
                "✨ _Feedback accettato\\!_\n"
                "Quante stelle vuoi assegnare\\?"
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # 5) Segnala in pending che stiamo aspettando il rating
        pending_feedback[request_id]["awaiting_rating"] = True
        return
    
    elif query.data.startswith("star_"):
        # data = "star_<request_id>_<n>"
        _, request_id, stars_str = query.data.split("_")
        stars = int(stars_str)

        pending = pending_feedback.get(request_id)
        if not pending or not pending.get("awaiting_rating"):
            await query.answer("Nessuna valutazione in corso.", show_alert=True)
            return

        origin_chat = pending["origin_chat_id"]
        sender_id   = pending["user_id"]
        target_id   = pending["target_user_id"]

        # Ricarica per sicurezza
        group_users = load_group_users()
        sender = group_users[origin_chat].get(sender_id)
        target = group_users[origin_chat].get(target_id)

        if not sender or not target:
            await query.edit_message_text("Errore: utente non trovato.")
            return

        # Assicuriamoci che esistano i dizionari per le carte
        sender.setdefault("cards_donate", {s: 0 for s in range(0, 6)})
        target.setdefault("cards_ricevute", {s: 0 for s in range(0, 6)})

        # Aggiorna carte donate del mittente
        sender["cards_ricevute"][stars] += 1
        # Aggiorna carte ricevute del destinatario
        target["cards_donate"][stars] += 1

        # Salva le modifiche
        save_group_users(group_users)

        # 1) Invia il feedback valutato al gruppo FEEDBACK
        mittente = escape_markdown(pending['sender_username'], version=2)
        destinatario = escape_markdown(pending['target_username'], version=2)
        mex = escape_markdown(pending['feedback_text'], version=2)
        caption = (
            f"_⭐ Feedback Valutato\\! ⭐_\n\n"
            f"*Da\\:* @{mittente} [`{sender_id}`]\n"
            f"*Per\\:* @{destinatario} [`{target_id}`]\n"
            f"*Stelle\\:* {stars}⭐️\n"
            f"*Messaggio\\:* {mex}"
        )
        await context.bot.send_photo(
            chat_id=GRUPPO_FEEDBACK,
            photo=pending["photo_id"],
            caption=caption,
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # 2) Risposta di conferma e pulizia del pending
        await query.edit_message_text(
            f"✅ Hai assegnato *{stars}⭐️* a @{pending['target_username']}\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        del pending_feedback[request_id]
        return

    elif query.data.startswith("reject_"):
        request_id = query.data.split("_", 1)[1]
        pending = pending_feedback.get(request_id)
        if pending:
            mittente = escape_markdown(pending['sender_username'], version=2)
            destinatario = escape_markdown(pending['target_username'], version=2)
            mex = escape_markdown(pending['feedback_text'], version=2)
            caption = (f"_🆕 Feedback ricevuto\\!_\n\n*Da\\:* @{mittente} \\[`{pending['user_id']}`\\]\n"
                       f"*Per\\:* @{destinatario} \\[`{pending['target_user_id']}`\\]\n*Messaggio\\:* {mex}")
            testo = caption + "\n\n*🤌 Feedback rifiutato\\.*"
            await query.edit_message_caption(caption=testo, parse_mode=ParseMode.MARKDOWN_V2)
            del pending_feedback[request_id]
        else:
            await query.edit_message_text("Feedback non più valido o già processato.")


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
