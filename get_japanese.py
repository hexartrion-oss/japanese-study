import os
import re
import sys
import glob
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

try:
    from playwright.sync_api import sync_playwright
    from playwright_stealth import stealth_sync
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 환경변수 ───────────────────────────────────────────
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PW  = os.environ.get("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    GMAIL_ADDRESS  = GMAIL_ADDRESS  or os.getenv("GMAIL_ADDRESS")
    GMAIL_APP_PW   = GMAIL_APP_PW   or os.getenv("GMAIL_APP_PASSWORD")
    GEMINI_API_KEY = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
except ImportError:
    pass

OUTPUT_PDF = os.path.join(os.path.dirname(__file__), "JPN.pdf")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ja,en;q=0.9",
}

NHK_RSS_LIST = [
    "https://www3.nhk.or.jp/rss/news/cat0.xml",
    "https://www3.nhk.or.jp/rss/news/cat1.xml",
    "https://www3.nhk.or.jp/rss/news/cat2.xml",
    "https://www3.nhk.or.jp/rss/news/cat3.xml",
    "https://www3.nhk.or.jp/rss/news/cat4.xml",
    "https://www3.nhk.or.jp/rss/news/cat5.xml",
    "https://www3.nhk.or.jp/rss/news/cat6.xml",
]

LEVEL_DESC = {
    "JLPT N4": {
        "desc": "JLPT N4（基礎）",
        "vocab": "小学校レベルの簡単な語彙のみ使う。経済・政治・専門用語は絶対に使わない。",
        "topic": "日常生活・買い物・学校・天気・家族・食べ物・趣味など身近な話題に変換して書く。",
        "grammar": "〜ます／〜です、〜てから、〜ので、〜たい、〜ている など基本文型のみ。",
        "example": "例：「今日は天気がいいので、友達と公園に行きました。」",
    },
    "JLPT N3": {
        "desc": "JLPT N3（初中級）",
        "vocab": "日常的な語彙。難しい専門用語（株価・金融・政策等）は使わず、身近な言葉に置き換える。",
        "topic": "日常生活・仕事・旅行・社会の身近な出来事。元記事が専門的な場合は日常的な話題に変換。",
        "grammar": "〜ながら、〜ばかり、〜ために、〜によって、〜ようになる など初中級文型。",
        "example": "例：「最近、健康のために毎朝ジョギングをする人が増えています。」",
    },
    "JLPT N2": {
        "desc": "JLPT N2（中級）",
        "vocab": "新聞・雑誌レベルの語彙。社会・経済の一般的な話題。",
        "topic": "社会問題・環境・経済の一般的な話題。元記事のテーマを活用してよい。",
        "grammar": "〜に加えて、〜ざるを得ない、〜に伴い、〜をめぐって など中級文型。",
        "example": "例：「少子化が進む中、政府はさまざまな対策を講じています。」",
    },
    "JLPT N1": {
        "desc": "JLPT N1（上級）",
        "vocab": "評論・社説レベル。専門用語も可。",
        "topic": "政治・経済・社会問題・文化。元記事のテーマをそのまま活用。",
        "grammar": "〜にほかならない、〜をもって、〜いかんによって など上級文型。",
        "example": "例：「経済格差の拡大は、社会の分断を招きかねないという懸念が高まっている。」",
    },
    "JLPT N0": {
        "desc": "JLPT N1超（専門・学術）",
        "vocab": "専門的・学術的語彙。高度な表現。",
        "topic": "学術・専門・政策・哲学的内容。",
        "grammar": "複雑な複文・倒置・専門的文体。",
        "example": "例：「量子コンピュータの実用化は、現行の暗号化技術に根本的な見直しを迫るものと予見されている。」",
    },
}
# JPT → JLPT 매핑
LEVEL_DESC["JPT 300"] = {**LEVEL_DESC["JLPT N4"], "desc": "JPT 300点（JLPT N4相当）"}
LEVEL_DESC["JPT 400"] = {**LEVEL_DESC["JLPT N4"], "desc": "JPT 400点（JLPT N4上位相当）"}
LEVEL_DESC["JPT 500"] = {**LEVEL_DESC["JLPT N3"], "desc": "JPT 500点（JLPT N3相当）"}
LEVEL_DESC["JPT 600"] = {**LEVEL_DESC["JLPT N2"], "desc": "JPT 600点（JLPT N2相当）"}
LEVEL_DESC["JPT 700"] = {**LEVEL_DESC["JLPT N2"], "desc": "JPT 700点（JLPT N2上位相当）"}
LEVEL_DESC["JPT 800"] = {**LEVEL_DESC["JLPT N1"], "desc": "JPT 800点（JLPT N1相当）"}
LEVEL_DESC["JPT 900"] = {**LEVEL_DESC["JLPT N0"], "desc": "JPT 900点（JLPT N1超相当）"}

# ── 레벨 정의 ─────────────────────────────────────────
JPT_PLAN  = ["JPT 300", "JPT 400", "JPT 500", "JPT 600", "JPT 700", "JPT 800", "JPT 900"]
JLPT_PLAN = ["JLPT N4", "JLPT N3", "JLPT N2", "JLPT N1", "JLPT N0"]


