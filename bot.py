# -------------------- إعداد الذكاء الاصطناعي (OpenAI أو DeepSeek) --------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

AI_AVAILABLE = False
ai_client = None
AI_MODEL = None
AI_PROVIDER = None

if OPENAI_API_KEY:
    try:
        ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        AI_MODEL = "gpt-3.5-turbo"
        AI_PROVIDER = "OpenAI"
        AI_AVAILABLE = True
        logger.info("تم تفعيل OpenAI API")
    except Exception as e:
        logger.error(f"خطأ في إعداد OpenAI: {e}")
elif DEEPSEEK_API_KEY:
    try:
        ai_client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1"
        )
        AI_MODEL = "deepseek-chat"
        AI_PROVIDER = "DeepSeek"
        AI_AVAILABLE = True
        logger.info("تم تفعيل DeepSeek API")
    except Exception as e:
        logger.error(f"خطأ في إعداد DeepSeek: {e}")

if not AI_AVAILABLE:
    logger.warning("لا يوجد مفتاح API صالح للذكاء الاصطناعي. سيتم تعطيل الردود التلقائية.")
