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

try:
    from google import genai as google_genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 환경변수 ───────────────────────────────────────────
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PW   = os.environ.get("GMAIL_APP_PASSWORD")
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

# ── 레벨 정의 ─────────────────────────────────────────
JPT_PLAN  = ["JPT 300", "JPT 400", "JPT 500", "JPT 600", "JPT 700", "JPT 800", "JPT 900"]
JLPT_PLAN = ["JLPT N4", "JLPT N3", "JLPT N2", "JLPT N1", "JLPT N0"]

LEVEL_DESC = {
    "JLPT N4": {
        "desc": "JLPT N4（基礎）",
        "vocab": "小学校3〜4年生レベルの語彙のみ。株・金利・政策・外交などの専門語は一切使わない。",
        "topic": "買い物・学校・天気・家族・食事・趣味・日常のできごと。",
        "grammar": "〜ます・〜です・〜てから・〜ので・〜たい・〜ている など基本文型のみ。",
        "example": "「今日は天気がいいので、友達と公園に行きました。」",
    },
    "JLPT N3": {
        "desc": "JLPT N3（初中級）",
        "vocab": "日常語彙。難しい専門語は使わず、身近な言葉で言い換える。",
        "topic": "日常生活・仕事・旅行・地域のニュース。",
        "grammar": "〜ながら・〜ために・〜によって・〜ようになる など初中級文型。",
        "example": "「最近、健康のために毎朝ジョギングをする人が増えています。」",
    },
    "JLPT N2": {
        "desc": "JLPT N2（中級）",
        "vocab": "新聞・雑誌レベルの語彙。社会・経済の一般的な語彙は可。",
        "topic": "社会問題・環境・経済の一般的な話題。",
        "grammar": "〜に加えて・〜ざるを得ない・〜に伴い など中級文型。",
        "example": "「少子化が進む中、政府はさまざまな対策を講じています。」",
    },
    "JLPT N1": {
        "desc": "JLPT N1（上級）",
        "vocab": "評論・社説レベル。専門用語・抽象語も可。",
        "topic": "政治・経済・社会問題・文化・科学。",
        "grammar": "〜にほかならない・〜をもって・〜いかんによって など上級文型。",
        "example": "「経済格差の拡大は、社会の分断を招きかねないという懸念が高まっている。」",
    },
    "JLPT N0": {
        "desc": "JLPT N1超（専門・学術）",
        "vocab": "学術・専門語彙。高度な表現・難解な四字熟語も可。",
        "topic": "学術・専門分野・政策・哲学・科学技術。",
        "grammar": "複雑な複文・論文体・倒置構文なども可。",
        "example": "「量子コンピュータの実用化は、現行の暗号化技術に根本的な見直しを迫るものと予見されている。」",
    },
}
LEVEL_DESC["JPT 300"] = {**LEVEL_DESC["JLPT N4"], "desc": "JPT 300点（JLPT N4相当・基礎）"}
LEVEL_DESC["JPT 400"] = {**LEVEL_DESC["JLPT N4"], "desc": "JPT 400点（JLPT N4上位相当）"}
LEVEL_DESC["JPT 500"] = {**LEVEL_DESC["JLPT N3"], "desc": "JPT 500点（JLPT N3相当）"}
LEVEL_DESC["JPT 600"] = {**LEVEL_DESC["JLPT N2"], "desc": "JPT 600点（JLPT N2相当）"}
LEVEL_DESC["JPT 700"] = {**LEVEL_DESC["JLPT N2"], "desc": "JPT 700点（JLPT N2上位相当）"}
LEVEL_DESC["JPT 800"] = {**LEVEL_DESC["JLPT N1"], "desc": "JPT 800点（JLPT N1相当）"}
LEVEL_DESC["JPT 900"] = {**LEVEL_DESC["JLPT N0"], "desc": "JPT 900点（JLPT N1超相当）"}


# ── Gemini API 공통 호출 ──────────────────────────────
def _call_gemini(prompt: str, temperature: float = 0.1, max_tokens: int = 1024) -> str:
    """429 시 제안 대기 후 1회 재시도. 일일 한도 소진 시 즉시 포기."""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        return ""
    client = google_genai.Client(api_key=GEMINI_API_KEY)
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            return response.text or ""
        except Exception as e:
            err = str(e)
            is_quota = "429" in err or "quota" in err.lower()
            is_daily = "PerDay" in err or "per_day" in err.lower()
            if is_quota and is_daily:
                print(f"[Gemini] 일일 할당량 소진 — 오늘은 더 이상 재시도하지 않음")
                return ""
            if is_quota and attempt == 0:
                m = re.search(r"retry in (\d+(?:\.\d+)?)", err)
                wait = int(float(m.group(1))) + 5 if m else 60
                print(f"[Gemini] 분당 한도 초과. {wait}초 대기 후 재시도...")
                time.sleep(wait)
                continue
            print(f"[Gemini] API 오류: {e}")
            return ""
    return ""


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


