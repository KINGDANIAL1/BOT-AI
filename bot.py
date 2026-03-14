#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import io
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# -------------------- استيراد OpenAI Client (لـ DeepSeek) --------------------
from openai import AsyncOpenAI
from openai import APIError, AuthenticationError, RateLimitError

# -------------------- محاولة استيراد مكتبات تحليل الملفات --------------------
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import openpyxl
    XLSX_AVAILABLE = True
except ImportError:
    XLSX_AVAILABLE = False

# -------------------- الإعدادات الأساسية --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")  # نستخدم المفتاح المقدم
ENABLE_FILE_ANALYSIS = os.environ.get("ENABLE_FILE_ANALYSIS", "true").lower() == "true"

# التحقق من المتغيرات الإلزامية
if not BOT_TOKEN:
    raise ValueError("يجب تعيين BOT_TOKEN")
if not ADMIN_ID:
    raise ValueError("يجب تعيين ADMIN_ID")

# إعداد DeepSeek API (متوافق مع OpenAI client)
if DEEPSEEK_API_KEY:
    deepseek_client = AsyncOpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1"
    )
    AI_AVAILABLE = True
    AI_MODEL = "deepseek-chat"  # يمكن تغييره إلى "deepseek-coder" أو أي نموذج آخر
else:
    deepseek_client = None
    AI_AVAILABLE = False
    logger.warning("DEEPSEEK_API_KEY غير موجود، تعطيل الذكاء الاصطناعي")

DB_PATH = "bot_database.db"

# -------------------- دوال قاعدة البيانات (محدثة) --------------------
async def init_db():
    """إنشاء الجداول المطلوبة مع إحصائيات إضافية."""
    async with aiosqlite.connect(DB_PATH) as db:
        # جدول المستخدمين
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ai_messages_count INTEGER DEFAULT 0
            )
        """)
        # جدول حالات المستخدمين
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_states (
                chat_id INTEGER PRIMARY KEY,
                mode TEXT DEFAULT 'ai',
                FOREIGN KEY (chat_id) REFERENCES users(chat_id)
            )
        """)
        # جدول سجل المحادثات
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # جدول إحصائيات البوت
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_stats (
                key TEXT PRIMARY KEY,
                value INTEGER
            )
        """)
        # إدراج القيم الافتراضية للإحصائيات إذا لم تكن موجودة
        stats = [
            ('total_ai_requests', 0),
            ('total_admin_forwards', 0),
            ('total_broadcasts', 0)
        ]
        for key, val in stats:
            await db.execute(
                "INSERT OR IGNORE INTO bot_stats (key, value) VALUES (?, ?)",
                (key, val)
            )
        await db.commit()

async def add_user(chat_id: int, username: str, first_name: str):
    """إضافة أو تحديث مستخدم."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (chat_id, username, first_name, registered_at, last_active)
            VALUES (?, ?, ?, COALESCE((SELECT registered_at FROM users WHERE chat_id=?), CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
        """, (chat_id, username, first_name, chat_id))
        # تعيين الحالة الافتراضية إذا لم تكن موجودة
        default_mode = 'ai' if AI_AVAILABLE else 'admin'
        await db.execute(
            "INSERT OR IGNORE INTO user_states (chat_id, mode) VALUES (?, ?)",
            (chat_id, default_mode),
        )
        await db.commit()

async def update_user_activity(chat_id: int):
    """تحديث آخر نشاط للمستخدم."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (chat_id,)
        )
        await db.commit()

async def increment_user_ai_count(chat_id: int):
    """زيادة عدد رسائل الذكاء الاصطناعي للمستخدم."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET ai_messages_count = ai_messages_count + 1 WHERE chat_id = ?",
            (chat_id,)
        )
        await db.commit()

async def increment_stat(key: str):
    """زيادة إحصائية معينة."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bot_stats SET value = value + 1 WHERE key = ?",
            (key,)
        )
        await db.commit()

async def get_stat(key: str) -> int:
    """استرجاع قيمة إحصائية."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM bot_stats WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else 0

async def get_user_mode(chat_id: int) -> str:
    """استرجاع وضع المستخدم."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT mode FROM user_states WHERE chat_id = ?", (chat_id,))
        row = await cursor.fetchone()
        if row:
            return row[0]
        default = 'ai' if AI_AVAILABLE else 'admin'
        await db.execute("INSERT INTO user_states (chat_id, mode) VALUES (?, ?)", (chat_id, default))
        await db.commit()
        return default

