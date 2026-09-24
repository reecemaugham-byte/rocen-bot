from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from config import config
from core.database import db
from core.ai_engine import ai


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start — first contact with the user."""
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or ""

    # Ensure profile and settings exist
    db._ensure_profile(user_id)
    db._ensure_settings(user_id)
    if first_name:
        db.update_profile(user_id, name=first_name)

    welcome = (
        f"👋 Hey! I'm {config.BOT_NAME}.\n\n"
        "I'm not like other AI. I actually reach out. If we've been talking and "
        "I haven't heard from you in a while, I'll check in — not because a timer "
        "went off, but because I remember what you said.\n\n"
        "A few things to know:\n"
        "• I'm a companion, not a therapist. I care, but I know my limits.\n"
        "• I remember our conversations and follow up on things.\n"
        "• You can control how often I reach out with /settings\n"
        "• You can pause my check-ins anytime with /pause\n"
        "• Type /help for all commands\n\n"
        f"So — what's on your mind, {first_name}?"
    )

    await update.message.reply_text(welcome)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help."""
    help_text = (
        f"🤖 **{config.BOT_NAME} Commands**\n\n"
        "/start — Start or restart our conversation\n"
        "/help — Show this help message\n"
        "/pause — Pause proactive check-ins\n"
        "/resume — Resume proactive check-ins\n"
        "/settings — View your current settings\n"
        "/frequency <level> — Set outreach frequency: frequent, normal, minimal\n"
        "/quiethours <start> <end> — Set quiet hours (24h format, e.g. /quiethours 22 8)\n"
        "/profile — View what I remember about you\n"
        "/forget — Clear your profile and conversation history\n"
        "/status — See your emotional trajectory\n\n"
        "Or just talk to me. I'm here."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pause — stop proactive outreach."""
    user_id = update.effective_user.id
    db.update_settings(user_id, proactive_enabled=False)
    await update.message.reply_text(
        "Paused check-ins. I won't reach out proactively anymore. "
        "Type /resume whenever you want me back."
    )


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /resume — resume proactive outreach."""
    user_id = update.effective_user.id
    db.update_settings(user_id, proactive_enabled=True)
    db.reset_unanswered_count(user_id)
    await update.message.reply_text(
        "Resumed check-ins! I'll reach out if I haven't heard from you in a while. 💙"
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings — show current settings."""
    user_id = update.effective_user.id
    settings = db.get_settings(user_id)
    profile = db.get_profile(user_id)

    status = "✅ Active" if settings["proactive_enabled"] else "⏸ Paused"
    freq = settings["outreach_frequency"]
    qh_start = settings["quiet_hours_start"]
    qh_end = settings["quiet_hours_end"]

    last_contact = db.get_last_contact_time(user_id)
    last_outreach = db.get_last_outreach_time(user_id)

    text = (
        f"⚙️ **Your Settings**\n\n"
        f"Proactive check-ins: {status}\n"
        f"Outreach frequency: {freq}\n"
        f"Quiet hours: {qh_start}:00 — {qh_end}:00\n\n"
        f"📋 **Your Profile**\n"
        f"Name: {profile.get('name', 'Not set')}\n"
        f"Style: {profile.get('communication_style', 'casual')}\n"
        f"Interests: {profile.get('interests', 'Not set')}\n\n"
        f"📅 **Activity**\n"
        f"Last message: {last_contact.strftime('%Y-%m-%d %H:%M') if last_contact else 'N/A'}\n"
        f"Last outreach: {last_outreach.strftime('%Y-%m-%d %H:%M') if last_outreach else 'N/A'}\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def frequency_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /frequency — set outreach frequency."""
    user_id = update.effective_user.id

    if not context.args or context.args[0].lower() not in ["frequent", "normal", "minimal"]:
        await update.message.reply_text(
            "Usage: /frequency <frequent|normal|minimal>\n\n"
            "• **frequent** — I'll check in more often\n"
            "• **normal** — Default balance\n"
            "• **minimal** — I'll give you more space",
            parse_mode="Markdown",
        )
        return

    level = context.args[0].lower()
    db.update_settings(user_id, outreach_frequency=level)

    descriptions = {
        "frequent": "I'll check in more often. Hope that's what you want! 🤗",
        "normal": "Back to the default balance. I'll reach out when it makes sense. 👍",
        "minimal": "I'll give you more space. I'm still here if you need me. 🌙",
    }
    await update.message.reply_text(descriptions[level])


async def quiethours_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /quiethours — set quiet hours."""
    user_id = update.effective_user.id

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /quiethours <start> <end>\n\n"
            "Example: /quiethours 22 8\n"
            "This means I won't reach out between 10pm and 8am.",
        )
        return

    try:
        start = int(context.args[0])
        end = int(context.args[1])
        if not (0 <= start <= 23 and 0 <= end <= 23):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please use valid hours (0-23). Example: /quiethours 22 8")
        return

    db.update_settings(user_id, quiet_hours_start=start, quiet_hours_end=end)
    await update.message.reply_text(
        f"Quiet hours set: {start}:00 — {end}:00. I won't reach out during those times. 🌙"
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /profile — show what Rocen remembers."""
    user_id = update.effective_user.id
    profile = db.get_profile(user_id)
    last_emotion = db.get_latest_emotional_state(user_id)
    trajectory = db.get_emotional_trajectory(user_id, days=7)

    # Count total messages
    messages = db.get_recent_messages(user_id, limit=1000)
    msg_count = len(messages)

    # Build trajectory summary
    if trajectory:
        emotions = [t["state"] for t in trajectory]
        recent = ", ".join(emotions[-5:])
    else:
        recent = "Not enough data yet"

    text = (
        f"🧠 **What I Remember About You**\n\n"
        f"Name: {profile.get('name', 'Not set yet')}\n"
        f"Communication style: {profile.get('communication_style', 'Learning...')}\n"
        f"Interests: {profile.get('interests', 'Still learning...')}\n"
        f"Personality notes: {profile.get('personality_notes', 'Getting to know you...')}\n\n"
        f"📊 **Recent Emotional Pattern**\n"
        f"Last emotional state: {last_emotion.get('state', 'neutral')}\n"
        f"Recent trajectory: {recent}\n"
        f"Total messages: {msg_count}\n\n"
        f"I learn more every time we talk. 💙"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status — show emotional trajectory."""
    user_id = update.effective_user.id
    trajectory = db.get_emotional_trajectory(user_id, days=7)

    if not trajectory:
        await update.message.reply_text("I don't have enough conversation history yet. Talk to me more! 😊")
        return

    # Build a simple text-based chart
    emotion_emoji = {
        "distressed": "🔴",
        "frustrated": "🟠",
        "sad": "🟡",
        "neutral": "⚪",
        "positive": "🟢",
        "excited": "🌟",
    }

    lines = ["📈 **Your Week**\n"]
    for entry in trajectory:
        emoji = emotion_emoji.get(entry["state"], "⚪")
        date = entry["timestamp"][:10] if entry["timestamp"] else "?"
        lines.append(f"{emoji} {date} — {entry['state']} ({entry['confidence']:.0%})")

    # Trend analysis
    recent_states = [t["state"] for t in trajectory[-3:]]
    positive_states = {"positive", "excited"}
    negative_states = {"distressed", "frustrated", "sad"}

    recent_positive = sum(1 for s in recent_states if s in positive_states)
    recent_negative = sum(1 for s in recent_states if s in negative_states)

    if recent_positive > recent_negative:
        lines.append("\n🌱 Things seem to be trending positive lately.")
    elif recent_negative > recent_positive:
        lines.append("\n💙 I've noticed you've been having a harder time lately. I'm here.")
    else:
        lines.append("\n⚖️ Things seem fairly balanced recently.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /forget — clear user data."""
    user_id = update.effective_user.id

    # Delete all data for this user
    conn = db._connect()
    for table in ["conversations", "emotional_states", "user_profiles",
                   "outreach_log", "user_settings"]:
        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        "Done. I've cleared everything I knew about you. Fresh start. "
        "If you want to start over, just say hi."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text messages — the main conversation handler."""
    user_id = update.effective_user.id
    user_message = update.message.text

    # Ensure user exists in the system
    db._ensure_profile(user_id)
    db._ensure_settings(user_id)

    # Update name if changed
    first_name = update.effective_user.first_name
    if first_name:
        current_profile = db.get_profile(user_id)
        if not current_profile.get("name"):
            db.update_profile(user_id, name=first_name)

    # Show typing indicator
    await context.bot.send_chat_action(chat_id=user_id, action="typing")

    # Generate response
    try:
        reply = ai.chat(user_id, user_message)
        await update.message.reply_text(reply)
    except Exception as e:
        error_msg = f"Something went wrong. Let me try again. (Error: {str(e)[:50]})"
        await update.message.reply_text(error_msg)
        print(f"[CHAT ERROR] User {user_id}: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    print(f"[ERROR] {context.error}")