# ── 문장 완성도 검증 ──────────────────────────────────
def validate_sentences(sentences: list, label: str) -> list:
    """
    각 문장이 。！？로 끝나는지 확인.
    불완전 문장 발견 시 전체 내용을 터미널에 출력하고 빈 리스트 반환.
    """
    cleaned = []
    for line in sentences:
        line = sanitize_text(line.strip())
        line = re.sub(r"^[\d\.\-・\*\①-⑩\s]+", "", line).strip()
        if not line or not is_japanese(line):
            continue
        cleaned.append(line)

    # 불완전 문장 검사
    incomplete = [s for s in cleaned if s and s[-1] not in "。！？"]
    if incomplete:
        print("\n" + "="*60)
        print(f"[경고] 불완전 문장 발견 — 이메일 전송 중단")
        print(f"레벨: {label}")
        print("="*60)
        print("[생성된 전체 내용]")
        for i, s in enumerate(cleaned, 1):
            mark = " ← 불완전" if s[-1] not in "。！？" else ""
            print(f"{i:2}. {s}{mark}")
        print("="*60)
        return []  # 빈 리스트 반환 → PDF 저장 안 함

    # 길이 미달 문장 제거 (10자 미만)
    valid = [s for s in cleaned if len(s) >= 10]
    return valid[:10]


