import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from zoneinfo import ZoneInfo

try:
    from linebot.v3.webhook import WebhookSignatureValidator
except Exception:  # pragma: no cover - optional fallback for signature validation
    WebhookSignatureValidator = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

DEFAULT_TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tokyo")
DEFAULT_DURATION_MIN = int(os.environ.get("DEFAULT_DURATION_MINUTES", "60"))
WORK_START = os.environ.get("WORK_START", "09:00")
WORK_END = os.environ.get("WORK_END", "18:00")

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

MODE_PROMPTS = {
    "dental": (
        "あなたは歯科の予約管理アシスタントです。患者への案内は丁寧で簡潔に。"
        "治療や検診の予約の自然な表現を理解し、要件の抜けがあれば穏やかに確認します。"
    ),
    "logistics": (
        "あなたは物流の集荷受付アシスタントです。集荷依頼の日時や条件を丁寧に確認します。"
        "荷物の集荷や配送の表現を理解し、業務的で丁寧な文体で対応します。"
    ),
    "professional": (
        "あなたは士業の面談調整アシスタントです。相談や面談の予定調整を丁寧に案内します。"
        "日程の変更・キャンセルにも誠実で丁寧に対応します。"
    ),
}

MODE_KEYWORDS = {
    "dental": ["歯科", "歯医者", "検診", "治療", "クリーニング", "dental"],
    "logistics": ["物流", "集荷", "配送", "発送", "荷物", "引き取り", "logistics"],
    "professional": ["面談", "相談", "打ち合わせ", "士業", "税理", "法律", "社労士", "行政書士", "professional"],
}


def _get_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


def _access_secret_payload(secret_name: str) -> str:
    try:
        from google.cloud import secretmanager
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Secret Manager is requested but google-cloud-secret-manager is not installed."
        ) from exc

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_name)
    return response.payload.data.decode("utf-8")


def _load_service_account() -> service_account.Credentials:
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    secret_name = os.environ.get("GOOGLE_SERVICE_ACCOUNT_SECRET")
    scopes = [CALENDAR_SCOPE, SHEETS_SCOPE]

    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=scopes)
    if secret_name:
        payload = _access_secret_payload(secret_name)
        info = json.loads(payload)
        return service_account.Credentials.from_service_account_info(info, scopes=scopes)
    raise ValueError("Missing GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_SECRET")


def _get_services():
    creds = _load_service_account()
    calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return calendar, sheets


def _detect_mode(text: str) -> str:
    for mode, keywords in MODE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return mode
    return "dental"


def _parse_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Gemini response did not contain JSON")
    return json.loads(match.group(0))


def _parse_rfc3339(value: Optional[str], timezone: str) -> Optional[dt.datetime]:
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def _to_rfc3339(value: dt.datetime) -> str:
    return value.isoformat()


def _extract_intent(text: str, mode: str, timezone: str) -> Dict[str, Any]:
    api_key = _get_env("GEMINI_API_KEY")
    genai.configure(api_key=api_key)
    system_prompt = MODE_PROMPTS.get(mode, MODE_PROMPTS["dental"])
    model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

    instruction = (
        "次のユーザー文から予約の意図を抽出し、JSONのみを返してください。"
        "出力形式は以下のキーを持つJSONです。\n"
        'intent: "new" | "change" | "cancel"\n'
        "start_iso: RFC3339 (新規/変更の開始日時)\n"
        "end_iso: RFC3339 (新規/変更の終了日時)\n"
        "target_start_iso: RFC3339 (変更/キャンセル対象の開始日時、分かる場合)\n"
        "summary: 短い用件\n"
        "notes: 補足\n"
        "timezone: IANAタイムゾーン\n"
        "confidence: 0-1\n"
        "不明な項目は null を設定。"
    )

    model = genai.GenerativeModel(model_name, system_instruction=system_prompt)
    response = model.generate_content(f"{instruction}\nユーザー文: {text}")
    payload = _parse_json(response.text)

    intent = payload.get("intent") or "new"
    start_iso = payload.get("start_iso")
    end_iso = payload.get("end_iso")
    target_start_iso = payload.get("target_start_iso")
    summary = payload.get("summary") or "予約"
    notes = payload.get("notes") or ""
    tz = payload.get("timezone") or timezone

    start_dt = _parse_rfc3339(start_iso, tz)
    end_dt = _parse_rfc3339(end_iso, tz)
    if start_dt and not end_dt:
        end_dt = start_dt + dt.timedelta(minutes=DEFAULT_DURATION_MIN)
    if start_dt and end_dt and end_dt <= start_dt:
        end_dt = start_dt + dt.timedelta(minutes=DEFAULT_DURATION_MIN)

    target_dt = _parse_rfc3339(target_start_iso, tz)

    return {
        "intent": intent,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "target_dt": target_dt,
        "summary": summary,
        "notes": notes,
        "timezone": tz,
        "raw": payload,
    }


