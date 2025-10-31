from flask import Flask, request, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
from datetime import datetime, timedelta, time as dtime
import pytz
import requests
import json
import os

app = Flask(__name__)
tz = pytz.timezone("Asia/Jakarta")

# 🔐 Load credentials from environment
creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEFAULT_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 📄 Spreadsheet setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

sheet_id = os.getenv("SHEET_ID")
#sheet_name = os.getenv("SHEET_NAME", "New Shift 24/7 ")
sheet = client.open_by_key(sheet_id).worksheet("New Shift 24/7 ")

# 📘 Load shift type mapping
df_shift = pd.read_csv("shift_type.csv")
shift_times = {
    row["shift_type"]: (row["begin"], row["end"])
    for _, row in df_shift.iterrows()
}

def parse_time(t):
    try:
        return datetime.strptime(t, "%I:%M %p").time()
    except:
        return None

def get_schedule():
    all_rows = sheet.get_all_values()
    today = datetime.now(tz)
    current_month = today.month
    current_year = today.year

    next_month = (today.replace(day=28) + timedelta(days=4)).month
    next_year = (today.replace(day=28) + timedelta(days=4)).year

    allowed_months = [current_month, next_month]
    allowed_years = [current_year, next_year]

    all_data = []
    date_columns = []

    for i, row in enumerate(all_rows):
        for j, cell in enumerate(row):
            try:
                cell_date = datetime.strptime(cell.strip(), '%m/%d/%Y')
                if cell_date.month in allowed_months and cell_date.year in allowed_years:
                    date_columns.append((i, j, cell_date))
            except:
                continue
        if date_columns:
            break

    for date_row_index, target_col_index, target_date in date_columns:
        current_name = None
        for row in all_rows[date_row_index + 1:]:
            if len(row) <= target_col_index:
                continue

            name_cell = row[1].strip()
            shift = row[target_col_index].strip()

            if all(cell.strip() == "" for cell in row):
                break

            if name_cell:
                current_name = name_cell

            if current_name and shift:
                begin, end = shift_times.get(shift, ("-", "-"))
                all_data.append({
                    'SHIFT_DATE': target_date.strftime('%d-%m-%Y'),
                    'USER_DESCRIPTION': current_name,
                    'SHIFT': shift,
                    'START_TIME': begin,
                    'END_TIME': end
                })

    df = pd.DataFrame(all_data)
    df["START_TIME"] = df["START_TIME"].fillna("00:00:00")
    df["END_TIME"] = df["END_TIME"].fillna("00:00:00")
    df["start_time_obj"] = df["START_TIME"].apply(parse_time)
    df["end_time_obj"] = df["END_TIME"].apply(parse_time)
    df["date_obj"] = pd.to_datetime(df["SHIFT_DATE"], format="%d-%m-%Y").dt.date
    return df

def is_active_now(row):
    now = datetime.now(tz)
    if pd.notnull(row["date_obj"]) and pd.notnull(row["start_time_obj"]) and pd.notnull(row["end_time_obj"]):
        start_naive = datetime.combine(row["date_obj"], row["start_time_obj"])
        end_naive = datetime.combine(row["date_obj"], row["end_time_obj"])
        start = tz.localize(start_naive)
        end = tz.localize(end_naive)
        if end < start:
            end += timedelta(days=1)
        return start <= now <= end
    return False

def send_telegram_message(message, chat_id=DEFAULT_CHAT_ID):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"❌ Gagal kirim notifikasi: {e}")

def format_shift_message(df, label):
    if df.empty:
        return f"📅 Tidak ada jadwal shift untuk {label}."
    lines = [f"📅 Jadwal Shift ({label}):"]
    for _, row in df.iterrows():
        lines.append(f"• {row['USER_DESCRIPTION']} ({row['SHIFT']}) — {row['START_TIME']} s/d {row['END_TIME']}")
    return "\n".join(lines)

def format_active_message(df):
    active_df = df[df.apply(is_active_now, axis=1)]
    if active_df.empty:
        return "🔍 Tidak ada yang sedang aktif saat ini."
    lines = ["🟢 Yang sedang aktif sekarang:"]
    for _, row in active_df.iterrows():
        lines.append(f"• {row['USER_DESCRIPTION']} ({row['SHIFT']}) — {row['START_TIME']} s/d {row['END_TIME']}")
    return "\n".join(lines)

@app.route("/", methods=["GET"])
def home():
    return "✅ Shift Bot Flask aktif."

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    message = data.get("message", {})
    text = message.get("text", "").strip().lower()
    chat_id = message.get("chat", {}).get("id")

    df_schedule = get_schedule()
    today_str = datetime.now(tz).strftime("%d-%m-%Y")
    tomorrow_str = (datetime.now(tz) + timedelta(days=1)).strftime("%d-%m-%Y")

    if text == "/shift_today":
        df_today = df_schedule[df_schedule["SHIFT_DATE"] == today_str]
        msg = format_shift_message(df_today, today_str)
        send_telegram_message(msg, chat_id)

    elif text == "/shift_tomorrow":
        df_tomorrow = df_schedule[df_schedule["SHIFT_DATE"] == tomorrow_str]
        msg = format_shift_message(df_tomorrow, tomorrow_str)
        send_telegram_message(msg, chat_id)

    # elif text == "/active_now":
    #     msg = format_active_message(df_schedule)
    #     send_telegram_message(msg, chat_id)

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)