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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ja,en;q=0.9",
}

NHK_RSS_LIST = [
    "https://www3.nhk.or.jp/rss/news/cat0.xml",  # 全ニュース
    "https://www3.nhk.or.jp/rss/news/cat1.xml",  # 社会
    "https://www3.nhk.or.jp/rss/news/cat2.xml",  # 経済
    "https://www3.nhk.or.jp/rss/news/cat3.xml",  # 政治
    "https://www3.nhk.or.jp/rss/news/cat4.xml",  # 国際
    "https://www3.nhk.or.jp/rss/news/cat5.xml",  # 科学・文化
    "https://www3.nhk.or.jp/rss/news/cat6.xml",  # スポーツ
]

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
    # 환경변수로 폰트 경로를 직접 지정한 경우
    env_font = os.environ.get("JAPANESE_FONT_PATH")
    if env_font and os.path.exists(env_font):
        return env_font

    if platform.system() == "Windows":
        for f in [r"C:\Windows\Fonts\msgothic.ttc",
                  r"C:\Windows\Fonts\meiryo.ttc",
                  r"C:\Windows\Fonts\YuGothR.ttc"]:
            if os.path.exists(f):
                return f

    for pattern in [
        # GitHub Actions: apt fonts-noto-cjk 설치 경로
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/*Regular*.ttc",
        "/usr/share/fonts/**/*CJK*Regular*.otf",
        "/usr/share/fonts/**/*CJK*Regular*.ttc",
        "/usr/share/fonts/**/*Noto*JP*.otf",
        "/usr/share/fonts/**/*Noto*JP*.ttf",
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


# ── 크롤링: NHK News ──────────────────────────────────
def get_news_article() -> tuple:
    """NHK뉴스 RSS에서 랜덤 기사 1개 가져오기."""
    rss_url = random.choice(NHK_RSS_LIST)

    try:
        r = requests.get(rss_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception:
        # cat별 RSS 실패 시 전체 RSS 시도
        r = requests.get(NHK_RSS_LIST[0], headers=HEADERS, timeout=15)
        r.raise_for_status()

    soup = BeautifulSoup(r.text, "xml")
    items = soup.find_all("item")
    if not items:
        return None, None, ""

    random.shuffle(items)

    for item in items:
        title_tag = item.find("title")
        link_tag  = item.find("link")
        if not title_tag or not link_tag:
            continue

        title = title_tag.text.strip()
        url   = link_tag.text.strip()

        try:
            r2 = requests.get(url, headers=HEADERS, timeout=15)
            r2.raise_for_status()
        except Exception:
            continue

        soup2 = BeautifulSoup(r2.text, "html.parser")

        for tag in soup2.find_all(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        paragraphs = []
        for p in soup2.find_all("p"):
            text = p.get_text().strip()
            if len(text) > 20 and is_japanese(text):
                paragraphs.append(text)

        if len(paragraphs) >= 3:
            return title, url, "\n".join(paragraphs)

    return None, None, ""


def get_enough_lines(lo: int = 10) -> tuple:
    """기사를 최대 3개까지 합쳐서 lo줄 이상 확보."""
    combined_lines = []
    titles = []
    url_first = None

    for _ in range(3):
        title, url, raw_text = get_news_article()
        if not raw_text:
            continue
        if url_first is None:
            url_first = url
        if title:
            titles.append(title)
        lines = extract_lines(raw_text, 3, 8)
        combined_lines.extend(lines)
        if len(combined_lines) >= lo:
            break

    combined_title = " / ".join(titles[:2]) if titles else "NHK News"
    return combined_title, url_first, combined_lines


def sanitize_text(text: str) -> str:
    """MS Gothic 등 기본 폰트가 지원하지 않는 문자 제거."""
    # BMP 초과 문자(이모지·희귀 한자 등) 제거
    text = "".join(c for c in text if ord(c) <= 0xFFFF)
    # 줄 바꿈 정규화
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def extract_lines(raw_text: str, lo: int = 10, hi: int = 15) -> list:
    """뉴스 본문을 문장 단위로 분리 → lo~hi 문장 연속 발췌."""
    text = sanitize_text(raw_text)
    # 단락 → 문장 분리
    sentences = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        parts = [s.strip() for s in re.split(r"(?<=[。！？])", para) if s.strip()]
        sentences.extend(parts)

    sentences = [s for s in sentences if is_japanese(s) and len(s) > 10]

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
    safe_title = title if title else "NHK News"
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

    print("Fetching article from NHK News...")
    title, url, lines = get_enough_lines(lo=10)
    lines = lines[:15]
    print(f"Article: {title}")
    print(f"Lines extracted: {len(lines)}")

    build_pdf(label, title, url, lines, date_str, week_label, mode)
    send_email(date_str, label, mode)
    print("Done!")


if __name__ == "__main__":
    main()
