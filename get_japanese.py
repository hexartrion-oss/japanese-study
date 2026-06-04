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
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PW = os.environ.get("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    GMAIL_ADDRESS = GMAIL_ADDRESS or os.getenv("GMAIL_ADDRESS")
    GMAIL_APP_PW = GMAIL_APP_PW or os.getenv("GMAIL_APP_PASSWORD")
    GEMINI_API_KEY = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
except ImportError:
    pass

OUTPUT_PDF = os.path.join(os.path.dirname(__file__), "JPN.pdf")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ja,en;q=0.9",
}

# N2 이상만 사용하는 NHK RSS
NHK_RSS_LIST = [
    "https://www3.nhk.or.jp/rss/news/cat0.xml",
    "https://www3.nhk.or.jp/rss/news/cat1.xml",
    "https://www3.nhk.or.jp/rss/news/cat2.xml",
    "https://www3.nhk.or.jp/rss/news/cat3.xml",
    "https://www3.nhk.or.jp/rss/news/cat4.xml",
    "https://www3.nhk.or.jp/rss/news/cat5.xml",
    "https://www3.nhk.or.jp/rss/news/cat6.xml",
]

# Gemini 안전필터가 차단할 가능성 높은 키워드 (N2 이상 RSS용)
_BLOCK_KEYWORDS = [
    "死亡", "殺害", "殺人", "遺体", "事件", "逮捕", "容疑者", "被害",
    "自殺", "虐待", "暴行", "強盗", "爆発", "テロ", "戦争", "攻撃",
    "崩壊", "惨事", "惨殺", "銃撃", "刺殺", "溺死", "焼死",
    # 3) 대외적으로 잘 알려지지 않은 종교 관련 차단
    "宗教", "教団", "カルト", "新興宗教", "霊感", "布教", "信仰",
    "祈祷", "呪術", "占い", "スピリチュアル",
]

# ── 레벨 정의 ─────────────────────────────────────────
JPT_PLAN = ["JPT 300", "JPT 400", "JPT 500", "JPT 600", "JPT 700", "JPT 800", "JPT 900"]
JLPT_PLAN = ["JLPT N4", "JLPT N3", "JLPT N2", "JLPT N1", "JLPT N0"]

# N2 이상은 RSS 사용, N3/N4는 주제 풀 사용
RSS_LEVELS = {"JLPT N2", "JLPT N1", "JLPT N0", "JPT 600", "JPT 700", "JPT 800", "JPT 900"}

# 경어 표현 적용 레벨 (N1/JPT800 포함)
KEIGO_LEVELS = {"JLPT N1", "JLPT N0", "JPT 800", "JPT 900"}

