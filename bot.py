#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import re
from datetime import datetime
import io

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

# -------------------- محاولة استيراد مكتبات تحليل الملفات --------------------
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logging.warning("pytesseract غير مثبت، تعطيل OCR")

try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    logging.warning("PyPDF2 غير مثبت، تعطيل قراءة PDF")

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    logging.warning("python-docx غير مثبت، تعطيل قراءة Word")

try:
    import openpyxl
    XLSX_AVAILABLE = True
except ImportError:
    XLSX_AVAILABLE = False
    logging.warning("openpyxl غير مثبت، تعطيل قراءة Excel")

ENABLE_FILE_ANALYSIS = os.environ.get("ENABLE_FILE_ANALYSIS", "true").lower() == "true"

# -------------------- الإعدادات الأساسية --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")  # غير مستخدم حالياً

DB_PATH = "bot_database.db"

# -------------------- دوال قاعدة البيانات --------------------
async def init_db():
    """إنشاء جدول المستخدمين إذا لم يكن موجوداً."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def add_user(chat_id: int, username: str, first_name: str):
    """إضافة مستخدم جديد (تجاهل إذا كان موجوداً)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (chat_id, username, first_name) VALUES (?, ?, ?)",
            (chat_id, username, first_name),
        )
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

# -------------------- دوال تحليل الملفات --------------------
async def extract_text_from_image(file_bytes: bytes) -> str:
    """استخراج النص من الصورة باستخدام Tesseract."""
    if not OCR_AVAILABLE or not ENABLE_FILE_ANALYSIS:
        return ""
    try:
        image = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(image, lang='ara+eng')  # يدعم العربية والإنجليزية
        return text.strip()
    except Exception as e:
        logger.error(f"OCR فشل: {e}")
        return ""

async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """استخراج النص من PDF."""
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
    """استخراج النص من ملف Word."""
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
    """استخراج النص من ملف Excel."""
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
    """قراءة ملف نصي."""
    try:
        return file_bytes.decode('utf-8', errors='ignore').strip()
    except Exception as e:
        logger.error(f"TXT extraction failed: {e}")
        return ""

