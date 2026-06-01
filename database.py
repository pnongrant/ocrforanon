import json
import os
from datetime import datetime

DB_FILE = "users.json"


def load_db():
    """Загрузка базы данных"""
    if not os.path.exists(DB_FILE):
        db = {
            "allowed_users": {},
            "banned_users": []
        }
        save_db(db)
        return db
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db):
    """Сохранение базы данных"""
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def is_allowed(user_id):
    """Проверка доступа пользователя"""
    db = load_db()
    return str(user_id) in db["allowed_users"]


def is_banned(user_id):
    """Проверка бана"""
    db = load_db()
    return user_id in db["banned_users"]


def add_user(user_id, username="", added_by=None):
    """Добавление пользователя"""
    db = load_db()
    db["allowed_users"][str(user_id)] = {
        "username": username,
        "added_by": added_by,
        "added_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "active": True
    }
    save_db(db)


def remove_user(user_id):
    """Удаление пользователя"""
    db = load_db()
    if str(user_id) in db["allowed_users"]:
        del db["allowed_users"][str(user_id)]
        save_db(db)
        return True
    return False


def ban_user(user_id):
    """Бан пользователя"""
    db = load_db()
    if user_id not in db["banned_users"]:
        db["banned_users"].append(user_id)
    # Удаляем из разрешённых если был
    if str(user_id) in db["allowed_users"]:
        del db["allowed_users"][str(user_id)]
    save_db(db)


def unban_user(user_id):
    """Разбан пользователя"""
    db = load_db()
    if user_id in db["banned_users"]:
        db["banned_users"].remove(user_id)
        save_db(db)
        return True
    return False


def get_all_users():
    """Получить всех пользователей"""
    db = load_db()
    return db["allowed_users"]


def get_banned_users():
    """Получить забаненных"""
    db = load_db()
    return db["banned_users"]


def get_stats():
    """Статистика"""
    db = load_db()
    return {
        "total_allowed": len(db["allowed_users"]),
        "total_banned": len(db["banned_users"])
    }