# ── 폰트 탐색 ─────────────────────────────────────────
def find_font() -> str:
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
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/truetype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/**/*ipag*.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/**/*CJK*Regular*.ttc",
        "/usr/share/fonts/**/*CJK*Regular*.otf",
    ]:
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return sorted(hits)[0]
    raise FileNotFoundError("Japanese font not found.")


# ── 유틸 ──────────────────────────────────────────────
def is_japanese(text: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ン一-鿿]", text))


def sanitize_text(text: str) -> str:
    text = "".join(c for c in text if ord(c) <= 0xFFFF)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def get_week_of_month(dt: datetime.date) -> int:
    return (dt.day + dt.replace(day=1).weekday() - 1) // 7 + 1


# ── 크롤링: NHK Web Easy (Playwright) ─────────────────
def crawl_easy_article() -> tuple:
    """NHK Web Easy 기사 원문 가져오기."""
    if not PLAYWRIGHT_AVAILABLE:
        return None, None, ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(
                locale="ja-JP",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            ).new_page()
            stealth_sync(page)

            resp = page.goto(
                "https://www3.nhk.or.jp/news/easy/news-list.json",
                wait_until="networkidle", timeout=20000
            )
            if not resp or resp.status != 200:
                browser.close()
                return None, None, ""

            data = resp.json()
            date_key = sorted(data.keys())[-1]
            articles = list(data[date_key].values())
            random.shuffle(articles)

            for art in articles[:5]:
                news_id = art.get("news_id", "")
                title   = art.get("title", "")
                if not news_id:
                    continue
                url = f"https://www3.nhk.or.jp/news/easy/{news_id}/{news_id}.html"
                page.goto(url, wait_until="networkidle", timeout=20000)
                page.evaluate("document.querySelectorAll('rt,rp').forEach(e=>e.remove())")
                el = page.query_selector("article") or page.query_selector(".article-body")
                if not el:
                    continue
                text = el.inner_text().strip()
                if len(text) > 200 and is_japanese(text):
                    browser.close()
                    return title, url, text

            browser.close()
    except Exception as e:
        print(f"Easy crawl failed: {e}")
    return None, None, ""


# ── 크롤링: NHK 일반 뉴스 (fallback) ─────────────────
def crawl_news_article() -> tuple:
    """NHK 뉴스 RSS → 기사 본문 가져오기."""
    try:
        rss_url = random.choice(NHK_RSS_LIST)
        r = requests.get(rss_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "xml")
        items = soup.find_all("item")
        random.shuffle(items)
        for item in items:
            title_tag = item.find("title")
            link_tag  = item.find("link")
            if not title_tag or not link_tag:
                continue
            title = title_tag.text.strip()
            url   = link_tag.text.strip()
            r2 = requests.get(url, headers=HEADERS, timeout=15)
            r2.raise_for_status()
            soup2 = BeautifulSoup(r2.text, "html.parser")
            for tag in soup2.find_all(["script","style","nav","header","footer","aside"]):
                tag.decompose()
            paras = [p.get_text().strip() for p in soup2.find_all("p")
                     if len(p.get_text().strip()) > 20 and is_japanese(p.get_text())]
            if len(paras) >= 3:
                return title, url, "\n".join(paras)
    except Exception as e:
        print(f"News crawl failed: {e}")
    return None, None, ""


# ── 주제 키워드 추출 ──────────────────────────────────
def extract_topic_hint(title: str, raw_text: str) -> str:
    """원문에서 주제 힌트(제목 + 첫 2문장)만 추출. Gemini에 원문 전달 금지."""
    sentences = []
    for para in sanitize_text(raw_text).split("\n"):
        parts = [s.strip() for s in re.split(r"(?<=[。！？])", para) if s.strip()]
        sentences.extend(parts)
    sentences = [s for s in sentences if is_japanese(s) and len(s) > 10]
    first_two = "　".join(sentences[:2]) if sentences else ""
    return f"{title}。{first_two}"


# ── Gemini: 주제 기반 창작 ────────────────────────────
def generate_with_gemini(topic_hint: str, label: str) -> list:
    """주제 힌트를 바탕으로 Gemini가 지정 레벨 문장을 새로 창작."""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        print("Gemini not available.")
        return []

    lv = LEVEL_DESC.get(label, LEVEL_DESC["JLPT N3"])
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = f"""あなたはJLPT・JPT専門の日本語教師です。

【今日のニュース話題ヒント】
{topic_hint}

【あなたのタスク】
上記の話題からヒントを得て、{lv['desc']}レベルの学習者向けの短い読み物を
完全にゼロから新しく創作してください。
元のニュース記事をそのまま使わず、テーマだけ参考にして書くこと。

【レベル要件 — 絶対厳守】
・語彙制限: {lv['vocab']}
・話題設定: {lv['topic']}
・使用文法: {lv['grammar']}
・レベル確認例: {lv['example']}

【出力ルール — 絶対厳守】
・10文ちょうど出力すること
・1行に1文のみ（改行で区切る）
・各文は必ず「。」または「！」または「？」で終わること
・文が途中で切れることは絶対に禁止
・番号・記号・ふりがな・説明・コメントは一切書かないこと
・{lv['desc']}のレベルに合わない難しい単語・文法が1つでもあれば書き直すこと"""

    try:
        response = model.generate_content(prompt)
        raw_lines = [l.strip() for l in response.text.strip().split("\n") if l.strip()]
        sentences = []
        for line in raw_lines:
            line = re.sub(r"^[\d\.\-・\*\①-⑩\s]+", "", line).strip()
            if is_japanese(line) and line and line[-1] in "。！？" and len(line) >= 10:
                sentences.append(line)
        print(f"Gemini created {len(sentences)} sentences for [{label}].")
        return sentences[:10]
    except Exception as e:
        print(f"Gemini generation failed: {e}")
        return []