async def set_user_mode(chat_id: int, mode: str):
    """تغيير وضع المستخدم."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_states SET mode = ? WHERE chat_id = ?", (mode, chat_id))
        await db.commit()

async def save_chat_message(chat_id: int, role: str, content: str):
    """حفظ رسالة في سجل المحادثة."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )
        # حذف القديم (الاحتفاظ بآخر 20)
        await db.execute("""
            DELETE FROM chat_history
            WHERE id IN (
                SELECT id FROM chat_history
                WHERE chat_id = ?
                ORDER BY timestamp DESC
                LIMIT -1 OFFSET 20
            )
        """, (chat_id,))
        await db.commit()

async def get_chat_history(chat_id: int, limit: int = 10) -> list:
    """استرجاع آخر محادثات المستخدم."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT role, content FROM chat_history
            WHERE chat_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
        """, (chat_id, limit))
        rows = await cursor.fetchall()
        return [{"role": row[0], "content": row[1]} for row in rows]

async def clear_chat_history(chat_id: int):
    """مسح سجل المحادثة."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def get_all_users() -> list:
    """إرجاع قائمة بكل معرفات المستخدمين."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT chat_id FROM users")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def get_user_count() -> int:
    """عدد المستخدمين المسجلين."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        count = await cursor.fetchone()
        return count[0]

async def get_users_by_mode(mode: str) -> int:
    """عدد المستخدمين في وضع معين."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_states WHERE mode = ?",
            (mode,)
        )
        count = await cursor.fetchone()
        return count[0]

async def get_recent_users(limit: int = 5) -> list:
    """آخر المستخدمين نشاطاً."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT chat_id, username, first_name, last_active
            FROM users
            ORDER BY last_active DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return rows

async def delete_user(chat_id: int) -> bool:
    """حذف مستخدم وجميع بياناته."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM user_states WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        await db.commit()
        return True

# -------------------- دوال تحليل الملفات (مثل السابق) --------------------
async def extract_text_from_image(file_bytes: bytes) -> str:
    if not OCR_AVAILABLE or not ENABLE_FILE_ANALYSIS:
        return ""
    try:
        image = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(image, lang='ara+eng')
        return text.strip()
    except Exception as e:
        logger.error(f"OCR فشل: {e}")
        return ""

async def extract_text_from_pdf(file_bytes: bytes) -> str:
    if not PDF_AVAILABLE or not ENABLE_FILE_ANALYSIS:
        return ""
    try:
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text.strip()
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""

async def extract_text_from_docx(file_bytes: bytes) -> str:
    if not DOCX_AVAILABLE or not ENABLE_FILE_ANALYSIS:
        return ""
    try:
        doc = Document(io.BytesIO(file_bytes))
        text = "\n".join([para.text for para in doc.paragraphs])
        return text.strip()
    except Exception as e:
        logger.error(f"DOCX extraction failed: {e}")
        return ""

async def extract_text_from_xlsx(file_bytes: bytes) -> str:
    if not XLSX_AVAILABLE or not ENABLE_FILE_ANALYSIS:
        return ""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        text = ""
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                row_text = " | ".join([str(cell) if cell is not None else "" for cell in row])
                if row_text.strip():
                    text += row_text + "\n"
        return text.strip()
    except Exception as e:
        logger.error(f"XLSX extraction failed: {e}")
        return ""

async def extract_text_from_txt(file_bytes: bytes) -> str:
    try:
        return file_bytes.decode('utf-8', errors='ignore').strip()
    except Exception as e:
        logger.error(f"TXT extraction failed: {e}")
        return ""