def _list_events(calendar, calendar_id: str, time_min: dt.datetime, time_max: dt.datetime) -> List[Dict[str, Any]]:
    result = calendar.events().list(
        calendarId=calendar_id,
        timeMin=_to_rfc3339(time_min),
        timeMax=_to_rfc3339(time_max),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


def _event_range(event: Dict[str, Any], tz: str) -> Tuple[dt.datetime, dt.datetime]:
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
    if "T" in start:
        start_dt = _parse_rfc3339(start, tz)
        end_dt = _parse_rfc3339(end, tz)
        return start_dt, end_dt
    date_start = dt.date.fromisoformat(start)
    date_end = dt.date.fromisoformat(end)
    tzinfo = ZoneInfo(tz)
    return (
        dt.datetime.combine(date_start, dt.time.min, tzinfo=tzinfo),
        dt.datetime.combine(date_end, dt.time.min, tzinfo=tzinfo),
    )


def _has_conflict(events: List[Dict[str, Any]], exclude_event_id: Optional[str] = None) -> bool:
    for event in events:
        if event.get("status") == "cancelled":
            continue
        if exclude_event_id and event.get("id") == exclude_event_id:
            continue
        return True
    return False


def _merge_busy_ranges(ranges: List[Tuple[dt.datetime, dt.datetime]]) -> List[Tuple[dt.datetime, dt.datetime]]:
    if not ranges:
        return []
    ranges.sort(key=lambda x: x[0])
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _compute_free_slots(
    events: List[Dict[str, Any]],
    target_date: dt.date,
    tz: str,
    duration_minutes: int,
) -> List[Tuple[dt.datetime, dt.datetime]]:
    tzinfo = ZoneInfo(tz)
    work_start_time = dt.time.fromisoformat(WORK_START)
    work_end_time = dt.time.fromisoformat(WORK_END)
    window_start = dt.datetime.combine(target_date, work_start_time, tzinfo=tzinfo)
    window_end = dt.datetime.combine(target_date, work_end_time, tzinfo=tzinfo)

    busy = []
    for event in events:
        if event.get("status") == "cancelled":
            continue
        start_dt, end_dt = _event_range(event, tz)
        if end_dt <= window_start or start_dt >= window_end:
            continue
        busy.append((max(start_dt, window_start), min(end_dt, window_end)))

    busy = _merge_busy_ranges(busy)
    free = []
    cursor = window_start
    for start, end in busy:
        if cursor < start:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < window_end:
        free.append((cursor, window_end))

    duration = dt.timedelta(minutes=duration_minutes)
    return [(s, e) for s, e in free if (e - s) >= duration]


def _format_slots(slots: List[Tuple[dt.datetime, dt.datetime]]) -> List[str]:
    formatted = []
    for start, end in slots:
        formatted.append(f"{start:%Y-%m-%d %H:%M} - {end:%H:%M}")
    return formatted


def _generate_refusal_message(mode: str, text: str, slots: List[str]) -> str:
    api_key = _get_env("GEMINI_API_KEY")
    genai.configure(api_key=api_key)
    system_prompt = MODE_PROMPTS.get(mode, MODE_PROMPTS["dental"])
    model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
    model = genai.GenerativeModel(model_name, system_instruction=system_prompt)

    slot_block = "\n".join(slots) if slots else "該当日に空き時間が見つかりませんでした。"
    instruction = (
        "以下の予約依頼は予定が重複しています。"
        "丁寧な断り文と、その日の別の空き時間の提案を含む返信文を作成してください。"
        "候補は2〜3件に絞り、日本語で簡潔に。"
    )
    response = model.generate_content(
        f"{instruction}\n依頼文: {text}\n空き時間候補:\n{slot_block}"
    )
    return response.text.strip()


def _find_target_event(
    calendar,
    calendar_id: str,
    user_id: str,
    target_dt: Optional[dt.datetime],
    tz: str,
) -> Optional[Dict[str, Any]]:
    if target_dt:
        time_min = target_dt - dt.timedelta(hours=4)
        time_max = target_dt + dt.timedelta(hours=4)
    else:
        now = dt.datetime.now(ZoneInfo(tz))
        time_min = now
        time_max = now + dt.timedelta(days=30)

    events = _list_events(calendar, calendar_id, time_min, time_max)
    for event in events:
        description = event.get("description", "")
        if user_id and f"LINE_USER_ID:{user_id}" not in description:
            continue
        if target_dt:
            start_dt, _ = _event_range(event, tz)
            if abs((start_dt - target_dt).total_seconds()) <= 3600:
                return event
        else:
            return event
    return None


def _send_line_reply(reply_token: str, text: str) -> None:
    token = _get_env("LINE_CHANNEL_ACCESS_TOKEN")
    payload = json.dumps({
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }).encode("utf-8")
    req = urllib.request.Request(
        LINE_REPLY_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        res.read()


def _validate_line_signature(body: str, signature: str) -> bool:
    if not signature:
        return False
    secret = _get_env("LINE_CHANNEL_SECRET")
    if WebhookSignatureValidator:
        validator = WebhookSignatureValidator(secret)
        return validator.validate(body, signature)
    digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, signature)


def _append_log(sheets, spreadsheet_id: str, row: List[Any]) -> None:
    sheet_name = os.environ.get("LOG_SHEET_NAME", "Logs")
    body = {"values": [row]}
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()


def _handle_message(
    text: str,
    user_id: str,
    calendar,
    sheets,
) -> Tuple[str, Dict[str, Any]]:
    mode = _detect_mode(text)
    timezone = DEFAULT_TIMEZONE
    calendar_id = _get_env("CALENDAR_ID")
    spreadsheet_id = _get_env("SPREADSHEET_ID")

    log = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "user_id": user_id,
        "mode": mode,
        "intent": None,
        "start": None,
        "end": None,
        "status": "error",
        "message": "",
        "error": "",
    }

    try:
        intent_data = _extract_intent(text, mode, timezone)
        intent = intent_data["intent"]
        start_dt = intent_data["start_dt"]
        end_dt = intent_data["end_dt"]
        target_dt = intent_data["target_dt"]
        summary = intent_data["summary"]
        notes = intent_data["notes"]
        timezone = intent_data["timezone"] or timezone

        log["intent"] = intent
        log["start"] = start_dt.isoformat() if start_dt else ""
        log["end"] = end_dt.isoformat() if end_dt else ""

        if intent in ("new", "change") and (not start_dt or not end_dt):
            raise ValueError("開始・終了日時の抽出に失敗しました。")

        if intent == "cancel":
            target_event = _find_target_event(calendar, calendar_id, user_id, target_dt, timezone)
            if not target_event:
                message = "対象の予定が見つかりませんでした。日時の指定をお願いします。"
                log["status"] = "not_found"
                log["message"] = message
                return message, log
            calendar.events().delete(calendarId=calendar_id, eventId=target_event["id"]).execute()
            message = "予約をキャンセルしました。"
            log["status"] = "cancelled"
            log["message"] = message
            return message, log

        target_event = None
        if intent == "change":
            target_event = _find_target_event(calendar, calendar_id, user_id, target_dt, timezone)
            if not target_event:
                message = "変更対象の予定が見つかりませんでした。元の日時を教えてください。"
                log["status"] = "not_found"
                log["message"] = message
                return message, log

        events = _list_events(calendar, calendar_id, start_dt, end_dt)
        exclude_id = target_event["id"] if target_event else None
        if _has_conflict(events, exclude_event_id=exclude_id):
            day_start = start_dt.astimezone(ZoneInfo(timezone)).date()
            day_events = _list_events(
                calendar,
                calendar_id,
                dt.datetime.combine(day_start, dt.time.min, tzinfo=ZoneInfo(timezone)),
                dt.datetime.combine(day_start, dt.time.max, tzinfo=ZoneInfo(timezone)),
            )
            duration = int((end_dt - start_dt).total_seconds() / 60)
            slots = _compute_free_slots(day_events, day_start, timezone, duration)
            slot_texts = _format_slots(slots[:3])
            message = _generate_refusal_message(mode, text, slot_texts)
            log["status"] = "conflict"
            log["message"] = message
            return message, log

        description = f"LINE_USER_ID:{user_id}\nMODE:{mode}\n{notes}".strip()
        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": _to_rfc3339(start_dt), "timeZone": timezone},
            "end": {"dateTime": _to_rfc3339(end_dt), "timeZone": timezone},
        }

        if intent == "change" and target_event:
            calendar.events().update(
                calendarId=calendar_id,
                eventId=target_event["id"],
                body=event_body,
            ).execute()
            message = f"予約を変更しました。{start_dt:%Y-%m-%d %H:%M}からの予定です。"
            log["status"] = "updated"
            log["message"] = message
            return message, log

        calendar.events().insert(calendarId=calendar_id, body=event_body).execute()
        message = f"予約を受け付けました。{start_dt:%Y-%m-%d %H:%M}からの予定です。"
        log["status"] = "created"
        log["message"] = message
        return message, log

    except Exception as exc:
        logger.exception("failed to handle message")
        message = "処理中にエラーが発生しました。お手数ですが内容を確認してください。"
        log["status"] = "error"
        log["error"] = str(exc)
        log["message"] = message
        return message, log

    finally:
        try:
            row = [
                log["timestamp"],
                log["user_id"],
                log["mode"],
                log["intent"] or "",
                log["start"],
                log["end"],
                log["status"],
                log["message"],
                log["error"],
            ]
            _append_log(sheets, spreadsheet_id, row)
        except Exception:
            logger.exception("failed to append log")


def main(request):
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")
    if not _validate_line_signature(body, signature or ""):
        logger.warning("invalid line signature")
        return "Invalid signature", 403

    try:
        data = request.get_json()
    except Exception:
        logger.exception("invalid request")
        return "Invalid request", 400

    if data is None:
        try:
            data = json.loads(body) if body else {}
        except Exception:
            logger.exception("invalid json payload")
            return "Invalid request", 400

    events = data.get("events", []) if isinstance(data, dict) else []
    if not events:
        return "OK", 200

    calendar, sheets = _get_services()

    for item in events:
        if item.get("type") != "message":
            continue
        message = item.get("message", {})
        if message.get("type") != "text":
            continue
        text = message.get("text", "")
        user_id = item.get("source", {}).get("userId", "")
        reply_token = item.get("replyToken")

        reply_text, _log = _handle_message(text, user_id, calendar, sheets)
        if reply_token and reply_text:
            _send_line_reply(reply_token, reply_text)

    return "OK", 200
