# Webull MY OpenAPI 24h 1m candle recorder -> CSV -> Google Drive
# Created 2026-07-06. Sessions: PRE + RTH + ATH + OVN (full 24h coverage).
# CSV format matches SOXXPriceAction: datetime_utc,datetime_et,session,open,high,low,close,volume

import os, csv, json, io
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from webull.core.client import ApiClient
from webull.data.data_client import DataClient

TICKERS = ["SOXX"]
CATEGORY = {"SOXX": "US_ETF"}  # default US_STOCK; SOXX is an ETF
ET = ZoneInfo("America/New_York")
DATA_DIR = "data"
SESSIONS = ["PRE", "RTH", "ATH", "OVN"]
ENDPOINT = os.environ.get("WEBULL_ENDPOINT", "api.webull.com.my")

APP_KEY = os.environ["WEBULL_APP_KEY"]
APP_SECRET = os.environ["WEBULL_APP_SECRET"]

api_client = ApiClient(APP_KEY, APP_SECRET, "my")
api_client.add_endpoint("my", ENDPOINT)
dc = DataClient(api_client)


def csv_path(symbol, et_date):
    return os.path.join(DATA_DIR, str(et_date), f"{symbol.lower()}_1m_{et_date}.csv")


def last_recorded_utc(symbol):
    """Newest bar timestamp across today's and yesterday's ET-date files."""
    today_et = datetime.now(ET).date()
    last = None
    for d in [today_et - timedelta(days=1), today_et]:
        p = csv_path(symbol, d)
        if not os.path.exists(p):
            continue
        with open(p) as f:
            for row in csv.reader(f):
                if row and row[0] != "datetime_utc":
                    last = row[0]
    if last:
        return datetime.strptime(last, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return None


def append_bars(symbol, bars):
    """bars: list of dicts from API, each with time/open/high/low/close/volume/trading_session"""
    writers = {}
    files = {}
    n = 0
    try:
        for b in sorted(bars, key=lambda x: x["time"]):
            t_utc = datetime.strptime(b["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            t_et = t_utc.astimezone(ET)
            d = t_et.date()
            if d not in writers:
                p = csv_path(symbol, d)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                new = not os.path.exists(p)
                fh = open(p, "a", newline="")
                files[d] = fh
                writers[d] = csv.writer(fh)
                if new:
                    writers[d].writerow(["datetime_utc", "datetime_et", "session",
                                         "open", "high", "low", "close", "volume"])
            writers[d].writerow([t_utc.strftime("%Y-%m-%d %H:%M"), t_et.strftime("%Y-%m-%d %H:%M"),
                                 b.get("trading_session", ""), b["open"], b["high"], b["low"],
                                 b["close"], b.get("volume", "")])
            n += 1
    finally:
        for fh in files.values():
            fh.close()
    return n


def record(symbol):
    last = last_recorded_utc(symbol)
    kwargs = dict(count="1650", real_time_required="true", trading_sessions=SESSIONS)
    if last:
        kwargs["start_time"] = int((last + timedelta(minutes=1)).timestamp() * 1000)
    res = dc.market_data.get_history_bar(symbol, CATEGORY.get(symbol, "US_STOCK"), "M1", **kwargs)
    if res.status_code != 200:
        print(f"{symbol}: HTTP {res.status_code} {res.text[:150]}")
        return 0
    bars = res.json() or []
    if last:  # safety: drop anything at or before last recorded
        cutoff = last.strftime("%Y-%m-%dT%H:%M")
        bars = [b for b in bars if b["time"][:16] > cutoff]
    return append_bars(symbol, bars)


# ---------- Google Drive upload (skipped if secrets absent) ----------
def drive_upload(dates):
    sa_json = os.environ.get("GDRIVE_SA_JSON")
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "1_PviA-YUTJ1PgwfvSWY7Oui5Xfa-AwBm")
    if not sa_json:
        print("Drive secrets not set - skipping upload")
        return
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=["https://www.googleapis.com/auth/drive"])
    svc = build("drive", "v3", credentials=creds)
    for d in sorted(set(dates)):
        q = f"name='{d}' and '{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        res = svc.files().list(q=q, fields="files(id)").execute().get("files")
        day_id = res[0]["id"] if res else svc.files().create(
            body={"name": str(d), "parents": [folder_id],
                  "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()["id"]
        day_dir = os.path.join(DATA_DIR, str(d))
        for fn in sorted(os.listdir(day_dir)):
            with open(os.path.join(day_dir, fn), "rb") as fh:
                media = MediaIoBaseUpload(io.BytesIO(fh.read()), mimetype="text/csv")
            q = f"name='{fn}' and '{day_id}' in parents and trashed=false"
            ex = svc.files().list(q=q, fields="files(id)").execute().get("files")
            if ex:
                svc.files().update(fileId=ex[0]["id"], media_body=media).execute()
            else:
                svc.files().create(body={"name": fn, "parents": [day_id]}, media_body=media).execute()
            print("Drive:", fn)


if __name__ == "__main__":
    total = 0
    for s in TICKERS:
        try:
            n = record(s)
        except Exception as e:
            print(f"{s}: ERROR {str(e)[:150]}")
            n = 0
        total += n
        print(f"{s}: +{n} bars")
    if total:
        today = datetime.now(ET).date()
        drive_upload([today - timedelta(days=1), today])
    print(f"Total new bars: {total}")