# -------------------- دوال الذكاء الاصطناعي (DeepSeek) --------------------
async def get_ai_response(chat_id: int, user_message: str) -> str:
    """استدعاء DeepSeek API مع مهلة زمنية وحفظ السياق."""
    if not AI_AVAILABLE or deepseek_client is None:
        return "عذراً، الذكاء الاصطناعي غير مفعل حالياً."

    # حفظ رسالة المستخدم
    await save_chat_message(chat_id, "user", user_message)

    # استرجاع آخر 10 رسائل
    history = await get_chat_history(chat_id, 10)

    try:
        response = await asyncio.wait_for(
            deepseek_client.chat.completions.create(
                model=AI_MODEL,
                messages=history,
                max_tokens=500,
                temperature=0.7,
            ),
            timeout=30.0
        )
        reply = response.choices[0].message.content.strip()
        await save_chat_message(chat_id, "assistant", reply)
        await increment_user_ai_count(chat_id)
        await increment_stat('total_ai_requests')
        return reply

    except asyncio.TimeoutError:
        logger.error(f"DeepSeek API timeout for user {chat_id}")
        return "الرد استغرق وقتاً طويلاً، حاول مرة أخرى لاحقاً."

    except AuthenticationError:
        logger.error("DeepSeek API key is invalid or expired.")
        return "خطأ في المصادقة مع الذكاء الاصطناعي. تواصل مع الإدمن."

    except RateLimitError:
        logger.error("DeepSeek API rate limit exceeded or insufficient quota.")
        return "تم تجاوز حد الاستخدام للذكاء الاصطناعي. حاول لاحقاً."

    except APIError as e:
        logger.error(f"DeepSeek API error: {e}")
        # التحقق من خطأ الرصيد غير الكافي
        if "Insufficient Balance" in str(e) or "402" in str(e):
            return "رصيد DeepSeek غير كافٍ. تواصل مع الإدمن لإعادة الشحن."
        return "حدث خطأ في الذكاء الاصطناعي. حاول لاحقاً."

    except Exception as e:
        logger.error(f"Unexpected DeepSeek error: {e}", exc_info=True)
        return "حدث خطأ غير متوقع. حاول لاحقاً."

# -------------------- أوامر البوت --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = user.id
    username = user.username
    first_name = user.first_name

    await add_user(chat_id, username, first_name)
    await clear_chat_history(chat_id)

    welcome_text = (
        f"مرحباً {first_name}!\n"
        "أنا بوت ذكي يعمل بـ **DeepSeek AI**.\n"
    )
    if AI_AVAILABLE:
        welcome_text += "يمكنك التحدث معي مباشرة، وإذا أردت التحدث مع إدمن بشري أرسل 'بشري' أو استخدم /admin.\n"
    else:
        welcome_text += "سيتم تحويل رسائلك إلى الإدمن البشري.\n"
    welcome_text += "للعودة للذكاء الاصطناعي أرسل 'ذكاء اصطناعي' أو استخدم /ai."

    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "الأوامر المتاحة:\n"
        "/start - بدء المحادثة\n"
        "/help - هذه المساعدة\n"
        "/mode - معرفة وضعك الحالي\n"
        "/admin - التحويل إلى إدمن بشري\n"
        "/ai - العودة إلى الذكاء الاصطناعي\n\n"
        "للإدمن فقط:\n"
        "/panel - لوحة المعلومات المتطورة\n"
        "/broadcast - إذاعة رسالة للمستخدمين\n"
        "/testai - اختبار الاتصال بالذكاء الاصطناعي\n"
        "/deleteuser <chat_id> - حذف مستخدم"
    )
    await update.message.reply_text(help_text)

async def show_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    mode = await get_user_mode(chat_id)
    mode_text = "ذكاء اصطناعي (DeepSeek)" if mode == 'ai' else "إدمن بشري"
    await update.message.reply_text(f"وضعك الحالي: {mode_text}")

async def switch_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    await set_user_mode(chat_id, 'admin')
    await update.message.reply_text("تم التحويل إلى الإدمن البشري. سيتم إرسال رسائلك للإدمن.")

