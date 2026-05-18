import os
import re
import sys
import glob
import time
import random
import smtplib
import datetime
import platform
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from fpdf import FPDF
from fpdf.enums import XPos, YPos
import requests
from bs4 import BeautifulSoup

# Windows 터미널 인코딩 문제 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 환경변수 ──────────────────────────────────────────
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PW  = os.environ.get("GMAIL_APP_PASSWORD")
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    GMAIL_ADDRESS = GMAIL_ADDRESS or os.getenv("GMAIL_ADDRESS")
    GMAIL_APP_PW  = GMAIL_APP_PW  or os.getenv("GMAIL_APP_PASSWORD")
except ImportError:
    pass

OUTPUT_PDF = os.path.join(os.path.dirname(__file__), "JPN.pdf")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StudyBot/1.0)"}

# ── 레벨 정의 ─────────────────────────────────────────
JPT_PLAN = [
    "JPT 300",
    "JPT 400",
    "JPT 500",
    "JPT 600",
    "JPT 700",
    "JPT 800",
    "JPT 900",
]
JLPT_PLAN = ["JLPT N4", "JLPT N3", "JLPT N2", "JLPT N1", "JLPT N0"]


# ── 폰트 탐색 ─────────────────────────────────────────
def find_font() -> str:
    if platform.system() == "Windows":
        for f in [r"C:\Windows\Fonts\msgothic.ttc",
                  r"C:\Windows\Fonts\meiryo.ttc",
                  r"C:\Windows\Fonts\YuGothR.ttc"]:
            if os.path.exists(f):
                return f
    for pattern in [
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/noto-cjk/*Regular*.ttc",
        "/usr/share/fonts/**/*CJK*Regular*.otf",
        "/usr/share/fonts/**/*CJK*Regular*.ttc",
    ]:
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return sorted(hits)[0]
    raise FileNotFoundError("Japanese font not found.")


# ── 유틸 ──────────────────────────────────────────────
def is_japanese(text: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ン一-鿿]", text))


def get_week_of_month(dt: datetime.date) -> int:
    return (dt.day + dt.replace(day=1).weekday() - 1) // 7 + 1


# ── 크롤링: Wikipedia 일본어 API ──────────────────────
def get_wikipedia_article() -> tuple:
    """
    Wikipedia 일본어판에서 랜덤 기사 1개 가져오기.
    반환: (제목, URL, 본문 텍스트)
    """
    api = "https://ja.wikipedia.org/w/api.php"

    # 랜덤 기사 5개 후보
    r = requests.get(api, params={
        "action": "query", "list": "random",
        "rnnamespace": 0, "rnlimit": 5, "format": "json"
    }, headers=HEADERS, timeout=15)
    r.raise_for_status()
    candidates = r.json()["query"]["random"]

    for candidate in candidates:
        title = candidate["title"]
        # 본문 평문 취득
        r2 = requests.get(api, params={
            "action": "query", "titles": title,
            "prop": "extracts", "explaintext": True,
            "exsectionformat": "plain", "format": "json"
        }, headers=HEADERS, timeout=15)
        r2.raise_for_status()
        pages = r2.json()["query"]["pages"]
        page  = next(iter(pages.values()))
        text  = page.get("extract", "").strip()

        if text and len(text) > 300 and is_japanese(text):
            url = f"https://ja.wikipedia.org/wiki/{title}"
            return title, url, text

    return None, None, ""


def sanitize_text(text: str) -> str:
    """MS Gothic 등 기본 폰트가 지원하지 않는 문자 제거."""
    # BMP 초과 문자(이모지·희귀 한자 등) 제거
    text = "".join(c for c in text if ord(c) <= 0xFFFF)
    # 줄 바꿈 정규화
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def extract_lines(raw_text: str, lo: int = 10, hi: int = 15) -> list:
    """본문을 문장 단위로 분리 → lo~hi 줄 연속 발췌."""
    text = sanitize_text(raw_text)
    # 섹션 헤더(== ... ==) 제거
    text = re.sub(r"=+[^=]+=+", "", text)
    # 문장 분리
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])", text) if s.strip()]
    sentences = [s for s in sentences if is_japanese(s) and len(s) > 5]

    if not sentences:
        return []

    target = random.randint(lo, hi)
    if len(sentences) <= target:
        return sentences

    start = random.randint(0, len(sentences) - target)
    return sentences[start:start + target]