LEVEL_DESC = {
    "JLPT N4": {
        "desc": "JLPT N4（基礎）",
        "vocab": "小学校3〜4年生レベルの語彙のみ。株・金利・政策・外交などの専門語は一切使わない。",
        "topic": "買い物・学校・天気・家族・食事・趣味・日常のできごと。",
        "grammar": "〜ます・〜です・〜てから・〜ので・〜たい・〜ている など基本文型のみ。",
        "example": (
            "今日は土曜日なので、お母さんと一緒にスーパーへ買い物に行きました。"
            "お店にはたくさんの野菜や果物が並んでいて、とてもにぎやかでした。"
            "私はいちごが好きなので、お母さんにお願いして買ってもらいました。"
            "レジで順番を待っている間、となりのお客さんがやさしく話しかけてくれました。"
            "家に帰ってから、買ってきた野菜でサラダを作るのを手伝いました。"
        ),
    },
    "JLPT N3": {
        "desc": "JLPT N3（初中級）",
        "vocab": "日常語彙。難しい専門語は使わず、身近な言葉で言い換える。",
        "topic": "日常生活・仕事・旅行・地域のニュース。",
        "grammar": "〜ながら・〜ために・〜によって・〜ようになる など初中級文型。",
        "example": (
            "近年、健康への関心が高まるにつれて、毎朝ジョギングをする人が増えている。"
            "早起きして体を動かすことで、一日の始まりを気持ちよく迎えられるからだ。"
            "特に都市部の公園では、朝の時間帯に多くの市民が運動する姿が見られるようになった。"
            "このような習慣は、生活習慣病の予防にも効果があると言われている。"
            "無理なく続けられる運動を日常に取り入れることが、健康維持の第一歩となる。"
        ),
    },
    "JLPT N2": {
        "desc": "JLPT N2（中級）",
        "vocab": "新聞・雑誌レベルの語彙。社会・経済の一般的な語彙は可。",
        "topic": "社会問題・環境・経済の一般的な話題。",
        "grammar": "〜に加えて・〜ざるを得ない・〜に伴い など中級文型。",
        "example": (
            "少子化が急速に進む中、政府はさまざまな支援策を講じているが、出生率の回復には至っていない。"
            "子育て費用の増大に加えて、働き方の柔軟性不足が若い世代の出産をためらわせる要因となっている。"
            "こうした背景から、企業における育児休業制度の充実が急務とされるようになった。"
            "一方で、地方自治体の中には独自の支援策を打ち出し、一定の成果を上げているところもある。"
            "少子化問題の解決には、社会全体で子育てを支える仕組みを整えていかざるを得ない。"
        ),
    },
    "JLPT N1": {
        "desc": "JLPT N1（上級）",
        # 1) 난해한 사자성어 제거, 종교 주제 제외
        "vocab": "評論・社説レベルの語彙。専門用語・抽象語は可。ただし難解な四字熟語・文語体・古典語は使わない。",
        "topic": "政治・経済・社会問題・文化・科学。宗教・信仰に関する話題は除く。",
        "grammar": "〜にほかならない・〜をもって・〜いかんによって など上級文型。",
        "example": (
            "経済格差の拡大は、単なる所得の問題にとどまらず、社会的分断を招きかねない構造的課題である。"
            "教育機会の不均等が固定化されるにつれ、階層の流動性は失われ、社会の活力が損なわれていく。"
            "こうした問題の根本には、成長の果実が一部に集中するという経済システムの歪みがあると言わざるを得ない。"
            "政策の有効性はその設計いかんによって大きく左右されるため、実証的な検証に基づく立案が求められる。"
            "格差是正に向けた取り組みは、社会の持続可能性を担保するためにも、早急に進めるべき課題にほかならない。"
        ),
    },
    "JLPT N0": {
        "desc": "JLPT N1超（専門・学術）",
        # 1) 난해한 사자성어 제거, 종교 주제 제외
        "vocab": "学術・専門語彙。高度な表現は可。ただし難解な四字熟語・文語体・古典語・日常では使わない専門語は使わない。",
        "topic": "学術・専門分野・政策・科学技術・ビジネス。哲学・宗教・信仰に関する話題は除く。",
        "grammar": "複雑な複文・論述体・接続表現など。倒置構文・文語体は使わない。",
        "example": (
            "再生可能エネルギーの導入拡大は、エネルギー安全保障の観点からも重要な政策課題となっている。"
            "太陽光や風力などの自然エネルギーを活用することで、化石燃料への依存度を下げることが期待されている。"
            "一方、電力の安定供給を確保するためには、蓄電技術の向上が不可欠である。"
            "各国政府は、カーボンニュートラルの実現に向けた具体的な目標を掲げ、取り組みを加速させている。"
            "企業においても、ESG経営の観点から環境負荷の低減が求められるようになっている。"
        ),
    },
}

LEVEL_DESC["JPT 300"] = {**LEVEL_DESC["JLPT N4"], "desc": "JPT 300点（JLPT N4相当・基礎）"}
LEVEL_DESC["JPT 400"] = {**LEVEL_DESC["JLPT N4"], "desc": "JPT 400点（JLPT N4上位相当）"}
LEVEL_DESC["JPT 500"] = {**LEVEL_DESC["JLPT N3"], "desc": "JPT 500点（JLPT N3相当）"}
LEVEL_DESC["JPT 600"] = {**LEVEL_DESC["JLPT N2"], "desc": "JPT 600点（JLPT N2相当）"}
LEVEL_DESC["JPT 700"] = {**LEVEL_DESC["JLPT N2"], "desc": "JPT 700点（JLPT N2上位相当）"}
LEVEL_DESC["JPT 800"] = {**LEVEL_DESC["JLPT N1"], "desc": "JPT 800点（JLPT N1相当）"}
LEVEL_DESC["JPT 900"] = {**LEVEL_DESC["JLPT N0"], "desc": "JPT 900点（JLPT N1超相当）"}