async def switch_to_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AI_AVAILABLE:
        await update.message.reply_text("الذكاء الاصطناعي غير مفعل حالياً.")
        return
    chat_id = update.effective_user.id
    await set_user_mode(chat_id, 'ai')
    await clear_chat_history(chat_id)
    await update.message.reply_text("تم العودة إلى الذكاء الاصطناعي. يمكنك التحدث معي الآن.")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة معلومات متطورة للإدمن."""
    if update.effective_user.id != ADMIN_ID:
        return

    # إحصائيات أساسية
    total_users = await get_user_count()
    ai_mode_users = await get_users_by_mode('ai')
    admin_mode_users = await get_users_by_mode('admin')
    total_ai_requests = await get_stat('total_ai_requests')
    total_forwards = await get_stat('total_admin_forwards')
    total_broadcasts = await get_stat('total_broadcasts')

    # آخر المستخدمين نشاطاً
    recent = await get_recent_users(5)
    recent_text = "\n".join([
        f"• {r[2] or 'لا اسم'} (@{r[1] or 'لايوزر'}) - آخر نشاط: {r[3]}"
        for r in recent
    ]) or "لا يوجد مستخدمين بعد."

    # حالة الذكاء الاصطناعي
    ai_status = "✅ مفعل (DeepSeek)" if AI_AVAILABLE else "❌ غير مفعل"
    ai_model = AI_MODEL if AI_AVAILABLE else "-"

    panel_text = (
        f"📊 **لوحة المعلومات المتطورة**\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👥 **المستخدمين:**\n"
        f"• الإجمالي: {total_users}\n"
        f"• وضع AI: {ai_mode_users}\n"
        f"• وضع Admin: {admin_mode_users}\n\n"
        f"🤖 **الذكاء الاصطناعي:**\n"
        f"• الحالة: {ai_status}\n"
        f"• النموذج: {ai_model}\n"
        f"• إجمالي الطلبات: {total_ai_requests}\n\n"
        f"📨 **الإحصائيات العامة:**\n"
        f"• تحويلات للإدمن: {total_forwards}\n"
        f"• إذاعات جماعية: {total_broadcasts}\n\n"
        f"⏱ **آخر 5 مستخدمين نشطين:**\n"
        f"{recent_text}\n\n"
        f"🔧 **أوامر الإدارة:**\n"
        f"/broadcast - إذاعة\n"
        f"/deleteuser <id> - حذف مستخدم\n"
        f"/testai - اختبار الذكاء الاصطناعي"
    )

    await update.message.reply_text(panel_text, parse_mode='Markdown')

async def test_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختبار اتصال DeepSeek API."""
    if update.effective_user.id != ADMIN_ID:
        return
    if not AI_AVAILABLE or deepseek_client is None:
        await update.message.reply_text("الذكاء الاصطناعي غير مفعل.")
        return
    try:
        await update.message.reply_text("جاري اختبار الاتصال بـ DeepSeek...")
        response = await asyncio.wait_for(
            deepseek_client.chat.completions.create(
                model=AI_MODEL,
                messages=[{"role": "user", "content": "قل: البوت يعمل بشكل جيد مع DeepSeek"}],
                max_tokens=15
            ),
            timeout=15
        )
        reply = response.choices[0].message.content.strip()
        await update.message.reply_text(f"✅ DeepSeek يعمل:\n{reply}")
    except Exception as e:
        error_message = str(e)
        if "Insufficient Balance" in error_message:
            await update.message.reply_text("❌ رصيد DeepSeek غير كافٍ. يرجى شحن الحساب.")
        else:
            await update.message.reply_text(f"❌ خطأ: {error_message}")

