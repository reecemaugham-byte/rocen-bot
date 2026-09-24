import json
from datetime import datetime
from openai import OpenAI
from config import config
from core.database import db


class AIEngine:
    def __init__(self):
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)

    def chat(self, user_id: int, user_message: str) -> str:
        """Main chat function. Saves message, generates response, saves response."""
        # 1. Analyze emotional state of the user's message
        emotion = self.analyze_emotion(user_id, user_message)
        db.save_message(user_id, "user", user_message, emotional_state=emotion["state"])
        db.save_emotional_state(user_id, emotion["state"], emotion["confidence"], user_message[:200])

        # 2. If they had unanswered outreach, mark it as answered
        db.mark_outreach_answered(user_id)
        db.reset_unanswered_count(user_id)

        # 3. Build context
        history = db.get_recent_messages(user_id, limit=config.MAX_HISTORY_MESSAGES)
        profile = db.get_profile(user_id)
        last_emotion = db.get_latest_emotional_state(user_id)

        # 4. Build system prompt
        system_prompt = self._build_system_prompt(user_id, profile, last_emotion, history)

        # 5. Build messages for OpenAI
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            role = msg["role"]
            content = msg["content"]
            if len(content) > config.MAX_CONTEXT_CHARS:
                content = content[:config.MAX_CONTEXT_CHARS] + "...[trimmed]"
            messages.append({"role": role, "content": content})

        # 6. Call OpenAI
        response = self.client.chat.completions.create(
            model=config.MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
        )
        reply = response.choices[0].message.content.strip()

        # 7. Save Rocen's response
        db.save_message(user_id, "assistant", reply)

        # 8. Update profile periodically (learn about the user)
        self._maybe_update_profile(user_id, history)

        return reply

    def analyze_emotion(self, user_id: int, message: str) -> dict:
        """Analyze the emotional state of a message."""
        try:
            recent = db.get_recent_messages(user_id, limit=3)
            context = " | ".join([m["content"][:100] for m in recent])
            prompt = config.EMOTION_PROMPT.format(message=message[:500], context=context[:300])

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=20,
            )
            result = response.choices[0].message.content.strip().lower()

            # Parse "emotion|confidence" format
            valid_emotions = ["distressed", "frustrated", "sad", "neutral", "positive", "excited"]
            parts = result.split("|")
            emotion = parts[0].strip() if parts else "neutral"
            if emotion not in valid_emotions:
                emotion = "neutral"

            confidence = 0.5
            if len(parts) > 1:
                try:
                    confidence = float(parts[1].strip())
                    confidence = max(0.0, min(1.0, confidence))
                except ValueError:
                    confidence = 0.5

            return {"state": emotion, "confidence": confidence}

        except Exception:
            return {"state": "neutral", "confidence": 0.3}

    def generate_outreach(self, user_id: int) -> str | None:
        """Generate a proactive outreach message."""
        profile = db.get_profile(user_id)
        last_emotion = db.get_latest_emotional_state(user_id)
        last_msg = db.get_last_user_message(user_id)
        summary = db.get_conversation_summary(user_id, max_messages=4)

        if not last_msg and not summary:
            return None  # No conversation history, can't reach out meaningfully

        hours_since = 0
        last_contact = db.get_last_contact_time(user_id)
        if last_contact:
            delta = datetime.now() - last_contact
            hours_since = delta.total_seconds() / 3600

        prompt = config.PROACTIVE_PROMPT.format(
            bot_name=config.BOT_NAME,
            last_conversation=summary[:500],
            last_emotion=last_emotion.get("state", "neutral"),
            hours_since=int(hours_since),
            comm_style=profile.get("communication_style", "casual"),
            user_name=profile.get("name", ""),
        )

        try:
            response = self.client.chat.completions.create(
                model=config.MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=150,
            )
            message = response.choices[0].message.content.strip()
            # Clean up any quotes wrapping
            message = message.strip('"').strip("'")
            # Safety check — if the message is too long or looks weird, don't send it
            if len(message) > 300 or len(message) < 5:
                return None
            return message
        except Exception:
            return None

    def _build_system_prompt(self, user_id: int, profile: dict,
                              last_emotion: dict, history: list) -> str:
        now = datetime.now()
        hours_since = 0
        last_contact = db.get_last_contact_time(user_id)
        if last_contact:
            hours_since = (now - last_contact).total_seconds() / 3600

        conversation_summary = db.get_conversation_summary(user_id, max_messages=4)

        # Build profile text
        profile_text = f"Communication style: {profile.get('communication_style', 'casual')}"
        if profile.get("name"):
            profile_text += f"\nName: {profile['name']}"
        if profile.get("interests"):
            profile_text += f"\nInterests: {profile['interests']}"
        if profile.get("personality_notes"):
            profile_text += f"\nPersonality notes: {profile['personality_notes']}"

        return config.SYSTEM_PROMPT.format(
            bot_name=config.BOT_NAME,
            date=now.strftime("%Y-%m-%d %A"),
            time=now.strftime("%H:%M"),
            last_emotion=last_emotion.get("state", "neutral"),
            hours_since=int(hours_since),
            profile=profile_text,
            conversation_summary=conversation_summary[:600],
        )

    def _maybe_update_profile(self, user_id: int, history: list):
        """Periodically update the user profile based on conversation patterns."""
        # Only update every ~10 messages to avoid burning API calls
        msg_count = len(history)
        if msg_count % 10 != 0 or msg_count < 5:
            return

        try:
            recent_text = "\n".join([f"{m['role']}: {m['content'][:200]}" for m in history[-10:]])
            prompt = (
                "Based on these recent messages, update what you know about this person. "
                "Return ONLY a JSON object with these keys: "
                '"name" (how they like to be called), '
                '"communication_style" (brief, detailed, casual, formal, etc), '
                '"interests" (what they care about), '
                '"personality_notes" (brief observations). '
                "Only include keys you can determine from the messages. "
                "If nothing new, return {}.\n\n"
                f"Messages:\n{recent_text}"
            )

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
            result = response.choices[0].message.content.strip()

            # Extract JSON
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                result = result.split("```")[1].split("```")[0]

            updates = json.loads(result)
            if updates:
                db.update_profile(user_id, **updates)

        except Exception:
            pass  # Profile update failure should never break chat


# Singleton
ai = AIEngine()