# ── N3/N4용 주제 풀 (RSS 대체) ────────────────────────
_TOPIC_POOL = {
    # N4 / JPT 300 / JPT 400
    "N4": [
        "スーパーでの買い物",
        "週末の家族の時間",
        "学校の給食",
        "雨の日の過ごし方",
        "好きな季節について",
        "ペットの世話",
        "誕生日のプレゼント",
        "近所の公園で遊ぶ",
        "朝ごはんを作る",
        "友達と映画を見に行く",
        "図書館で本を借りる",
        "バスや電車の乗り方",
        "学校のクラブ活動",
        "家の掃除を手伝う",
        "花屋さんで花を買う",
        "動物園に行く",
        "お正月の過ごし方",
        "夏祭りに行く",
        "近くのコンビニでの買い物",
        "家族で料理をする",
        "学校のテスト勉強",
        "友達と公園でサッカーをする",
        "お母さんへのプレゼントを探す",
        "駅で道を聞く",
        "病院で診察を受ける",
        "郵便局で荷物を送る",
        "新しい学校に転校する",
        "春の花見に行く",
        "冬の雪遊び",
        "夏休みの宿題をする",
    ],
    # N3 / JPT 500
    "N3": [
        "初めての一人旅",
        "地域のボランティア活動",
        "アルバイトの初日",
        "引っ越しの準備",
        "友人の結婚式に参加する",
        "健康のための運動習慣",
        "料理教室に通い始める",
        "職場の歓迎会",
        "スマートフォンを買い替える",
        "図書館でレポートを書く",
        "電車の遅延でのできごと",
        "近所の商店街の変化",
        "週末のサイクリング",
        "日本語学校での友人関係",
        "アパートを探す",
        "公共施設でのマナー",
        "地元の祭りを手伝う",
        "同僚との昼食時間",
        "自転車通勤を始める",
        "趣味のカメラ撮影",
        "旅行先での思わぬ出来事",
        "カフェでのリモートワーク",
        "近所の新しいレストラン",
        "友人との久しぶりの再会",
        "地域の防災訓練",
        "読書感想文を書く",
        "季節の変わり目と体調管理",
        "スポーツジムに入会する",
        "二日間の小旅行",
        "ふるさとへの帰省",
    ],
}

def _get_topic_pool(label: str) -> list:
    """레벨에 맞는 주제 풀 반환."""
    if label in {"JLPT N4", "JPT 300", "JPT 400"}:
        return _TOPIC_POOL["N4"]
    return _TOPIC_POOL["N3"]  # JLPT N3, JPT 500

# ── Gemini API 공통 호출 ──────────────────────────────
_GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

def _call_gemini(prompt: str, temperature: float = 0.1, max_tokens: int = 1024) -> str:
    """quota/503 오류 시 대기 후 재시도, 모델 폴백 포함."""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        return ""
    client = google_genai.Client(api_key=GEMINI_API_KEY)
    for model_id in _GEMINI_MODELS:
        print(f"[Gemini] 모델 시도: {model_id}")
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_id,
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
                if is_quota and attempt == 0:
                    m = re.search(r"retry in (\d+(?:\.\d+)?)", err)
                    wait = int(float(m.group(1))) + 5 if m else 60
                    print(f"[Gemini] {model_id} 한도 초과. {wait}초 대기 후 재시도...")
                    time.sleep(wait)
                    continue
                if is_quota:
                    print(f"[Gemini] {model_id} 재시도 실패 → 10초 후 다음 모델로 전환")
                    time.sleep(10)
                    break
                is_unavailable = "503" in err or "UNAVAILABLE" in err
                if is_unavailable and attempt == 0:
                    print(f"[Gemini] {model_id} 서버 과부하(503). 30초 대기 후 재시도...")
                    time.sleep(30)
                    continue
                if is_unavailable:
                    print(f"[Gemini] {model_id} 503 재시도 실패 → 다음 모델로 전환")
                    time.sleep(10)
                    break
                print(f"[Gemini] API 오류: {e}")
                return ""
    print("[Gemini] 모든 모델 실패")
    return ""