async def delete_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف مستخدم بواسطة الإدمن."""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("الرجاء إرسال معرف المستخدم. مثال: /deleteuser 123456789")
        return
    try:
        chat_id = int(context.args[0])
        await delete_user(chat_id)
        await update.message.reply_text(f"✅ تم حذف المستخدم {chat_id} وجميع بياناته.")
    except ValueError:
        await update.message.reply_text("معرف غير صالح. يجب أن يكون رقماً.")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")

# -------------------- معالج الرسائل حسب الوضع --------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = user.id

    await add_user(chat_id, user.username, user.first_name)
    await update_user_activity(chat_id)

    message = update.message
    text = message.text or message.caption or ""

    # التحويل السريع
    if text.strip() in ["بشري", "ادمن", "إدمن", "/admin"]:
        await switch_to_admin(update, context)
        return
    elif text.strip() in ["ذكاء اصطناعي", "ai", "/ai"] and AI_AVAILABLE:
        await switch_to_ai(update, context)
        return

    mode = await get_user_mode(chat_id)

    if mode == 'ai' and AI_AVAILABLE:
        await update.message.reply_chat_action("typing")
        extracted = ""
        if message.photo and ENABLE_FILE_ANALYSIS:
            photo_file = await message.photo[-1].get_file()
            file_bytes = await photo_file.download_as_bytearray()
            extracted = await extract_text_from_image(bytes(file_bytes))
        elif message.document and ENABLE_FILE_ANALYSIS:
            doc_file = await message.document.get_file()
            file_bytes = await doc_file.download_as_bytearray()
            file_bytes = bytes(file_bytes)
            mime = message.document.mime_type or ""
            name = message.document.file_name or ""
            if mime == "application/pdf" or name.endswith('.pdf'):
                extracted = await extract_text_from_pdf(file_bytes)
            elif "word" in mime or name.endswith(('.docx', '.doc')):
                extracted = await extract_text_from_docx(file_bytes)
            elif "excel" in mime or "spreadsheet" in mime or name.endswith(('.xlsx', '.xls')):
                extracted = await extract_text_from_xlsx(file_bytes)
            elif mime.startswith("text/") or name.endswith('.txt'):
                extracted = await extract_text_from_txt(file_bytes)

        user_message = text
        if extracted:
            user_message = f"المستخدم قال: {text}\nمحتوى الملف المرفق:\n{extracted}"

        reply = await get_ai_response(chat_id, user_message)
        await update.message.reply_text(reply)

    else:
        # وضع الإدمن البشري
        await forward_to_admin(update, context)
        await increment_stat('total_admin_forwards')

# -------------------- تحويل الرسائل للإدمن --------------------
async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = user.id
    message = update.message

    caption_parts = [f"رسالة من @{user.username or 'لا يوجد يوزر'} (ID: {chat_id}):"]
    extracted_text = ""

    if message.photo:
        photo_file = await message.photo[-1].get_file()
        file_bytes = await photo_file.download_as_bytearray()
        if ENABLE_FILE_ANALYSIS and OCR_AVAILABLE:
            extracted_text = await extract_text_from_image(bytes(file_bytes))
        caption_parts.append("[صورة]")
    elif message.document:
        doc = message.document
        caption_parts.append(f"[مستند: {doc.file_name}]")
        doc_file = await doc.get_file()
        file_bytes = await doc_file.download_as_bytearray()
        file_bytes = bytes(file_bytes)
        if ENABLE_FILE_ANALYSIS:
            mime = doc.mime_type or ""
            name = doc.file_name or ""
            if mime == "application/pdf" or name.endswith('.pdf'):
                extracted_text = await extract_text_from_pdf(file_bytes)
            elif "word" in mime or name.endswith(('.docx', '.doc')):
                extracted_text = await extract_text_from_docx(file_bytes)
            elif "excel" in mime or "spreadsheet" in mime or name.endswith(('.xlsx', '.xls')):
                extracted_text = await extract_text_from_xlsx(file_bytes)
            elif mime.startswith("text/") or name.endswith('.txt'):
                extracted_text = await extract_text_from_txt(file_bytes)
    elif message.video:
        caption_parts.append("[فيديو]")
    elif message.audio:
        caption_parts.append("[صوت]")
    elif message.voice:
        caption_parts.append("[رسالة صوتية]")
    else:
        caption_parts.append(message.text or "")

    if extracted_text:
        if len(extracted_text) > 4000:
            extracted_text = extracted_text[:4000] + "\n... (اقتطاع)"
        caption_parts.append(f"\n📄 محتوى الملف المستخلص:\n{extracted_text}")

    caption = "\n".join(caption_parts)

    try:
        if message.photo:
            sent = await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=message.photo[-1].file_id,
                caption=caption[:1024]
            )
        elif message.video:
            sent = await context.bot.send_video(
                chat_id=ADMIN_ID,
                video=message.video.file_id,
                caption=caption[:1024]
            )
        elif message.document:
            sent = await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=message.document.file_id,
                caption=caption[:1024]
            )
        elif message.audio:
            sent = await context.bot.send_audio(
                chat_id=ADMIN_ID,
                audio=message.audio.file_id,
                caption=caption[:1024]
            )
        elif message.voice:
            sent = await context.bot.send_voice(
                chat_id=ADMIN_ID,
                voice=message.voice.file_id,
                caption=caption[:200]
            )
        else:
            sent = await context.bot.send_message(chat_id=ADMIN_ID, text=caption)

        context.bot_data.setdefault("forwarded_messages", {})[sent.message_id] = chat_id
        await update.message.reply_text("✅ تم إرسال رسالتك إلى الإدمن، سيرد عليك قريباً.")
    except Exception as e:
        logger.error(f"فشل إعادة التوجيه: {e}")
        await update.message.reply_text("عذراً، حدث خطأ في الإرسال.")

# -------------------- رد الإدمن على المستخدم --------------------
async def admin_reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    replied = update.message.reply_to_message
    if not replied:
        await update.message.reply_text("الرجاء الرد على رسالة مُعاد توجيهها.")
        return
    msg_id = replied.message_id
    user_id = context.bot_data.get("forwarded_messages", {}).get(msg_id)
    if not user_id:
        await update.message.reply_text("لا يمكن العثور على المستخدم. قد تكون الرسالة قديمة.")
        return
    reply_text = update.message.text
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📨 رد من الإدمن:\n{reply_text}"
        )
        await update.message.reply_text("✅ تم إرسال الرد.")
    except Exception as e:
        logger.error(f"فشل إرسال الرد: {e}")
        await update.message.reply_text("❌ فشل الإرسال.")

# -------------------- الإذاعة (مع إحصائيات) --------------------
BROADCAST_MSG, BROADCAST_CONFIRM = range(2)

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("📢 أرسل الرسالة التي تريد إذاعتها.")
    return BROADCAST_MSG

async def broadcast_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["broadcast_msg"] = update.message
    await update.message.reply_text("هل أنت متأكد؟ (أرسل 'نعم' للتأكيد)")
    return BROADCAST_CONFIRM

async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    text = update.message.text
    if text.lower() not in ["نعم", "yes", "y"]:
        await update.message.reply_text("تم الإلغاء.")
        return ConversationHandler.END
    msg = context.user_data.get("broadcast_msg")
    if not msg:
        await update.message.reply_text("حدث خطأ. أعد المحاولة.")
        return ConversationHandler.END

    users = await get_all_users()
    total = len(users)
    success = 0
    fail = 0

    await update.message.reply_text(f"🚀 بدء الإذاعة لـ {total} مستخدم...")

    for chat_id in users:
        try:
            if msg.text:
                await context.bot.send_message(chat_id=chat_id, text=msg.text)
            elif msg.photo:
                await context.bot.send_photo(chat_id=chat_id, photo=msg.photo[-1].file_id, caption=msg.caption)
            elif msg.video:
                await context.bot.send_video(chat_id=chat_id, video=msg.video.file_id, caption=msg.caption)
            elif msg.document:
                await context.bot.send_document(chat_id=chat_id, document=msg.document.file_id, caption=msg.caption)
            elif msg.audio:
                await context.bot.send_audio(chat_id=chat_id, audio=msg.audio.file_id, caption=msg.caption)
            elif msg.voice:
                await context.bot.send_voice(chat_id=chat_id, voice=msg.voice.file_id, caption=msg.caption)
            else:
                continue
            success += 1
        except Exception as e:
            logger.error(f"فشل الإذاعة للمستخدم {chat_id}: {e}")
            fail += 1
        await asyncio.sleep(0.05)

    await increment_stat('total_broadcasts')
    await update.message.reply_text(f"✅ انتهت الإذاعة. نجح: {success}، فشل: {fail}")
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END

# -------------------- معالج الأخطاء --------------------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="حدث خطأ أثناء معالجة التحديث:", exc_info=context.error)

# -------------------- التشغيل --------------------
def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())

    app = Application.builder().token(BOT_TOKEN).build()

    # الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mode", show_mode))
    app.add_handler(CommandHandler("admin", switch_to_admin))
    app.add_handler(CommandHandler("ai", switch_to_ai))
    app.add_handler(CommandHandler("panel", admin_panel))
    app.add_handler(CommandHandler("testai", test_ai))
    app.add_handler(CommandHandler("deleteuser", delete_user_command))

    # الإذاعة
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_receive_message)],
            BROADCAST_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_confirm)],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    )
    app.add_handler(broadcast_conv)

    # الرسائل
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.REPLY & filters.User(user_id=ADMIN_ID), admin_reply_to_user))

    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
