
import os
import logging
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables from .env file (for local testing)
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Bot token and OpenAI API key from environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
ADMIN_ID = int(os.getenv('ADMIN_ID')) if os.getenv('ADMIN_ID') else None

if not BOT_TOKEN or not OPENAI_API_KEY or not ADMIN_ID:
    logger.error("Missing environment variables: BOT_TOKEN, OPENAI_API_KEY, or ADMIN_ID")
    # In a real scenario, you might want to exit here, but for this example, we'll continue
    # and let the bot fail gracefully if these are missing.

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Initialize OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# --- Database Setup ---

DB_PATH = 'bot_database.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def add_user(user_id, username, full_name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)', (user_id, username, full_name))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def get_user_count():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    count = cursor.fetchone()[0]
    conn.close()
    return count

# Initialize the database
init_db()

# --- States for admin broadcast ---

class AdminStates(StatesGroup):
    broadcast_message = State()

# Admin check decorator
def is_admin(func):
    async def wrapper(message: types.Message, *args, **kwargs):
        if message.from_user.id == ADMIN_ID:
            return await func(message, *args, **kwargs)
        else:
            await message.reply("⚠️ ليس لديك صلاحية للوصول إلى هذا الأمر.")
    return wrapper

# --- User Commands ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    welcome_text = (
        f"مرحباً بك يا {message.from_user.full_name}! 👋\n\n"
        "أنا بوت تلغرام متطور مدعوم بالذكاء الاصطناعي. 🤖\n\n"
        "يمكنك سؤالي أي شيء باستخدام الأمر /ask أو التحدث معي مباشرة وسأقوم بتحويل رسالتك للأدمن."
    )
    await message.reply(welcome_text)

@dp.message_handler(commands=['help'])
async def cmd_help(message: types.Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    help_text = (
        "📖 **قائمة المساعدة:**\n\n"
        "/start - بدء البوت وتفعيله\n"
        "/help - عرض هذه الرسالة\n"
        "/ask <سؤالك> - طرح سؤال على الذكاء الاصطناعي\n\n"
        "💬 **التواصل مع الإدارة:**\n"
        "ببساطة أرسل أي رسالة نصية، صورة، أو ملف وسأقوم بتحويله للأدمن مباشرة."
    )
    await message.reply(help_text, parse_mode='Markdown')

@dp.message_handler(commands=['ask'])
async def cmd_ask(message: types.Message):
    add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    query = message.get_args()
    if not query:
        await message.reply("❓ الرجاء كتابة سؤالك بعد الأمر /ask.\nمثال: `/ask ما هي عاصمة فرنسا؟`", parse_mode='Markdown')
        return

    processing_msg = await message.reply("⏳ جاري التفكير...")
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "أنت مساعد ذكي ومفيد باللغة العربية."},
                {"role": "user", "content": query}
            ]
        )
        ai_response = response.choices[0].message.content
        await bot.edit_message_text(ai_response, chat_id=message.chat.id, message_id=processing_msg.message_id)
    except Exception as e:
        logger.error(f"OpenAI Error: {e}")
        await bot.edit_message_text("❌ عذراً، حدث خطأ أثناء الاتصال بالذكاء الاصطناعي. حاول مرة أخرى لاحقاً.", chat_id=message.chat.id, message_id=processing_msg.message_id)

# --- Admin Commands ---

@dp.message_handler(commands=['admin'])
@is_admin
async def cmd_admin(message: types.Message):
    admin_menu = (
        "🛠 **لوحة تحكم الأدمن:**\n\n"
        "📢 /broadcast - إرسال رسالة لجميع المستخدمين\n"
        "📊 /users - عرض إحصائيات المستخدمين\n"
        "❓ /help_admin - تعليمات إضافية"
    )
    await message.reply(admin_menu, parse_mode='Markdown')

@dp.message_handler(commands=['users'])
@is_admin
async def cmd_users(message: types.Message):
    count = get_user_count()
    await message.reply(f"📊 **إحصائيات البوت:**\n\nعدد المستخدمين الإجمالي: `{count}`", parse_mode='Markdown')

@dp.message_handler(commands=['broadcast'])
@is_admin
async def cmd_broadcast_start(message: types.Message):
    await message.reply("📢 **إرسال إذاعة:**\n\nمن فضلك أرسل الرسالة التي تريد بثها لجميع المستخدمين الآن. (أو أرسل /cancel للإلغاء)")
    await AdminStates.broadcast_message.set()

@dp.message_handler(commands=['cancel'], state=AdminStates.broadcast_message)
@is_admin
async def cmd_cancel_broadcast(message: types.Message, state: FSMContext):
    await state.finish()
    await message.reply("✅ تم إلغاء عملية الإذاعة.")

@dp.message_handler(state=AdminStates.broadcast_message, content_types=types.ContentType.ANY)
@is_admin
async def cmd_broadcast_send(message: types.Message, state: FSMContext):
    users = get_all_users()
    await message.reply(f"⏳ جاري بدء الإرسال إلى {len(users)} مستخدم...")
    
    success = 0
    failed = 0
    
    for user_id in users:
        try:
            await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
            success += 1
            await asyncio.sleep(0.05) # Avoid hitting Telegram rate limits
        except Exception as e:
            logger.warning(f"Failed to send to {user_id}: {e}")
            failed += 1
            
    await message.reply(f"✅ **اكتملت الإذاعة:**\n\nتم الإرسال بنجاح: `{success}`\nفشل الإرسال: `{failed}`", parse_mode='Markdown')
    await state.finish()

# --- Message Forwarding & Admin Reply ---

@dp.message_handler(content_types=types.ContentType.ANY)
async def handle_all_messages(message: types.Message):
    # Add user to DB
    add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    
    if message.from_user.id == ADMIN_ID:
        # Admin logic: Reply to forwarded messages
        if message.reply_to_message and message.reply_to_message.forward_from:
            target_user_id = message.reply_to_message.forward_from.id
            try:
                await bot.copy_message(chat_id=target_user_id, from_chat_id=message.chat.id, message_id=message.message_id)
                await message.reply(f"✅ تم إرسال ردك إلى المستخدم (ID: {target_user_id})")
            except Exception as e:
                logger.error(f"Reply failed: {e}")
                await message.reply("❌ فشل إرسال الرد. قد يكون المستخدم قد قام بحظر البوت.")
        else:
            # Admin sending a general message, ignore or handle as needed
            pass
    else:
        # User logic: Forward to admin
        try:
            # Forward the message itself
            forwarded = await message.forward(ADMIN_ID)
            # Send a notification to admin with user info
            user_info = f"📩 **رسالة جديدة من:**\nالاسم: {message.from_user.full_name}\nالمعرف: @{message.from_user.username if message.from_user.username else 'لا يوجد'}\nID: `{message.from_user.id}`\n\n*للرد على المستخدم، قم بالرد (Reply) على هذه الرسالة مباشرة.*"
            await bot.send_message(ADMIN_ID, user_info, parse_mode='Markdown', reply_to_message_id=forwarded.message_id)
            
            await message.reply("✅ تم استلام رسالتك وتحويلها للإدارة. سيتم الرد عليك في أقرب وقت ممكن.")
        except Exception as e:
            logger.error(f"Forwarding failed: {e}")
            await message.reply("❌ عذراً، حدث خطأ أثناء محاولة إرسال رسالتك للإدارة. حاول مرة أخرى لاحقاً.")

# --- Start the Bot ---

if __name__ == '__main__':
    logger.info("Bot is starting...")
    executor.start_polling(dp, skip_updates=True)