# ── 폰트 탐색 ─────────────────────────────────────────
def find_font() -> str:
    env_font = os.environ.get("JAPANESE_FONT_PATH")
    if env_font and os.path.exists(env_font):
        return env_font
    system = platform.system()
    if system == "Darwin":
        for f in [
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/Library/Fonts/Osaka.ttf",
        ]:
            if os.path.exists(f):
                if f.endswith(".ttf"):
                    return f
                print(f"[경고] macOS TTC 폰트: {f} — JAPANESE_FONT_PATH에 .ttf 지정 필요")
    if system == "Windows":
        for f in [
            r"C:\Windows\Fonts\msgothic.ttc",
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\YuGothR.ttc",
        ]:
            if os.path.exists(f):
                print(f"[경고] TTC 폰트 사용 중: {f}")
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
    raise FileNotFoundError(
        "Japanese font not found. Set JAPANESE_FONT_PATH env var to a .ttf file path."
    )

# ── 유틸 ──────────────────────────────────────────────
def is_japanese(text: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ン一-鿿]", text))

def sanitize_text(text: str) -> str:
    text = "".join(c for c in text if ord(c) <= 0xFFFF)
    return text.replace("\r\n", "\n").replace("\r", "\n")

def get_week_of_month(dt: datetime.date) -> int:
    return (dt.day + dt.replace(day=1).weekday() - 1) // 7 + 1

def has_block_keyword(text: str) -> bool:
    """Gemini 안전필터 차단 가능성 높은 키워드 포함 여부."""
    return any(kw in text for kw in _BLOCK_KEYWORDS)

# ── 문장 완성도 검증 ──────────────────────────────────
_SENTENCE_END = set("。！？")
_CLOSING_QUOTES = set("」』）")

def _sentence_ends_properly(s: str) -> bool:
    if not s:
        return False
    last = s[-1]
    if last in _SENTENCE_END:
        return True
    if last in _CLOSING_QUOTES and len(s) >= 2 and s[-2] in _SENTENCE_END:
        return True
    return False

def _merge_split_lines(lines: list) -> list:
    _CONTINUATION_START = re.compile(
        r"^(と|が|を|に|で|は|も|か|な|の|より|から|まで|として|について|によって|において)"
    )
    merged = []
    i = 0
    while i < len(lines):
        current = lines[i]
        if not _sentence_ends_properly(current) and i + 1 < len(lines):
            next_line = lines[i + 1]
            combined = current + next_line
            if _sentence_ends_properly(combined) or _continuation_needed(current, next_line):
                print(f"[병합 복구] '{current[:30]}...' + '{next_line[:30]}...'")
                merged.append(combined)
                i += 2
                continue
        merged.append(current)
        i += 1
    return merged

def _continuation_needed(current: str, next_line: str) -> bool:
    _CONTINUATION_START = re.compile(
        r"^(と|が|を|に|で|は|も|か|な|の|より|から|まで|として|について|によって|において)"
    )
    if _CONTINUATION_START.match(next_line):
        return True
    if current and current[-1] in _CLOSING_QUOTES:
        return True
    return False

def validate_sentences(sentences: list, label: str) -> list:
    cleaned = []
    for line in sentences:
        line = sanitize_text(line.strip())
        line = re.sub(r"^[\d\.\-・\*\①-⑩\s]+", "", line).strip()
        if not line or not is_japanese(line):
            continue
        cleaned.append(line)

    cleaned = _merge_split_lines(cleaned)

    incomplete = [s for s in cleaned if s and not _sentence_ends_properly(s)]
    if incomplete:
        print("\n" + "=" * 60)
        print(f"[경고] 복구 후에도 불완전 문장 존재")
        print(f"레벨: {label}")
        print("=" * 60)
        for i, s in enumerate(cleaned, 1):
            mark = " ← 불완전" if not _sentence_ends_properly(s) else ""
            print(f"{i:2}. {s}{mark}")
        print("=" * 60)
        return []

    valid = [s for s in cleaned if len(s) >= 10]

    if len(valid) < 10:
        print(f"[경고] 문장 수 부족: {len(valid)}개 (10개 필요 — 재시도)")
        return []

    return valid