# ── PDF 생성 ───────────────────────────────────────────
def build_pdf(label: str, title: str, url: str,
              lines: list, date_str: str, week_num: int, mode: str):
    font = find_font()
    print(f"Font: {font}")

    pdf = FPDF()
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_font("JP", fname=font)
    pdf.add_page()

    # ─ ヘッダー
    pdf.set_font("JP", size=18)
    pdf.cell(0, 12, "日本語学習 例文集",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("JP", size=11)
    pdf.cell(0, 8, f"{date_str}  |  {mode}  {week_num}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(3)
    pdf.set_draw_color(160, 160, 160)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(8)

    # ─ レベルヘッダー
    pdf.set_fill_color(218, 232, 255)
    pdf.set_font("JP", size=13)
    pdf.cell(0, 9, f"[ {label} ]",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
    pdf.ln(4)

    # ─ 出典
    pdf.set_font("JP", size=9)
    pdf.set_text_color(120, 120, 120)
    safe_title = title if title else "Wikipedia"
    pdf.cell(0, 6, f"出典: {safe_title}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if url:
        pdf.cell(0, 5, url[:80],
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    # ─ 本文（番号なし・連続）
    pdf.set_font("JP", size=11)
    if lines:
        for line in lines:
            pdf.multi_cell(0, 8, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.set_text_color(200, 0, 0)
        pdf.multi_cell(0, 8, "記事を取得できませんでした。", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.output(OUTPUT_PDF)
    print(f"PDF saved: {OUTPUT_PDF}  ({len(lines)} lines)")


# ── メール送信 ─────────────────────────────────────────
def send_email(date_str: str, label: str, mode: str):
    if not GMAIL_ADDRESS or not GMAIL_APP_PW:
        print("Email credentials not set — skipping.")
        return
    # 자격증명이 placeholder인지 확인
    if "입력" in str(GMAIL_APP_PW) or len(str(GMAIL_APP_PW)) < 10:
        print("App password placeholder detected — skipping email.")
        return

    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = GMAIL_ADDRESS
        msg["Subject"] = f"[Japanese Study] {date_str} — {label}"

        body = f"Today's Japanese study material.\nLevel: {label}\nMode: {mode}"
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with open(OUTPUT_PDF, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition",
                            f'attachment; filename="JPN_{date_str[:10]}.pdf"')
            msg.attach(part)

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
            server.send_message(msg)
        print(f"Email sent → {GMAIL_ADDRESS}")

    except Exception as e:
        print(f"Email failed: {e}")


# ── メイン ─────────────────────────────────────────────
def main():
    today    = datetime.date.today()
    date_str = today.strftime("%Y-%m-%d (%a)")
    week_num = get_week_of_month(today)

    if week_num % 2 == 1:
        plan = JPT_PLAN[:]
        mode = "JPT"
        week_label = f"Week {week_num} (JPT)"
    else:
        plan = JLPT_PLAN[:]
        mode = "JLPT"
        week_label = f"Week {week_num} (JLPT)"

    label = random.choice(plan)
    print(f"Today: {label}  |  {week_label}")

    print("Fetching article from Wikipedia...")
    title, url, raw_text = get_wikipedia_article()
    lines = extract_lines(raw_text, 10, 15)
    print(f"Article: {title}")
    print(f"Lines extracted: {len(lines)}")

    build_pdf(label, title, url, lines, date_str, week_label, mode)
    send_email(date_str, label, mode)
    print("Done!")


if __name__ == "__main__":
    main()