# ── 메인 크롤링+창작 ──────────────────────────────────
def fetch_study_lines(label: str) -> tuple:
    """크롤링으로 주제 힌트 수집 → Gemini가 해당 레벨로 새로 창작."""
    # 1순위: NHK Web Easy
    title, url, raw_text = crawl_easy_article()
    source = "NHK Web Easy"

    # 2순위: NHK 일반 뉴스
    if not raw_text:
        title, url, raw_text = crawl_news_article()
        source = "NHK News"

    if not raw_text:
        print("All crawling failed — Gemini will create freely.")
        title, url = "今日のニュース", None
        raw_text = ""

    print(f"Source: {source} / {title}")

    # 주제 키워드만 추출 (원문 전체는 Gemini에 전달 안 함)
    topic_hint = extract_topic_hint(title or "", raw_text)
    print(f"Topic hint: {topic_hint[:80]}")

    # Gemini가 해당 레벨로 새로 창작
    sentences = generate_with_gemini(topic_hint, label)

    # Gemini 실패 시 원문 문장 그대로 fallback
    if not sentences and raw_text:
        raw_sentences = []
        for para in sanitize_text(raw_text).split("\n"):
            parts = [s.strip() for s in re.split(r"(?<=[。！？])", para) if s.strip()]
            raw_sentences.extend(parts)
        sentences = [s for s in raw_sentences if is_japanese(s) and len(s) > 10][:10]

    return title, url, sentences


# ── PDF 생성 ───────────────────────────────────────────
def build_pdf(label: str, title: str, url: str,
              lines: list, date_str: str, week_label: str, mode: str):
    font = find_font()
    print(f"Font: {font}")

    pdf = FPDF()
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_font("JP", fname=font)
    pdf.add_page()

    pdf.set_font("JP", size=18)
    pdf.cell(0, 12, "日本語学習 例文集",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("JP", size=11)
    pdf.cell(0, 8, f"{date_str}  |  {mode}  {week_label}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(3)
    pdf.set_draw_color(160, 160, 160)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(8)

    pdf.set_fill_color(218, 232, 255)
    pdf.set_font("JP", size=13)
    pdf.cell(0, 9, f"[ {label} ]",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
    pdf.ln(4)

    pdf.set_font("JP", size=9)
    pdf.set_text_color(120, 120, 120)
    safe_title = title if title else "NHK"
    pdf.cell(0, 6, f"出典: {safe_title}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if url:
        pdf.cell(0, 5, url[:80], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    pdf.set_font("JP", size=11)
    if lines:
        for line in lines:
            pdf.multi_cell(0, 8, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.set_text_color(200, 0, 0)
        pdf.multi_cell(0, 8, "記事を取得できませんでした。",
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.output(OUTPUT_PDF)
    print(f"PDF saved: {OUTPUT_PDF}  ({len(lines)} lines)")


# ── 이메일 전송 ────────────────────────────────────────
def send_email(date_str: str, label: str, mode: str):
    if not GMAIL_ADDRESS or not GMAIL_APP_PW:
        print("Email credentials not set — skipping.")
        return
    if "입력" in str(GMAIL_APP_PW) or len(str(GMAIL_APP_PW)) < 10:
        print("App password placeholder detected — skipping email.")
        return
    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = GMAIL_ADDRESS
        msg["Subject"] = f"[Japanese Study] {date_str} — {label}"
        msg.attach(MIMEText(
            f"Today's Japanese study material.\nLevel: {label}\nMode: {mode}",
            "plain", "utf-8"
        ))
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


# ── 메인 ──────────────────────────────────────────────
def main():
    today    = datetime.date.today()
    date_str = today.strftime("%Y-%m-%d (%a)")
    week_num = get_week_of_month(today)

    if week_num % 2 == 1:
        plan       = JPT_PLAN[:]
        mode       = "JPT"
        week_label = f"Week {week_num} (JPT)"
    else:
        plan       = JLPT_PLAN[:]
        mode       = "JLPT"
        week_label = f"Week {week_num} (JLPT)"

    label = random.choice(plan)
    print(f"Today: {label}  |  {week_label}")

    title, url, lines = fetch_study_lines(label)
    print(f"Lines extracted: {len(lines)}")

    build_pdf(label, title, url, lines, date_str, week_label, mode)
    send_email(date_str, label, mode)
    print("Done!")


if __name__ == "__main__":
    main()