# ── N3/N4: 주제 풀에서 랜덤 선택 ─────────────────────
def pick_topic(label: str) -> tuple:
    """N3/N4용 — 주제 풀에서 랜덤으로 주제 선택. (title, url) 형식 반환."""
    pool = _get_topic_pool(label)
    topic = random.choice(pool)
    print(f"[주제 풀] 선택된 주제: {topic}")
    return topic, ""

# ── N2 이상: NHK RSS 크롤링 ──────────────────────────
def crawl_titles(count: int = 10) -> list:
    """NHK RSS에서 뉴스 제목 수집. 차단 키워드 포함 제목은 미리 제거."""
    collected = []
    rss_urls = NHK_RSS_LIST[:]
    random.shuffle(rss_urls)
    for rss_url in rss_urls:
        if len(collected) >= count:
            break
        try:
            r = requests.get(rss_url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "xml")
            items = soup.find_all("item")
            random.shuffle(items)
            for item in items:
                t = item.find("title")
                l = item.find("link")
                if not t or not l:
                    continue
                title = t.text.strip()
                url = l.text.strip()
                if not is_japanese(title):
                    continue
                if has_block_keyword(title):
                    print(f"[RSS 필터] 차단 키워드 포함 제목 제외: {title}")
                    continue
                collected.append((title, url))
                if len(collected) >= count:
                    break
        except Exception as e:
            print(f"RSS crawl failed ({rss_url}): {e}")
    print(f"RSS 수집 완료: {len(collected)}개")
    return collected

def select_title_with_gemini(title_pairs: list, label: str) -> tuple:
    """수집된 제목 중 레벨에 맞는 제목 1개를 Gemini가 선택."""
    if len(title_pairs) == 1:
        return title_pairs[0]
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        return random.choice(title_pairs) if title_pairs else ("今日のニュース", "")

    lv = LEVEL_DESC.get(label, LEVEL_DESC["JLPT N2"])
    title_list = "\n".join(f"{i+1}. {t}" for i, (t, _) in enumerate(title_pairs))
    prompt = f"""あなたはJLPT・JPT専門の日本語教師です。

【今日のレベル】{lv['desc']}
【レベルの語彙基準】{lv['vocab']}
【レベルの話題基準】{lv['topic']}

【ニュースタイトル一覧】
{title_list}

上記のタイトルの中から、{lv['desc']}レベルの学習者に最も適したテーマのタイトルを1つ選んでください。
・暴力・犯罪・死亡・事故に関するタイトルは選ばないこと
・宗教・信仰・スピリチュアルに関するタイトルは選ばないこと
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
    return title_pairs[0] if title_pairs else ("今日のニュース", "")

# ── 문장 생성 ─────────────────────────────────────────
def write_story_with_gemini(theme: str, label: str, attempt: int = 0) -> list:
    """주제로 Gemini가 지정 레벨 읽기 자료(10문장) 창작."""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        print("Gemini API not available.")
        return []

    lv = LEVEL_DESC.get(label, LEVEL_DESC["JLPT N3"])
    is_beginner = label in {"JLPT N4", "JPT 300", "JPT 400"}
    is_keigo    = label in KEIGO_LEVELS  # N1 / JPT800 / N0 / JPT900

    if is_beginner:
        style_instruction = """【文体】
・一人称（私）で書く短い日記・エッセイ形式
・会話文（「〜」と言った）は使わない
・ですます調（〜ます・〜です）で統一する
・一続きの体験談として自然に流れる文章にすること"""
        scene_instruction = "に関する短いエッセイ（日記風）"

    elif is_keigo:
        # 4) 비즈니스 맥락이면 경어, 아니면 である 유지
        style_instruction = """【文体・敬語ルール】