# -------------------- دوال البوت --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب وتسجيل المستخدم."""
    user = update.effective_user
    chat_id = user.id
    username = user.username
    first_name = user.first_name

    await add_user(chat_id, username, first_name)
    await update.message.reply_text(
        f"مرحباً {first_name}!\n"
        "أنا بوت خدمة العملة. يمكنك إرسال رسالتك وسيتم تحويلها إلى الإدمن مع تحليل الصور والملفات إن أمكن."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مساعدة عامة."""
    await update.message.reply_text(
        "الأوامر المتاحة:\n"
        "/start - بدء المحادثة\n"
        "/help - هذه المساعدة\n\n"
        "للمستخدمين: أرسل رسالتك وسيتم تحويلها للإدمن.\n"
        "للإدمن: استخدم /panel للإحصائيات، /broadcast للإذاعة."
    )

# -------------------- لوحة معلومات الإدمن --------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات البوت (للإدمن فقط)."""
    if update.effective_user.id != ADMIN_ID:
        return
    count = await get_user_count()
    await update.message.reply_text(
        f"📊 لوحة المعلومات:\n"
        f"إجمالي المستخدمين: {count}\n"
        f"معرف الإدمن: {ADMIN_ID}\n"
        f"حالة البوت: نشط ✅\n"
        f"تحليل الملفات: {'مفعل' if ENABLE_FILE_ANALYSIS else 'معطل'}"
    )

# -------------------- استقبال رسائل المستخدمين وتحويلها للإدمن مع تحليل الملفات --------------------
async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أي رسالة من مستخدم عادي تُعاد توجيهها للإدمن مع محتوى إضافي مستخلص من الملفات."""
    user = update.effective_user
    chat_id = user.id

    await add_user(chat_id, user.username, user.first_name)

    message = update.message
    caption_parts = [f"رسالة من @{user.username or 'لا يوجد يوزر'} (ID: {chat_id}):"]

    # تحديد نوع الوسائط واستخراج النص إن أمكن
    extracted_text = ""
    file_type = ""

    if message.photo:
        # الصورة: نقوم بتحميلها واستخراج النص عبر OCR
        photo_file = await message.photo[-1].get_file()
        file_bytes = await photo_file.download_as_bytearray()
        if ENABLE_FILE_ANALYSIS and OCR_AVAILABLE:
            extracted_text = await extract_text_from_image(bytes(file_bytes))
        caption_parts.append(f"[صورة]")
        file_type = "photo"

    elif message.document:
        doc = message.document
        mime_type = doc.mime_type or ""
        file_name = doc.file_name or ""
        caption_parts.append(f"[مستند: {file_name}]")

        # تحميل الملف
        doc_file = await doc.get_file()
        file_bytes = await doc_file.download_as_bytearray()
        file_bytes = bytes(file_bytes)

        if ENABLE_FILE_ANALYSIS:
            if mime_type == "application/pdf" or file_name.lower().endswith('.pdf'):
                extracted_text = await extract_text_from_pdf(file_bytes)
                file_type = "PDF"
            elif mime_type in ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"] or file_name.lower().endswith(('.docx', '.doc')):
                extracted_text = await extract_text_from_docx(file_bytes)
                file_type = "Word"
            elif mime_type in ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel"] or file_name.lower().endswith(('.xlsx', '.xls')):
                extracted_text = await extract_text_from_xlsx(file_bytes)
                file_type = "Excel"
            elif mime_type.startswith("text/") or file_name.lower().endswith('.txt'):
                extracted_text = await extract_text_from_txt(file_bytes)
                file_type = "نص"
            else:
                extracted_text = ""  # نوع غير مدعوم
        else:
            extracted_text = ""

    elif message.video:
        caption_parts.append("[فيديو]")
        file_type = "video"
    elif message.audio:
        caption_parts.append("[صوت]")
        file_type = "audio"
    elif message.voice:
        caption_parts.append("[رسالة صوتية]")
        file_type = "voice"
    else:  # رسالة نصية
        caption_parts.append(message.text or "")
        file_type = "text"

    # إضافة النص المستخرج إن وجد
    if extracted_text:
        # تحديد طول معقول (4000 حرف) لتجنب تجاوز حد التيليغرام
        if len(extracted_text) > 4000:
            extracted_text = extracted_text[:4000] + "\n... (اقتطاع)"
        caption_parts.append(f"\n📄 محتوى الملف المستخلص:\n{extracted_text}")

    caption = "\n".join(caption_parts)

    try:
        # إرسال الرسالة إلى الإدمن حسب نوعها
        if message.photo:
            sent = await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=message.photo[-1].file_id,
                caption=caption[:1024]  # حد الكابشن للصورة
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
                caption=caption[:200]  # حد الكابشن للصوت
            )
        else:  # نص
            sent = await context.bot.send_message(chat_id=ADMIN_ID, text=caption)

        # تخزين مرجع الرسالة
        context.bot_data.setdefault("forwarded_messages", {})[sent.message_id] = chat_id

    except Exception as e:
        logger.error(f"فشل إعادة توجيه رسالة المستخدم {chat_id}: {e}")
        await update.message.reply_text("عذراً، حدث خطأ في إرسال رسالتك. حاول لاحقاً.")
        return

    # تأكيد للمستخدم
    await update.message.reply_text("✅ تم إرسال رسالتك إلى الإدمن، سيرد عليك قريباً.")

# -------------------- رد الإدمن على مستخدم معين --------------------
async def admin_reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عندما يرد الإدمن على رسالة مُعاد توجيهها، نرسل الرد إلى المستخدم الأصلي."""
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
        await update.message.reply_text("✅ تم إرسال الرد بنجاح.")
    except Exception as e:
        logger.error(f"فشل إرسال الرد للمستخدم {user_id}: {e}")
        await update.message.reply_text("❌ فشل الإرسال. قد يكون المستخدم أوقف البوت.")

# -------------------- الإذاعة (للإدمن) --------------------
BROADCAST_MSG, BROADCAST_CONFIRM = range(2)

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية الإذاعة."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("📢 أرسل الرسالة التي تريد إذاعتها (نص، صورة، فيديو...).")
    return BROADCAST_MSG

async def broadcast_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام محتوى الإذاعة."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["broadcast_msg"] = update.message
    await update.message.reply_text("هل أنت متأكد؟ (أرسل 'نعم' للتأكيد أو 'لا' للإلغاء)")
    return BROADCAST_CONFIRM

async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تأكيد الإذاعة وإرسالها لكل المستخدمين."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    text = update.message.text
    if text.lower() not in ["نعم", "yes", "y"]:
        await update.message.reply_text("تم إلغاء الإذاعة.")
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
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=msg.photo[-1].file_id,
                    caption=msg.caption
                )
            elif msg.video:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=msg.video.file_id,
                    caption=msg.caption
                )
            elif msg.document:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=msg.document.file_id,
                    caption=msg.caption
                )
            elif msg.audio:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=msg.audio.file_id,
                    caption=msg.caption
                )
            elif msg.voice:
                await context.bot.send_voice(
                    chat_id=chat_id,
                    voice=msg.voice.file_id,
                    caption=msg.caption
                )
            else:
                continue
            success += 1
        except Exception as e:
            logger.error(f"فشل الإذاعة للمستخدم {chat_id}: {e}")
            fail += 1
        await asyncio.sleep(0.05)

    await update.message.reply_text(f"✅ انتهت الإذاعة. نجح: {success}، فشل: {fail}")
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء الإذاعة."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("تم إلغاء الإذاعة.")
    return ConversationHandler.END

# -------------------- معالج الأخطاء --------------------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="حدث خطأ أثناء معالجة التحديث:", exc_info=context.error)

# -------------------- تشغيل البوت --------------------
def main():
    # تهيئة قاعدة البيانات
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())

    # إنشاء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()

    # أوامر بسيطة
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("panel", admin_panel))

    # محادثة الإذاعة
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_receive_message)],
            BROADCAST_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_confirm)],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    )
    application.add_handler(broadcast_conv)

    # استقبال رسائل المستخدمين العاديين (في الخاص) مع تحليل الملفات
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.ALL & ~filters.COMMAND,
            handle_user_message
        )
    )

    # رد الإدمن على رسالة مُعاد توجيهها
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.REPLY & filters.User(user_id=ADMIN_ID),
            admin_reply_to_user
        )
    )

    # معالج الأخطاء
    application.add_error_handler(error_handler)

    # بدء البوت
    application.run_polling()

if __name__ == "__main__":
    main()