# ── 1단계: RSS에서 제목 5~7개 수집 ───────────────────
def crawl_titles(count: int = 7) -> list:
    """NHK RSS에서 뉴스 제목 여러 개 수집."""
    titles = []
    urls   = []
    try:
        rss_url = random.choice(NHK_RSS_LIST)
        r = requests.get(rss_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup  = BeautifulSoup(r.text, "xml")
        items = soup.find_all("item")
        random.shuffle(items)
        for item in items[:count]:
            t = item.find("title")
            l = item.find("link")
            if t and l and is_japanese(t.text.strip()):
                titles.append(t.text.strip())
                urls.append(l.text.strip())
    except Exception as e:
        print(f"RSS crawl failed: {e}")
    return list(zip(titles, urls))


# ── 2단계: Gemini가 레벨에 맞는 제목 선택 ─────────────
def select_title_with_gemini(title_pairs: list, label: str) -> tuple:
    """
    수집된 제목들 중 해당 레벨에 가장 적합한 제목 1개를 Gemini가 선택.
    레벨에 맞지 않는 단어가 포함된 제목은 건너뜀.
    """
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        return random.choice(title_pairs) if title_pairs else ("今日のできごと", "")

    lv = LEVEL_DESC.get(label, LEVEL_DESC["JLPT N3"])
    title_list = "\n".join(f"{i+1}. {t}" for i, (t, _) in enumerate(title_pairs))

    prompt = f"""あなたはJLPT・JPT専門の日本語教師です。

【今日のレベル】{lv['desc']}
【レベルの語彙基準】{lv['vocab']}
【レベルの話題基準】{lv['topic']}

【ニュースタイトル一覧】
{title_list}

上記のタイトルの中から、{lv['desc']}レベルの学習者に最も適したテーマのタイトルを1つ選んでください。
・{lv['desc']}レベルに合わない難解な専門語を含むタイトルは選ばないこと
・選んだタイトルの番号だけを答えてください（例：3）"""

    answer = _call_gemini(prompt, temperature=0.0, max_tokens=10)
    match = re.search(r"\d+", answer)
    if match:
        idx = int(match.group()) - 1
        if 0 <= idx < len(title_pairs):
            selected = title_pairs[idx]
            print(f"Gemini selected title #{idx+1}: {selected[0]}")
            return selected

    return title_pairs[0] if title_pairs else ("今日のできごと", "")


# ── 레벨별 폴백 주제 ──────────────────────────────────
_FALLBACK_TOPIC = {
    "JLPT N4": "買い物と日常生活",   "JPT 300": "買い物と日常生活",
    "JPT 400": "学校と友達",
    "JLPT N3": "旅行と地域の話",     "JPT 500": "旅行と地域の話",
    "JLPT N2": "仕事と社会生活",     "JPT 600": "仕事と社会生活",
    "JPT 700": "環境と健康",
    "JLPT N1": "社会問題と文化",     "JPT 800": "社会問題と文化",
    "JLPT N0": "科学技術と哲学",     "JPT 900": "科学技術と哲学",
}


# ── 3단계: Gemini가 소설 창작 ─────────────────────────
def write_story_with_gemini(news_title: str, label: str, attempt: int = 0) -> list:
    """선택된 제목을 주제로 Gemini가 지정 레벨 소설(10문장) 창작. 재시도 시 attempt 증가."""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        print("Gemini API not available.")
        return []

    lv = LEVEL_DESC.get(label, LEVEL_DESC["JLPT N3"])

    if attempt >= 1:
        theme = _FALLBACK_TOPIC.get(label, "日常生活")
        print(f"[재시도 {attempt}] 폴백 주제 사용: {theme}")
    else:
        theme = news_title

    prompt = f"""あなたは日本語教師です。今から{lv['desc']}レベルの学習者向けに読み物を書きます。

【テーマ】「{theme}」に関連した日常的な場面

【語彙制限 — 絶対厳守】
{lv['vocab']}
※ 上記レベル外の語彙・専門用語・経済用語・政治用語は一切使用禁止

【使用する文法パターン】
{lv['grammar']}

【参考例文のレベル感】
{lv['example']}

【出力ルール — 全て絶対厳守】
1. 文章のみを出力する（タイトル・ヘッダー・番号・説明・コメント禁止）
2. マークダウン記号（**、##など）は一切使用しない
3. 10文ちょうど出力する（少なくても多くても禁止）
4. 1行に1文のみ、改行で区切る
5. 各文は必ず「。」「！」「？」のいずれかで終わること（これは絶対条件）
6. 文が途中で切れることは絶対禁止
7. 最後の文も必ず「。」「！」「？」で終わること

今すぐ10文の読み物を書いてください："""

    raw = _call_gemini(prompt, temperature=0.1, max_tokens=1024)
    if not raw:
        return []

    raw = re.sub(r"\*+", "", raw)
    raw = re.sub(r"^#+\s*", "", raw, flags=re.MULTILINE)
    raw_lines = [l.strip() for l in raw.split("\n") if l.strip()]
    print(f"Gemini raw output (attempt {attempt + 1}, {len(raw_lines)} lines):")
    for i, l in enumerate(raw_lines, 1):
        print(f"  {i}. {l[:80]}")
    return raw_lines


# ── 메인 흐름 ─────────────────────────────────────────
def fetch_study_lines(label: str) -> tuple:
    """
    1) RSS에서 제목 수집
    2) Gemini가 레벨에 맞는 제목 선택
    3) Gemini가 소설 창작 (최대 3회 재시도)
    4) 문장 완성도 검증
    """
    # 1단계: 제목 수집
    title_pairs = crawl_titles(count=7)
    if not title_pairs:
        title_pairs = [("今日のできごと", "")]
    print(f"Collected {len(title_pairs)} titles.")

    # 2단계: 레벨에 맞는 제목 선택
    selected_title, selected_url = select_title_with_gemini(title_pairs, label)
    print(f"Selected: {selected_title}")

    # 3단계 + 4단계: 소설 창작 → 검증 (최대 3회 재시도)
    for attempt in range(3):
        raw_lines = write_story_with_gemini(selected_title, label, attempt=attempt)
        sentences = validate_sentences(raw_lines, label)
        if sentences:
            return selected_title, selected_url, sentences
        if attempt < 2:
            print(f"[재시도 {attempt + 1}/3] 문장 검증 실패, 다시 생성합니다...")

    return selected_title, selected_url, []


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
    pdf.cell(0, 12, "日本語学習 読み物",
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
    pdf.cell(0, 6, f"テーマ: {title if title else 'NHK News'}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if url:
        pdf.cell(0, 5, url[:80], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    pdf.set_font("JP", size=11)
    for line in lines:
        pdf.multi_cell(0, 8, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.output(OUTPUT_PDF)
    print(f"PDF saved: {OUTPUT_PDF}  ({len(lines)} lines)")


# ── 이메일 전송 ────────────────────────────────────────
def send_email(date_str: str, label: str, mode: str):
    if not GMAIL_ADDRESS or not GMAIL_APP_PW:
        print("Email credentials not set — skipping.")
        return
    if "입력" in str(GMAIL_APP_PW) or len(str(GMAIL_APP_PW)) < 10:
        print("App password placeholder — skipping email.")
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

    title, url, sentences = fetch_study_lines(label)

    # 불완전 문장 → 전송 중단 (validate_sentences에서 빈 리스트 반환됨)
    if not sentences:
        print("[중단] 유효한 문장이 없어 PDF/이메일 전송을 건너뜁니다.")
        sys.exit(1)

    print(f"Lines validated: {len(sentences)}")
    build_pdf(label, title, url, sentences, date_str, week_label, mode)
    send_email(date_str, label, mode)
    print("Done!")


if __name__ == "__main__":
    main()