・テーマがビジネス・職場・取引・報告・依頼・会議など実務的な場面の場合：
  　→ 尊敬語（「〜していただく」「〜なさる」「ご〜ください」等）・
  　　 謙譲語（「〜いたします」「拝見する」「お伺いする」等）・
  　　 丁寧語（「〜でございます」「〜ております」等）を自然に組み合わせて使うこと
  　→ ビジネスメール・報告書・依頼文・議事録など実際の実務場面で使われる表現を中心にすること
・テーマが社会問題・科学技術・政策など解説・論述的な場面の場合：
  　→ だ・である調（〜である・〜だ・〜している）で統一する
  　→ 客観的な視点で事実・現状・背景を説明する論述文にすること
・いずれの場合も：
  　→ 難解な四字熟語・文語体・古典語・日常では使わない専門語は使わない
  　→ 宗教・信仰・スピリチュアルに関する表現は一切使わない
  　→ 感情描写や登場人物の心理描写は禁止"""
        scene_instruction = "に関する文章（ビジネス実務場面なら敬語、解説・論述場面ならである調）"

    else:
        # N2 / N3 / JPT600 / JPT700
        style_instruction = """【文体】
・新聞記事・解説記事・寄稿文など、外部に公表する文書形式で書く
・会話文（「〜」と言った／と述べた）は一切使わない
・だ・である調（〜である・〜だ・〜している）で統一する
・客観的な視点で事実・現状・背景を説明する論述文にすること
・感情描写や登場人物の心理描写は禁止"""
        scene_instruction = "に関する解説記事・寄稿文"

    prompt = f"""あなたは日本語教師です。今から{lv['desc']}レベルの学習者向けに読み物を書きます。

【テーマ】「{theme}」{scene_instruction}

【語彙制限 — 絶対厳守】
{lv['vocab']}
※ 上記レベル外の語彙・専門用語・経済用語・政治用語は一切使用禁止
※ 難解な四字熟語・文語体・古典語は使用禁止
※ 宗教・信仰・スピリチュアルに関する表現は使用禁止

【使用する文法パターン】
{lv['grammar']}

【参考例文のレベル感】
{lv['example']}

{style_instruction}

【出力ルール — 全て絶対厳守】
1. 文章のみを出力する（タイトル・ヘッダー・番号・説明・コメント禁止）
2. マークダウン記号（**、##など）は一切使用しない
3. 20文出力する（少なくても多くても禁止）
4. 1行に1文のみ、改行で区切る
5. 各文は必ず「。」で終わること
6. 文が途中で切れることは絶対禁止
7. 会話文・引用符（「」）は一切使わない

