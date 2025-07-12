import os
import logging
from typing import Set, Dict, Optional
import firebase_admin
from firebase_admin import credentials, db
from dotenv import load_dotenv
import json

load_dotenv()

GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Initialize Firebase Admin SDK if not already initialized
def initialize_firebase():
    if not firebase_admin._apps:
        cred_path = GOOGLE_APPLICATION_CREDENTIALS
        db_url = FIREBASE_DATABASE_URL
        if not cred_path or not db_url:
            logger.error("Env var mancanti: GOOGLE_APPLICATION_CREDENTIALS o FIREBASE_DATABASE_URL")
        else:
            try:
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred, {'databaseURL': db_url})
                logger.info("Firebase Admin SDK inizializzato.")
            except Exception as e:
                logger.error(f"Errore inizializzazione Firebase: {e}")

initialize_firebase()


def load_admin_ids() -> Set[int]:
    """
    Carica gli ID degli admin dal nodo 'admin_ids' di Firebase Realtime Database.
    """
    admins: Set[int] = set()
    try:
        ref = db.reference('admin_ids')
        data = ref.get() or {}
        admins = {int(uid) for uid in data.get('admin_ids', [])}
    except Exception as e:
        logger.error(f"Errore load_admin_ids da Firebase: {e}")

    if not admins:
        logger.warning("Nessun admin trovato in Firebase, la lista admin è vuota.")
    return admins


def save_admin_ids(admin_ids: Set[int]) -> None:
    """
    Salva gli ID degli admin sul nodo 'admin_ids' di Firebase Realtime Database.
    """
    try:
        ref = db.reference('admin_ids')
        ref.set({'admin_ids': list(admin_ids)})
    except Exception as e:
        logger.error(f"Errore save_admin_ids su Firebase: {e}")


def load_group_users() -> Dict[int, Dict[int, dict]]:
    """
    Carica i dati dei group users dal nodo 'group_users' di Firebase Realtime Database.
    """
    try:
        ref = db.reference('group_users')
        raw = ref.get() or {}
        result: Dict[int, Dict[int, dict]] = {}
        for chat_id_str, users_map in raw.items():
            try:
                chat_id = int(chat_id_str)
            except ValueError:
                logger.warning(f"Chat ID non valido: {chat_id_str}, skip.")
                continue

            proc: Dict[int, dict] = {}
            if isinstance(users_map, dict):
                for user_id_str, info in users_map.items():
                    try:
                        proc[int(user_id_str)] = info
                    except ValueError:
                        logger.warning(f"User ID non valido: {user_id_str}, skip.")
            result[chat_id] = proc

        return result
    except Exception as e:
        logger.error(f"Errore load_group_users da Firebase: {e}")
        return {}


def save_group_users(group_users: Dict[int, Dict[int, dict]]) -> None:
    """
    Salva i dati dei group users sul nodo 'group_users' di Firebase Realtime Database.
    """
    try:
        payload = {
            str(chat_id): {str(uid): info for uid, info in users.items()}
            for chat_id, users in group_users.items()
        }
        ref = db.reference('group_users')
        ref.set(payload)
    except Exception as e:
        logger.error(f"Errore save_group_users su Firebase: {e}")


def load_stats() -> Dict[int, dict]:
    """
    Carica le statistiche dal nodo 'stats' di Firebase Realtime Database.
    """
    try:
        ref = db.reference('stats')
        raw = ref.get() or {}
        return {int(uid): data for uid, data in raw.items()}
    except Exception as e:
        logger.error(f"Errore load_stats da Firebase: {e}")
        return {}


def save_stats(stats: Dict[int, dict]) -> None:
    """
    Salva le statistiche sul nodo 'stats' di Firebase Realtime Database.
    """
    try:
        payload = {str(uid): data for uid, data in stats.items()}
        ref = db.reference('stats')
        ref.set(payload)
    except Exception as e:
        logger.error(f"Errore save_stats su Firebase: {e}")

def load_pending_feedback() -> Dict[str, dict]:
    """
    Carica i dati dei feedback in sospeso dal nodo 'pending_feedback' di Firebase.
    """
    try:
        ref = db.reference('pending_feedback')
        return ref.get() or {}
    except Exception as e:
        logger.error(f"Errore load_pending_feedback da Firebase: {e}")
        return {}

def save_pending_feedback(pending_feedback: Dict[str, dict]) -> None:
    """
    Salva i dati dei feedback in sospeso sul nodo 'pending_feedback' di Firebase.
    """
    try:
        ref = db.reference('pending_feedback')
        ref.set(pending_feedback)
    except Exception as e:
        logger.error(f"Errore save_pending_feedback su Firebase: {e}")

def delete_pending_feedback_entry(request_id: str) -> None:
    """
    Elimina una specifica voce di feedback in sospeso da Firebase.
    """
    try:
        ref = db.reference(f'pending_feedback/{request_id}')
        ref.delete()
    except Exception as e:
        logger.error(f"Errore delete_pending_feedback_entry su Firebase: {e}")

def load_user_data(chat_id: int, user_id: int) -> Optional[Dict]:
    """
    Carica i dati di un utente specifico da Firebase.
    Restituisce i dati dell'utente se esiste, altrimenti None.
    """
    try:
        ref = db.reference(f'group_users/{chat_id}/{user_id}')
        user_data = ref.get()
        return user_data
    except Exception as e:
        logger.error(f"Errore nel caricamento dei dati per l'utente {user_id} nella chat {chat_id}: {e}")
        return None

def backup_to_json():
    """Esegue il backup dell'intero database Firebase in un file JSON."""
    try:
        ref = db.reference('/')
        data = ref.get()
        with open('firebase_backup.json', 'w') as f:
            json.dump(data, f, indent=4)
        logger.info("Backup del database completato con successo.")
    except Exception as e:
        logger.error(f"Errore durante il backup del database: {e}")

