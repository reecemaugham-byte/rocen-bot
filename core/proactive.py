from datetime import datetime
from telegram.ext import ContextTypes
from config import config
from core.database import db
from core.ai_engine import ai


def setup_proactive_jobs(application):
    """Register the proactive outreach check as a recurring job."""
    job_queue = application.job_queue
    job_queue.run_repeating(
        proactive_check,
        interval=config.PROACTIVE_CHECK_INTERVAL,
        first=60,  # Start 60 seconds after launch
        name="proactive_outreach",
    )


async def proactive_check(context: ContextTypes.DEFAULT_TYPE):
    """Check all users and reach out if appropriate."""
    users = db.get_all_proactive_users()
    now = datetime.now()

    for user_id in users:
        try:
            should_reach, reason = _should_reach_out(user_id, now)
            if should_reach:
                message = ai.generate_outreach(user_id)
                if message:
                    await _send_outreach(context, user_id, message, reason)
        except Exception as e:
            print(f"[PROACTIVE] Error for user {user_id}: {e}")


def _should_reach_out(user_id: int, now: datetime) -> tuple[bool, str]:
    """Determine if we should reach out to this user."""
    settings = db.get_settings(user_id)

    # Check if proactive is enabled
    if not settings["proactive_enabled"]:
        return False, "disabled"

    # Check quiet hours
    current_hour = now.hour
    qh_start = settings["quiet_hours_start"]
    qh_end = settings["quiet_hours_end"]

    if qh_start > qh_end:
        # Quiet hours span midnight (e.g., 22:00 to 08:00)
        if current_hour >= qh_start or current_hour < qh_end:
            return False, "quiet_hours"
    else:
        # Quiet hours within same day (e.g., 13:00 to 14:00 siesta)
        if qh_start <= current_hour < qh_end:
            return False, "quiet_hours"

    # Check when we last heard from them
    last_contact = db.get_last_contact_time(user_id)
    if not last_contact:
        return False, "no_history"  # Never talked, don't reach out cold

    hours_since = (now - last_contact).total_seconds() / 3600

    # Check last outreach time — don't spam
    last_outreach = db.get_last_outreach_time(user_id)
    if last_outreach:
        hours_since_outreach = (now - last_outreach).total_seconds() / 3600
        cooldown = config.PROACTIVE_COOLDOWN_HOURS
        # Adjust cooldown based on frequency setting
        if settings["outreach_frequency"] == "frequent":
            cooldown = max(2, cooldown // 2)
        elif settings["outreach_frequency"] == "minimal":
            cooldown = cooldown * 2
        if hours_since_outreach < cooldown:
            return False, "cooldown"

    # Check max daily outreach
    daily_count = db.get_recent_outreach_count(user_id, hours=24)
    if daily_count >= config.PROACTIVE_MAX_DAILY:
        return False, "max_daily"

    # Check unanswered count — stop if they're not responding
    unanswered = db.get_unanswered_count(user_id)
    if unanswered >= config.PROACTIVE_MAX_UNANSWERED:
        return False, "unanswered"

    # Get emotional state
    last_emotion = db.get_latest_emotional_state(user_id)
    emotion = last_emotion.get("state", "neutral")

    # Determine threshold based on emotional state
    threshold_hours = config.OUTREACH_THRESHOLDS.get(emotion, 24)

    # Adjust for frequency setting
    if settings["outreach_frequency"] == "frequent":
        threshold_hours = max(1, threshold_hours // 2)
    elif settings["outreach_frequency"] == "minimal":
        threshold_hours = threshold_hours * 2

    # Have they been silent long enough?
    if hours_since < threshold_hours:
        return False, "too_soon"

    # All checks passed — reach out!
    reason = f"silence_{int(hours_since)}h_emotion_{emotion}"
    return True, reason


async def _send_outreach(context: ContextTypes.DEFAULT_TYPE,
                          user_id: int, message: str, reason: str):
    """Send a proactive message and log it."""
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
        )
        db.log_outreach(user_id, message, reason)
        db.update_last_outreach(user_id)
        print(f"[PROACTIVE] Sent to {user_id}: {message[:80]}... (reason: {reason})")
    except Exception as e:
        print(f"[PROACTIVE] Failed to send to {user_id}: {e}")