今すぐ10文の読み物を書いてください："""

    raw = _call_gemini(prompt, temperature=0.1, max_tokens=2048)
    if not raw:
        return []

    raw = re.sub(r"\*+", "", raw)
    raw = re.sub(r"^#+\s*", "", raw, flags=re.MULTILINE)
    lines_by_newline = [l.strip() for l in raw.split("\n") if l.strip()]

    recovered = []
    for line in lines_by_newline:
        parts = re.split(r"(?<=[。！？」』])", line)
        for p in parts:
            p = p.strip()
            if p:
                recovered.append(p)

    raw_lines = recovered if len(recovered) >= len(lines_by_newline) else lines_by_newline

    print(f"Gemini raw output (attempt {attempt + 1}, {len(raw_lines)} lines):")
    for i, l in enumerate(raw_lines, 1):
        print(f"  {i}. {l[:80]}")

    return raw_lines

# ── 메인 흐름 ─────────────────────────────────────────
def fetch_study_lines(label: str) -> tuple:
    """
    N3/N4: 주제 풀 → 바로 문장 생성
    N2 이상: NHK RSS → 제목 선택 → 문장 생성
    Gemini 503/안전필터 차단 시 → 다른 주제로 재시도
    """
    use_rss = label in RSS_LEVELS

    if use_rss:
        title_pairs = crawl_titles(count=10)
        if not title_pairs:
            print("[RSS 실패] 폴백 주제 사용")
            fallback = {
                "JLPT N2": "仕事と社会生活", "JPT 600": "仕事と社会生活",
                "JPT 700": "環境と健康",
                "JLPT N1": "社内報告と業務連絡", "JPT 800": "社内報告と業務連絡",
                "JLPT N0": "ビジネスメールと取引先対応", "JPT 900": "ビジネスメールと取引先対応",
            }
            theme = fallback.get(label, "社会と生活")
            selected_title, selected_url = theme, ""
            title_pairs = [(theme, "")]
        else:
            selected_title, selected_url = select_title_with_gemini(title_pairs, label)
    else:
        selected_title, selected_url = pick_topic(label)
        title_pairs = [(selected_title, selected_url)]

    print(f"테마 확정: {selected_title}")

    sentences = []
    tried_titles = {selected_title}

    for attempt in range(3):
        raw_lines = write_story_with_gemini(selected_title, label, attempt=attempt)

        if not raw_lines:
            print("[중단] Gemini 응답 없음 — 다른 주제로 재시도")
            if use_rss:
                pool_key = "N3" if label in {"JLPT N2", "JPT 600", "JPT 700"} else "N4"
                new_theme = random.choice(_TOPIC_POOL[pool_key])
            else:
                new_theme = random.choice(_get_topic_pool(label))
            while new_theme in tried_titles and len(tried_titles) < 10:
                new_theme = random.choice(
                    _TOPIC_POOL["N3"] if use_rss else _get_topic_pool(label)
                )
            tried_titles.add(new_theme)
            selected_title = new_theme
            selected_url = ""
            print(f"[재시도 {attempt + 1}/3] 새 주제: {selected_title}")
            continue

        sentences = validate_sentences(raw_lines, label)
        if sentences:
            return selected_title, selected_url, sentences

        if use_rss and len(title_pairs) > 1:
            remaining = [(t, u) for t, u in title_pairs if t not in tried_titles]
            if remaining:
                selected_title, selected_url = random.choice(remaining)
                tried_titles.add(selected_title)
                print(f"[안전필터 차단 의심] 새 제목으로 교체: {selected_title}")
                continue

        if use_rss:
            pool_key = "N3" if label in {"JLPT N2", "JPT 600", "JPT 700"} else "N4"
            new_theme = random.choice(_TOPIC_POOL[pool_key])
        else:
            new_theme = random.choice(_get_topic_pool(label))

        while new_theme in tried_titles and len(tried_titles) < 10:
            new_theme = random.choice(
                _TOPIC_POOL["N3"] if use_rss else _get_topic_pool(label)
            )
        tried_titles.add(new_theme)
        selected_title = new_theme
        selected_url = ""
        print(f"[재시도 {attempt + 1}/3] 새 주제: {selected_title}")

    return selected_title, selected_url, sentences

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
    pdf.cell(0, 8, f"{date_str} | {mode} {week_label}",
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
    print(f"PDF saved: {OUTPUT_PDF} ({len(lines)} lines)")

# ── 이메일 전송 ────────────────────────────────────────
def send_email(date_str: str, label: str, mode: str):
    if not GMAIL_ADDRESS or not GMAIL_APP_PW:
        print("Email credentials not set — skipping.")
        return
    if "입력" in str(GMAIL_APP_PW) or len(str(GMAIL_APP_PW)) < 10:
        print("App password placeholder — skipping email.")
        return
    if not os.path.exists(OUTPUT_PDF):
        print(f"[오류] PDF 파일 없음: {OUTPUT_PDF} — 이메일 전송 건너뜀.")
        return
    try:
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = GMAIL_ADDRESS
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
    today = datetime.date.today()
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
    print(f"Today: {label} | {week_label}")

    title, url, sentences = fetch_study_lines(label)

    if not sentences:
        raise RuntimeError(
            "[중단] 유효한 문장이 없어 PDF/이메일 전송을 건너뜁니다."
        )

    print(f"Lines validated: {len(sentences)}")
    build_pdf(label, title, url, sentences, date_str, week_label, mode)
    send_email(date_str, label, mode)
    print("Done!")

if __name__ == "__main__":
    main()
