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
EMAIL_RECIPIENTS = os.environ.get("EMAIL_RECIPIENTS", "")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    GMAIL_ADDRESS = GMAIL_ADDRESS or os.getenv("GMAIL_ADDRESS")
    GMAIL_APP_PW = GMAIL_APP_PW or os.getenv("GMAIL_APP_PASSWORD")
    GEMINI_API_KEY = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    EMAIL_RECIPIENTS = EMAIL_RECIPIENTS or os.getenv("EMAIL_RECIPIENTS", "")
except ImportError:
    pass

OUTPUT_PDF = os.path.join(os.path.dirname(__file__), "JPN.pdf")

# ── 수동 실행(workflow_dispatch) 전용 설정 ─────────────
# 아래 기능은 전부 수동 실행에서만 발동한다. 스케줄 실행은 기존 동작 그대로.
MANUAL_RUN = os.environ.get("MANUAL_RUN") == "1"
MANUAL_MAIL_TO = "hexartrion@gmail.com"  # 수동 실행 시 유일한 수신자

RUN_LOG = []  # 생성 로그 (파일로도 기록 → 알림 메일이 kwonyh000@naver.com에 첨부)
RUN_LOG_FILE = os.path.join(os.path.dirname(__file__), "run_log.txt")
RUN_META_FILE = os.path.join(os.path.dirname(__file__), "run_meta.txt")  # 테스트 정보 블록

def _rlog(msg: str):
    """콘솔 출력 + 생성 로그 축적 + run_log.txt 기록.
    로그는 결과 알림 메일(kwonyh000@naver.com)에서만 열람한다."""
    print(msg)
    RUN_LOG.append(str(msg))
    try:
        with open(RUN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except OSError:
        pass

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
# JLPT 주: 무표기 N1/N0(통상문서)과 (경어) 카테고리를 독립 항목으로 분리 — 7항목/7일
JLPT_PLAN = ["JLPT N4", "JLPT N3", "JLPT N2", "JLPT N1", "JLPT N0",
             "JLPT N1(경어)", "JLPT N0(경어)"]

# N2 이상은 RSS 사용, N3/N4는 주제 풀 사용
RSS_LEVELS = {"JLPT N2", "JLPT N1", "JLPT N0", "JPT 600", "JPT 700", "JPT 800", "JPT 900"}

# 경어 표현 적용 레벨 (N1/JPT800 포함)
KEIGO_LEVELS = {"JLPT N1", "JLPT N0", "JPT 800", "JPT 900"}

def _split_level(label: str) -> tuple:
    """'JLPT N1(경어)' → ('JLPT N1', True) / 'JLPT N1' → ('JLPT N1', False)"""
    if label.endswith("(경어)"):
        return label[:-len("(경어)")], True
    return label, False

# ── 다양화 시드 (프롬프트 고정화 방지) ─────────────────
# 초급: 서술 목적 × 마무리 방식
_SEED_N4_PURPOSE = [
    "今日あったことを時間の順に書く日記",
    "最近の楽しかった思い出を振り返る日記",
    "明日や週末の計画と楽しみを書く日記",
    "初めて経験したことについて書く日記",
    "小さな失敗と次にどうしたいかを書く日記",
]
_SEED_N4_ENDING = [
    "最後の文は次の予定や計画で終わること",
    "最後の文は学んだことや気づいたことで終わること",
    "最後の文は感謝の気持ちや感想で終わること",
    "最後の文は楽しみな気持ちや期待で終わること",
]
# 중급 (N3/JPT500): 장르(だ・である 유지 범위) × 관점
_SEED_N3_GENRE = [
    "身近な話題を扱う生活コラム",
    "地域の話題を伝える解説記事",
    "変化やトレンドを紹介する記事",
]
_SEED_MID_VIEW = [
    "良い点と問題点の両方を比較しながら書くこと",
    "以前と現在の変化を対比しながら書くこと",
    "読者への具体的な助言を中心に書くこと",
    "具体的な事例や場面を中心に書くこと",
]
# N2 이상: 장르 × 관점 (+ A/B 대립 의견문)
_AB_GENRE = "AB_OPPOSING"
_SEED_ADV_GENRE = [
    "事実を客観的に伝える解説記事",
    "背景や原因を掘り下げる分析記事",
    "業界や社会全体の動向を紹介するリポート",
    "一人の筆者が賛否両面を整理する評論",
    "明確な主張と提言で締めくくる寄稿文",
    _AB_GENRE,
]
_SEED_ADV_VIEW = [
    "時間の流れ（過去→現在→今後）に沿って書くこと",
    "具体的な事例や数字を中心に書くこと",
    "課題とその解決策という構図で書くこと",
    "一般的な通念に疑問を投げかける導入で始めること",
]
_SEED_AB_STANCE = [
    "Aは賛成、Bは反対の立場",
    "Aは積極的な推進、Bは慎重論の立場",
    "Aは個人の工夫を重視、Bは社会の仕組みづくりを重視する立場",
    "Aは効率や利便性を優先、Bは安全や安心を優先する立場",
]
# 경어 모드: 문서 유형
_SEED_KEIGO_DOC = [
    "上司への社内報告メール",
    "取引先への依頼・案内文",
    "会議の議事録",
    "クレームへの対応・お詫び文",
    "業務の引き継ぎ文書",
    "社外向けプレゼンテーションの原稿",
]
# 학습 포커스 폴백 풀 (Gemini 능동 선정 실패 시) — JLPT N1/N0 전용
_SEED_N1_GRAMMAR = [
    "〜を余儀なくされる（やむを得ず〜する）",
    "〜に越したことはない（〜が一番良い）",
    "〜とあいまって（〜と相互に作用して）",
    "〜を皮切りに（〜を始まりとして）",
    "〜ないまでも（〜ほどではないが）",
    "〜きらいがある（〜という好ましくない傾向がある）",
    "〜にたえない（〜する価値がない・感情が抑えられない）",
    "〜んがために（〜する目的で）",
]
_SEED_IDIOM = [
    "拍車をかける（物事の進行を一段と速める）",
    "軌道に乗る（物事が順調に進み始める）",
    "一石を投じる（問題を提起して反響を呼ぶ）",
    "歯止めをかける（進行を食い止める）",
    "追い風となる（有利な状況になる）",
    "浮き彫りになる（はっきりと表れる）",
]
_SEED_BIZ_IDIOM = [
    "お力添えをいただく（協力してもらう）",
    "ご査収のほどお願いいたします（確認と受け取りの依頼）",
    "肝に銘じる（心に深く刻む）",
    "万全を期す（完全を目指して準備する）",
    "誠心誠意対応する（真心をもって対応する）",
    "ご高覧いただく（見ていただくの敬語表現）",
]

# 경어 모드: '오늘의 경어 포커스' (매일 2종 로테이션 — 所存です 등 상투구 편중 방지)
_SEED_KEIGO_FOCUS = [
    "尊敬語の特別動詞（おっしゃる・いらっしゃる・ご覧になる・お越しになる）",
    "謙譲語の特別動詞（伺う・拝見する・承る・申し伝える・存じます）",
    "お／ご＋授受表現（お目通しいただく・ご査収ください・お力添えいただく）",
    "クッション言葉（恐れ入りますが・差し支えなければ・お手数をおかけしますが）",
    "丁重語・改まり語（ございます・弊社・先般・後ほど・かねてより）",
]

# ── 조합식 주제 확장 부품 (내용 다양성: 풀 크기를 곱셈으로 확장) ──
# N4: 장소(12) × 사건(6) = 72개 조합
_N4_PLACES = [
    "スーパー", "図書館", "公園", "駅", "学校", "動物園",
    "郵便局", "病院", "レストラン", "デパート", "海", "山",
]
_N4_EVENTS = [
    "楽しかったこと", "困ったこと", "初めてしたこと",
    "友達と過ごした時間", "家族と過ごした時間", "びっくりしたこと",
]
# N3: 장면(10) × 각도(6) = 60개 조합
_N3_SCENES = [
    "アルバイト先", "旅行先", "料理教室", "スポーツジム", "商店街",
    "電車の中", "職場", "引っ越した町", "地域の行事", "オンライン授業",
]
_N3_ANGLES = [
    "経験した小さな失敗", "出会った人とのできごと", "気づいた変化",
    "学んだこと", "起きた思わぬ出来事", "続けている工夫",
]
# 비즈니스(N1/N0/JPT800/900): 부서(15) × 상황(8) = 120개 조합
_BIZ_DOMAINS = [
    "人事", "営業", "経理", "総務", "企画", "製造", "物流", "購買",
    "広報", "法務", "情報システム", "品質管理", "海外事業",
    "研究開発", "カスタマーサポート",
]
_BIZ_SITUATIONS = [
    "新制度の導入", "業務改善の提案", "トラブルへの対応と報告",
    "取引先との交渉", "社内研修の企画", "プロジェクトの進捗報告",
    "コスト削減の取り組み", "顧客満足度向上の施策",
]

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
# 경어 카테고리: 기준 레벨의 어휘·문법을 상속하되 desc로 구분
LEVEL_DESC["JLPT N1(경어)"] = {**LEVEL_DESC["JLPT N1"], "desc": "JLPT N1（上級・ビジネス敬語）"}
LEVEL_DESC["JLPT N0(경어)"] = {**LEVEL_DESC["JLPT N0"], "desc": "JLPT N1超（専門・ビジネス敬語）"}

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
    # N1 / N0 / JPT 800 / JPT 900 — 비즈니스 주제 포함 (경어체 발동)
    "N1": [
        # 기존 비즈니스 전략·조직 주제
        "取引先へのメール対応",
        "会議の議事録作成",
        "プロジェクト進捗報告",
        "新入社員研修の企画",
        "クライアントへの提案書",
        "社内稟議書の作成",
        "業務改善の提言",
        "人事評価制度の見直し",
        "グローバル人材の育成",
        "デジタルトランスフォーメーション推進",
        "持続可能な経営戦略",
        "ダイバーシティと企業文化",
        "リモートワークの課題と対策",
        "M&Aによる事業拡大",
        "ESG投資と企業価値",
        # BJT 빈출 — 경어·대인 커뮤니케이션
        "クレーム対応と顧客への謝罪",
        "商談における価格交渉",
        "電話応対と取り次ぎの敬語表現",
        "上司への報告・連絡・相談",
        "社内会議の進行と発言",
        "採用面接の実施と評価",
        "顧客訪問とアポイントメントの調整",
        "見積書・請求書の確認と交渉",
        "契約書の読み合わせと締結",
        "社外向けプレゼンテーションの準備",
        "部下への業務指示と進捗確認",
        "社内コンプライアンス研修の実施",
        "危機管理とリスク対応の報告",
        "異文化ビジネスマナーと接待",
        "退職・異動の挨拶と引き継ぎ",
    ],
}

# ── 조합식 주제를 풀에 병합 (수제 30개 + 조합 = 90~150개) ──
_TOPIC_POOL["N4"] += [f"{p}で{e}" for p in _N4_PLACES for e in _N4_EVENTS]          # 30 + 72 = 102
_TOPIC_POOL["N3"] += [f"{s}で{a}" for s in _N3_SCENES for a in _N3_ANGLES]          # 30 + 60 = 90
_TOPIC_POOL["N1"] += [
    f"{d}部門における{s}" for d in _BIZ_DOMAINS for s in _BIZ_SITUATIONS
]                                                                                    # 30 + 120 = 150

def _get_topic_pool(label: str) -> list:
    """레벨에 맞는 주제 풀 반환."""
    if label in {"JLPT N4", "JPT 300", "JPT 400"}:
        return _TOPIC_POOL["N4"]
    if label in {"JLPT N1", "JLPT N0", "JPT 800", "JPT 900"}:
        return _TOPIC_POOL["N1"]
    return _TOPIC_POOL["N3"]  # JLPT N3, JPT 500, N2

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
                config_kwargs = {
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                }
                # gemini-2.5 계열은 thinking 토큰이 max_output_tokens를 소모해
                # 응답이 절단(5~7줄)되므로 thinking을 비활성화
                if "2.5" in model_id:
                    config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                        thinking_budget=0
                    )
                response = client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(**config_kwargs),
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
_SECTION_HEADERS = ("【Aの意見】", "【Bの意見】")

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
    merged = []
    i = 0
    while i < len(lines):
        current = lines[i]
        if current in _SECTION_HEADERS:
            merged.append(current)
            i += 1
            continue
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

_CONTINUATION_START = re.compile(
    r"^(と|が|を|に|で|は|も|か|な|の|より|から|まで|として|について|によって|において)"
)

def _continuation_needed(current: str, next_line: str) -> bool:
    if current and current[-1] == "、":
        return True
    if _CONTINUATION_START.match(next_line):
        return True
    if current and current[-1] in _CLOSING_QUOTES:
        return True
    return False

# 경어 상투구 게이트: 프롬프트 지시(2회まで)에 여유를 둔 상한 — 초과 시 재시도
_KEIGO_STOCK_LIMIT = 3
_KEIGO_STOCK_PHRASES = ["所存で", "申し上げます", "させていただき", "いたします", "てまいり"]

def _keigo_stock_overused(sentences: list) -> list:
    """상한 초과 상투구를 [(표현, 횟수)]로 반환. 비어 있으면 통과."""
    text = "".join(sentences)
    return [
        (kw, text.count(kw))
        for kw in _KEIGO_STOCK_PHRASES
        if text.count(kw) > _KEIGO_STOCK_LIMIT
    ]

def validate_sentences(sentences: list, label: str, keigo_doc: bool = False) -> list:
    cleaned = []
    for line in sentences:
        line = sanitize_text(line.strip())
        line = re.sub(r"^[\d\.\-・\*\①-⑩\s]+", "", line).strip()
        if not line or not is_japanese(line):
            continue
        cleaned.append(line)

    cleaned = _merge_split_lines(cleaned)

    incomplete = [s for s in cleaned if s and not _sentence_ends_properly(s) and s not in _SECTION_HEADERS]
    if incomplete:
        print("\n" + "=" * 60)
        print(f"[경고] 불완전 문장 {len(incomplete)}개 발견 — 유효 문장만 유지")
        print(f"레벨: {label}")
        print("=" * 60)
        for i, s in enumerate(cleaned, 1):
            mark = " ← 불완전" if not _sentence_ends_properly(s) else ""
            print(f"{i:2}. {s}{mark}")
        print("=" * 60)
        cleaned = [s for s in cleaned if _sentence_ends_properly(s) or s in _SECTION_HEADERS]
        if not cleaned:
            return []

    valid = [s for s in cleaned if len(s) >= 10 or s in _SECTION_HEADERS]

    if len([s for s in valid if s not in _SECTION_HEADERS]) < 10:
        print(f"[경고] 문장 수 부족: {len(valid)}개 (10개 필요 — 재시도)")
        return []

    if "【Aの意見】" in valid or "【Bの意見】" in valid:
        if not ("【Aの意見】" in valid and "【Bの意見】" in valid):
            print("[경고] A/B 형식 불완전 — 재시도")
            return []
        _bi = valid.index("【Bの意見】")
        _ac = len([s for s in valid[:_bi] if s not in _SECTION_HEADERS])
        _bc = len([s for s in valid[_bi + 1:] if s not in _SECTION_HEADERS])
        if _ac < 8 or _bc < 8:
            print(f"[경고] A/B 분량 부족 (A:{_ac}문, B:{_bc}문) — 재시도")
            return []

    if keigo_doc:
        overused = _keigo_stock_overused(valid)
        if overused:
            detail = ", ".join(f"「{k}」×{n}" for k, n in overused)
            _rlog(f"[경고] 경어 상투구 과다: {detail} (상한 {_KEIGO_STOCK_LIMIT}회) — 재시도")
            return []

    return valid

# ── N3/N4: 주제 풀에서 랜덤 선택 ─────────────────────
def pick_topic(label: str) -> tuple:
    """N3/N4용 — 주제 풀에서 랜덤으로 주제 선택. (title, url) 형식 반환."""
    pool = _get_topic_pool(label)
    topic = random.choice(pool)
    _rlog(f"[주제 풀] 선택된 주제: {topic} (풀 크기: {len(pool)})")
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
# 온도 사다리: 첫 시도는 다양성 우선(0.8), 검증 실패로 재시도할수록
# 보수적인 온도로 내려가 최종 전송 성공률을 보전한다.
_TEMP_LADDER = [0.8, 0.6, 0.3, 0.1]

# 논술체(だ・である) 공통 규칙 — 경어 레벨의 논술 모드와 N2/N3급이 공유 (중복 제거)
_STYLE_RONJUTSU_RULES = """・常体（だ・である体）を基本としつつ、文末は「〜である」ばかりに偏らせず、「〜だ」「〜という」「〜とされる」「〜と言える」「〜ている」「〜のだ」なども自然に織り交ぜる（「〜である」を3文以上連続させない）
・客観的な視点で事実・現状・背景を説明する論述文にすること
・感情描写や登場人物の心理描写は禁止"""

def _pick_adv_seed() -> tuple:
    """상급(논술) 시드 선택. (ab_mode, ab_stance, seed_lines) 반환.
    경어 레벨의 논술 모드와 N2급이 동일 로직을 공유한다 (중복 제거)."""
    _sg = random.choice(_SEED_ADV_GENRE)
    if _sg == _AB_GENRE:
        ab_stance = random.choice(_SEED_AB_STANCE)
        _rlog(f"[시드] A/B 대립 의견문 모드: {ab_stance}")
        return True, ab_stance, ""
    _sv = random.choice(_SEED_ADV_VIEW)
    _rlog(f"[시드] 장르: {_sg} / 관점: {_sv}")
    return False, "", f"・記事の種類：{_sg}\n・{_sv}"

def _gemini_keigo_focus() -> list:
    """수동 실행 전용: 고정 풀(_SEED_KEIGO_FOCUS) 대신 Gemini가
    오늘의 경어 포커스 2종을 능동적으로 선정한다. 실패 시 빈 리스트(→고정 풀 폴백)."""
    prompt = """あなたは日本語のビジネス敬語の専門家です。
ビジネス文書・ビジネス会話で使われる敬語表現の中から、今日学習者が練習すべき敬語のカテゴリを2つ、あなた自身が自由に選んでください。

・定番の表現（「申し上げます」「所存です」「いたします」等）以外の、幅広いレパートリーから選ぶこと
・2つは互いに異なる種類にすること（尊敬語・謙譲語・丁重語・美化語・クッション言葉・ビジネス慣用句・改まり語 など）
・出力は2行のみ。1行に1つ、「カテゴリ名（具体例1・具体例2・具体例3）」の形式で書くこと
・説明・番号・記号・前置きは一切書かない"""
    raw = _call_gemini(prompt, temperature=1.0, max_tokens=300)
    lines = [l.strip("・-* 　") for l in raw.split("\n") if l.strip()]
    lines = [l for l in lines if is_japanese(l)]
    return lines[:2] if len(lines) >= 2 else []

# ── 학습 포커스: N1급(JLPT N1/N0 전용) 문법·관용표현 능동 선정 ──
def _focus_key(display: str) -> str:
    """표시문에서 본문 검증용 핵심 문자열 추출.
    '〜を余儀なくされる（意味）' → 'を余儀なくされる'"""
    core = display.split("（")[0]
    core = core.replace("〜", " ").replace("～", " ")
    parts = [p for p in re.split(r"[ ・/／、]", core) if p]
    return max(parts, key=len) if parts else ""

def _gemini_study_focus(business: bool, include_idiom: bool = True) -> list:
    """오늘의 학습 항목을 Gemini가 능동 선정. (종류, 표시문, 검증키) 리스트 반환.
    JLPT N1/N0: N1 문법 3 + 관용표현 2 / JPT 800·900: N1 문법 3만 (관용 제외 —
    JPT는 관용표현을 배우는 용도가 아님). 실패 시 고정 풀 폴백."""
    idiom_kind = ("ビジネス文書・ビジネス会話でよく使われる慣用表現・決まり文句"
                  if business else "新聞・評論で使われる慣用句・比喩表現")
    if include_idiom:
        items_part = f"""1. JLPT N1レベルの文法パターンを3つ（定番に偏らず、毎回異なる組み合わせになるよう幅広いレパートリーから選ぶ）
2. {idiom_kind}を2つ

【出力形式 — 厳守】
・合計5行のみ。1行に1項目
・文法は「文法：パターン（短い意味）」、慣用表現は「慣用：表現（短い意味）」の形式"""
    else:
        items_part = """JLPT N1レベルの文法パターンを3つ（定番に偏らず、毎回異なる組み合わせになるよう幅広いレパートリーから選ぶ）

【出力形式 — 厳守】
・合計3行のみ。1行に1項目
・「文法：パターン（短い意味）」の形式"""
    prompt = f"""あなたは日本語教育の専門家です。今日の読み物に組み込む学習項目を、あなた自身が自由に選んでください。

{items_part}
・説明・番号・前置きは一切書かない"""
    raw = _call_gemini(prompt, temperature=1.0, max_tokens=400)
    grammar, idiom = [], []
    for l in (x.strip("・-* 　") for x in raw.split("\n") if x.strip()):
        if l.startswith("文法：") and len(grammar) < 3:
            grammar.append(l[len("文法："):].strip())
        elif include_idiom and l.startswith("慣用：") and len(idiom) < 2:
            idiom.append(l[len("慣用："):].strip())
    if len(grammar) < 3 or (include_idiom and len(idiom) < 2):
        grammar = random.sample(_SEED_N1_GRAMMAR, 3)
        idiom = random.sample(_SEED_BIZ_IDIOM if business else _SEED_IDIOM, 2) if include_idiom else []
        _rlog("[학습 포커스] Gemini 선정 실패 → 고정 풀 폴백")
    focus = ([("문법", g, _focus_key(g)) for g in grammar]
             + [("관용", i, _focus_key(i)) for i in idiom])
    _rlog("[학습 포커스] " + " / ".join(f"{k}:{d.split('（')[0]}" for k, d, _ in focus))
    return focus

def _focus_usage(sentences: list, study_focus: list) -> tuple:
    """본문에서 학습 항목 사용 여부 검사 → (사용 수, 누락 표시문 리스트)."""
    text = "".join(sentences)
    used = sum(1 for _, _, key in study_focus if key and key in text)
    missing = [d.split("（")[0] for _, d, key in study_focus if not key or key not in text]
    return used, missing

def write_story_with_gemini(theme: str, label: str, attempt: int = 0,
                            business_doc: bool = False,
                            study_focus: list = None) -> list:
    """주제로 Gemini가 지정 레벨 읽기 자료(20문장) 창작."""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        print("Gemini API not available.")
        return []

    lv = LEVEL_DESC.get(label, LEVEL_DESC["JLPT N3"])
    is_beginner = label in {"JLPT N4", "JPT 300", "JPT 400"}
    is_keigo    = label in KEIGO_LEVELS  # N1 / JPT800 / N0 / JPT900

    # ── 오늘의 시드 선택 (프롬프트 고정화 방지) ──
    ab_mode = False
    ab_stance = ""
    seed_lines = ""
    if is_beginner:
        _sp = random.choice(_SEED_N4_PURPOSE)
        _se = random.choice(_SEED_N4_ENDING)
        seed_lines = f"・今日書く日記の種類：{_sp}\n・{_se}"
        _rlog(f"[시드] 서술: {_sp} / 마무리: {_se}")
    elif is_keigo:
        if business_doc:
            _sd = random.choice(_SEED_KEIGO_DOC)
            _sf = _gemini_keigo_focus()  # 수동/스케줄 공통: Gemini 능동 선정, 실패 시 고정 풀
            if _sf:
                _rlog(f"[경어 포커스] Gemini 능동 선정: {_sf[0]} / {_sf[1]}")
            else:
                _sf = random.sample(_SEED_KEIGO_FOCUS, 2)
            seed_lines = (
                f"・「{_sd}」の本文として書くこと"
                f"（件名・宛名・挨拶・署名は書かない）\n"
                f"・今日の敬語フォーカス：次の2種類の敬語表現を本文の中で必ず自然に使うこと\n"
                f"　　1. {_sf[0]}\n"
                f"　　2. {_sf[1]}"
            )
            _rlog(f"[시드] 경어 문서: {_sd} / 포커스: {_sf[0][:20]}... + {_sf[1][:20]}...")
        else:
            ab_mode, ab_stance, seed_lines = _pick_adv_seed()
    elif label in {"JLPT N3", "JPT 500"}:
        _sg = random.choice(_SEED_N3_GENRE)
        _sv = random.choice(_SEED_MID_VIEW)
        seed_lines = f"・記事の種類：{_sg}（だ・である調を維持すること）\n・{_sv}"
        _rlog(f"[시드] 장르: {_sg} / 관점: {_sv}")
    else:
        ab_mode, ab_stance, seed_lines = _pick_adv_seed()

    if is_beginner:
        style_instruction = """【文体】
・一人称（私）で書く短い日記・エッセイ形式
・会話文（「〜」と言った）は使わない
・ですます調（〜ます・〜です）で統一する
・一続きの体験談として自然に流れる文章にすること"""
        scene_instruction = "に関する短いエッセイ（日記風）"

    elif is_keigo and business_doc:
        # 비즈니스 문서 확정 → 경어 지시만 전송 (である 지시 혼재 제거)
        style_instruction = """【文体・敬語ルール】
・尊敬語（「〜していただく」「〜なさる」「ご〜ください」等）・
  謙譲語（「〜いたします」「拝見する」「お伺いする」等）・
  丁寧語（「〜でございます」「〜ております」等）を自然に組み合わせて使うこと
・ビジネスメール・報告書・依頼文・議事録など実際の実務場面で使われる表現を中心にすること
・文末表現の偏り禁止：「〜申し上げます」「〜所存です」「〜いたします」「〜させていただきます」「〜てまいります」など、同じ文末表現は文書全体で2回まで。同じ文末を2文連続で使わない
・謙譲語だけに偏らず、読み手・相手の行為への尊敬語（ご覧になる・おっしゃる・お越しになる・ご確認なさる等）も3文以上で使うこと
・感情描写や登場人物の心理描写は禁止"""
        scene_instruction = "に関するビジネス文書（敬語を用いた実務文）"

    elif is_keigo:
        # 경어 레벨이지만 논술 주제(RSS 뉴스 등) → である 논술체
        style_instruction = f"""【文体】
{_STYLE_RONJUTSU_RULES}
・難解な四字熟語・文語体・古典語・日常では使わない専門語は使わない"""
        scene_instruction = "に関する解説記事・論説文（である調）"

    else:
        # N2 / N3 / JPT600 / JPT700
        style_instruction = f"""【文体】
・新聞記事・解説記事・寄稿文など、外部に公表する文書形式で書く
・会話文（「〜」と言った／と述べた）は一切使わない
{_STYLE_RONJUTSU_RULES}"""
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

【今日の書き方 — 本日のみの指定】
{seed_lines}

【出力ルール — 全て絶対厳守】
1. 文章のみを出力する（タイトル・ヘッダー・番号・説明・コメント禁止）
2. マークダウン記号（**、##など）は一切使用しない
3. 20文出力する（少なくても多くても禁止）
4. 1行に1文のみ、改行で区切る
5. 各文は必ず「。」で終わること
6. 文が途中で切れることは絶対禁止
7. 会話文・引用符（「」）は一切使わない

今すぐ20文の読み物を書いてください："""

    if ab_mode:
        prompt = f"""あなたは日本語教師です。JLPTの統合理解問題のように、同じテーマについて立場の異なるAとBの2つの意見文を書きます。レベルは{lv['desc']}です。

【テーマ】「{theme}」
【立場の構図】{ab_stance}

【語彙制限 — 絶対厳守】
{lv['vocab']}
※ 上記レベル外の語彙・専門用語は一切使用禁止

【使用する文法パターン】
{lv['grammar']}

【文体】
・AもBもだ・である調で統一する
・会話文・引用符（「」）は一切使わない
・AとBは同じ事実を扱いながら、明確に異なる立場を取ること
・それぞれの最後の1〜2文に、相手の立場への間接的な反論を含めること

【出力ルール — 全て絶対厳守】
1. 1行目に【Aの意見】とだけ書く
2. 2行目からAの意見文を10文書く（1行1文）
3. 次の行に【Bの意見】とだけ書く
4. その後Bの意見文を10文書く（1行1文）
5. 見出し2行以外の各文は必ず「。」で終わること
6. マークダウン記号・番号・タイトル・説明・コメントは一切禁止

今すぐ書いてください："""

    # 학습 포커스(N1급): 문법·관용표현 사용 지시를 두 프롬프트(일반/AB) 공통 삽입
    if study_focus:
        _g = [d for k, d, _ in study_focus if k == "문법"]
        _i = [d for k, d, _ in study_focus if k == "관용"]
        focus_block = "【今日の学習項目 — 本文に必ず織り込むこと】\n"
        if _g:
            focus_block += ("・次のN1文法パターンをそれぞれ1回以上、自然な文脈で使うこと\n"
                            + "".join(f"　　・{x}\n" for x in _g))
        if _i:
            focus_block += ("・次の慣用表現をそれぞれ1回以上、自然な文脈で使うこと\n"
                            + "".join(f"　　・{x}\n" for x in _i))
        prompt = prompt.replace("【出力ルール", focus_block + "\n【出力ルール")

    temp = _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
    print(f"[온도 사다리] attempt {attempt + 1} → temperature={temp}")
    raw = _call_gemini(prompt, temperature=temp, max_tokens=4096)
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

    if len(raw_lines) < 8:
        print(f"[경고] Gemini 응답 줄수 부족 ({len(raw_lines)}줄) — 절단 응답 → 재시도")
        return []

    print(f"Gemini raw output (attempt {attempt + 1}, {len(raw_lines)} lines):")
    for i, l in enumerate(raw_lines, 1):
        print(f"  {i}. {l[:80]}")

    return raw_lines

# ── 메인 흐름 ─────────────────────────────────────────
def _retry_theme(label: str, tried_titles: set) -> str:
    """재시도용 새 주제를 주제 풀에서 선택 (중복 회피). 호출부 2곳 공용."""
    new_theme = random.choice(_get_topic_pool(label))
    while new_theme in tried_titles and len(tried_titles) < 10:
        new_theme = random.choice(_get_topic_pool(label))
    tried_titles.add(new_theme)
    return new_theme

def fetch_study_lines(label: str, force_business: bool = False) -> tuple:
    """
    N3/N4: 주제 풀 → 바로 문장 생성
    N2 이상: NHK RSS → 제목 선택 → 문장 생성
    Gemini 503/안전필터 차단 시 → 다른 주제로 재시도
    force_business: (경어) 카테고리 전용 — 비즈니스 상황 + 경어 문서 강제.
    경어 발동 경로는 이것 하나뿐이다 (확률 굴림 폐지, 무표기·JPT 레벨은 전부 논술체).
    """
    use_rss = label in RSS_LEVELS
    keigo_business = False

    # 학습 포커스: JLPT N1/N0 = 문법 3 + 관용 2, JPT 800/900 = 문법 3만 (관용 제외)
    study_focus = []
    if label in {"JLPT N1", "JLPT N0"}:
        study_focus = _gemini_study_focus(force_business)
    elif label in {"JPT 800", "JPT 900"}:
        study_focus = _gemini_study_focus(force_business, include_idiom=False)

    if force_business:
        selected_title, selected_url = pick_topic(label)
        title_pairs = [(selected_title, selected_url)]
        _rlog(f"[경어 모드] 비즈니스 주제 선택: {selected_title}")
        use_rss = False
        keigo_business = True

    if use_rss:
        title_pairs = crawl_titles(count=10)
        if not title_pairs:
            print("[RSS 실패] 폴백 주제 사용")
            # RSS 폴백은 논술체 생성 경로이므로 논술형 주제로 통일
            fallback = {
                "JLPT N2": "仕事と社会生活", "JPT 600": "仕事と社会生活",
                "JPT 700": "環境と健康",
                "JLPT N1": "経済と社会の動向", "JPT 800": "経済と社会の動向",
                "JLPT N0": "科学技術と政策の課題", "JPT 900": "科学技術と政策の課題",
            }
            theme = fallback.get(label, "社会と生活")
            selected_title, selected_url = theme, ""
            title_pairs = [(theme, "")]
        else:
            selected_title, selected_url = select_title_with_gemini(title_pairs, label)
    elif not keigo_business:
        selected_title, selected_url = pick_topic(label)
        title_pairs = [(selected_title, selected_url)]

    _rlog(f"테마 확정: {selected_title}")

    sentences = []
    tried_titles = {selected_title}

    _MAX_ATTEMPTS = 4
    for attempt in range(_MAX_ATTEMPTS):
        raw_lines = write_story_with_gemini(selected_title, label, attempt=attempt,
                                            business_doc=keigo_business,
                                            study_focus=study_focus)

        if raw_lines:
            sentences = validate_sentences(raw_lines, label, keigo_doc=keigo_business)
            if sentences and study_focus:
                # 과반 게이트: 항목 과반 이상 실제 사용되어야 채택 (5개→3, 3개→2).
                # 단, 마지막 시도는 미달이어도 완화 채택 (당일 발송 실패 방지)
                _need = len(study_focus) // 2 + 1
                used, missing = _focus_usage(sentences, study_focus)
                if used < _need and attempt < _MAX_ATTEMPTS - 1:
                    _rlog(f"[학습 포커스] 사용 {used}/{len(study_focus)} — 과반 미달"
                          f" (누락: {', '.join(missing)}) → 재시도")
                    sentences = []
                elif used < _need:
                    _rlog(f"[학습 포커스] 사용 {used}/{len(study_focus)} — 과반 미달이나"
                          f" 마지막 시도이므로 완화 채택 (누락: {', '.join(missing)})")
                else:
                    _rlog(f"[학습 포커스] 사용 {used}/{len(study_focus)}"
                          + (f" (누락: {', '.join(missing)})" if missing else " — 전부 사용"))
            if sentences:
                return selected_title, selected_url, sentences, keigo_business

            if use_rss and len(title_pairs) > 1:
                remaining = [(t, u) for t, u in title_pairs if t not in tried_titles]
                if remaining:
                    selected_title, selected_url = random.choice(remaining)
                    tried_titles.add(selected_title)
                    print(f"[안전필터 차단 의심] 새 제목으로 교체: {selected_title}")
                    continue
        else:
            print("[중단] Gemini 응답 없음 — 다른 주제로 재시도")

        selected_title = _retry_theme(label, tried_titles)
        selected_url = ""
        if force_business:
            keigo_business = True
        _rlog(f"[재시도 {attempt + 1}/{_MAX_ATTEMPTS}] 새 주제: {selected_title}")

    return selected_title, selected_url, sentences, keigo_business

# ── 레벨 선택: 주간 셔플 백 (랜덤성 + 순환 보장) ──────
def _plan_for_date(dt: datetime.date) -> list:
    """해당 날짜의 주 모드에 따른 레벨 plan.
    주 단위 JPT/JLPT 구분은 get_week_of_month 홀짝으로 확정적 (근간 — 불변)."""
    return JPT_PLAN[:] if get_week_of_month(dt) % 2 == 1 else JLPT_PLAN[:]

def _weekly_bag(dt: datetime.date) -> list:
    """그 주 월요일 날짜를 시드로 plan을 셔플한 '이번 주 레벨 순서'.
    같은 주에는 순서가 고정되고, 주가 바뀌면 새로 섞인다 (상태 저장 불필요)."""
    monday = dt - datetime.timedelta(days=dt.weekday())
    bag = _plan_for_date(dt)
    random.Random(monday.toordinal()).shuffle(bag)
    return bag

# ── JLPT 주: 평일 7일 순환제 ─────────────────────────
# 7항목(경어 2 포함)을 'JLPT 주 평일'에만 흐르는 7일 주기로 순환시켜,
# 경어 포함 모든 레벨이 정확히 균등(JLPT 평일 7일당 1회)하고
# 누락 간격에 결정적 상한이 생긴다. 상태 저장 불필요(날짜만으로 결정).
_JLPT_CYCLE_EPOCH = datetime.date(2026, 1, 5)  # 기준 월요일 (고정)

def _jlpt_day_index(dt: datetime.date) -> int:
    """epoch부터 dt 직전까지의 'JLPT 주 평일' 개수 = dt의 순환 위치."""
    count = 0
    d = _JLPT_CYCLE_EPOCH
    one = datetime.timedelta(days=1)
    while d < dt:
        if d.weekday() < 5 and get_week_of_month(d) % 2 == 0:
            count += 1
        d += one
    return count

def _jlpt_block_order(block: int) -> list:
    """7일 블록별 JLPT 레벨 순서. 블록마다 셔플(패턴 고정 방지)하고,
    블록 경계에서 같은 레벨이 연속되면 앞 두 항목을 교환한다."""
    order = JLPT_PLAN[:]
    random.Random(1_000_003 + block).shuffle(order)
    if block > 0:
        prev = JLPT_PLAN[:]
        random.Random(1_000_003 + block - 1).shuffle(prev)
        if order[0] == prev[-1]:
            order[0], order[1] = order[1], order[0]
    return order

def pick_level(today: datetime.date) -> str:
    """레벨 선택.
    - JPT주(홀수 주): 기존 주간 셔플 백 유지 (경어 없음 — 전 레벨 논술체)
    - JLPT주(짝수 주): 평일 7일 순환제 — 경어 포함 7레벨이 정확히 균등 순환
    """
    if get_week_of_month(today) % 2 == 1:
        bag = _weekly_bag(today)
        if len(bag) > 1:
            monday = today - datetime.timedelta(days=today.weekday())
            prev_sunday = monday - datetime.timedelta(days=1)
            prev_bag = _weekly_bag(prev_sunday)
            prev_label = prev_bag[prev_sunday.weekday() % len(prev_bag)]
            if bag[0] == prev_label:
                bag[0], bag[1] = bag[1], bag[0]
        return bag[today.weekday() % len(bag)]
    block, pos = divmod(_jlpt_day_index(today), len(JLPT_PLAN))
    return _jlpt_block_order(block)[pos]

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
        if line in _SECTION_HEADERS:
            pdf.ln(2)
            pdf.set_fill_color(234, 240, 255)
            pdf.set_font("JP", size=12)
            pdf.cell(0, 8, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
            pdf.set_font("JP", size=11)
            pdf.ln(1)
            continue
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
        if MANUAL_RUN:
            # 수동 실행: hexartrion@gmail.com 단독 수신
            recipients = [MANUAL_MAIL_TO]
        else:
            recipients = [GMAIL_ADDRESS]
            for addr in re.split(r"[,;\s]+", EMAIL_RECIPIENTS):
                addr = addr.strip()
                if addr and addr not in recipients:
                    recipients.append(addr)
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = ", ".join(recipients)
        _test_tag = " [TEST]" if MANUAL_RUN else ""
        msg["Subject"] = f"[Japanese Study]{_test_tag} {date_str} — {label}"
        # 본문은 기존 형태 유지. 수동 실행 시 테스트 안내 한 줄만 추가
        # (레벨·주제 상세와 생성 로그는 결과 알림 메일(kwonyh000@naver.com) 전용)
        body = f"Today's Japanese study material.\nLevel: {label}\nMode: {mode}"
        if MANUAL_RUN:
            body += "\n이 메일은 테스트용 메일 입니다"
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
        print(f"Email sent → {', '.join(recipients)}")
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

    force_level = os.environ.get("FORCE_LEVEL", "").strip()
    level_choice = os.environ.get("LEVEL_CHOICE", "").strip()
    all_levels = JPT_PLAN + JLPT_PLAN
    force_business = False

    # 이전 실행 로그/메타 파일 초기화
    for _p in (RUN_LOG_FILE, RUN_META_FILE):
        try:
            open(_p, "w", encoding="utf-8").close()
        except OSError:
            pass

    if MANUAL_RUN and level_choice in all_levels:
        # 수동 실행: 기존 로직 무시하고 지정 레벨 사용 ((경어) 카테고리 포함)
        label = level_choice
        _rlog(f"[수동 실행] 레벨 지정: {label} (주간 로직 무시)")
    elif force_level in all_levels:
        label = force_level
        print(f"[FORCE_LEVEL] {label} 강제 지정")
    else:
        # 주간 셔플 백: 주 모드(JPT/JLPT)는 위에서 확정, 주 안에서만 랜덤 순환
        label = pick_level(today)
    print(f"Today: {label} | {week_label}")

    # ── 생성 모드 확정 (수동/스케줄/FORCE_LEVEL 공통 규칙) ──
    # 경어는 (경어) 카테고리에서만 발동한다.
    # 무표기 레벨(JLPT N1/N0, JPT 800/900 포함)은 전부 통상문서(논술체).
    label, _is_keigo_cat = _split_level(label)
    if _is_keigo_cat:
        force_business = True
        _rlog(f"[경어 카테고리] 기준 레벨 {label} — 비즈니스 상황에서만 경어 문서 생성")

    title, url, sentences, keigo_business = fetch_study_lines(
        label, force_business=force_business)

    if not sentences:
        raise RuntimeError(
            "[중단] 유효한 문장이 없어 PDF/이메일 전송을 건너뜁니다."
        )

    print(f"Lines validated: {len(sentences)}")

    # 실제 생성 결과(keigo_business) 기준 표기: 메일 "…[경어]", PDF 헤더 "…[敬語]"
    mail_label = f"{label}[경어]" if keigo_business else label
    pdf_label = f"{label}[敬語]" if keigo_business else label

    # 실행 정보 블록 기록 → 결과 알림 메일(kwonyh000@naver.com)에 첨부 (수동/스케줄 공통)
    _desc = LEVEL_DESC.get(f"{label}(경어)" if keigo_business else label,
                           LEVEL_DESC.get(label, {})).get("desc", label)
    try:
        with open(RUN_META_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join([
                f"지정 레벨    : {level_choice or ('(자동)' if MANUAL_RUN else '(스케줄 자동)')}",
                f"적용 레벨    : {mail_label}",
                f"세부 카테고리 : {_desc}",
                f"모드         : {'비즈니스 경어' if keigo_business else mode}",
                f"주제         : {title}",
            ]) + "\n")
    except OSError:
        pass

    build_pdf(pdf_label, title, url, sentences, date_str, week_label, mode)
    send_email(date_str, mail_label, mode)
    print("Done!")

if __name__ == "__main__":
    main()
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
EMAIL_RECIPIENTS = os.environ.get("EMAIL_RECIPIENTS", "")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    GMAIL_ADDRESS = GMAIL_ADDRESS or os.getenv("GMAIL_ADDRESS")
    GMAIL_APP_PW = GMAIL_APP_PW or os.getenv("GMAIL_APP_PASSWORD")
    GEMINI_API_KEY = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    EMAIL_RECIPIENTS = EMAIL_RECIPIENTS or os.getenv("EMAIL_RECIPIENTS", "")
except ImportError:
    pass

OUTPUT_PDF = os.path.join(os.path.dirname(__file__), "JPN.pdf")

# ── 수동 실행(workflow_dispatch) 전용 설정 ─────────────
# 아래 기능은 전부 수동 실행에서만 발동한다. 스케줄 실행은 기존 동작 그대로.
MANUAL_RUN = os.environ.get("MANUAL_RUN") == "1"
MANUAL_MAIL_TO = "hexartrion@gmail.com"  # 수동 실행 시 유일한 수신자

RUN_LOG = []  # 생성 로그 (파일로도 기록 → 알림 메일이 kwonyh000@naver.com에 첨부)
RUN_LOG_FILE = os.path.join(os.path.dirname(__file__), "run_log.txt")
RUN_META_FILE = os.path.join(os.path.dirname(__file__), "run_meta.txt")  # 테스트 정보 블록

def _rlog(msg: str):
    """콘솔 출력 + 생성 로그 축적 + run_log.txt 기록.
    로그는 결과 알림 메일(kwonyh000@naver.com)에서만 열람한다."""
    print(msg)
    RUN_LOG.append(str(msg))
    try:
        with open(RUN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except OSError:
        pass

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

# ── 다양화 시드 (프롬프트 고정화 방지) ─────────────────
# 초급: 서술 목적 × 마무리 방식
_SEED_N4_PURPOSE = [
    "今日あったことを時間の順に書く日記",
    "最近の楽しかった思い出を振り返る日記",
    "明日や週末の計画と楽しみを書く日記",
    "初めて経験したことについて書く日記",
    "小さな失敗と次にどうしたいかを書く日記",
]
_SEED_N4_ENDING = [
    "最後の文は次の予定や計画で終わること",
    "最後の文は学んだことや気づいたことで終わること",
    "最後の文は感謝の気持ちや感想で終わること",
    "最後の文は楽しみな気持ちや期待で終わること",
]
# 중급 (N3/JPT500): 장르(だ・である 유지 범위) × 관점
_SEED_N3_GENRE = [
    "身近な話題を扱う生活コラム",
    "地域の話題を伝える解説記事",
    "変化やトレンドを紹介する記事",
]
_SEED_MID_VIEW = [
    "良い点と問題点の両方を比較しながら書くこと",
    "以前と現在の変化を対比しながら書くこと",
    "読者への具体的な助言を中心に書くこと",
    "具体的な事例や場面を中心に書くこと",
]
# N2 이상: 장르 × 관점 (+ A/B 대립 의견문)
_AB_GENRE = "AB_OPPOSING"
_SEED_ADV_GENRE = [
    "事実を客観的に伝える解説記事",
    "背景や原因を掘り下げる分析記事",
    "業界や社会全体の動向を紹介するリポート",
    "一人の筆者が賛否両面を整理する評論",
    "明確な主張と提言で締めくくる寄稿文",
    _AB_GENRE,
]
_SEED_ADV_VIEW = [
    "時間の流れ（過去→現在→今後）に沿って書くこと",
    "具体的な事例や数字を中心に書くこと",
    "課題とその解決策という構図で書くこと",
    "一般的な通念に疑問を投げかける導入で始めること",
]
_SEED_AB_STANCE = [
    "Aは賛成、Bは反対の立場",
    "Aは積極的な推進、Bは慎重論の立場",
    "Aは個人の工夫を重視、Bは社会の仕組みづくりを重視する立場",
    "Aは効率や利便性を優先、Bは安全や安心を優先する立場",
]
# 경어 모드: 문서 유형
_SEED_KEIGO_DOC = [
    "上司への社内報告メール",
    "取引先への依頼・案内文",
    "会議の議事録",
    "クレームへの対応・お詫び文",
    "業務の引き継ぎ文書",
    "社外向けプレゼンテーションの原稿",
]
# 경어 모드: '오늘의 경어 포커스' (매일 2종 로테이션 — 所存です 등 상투구 편중 방지)
_SEED_KEIGO_FOCUS = [
    "尊敬語の特別動詞（おっしゃる・いらっしゃる・ご覧になる・お越しになる）",
    "謙譲語の特別動詞（伺う・拝見する・承る・申し伝える・存じます）",
    "お／ご＋授受表現（お目通しいただく・ご査収ください・お力添えいただく）",
    "クッション言葉（恐れ入りますが・差し支えなければ・お手数をおかけしますが）",
    "丁重語・改まり語（ございます・弊社・先般・後ほど・かねてより）",
]

# ── 조합식 주제 확장 부품 (내용 다양성: 풀 크기를 곱셈으로 확장) ──
# N4: 장소(12) × 사건(6) = 72개 조합
_N4_PLACES = [
    "スーパー", "図書館", "公園", "駅", "学校", "動物園",
    "郵便局", "病院", "レストラン", "デパート", "海", "山",
]
_N4_EVENTS = [
    "楽しかったこと", "困ったこと", "初めてしたこと",
    "友達と過ごした時間", "家族と過ごした時間", "びっくりしたこと",
]
# N3: 장면(10) × 각도(6) = 60개 조합
_N3_SCENES = [
    "アルバイト先", "旅行先", "料理教室", "スポーツジム", "商店街",
    "電車の中", "職場", "引っ越した町", "地域の行事", "オンライン授業",
]
_N3_ANGLES = [
    "経験した小さな失敗", "出会った人とのできごと", "気づいた変化",
    "学んだこと", "起きた思わぬ出来事", "続けている工夫",
]
# 비즈니스(N1/N0/JPT800/900): 부서(15) × 상황(8) = 120개 조합
_BIZ_DOMAINS = [
    "人事", "営業", "経理", "総務", "企画", "製造", "物流", "購買",
    "広報", "法務", "情報システム", "品質管理", "海外事業",
    "研究開発", "カスタマーサポート",
]
_BIZ_SITUATIONS = [
    "新制度の導入", "業務改善の提案", "トラブルへの対応と報告",
    "取引先との交渉", "社内研修の企画", "プロジェクトの進捗報告",
    "コスト削減の取り組み", "顧客満足度向上の施策",
]

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
    # N1 / N0 / JPT 800 / JPT 900 — 비즈니스 주제 포함 (경어체 발동)
    "N1": [
        # 기존 비즈니스 전략·조직 주제
        "取引先へのメール対応",
        "会議の議事録作成",
        "プロジェクト進捗報告",
        "新入社員研修の企画",
        "クライアントへの提案書",
        "社内稟議書の作成",
        "業務改善の提言",
        "人事評価制度の見直し",
        "グローバル人材の育成",
        "デジタルトランスフォーメーション推進",
        "持続可能な経営戦略",
        "ダイバーシティと企業文化",
        "リモートワークの課題と対策",
        "M&Aによる事業拡大",
        "ESG投資と企業価値",
        # BJT 빈출 — 경어·대인 커뮤니케이션
        "クレーム対応と顧客への謝罪",
        "商談における価格交渉",
        "電話応対と取り次ぎの敬語表現",
        "上司への報告・連絡・相談",
        "社内会議の進行と発言",
        "採用面接の実施と評価",
        "顧客訪問とアポイントメントの調整",
        "見積書・請求書の確認と交渉",
        "契約書の読み合わせと締結",
        "社外向けプレゼンテーションの準備",
        "部下への業務指示と進捗確認",
        "社内コンプライアンス研修の実施",
        "危機管理とリスク対応の報告",
        "異文化ビジネスマナーと接待",
        "退職・異動の挨拶と引き継ぎ",
    ],
}

# ── 조합식 주제를 풀에 병합 (수제 30개 + 조합 = 90~150개) ──
_TOPIC_POOL["N4"] += [f"{p}で{e}" for p in _N4_PLACES for e in _N4_EVENTS]          # 30 + 72 = 102
_TOPIC_POOL["N3"] += [f"{s}で{a}" for s in _N3_SCENES for a in _N3_ANGLES]          # 30 + 60 = 90
_TOPIC_POOL["N1"] += [
    f"{d}部門における{s}" for d in _BIZ_DOMAINS for s in _BIZ_SITUATIONS
]                                                                                    # 30 + 120 = 150

def _get_topic_pool(label: str) -> list:
    """레벨에 맞는 주제 풀 반환."""
    if label in {"JLPT N4", "JPT 300", "JPT 400"}:
        return _TOPIC_POOL["N4"]
    if label in {"JLPT N1", "JLPT N0", "JPT 800", "JPT 900"}:
        return _TOPIC_POOL["N1"]
    return _TOPIC_POOL["N3"]  # JLPT N3, JPT 500, N2

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
                config_kwargs = {
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                }
                # gemini-2.5 계열은 thinking 토큰이 max_output_tokens를 소모해
                # 응답이 절단(5~7줄)되므로 thinking을 비활성화
                if "2.5" in model_id:
                    config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                        thinking_budget=0
                    )
                response = client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(**config_kwargs),
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
_SECTION_HEADERS = ("【Aの意見】", "【Bの意見】")

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
    merged = []
    i = 0
    while i < len(lines):
        current = lines[i]
        if current in _SECTION_HEADERS:
            merged.append(current)
            i += 1
            continue
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

_CONTINUATION_START = re.compile(
    r"^(と|が|を|に|で|は|も|か|な|の|より|から|まで|として|について|によって|において)"
)

def _continuation_needed(current: str, next_line: str) -> bool:
    if current and current[-1] == "、":
        return True
    if _CONTINUATION_START.match(next_line):
        return True
    if current and current[-1] in _CLOSING_QUOTES:
        return True
    return False

# 경어 상투구 게이트: 프롬프트 지시(2회まで)에 여유를 둔 상한 — 초과 시 재시도
_KEIGO_STOCK_LIMIT = 3
_KEIGO_STOCK_PHRASES = ["所存で", "申し上げます", "させていただき", "いたします", "てまいり"]

def _keigo_stock_overused(sentences: list) -> list:
    """상한 초과 상투구를 [(표현, 횟수)]로 반환. 비어 있으면 통과."""
    text = "".join(sentences)
    return [
        (kw, text.count(kw))
        for kw in _KEIGO_STOCK_PHRASES
        if text.count(kw) > _KEIGO_STOCK_LIMIT
    ]

def validate_sentences(sentences: list, label: str, keigo_doc: bool = False) -> list:
    cleaned = []
    for line in sentences:
        line = sanitize_text(line.strip())
        line = re.sub(r"^[\d\.\-・\*\①-⑩\s]+", "", line).strip()
        if not line or not is_japanese(line):
            continue
        cleaned.append(line)

    cleaned = _merge_split_lines(cleaned)

    incomplete = [s for s in cleaned if s and not _sentence_ends_properly(s) and s not in _SECTION_HEADERS]
    if incomplete:
        print("\n" + "=" * 60)
        print(f"[경고] 불완전 문장 {len(incomplete)}개 발견 — 유효 문장만 유지")
        print(f"레벨: {label}")
        print("=" * 60)
        for i, s in enumerate(cleaned, 1):
            mark = " ← 불완전" if not _sentence_ends_properly(s) else ""
            print(f"{i:2}. {s}{mark}")
        print("=" * 60)
        cleaned = [s for s in cleaned if _sentence_ends_properly(s) or s in _SECTION_HEADERS]
        if not cleaned:
            return []

    valid = [s for s in cleaned if len(s) >= 10 or s in _SECTION_HEADERS]

    if len([s for s in valid if s not in _SECTION_HEADERS]) < 10:
        print(f"[경고] 문장 수 부족: {len(valid)}개 (10개 필요 — 재시도)")
        return []

    if "【Aの意見】" in valid or "【Bの意見】" in valid:
        if not ("【Aの意見】" in valid and "【Bの意見】" in valid):
            print("[경고] A/B 형식 불완전 — 재시도")
            return []
        _bi = valid.index("【Bの意見】")
        _ac = len([s for s in valid[:_bi] if s not in _SECTION_HEADERS])
        _bc = len([s for s in valid[_bi + 1:] if s not in _SECTION_HEADERS])
        if _ac < 8 or _bc < 8:
            print(f"[경고] A/B 분량 부족 (A:{_ac}문, B:{_bc}문) — 재시도")
            return []

    if keigo_doc:
        overused = _keigo_stock_overused(valid)
        if overused:
            detail = ", ".join(f"「{k}」×{n}" for k, n in overused)
            _rlog(f"[경고] 경어 상투구 과다: {detail} (상한 {_KEIGO_STOCK_LIMIT}회) — 재시도")
            return []

    return valid

# ── N3/N4: 주제 풀에서 랜덤 선택 ─────────────────────
def pick_topic(label: str) -> tuple:
    """N3/N4용 — 주제 풀에서 랜덤으로 주제 선택. (title, url) 형식 반환."""
    pool = _get_topic_pool(label)
    topic = random.choice(pool)
    _rlog(f"[주제 풀] 선택된 주제: {topic} (풀 크기: {len(pool)})")
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
# 온도 사다리: 첫 시도는 다양성 우선(0.8), 검증 실패로 재시도할수록
# 보수적인 온도로 내려가 최종 전송 성공률을 보전한다.
_TEMP_LADDER = [0.8, 0.6, 0.3, 0.1]

# 논술체(だ・である) 공통 규칙 — 경어 레벨의 논술 모드와 N2/N3급이 공유 (중복 제거)
_STYLE_RONJUTSU_RULES = """・常体（だ・である体）を基本としつつ、文末は「〜である」ばかりに偏らせず、「〜だ」「〜という」「〜とされる」「〜と言える」「〜ている」「〜のだ」なども自然に織り交ぜる（「〜である」を3文以上連続させない）
・客観的な視点で事実・現状・背景を説明する論述文にすること
・感情描写や登場人物の心理描写は禁止"""

def _pick_adv_seed() -> tuple:
    """상급(논술) 시드 선택. (ab_mode, ab_stance, seed_lines) 반환.
    경어 레벨의 논술 모드와 N2급이 동일 로직을 공유한다 (중복 제거)."""
    _sg = random.choice(_SEED_ADV_GENRE)
    if _sg == _AB_GENRE:
        ab_stance = random.choice(_SEED_AB_STANCE)
        _rlog(f"[시드] A/B 대립 의견문 모드: {ab_stance}")
        return True, ab_stance, ""
    _sv = random.choice(_SEED_ADV_VIEW)
    _rlog(f"[시드] 장르: {_sg} / 관점: {_sv}")
    return False, "", f"・記事の種類：{_sg}\n・{_sv}"

def _gemini_keigo_focus() -> list:
    """수동 실행 전용: 고정 풀(_SEED_KEIGO_FOCUS) 대신 Gemini가
    오늘의 경어 포커스 2종을 능동적으로 선정한다. 실패 시 빈 리스트(→고정 풀 폴백)."""
    prompt = """あなたは日本語のビジネス敬語の専門家です。
ビジネス文書・ビジネス会話で使われる敬語表現の中から、今日学習者が練習すべき敬語のカテゴリを2つ、あなた自身が自由に選んでください。

・定番の表現（「申し上げます」「所存です」「いたします」等）以外の、幅広いレパートリーから選ぶこと
・2つは互いに異なる種類にすること（尊敬語・謙譲語・丁重語・美化語・クッション言葉・ビジネス慣用句・改まり語 など）
・出力は2行のみ。1行に1つ、「カテゴリ名（具体例1・具体例2・具体例3）」の形式で書くこと
・説明・番号・記号・前置きは一切書かない"""
    raw = _call_gemini(prompt, temperature=1.0, max_tokens=300)
    lines = [l.strip("・-* 　") for l in raw.split("\n") if l.strip()]
    lines = [l for l in lines if is_japanese(l)]
    return lines[:2] if len(lines) >= 2 else []

def write_story_with_gemini(theme: str, label: str, attempt: int = 0,
                            business_doc: bool = False) -> list:
    """주제로 Gemini가 지정 레벨 읽기 자료(20문장) 창작."""
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        print("Gemini API not available.")
        return []

    lv = LEVEL_DESC.get(label, LEVEL_DESC["JLPT N3"])
    is_beginner = label in {"JLPT N4", "JPT 300", "JPT 400"}
    is_keigo    = label in KEIGO_LEVELS  # N1 / JPT800 / N0 / JPT900

    # ── 오늘의 시드 선택 (프롬프트 고정화 방지) ──
    ab_mode = False
    ab_stance = ""
    seed_lines = ""
    if is_beginner:
        _sp = random.choice(_SEED_N4_PURPOSE)
        _se = random.choice(_SEED_N4_ENDING)
        seed_lines = f"・今日書く日記の種類：{_sp}\n・{_se}"
        _rlog(f"[시드] 서술: {_sp} / 마무리: {_se}")
    elif is_keigo:
        if business_doc:
            _sd = random.choice(_SEED_KEIGO_DOC)
            _sf = _gemini_keigo_focus() if MANUAL_RUN else []
            if _sf:
                _rlog(f"[경어 포커스] Gemini 능동 선정: {_sf[0]} / {_sf[1]}")
            else:
                _sf = random.sample(_SEED_KEIGO_FOCUS, 2)
            seed_lines = (
                f"・「{_sd}」の本文として書くこと"
                f"（件名・宛名・挨拶・署名は書かない）\n"
                f"・今日の敬語フォーカス：次の2種類の敬語表現を本文の中で必ず自然に使うこと\n"
                f"　　1. {_sf[0]}\n"
                f"　　2. {_sf[1]}"
            )
            _rlog(f"[시드] 경어 문서: {_sd} / 포커스: {_sf[0][:20]}... + {_sf[1][:20]}...")
        else:
            ab_mode, ab_stance, seed_lines = _pick_adv_seed()
    elif label in {"JLPT N3", "JPT 500"}:
        _sg = random.choice(_SEED_N3_GENRE)
        _sv = random.choice(_SEED_MID_VIEW)
        seed_lines = f"・記事の種類：{_sg}（だ・である調を維持すること）\n・{_sv}"
        _rlog(f"[시드] 장르: {_sg} / 관점: {_sv}")
    else:
        ab_mode, ab_stance, seed_lines = _pick_adv_seed()

    if is_beginner:
        style_instruction = """【文体】
・一人称（私）で書く短い日記・エッセイ形式
・会話文（「〜」と言った）は使わない
・ですます調（〜ます・〜です）で統一する
・一続きの体験談として自然に流れる文章にすること"""
        scene_instruction = "に関する短いエッセイ（日記風）"

    elif is_keigo and business_doc:
        # 비즈니스 문서 확정 → 경어 지시만 전송 (である 지시 혼재 제거)
        style_instruction = """【文体・敬語ルール】
・尊敬語（「〜していただく」「〜なさる」「ご〜ください」等）・
  謙譲語（「〜いたします」「拝見する」「お伺いする」等）・
  丁寧語（「〜でございます」「〜ております」等）を自然に組み合わせて使うこと
・ビジネスメール・報告書・依頼文・議事録など実際の実務場面で使われる表現を中心にすること
・文末表現の偏り禁止：「〜申し上げます」「〜所存です」「〜いたします」「〜させていただきます」「〜てまいります」など、同じ文末表現は文書全体で2回まで。同じ文末を2文連続で使わない
・謙譲語だけに偏らず、読み手・相手の行為への尊敬語（ご覧になる・おっしゃる・お越しになる・ご確認なさる等）も3文以上で使うこと
・感情描写や登場人物の心理描写は禁止"""
        scene_instruction = "に関するビジネス文書（敬語を用いた実務文）"

    elif is_keigo:
        # 경어 레벨이지만 논술 주제(RSS 뉴스 등) → である 논술체
        style_instruction = f"""【文体】
{_STYLE_RONJUTSU_RULES}
・難解な四字熟語・文語体・古典語・日常では使わない専門語は使わない"""
        scene_instruction = "に関する解説記事・論説文（である調）"

    else:
        # N2 / N3 / JPT600 / JPT700
        style_instruction = f"""【文体】
・新聞記事・解説記事・寄稿文など、外部に公表する文書形式で書く
・会話文（「〜」と言った／と述べた）は一切使わない
{_STYLE_RONJUTSU_RULES}"""
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

【今日の書き方 — 本日のみの指定】
{seed_lines}

【出力ルール — 全て絶対厳守】
1. 文章のみを出力する（タイトル・ヘッダー・番号・説明・コメント禁止）
2. マークダウン記号（**、##など）は一切使用しない
3. 20文出力する（少なくても多くても禁止）
4. 1行に1文のみ、改行で区切る
5. 各文は必ず「。」で終わること
6. 文が途中で切れることは絶対禁止
7. 会話文・引用符（「」）は一切使わない

今すぐ20文の読み物を書いてください："""

    if ab_mode:
        prompt = f"""あなたは日本語教師です。JLPTの統合理解問題のように、同じテーマについて立場の異なるAとBの2つの意見文を書きます。レベルは{lv['desc']}です。

【テーマ】「{theme}」
【立場の構図】{ab_stance}

【語彙制限 — 絶対厳守】
{lv['vocab']}
※ 上記レベル外の語彙・専門用語は一切使用禁止

【使用する文法パターン】
{lv['grammar']}

【文体】
・AもBもだ・である調で統一する
・会話文・引用符（「」）は一切使わない
・AとBは同じ事実を扱いながら、明確に異なる立場を取ること
・それぞれの最後の1〜2文に、相手の立場への間接的な反論を含めること

【出力ルール — 全て絶対厳守】
1. 1行目に【Aの意見】とだけ書く
2. 2行目からAの意見文を10文書く（1行1文）
3. 次の行に【Bの意見】とだけ書く
4. その後Bの意見文を10文書く（1行1文）
5. 見出し2行以外の各文は必ず「。」で終わること
6. マークダウン記号・番号・タイトル・説明・コメントは一切禁止

今すぐ書いてください："""

    temp = _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
    print(f"[온도 사다리] attempt {attempt + 1} → temperature={temp}")
    raw = _call_gemini(prompt, temperature=temp, max_tokens=4096)
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

    if len(raw_lines) < 8:
        print(f"[경고] Gemini 응답 줄수 부족 ({len(raw_lines)}줄) — 절단 응답 → 재시도")
        return []

    print(f"Gemini raw output (attempt {attempt + 1}, {len(raw_lines)} lines):")
    for i, l in enumerate(raw_lines, 1):
        print(f"  {i}. {l[:80]}")

    return raw_lines

# ── 메인 흐름 ─────────────────────────────────────────
def _retry_theme(label: str, tried_titles: set) -> str:
    """재시도용 새 주제를 주제 풀에서 선택 (중복 회피). 호출부 2곳 공용."""
    new_theme = random.choice(_get_topic_pool(label))
    while new_theme in tried_titles and len(tried_titles) < 10:
        new_theme = random.choice(_get_topic_pool(label))
    tried_titles.add(new_theme)
    return new_theme

def fetch_study_lines(label: str, force_business: bool = False,
                      force_plain: bool = False) -> tuple:
    """
    N3/N4: 주제 풀 → 바로 문장 생성
    N2 이상: NHK RSS → 제목 선택 → 문장 생성
    Gemini 503/안전필터 차단 시 → 다른 주제로 재시도
    force_business: 수동 실행 'JLPT N1(경어)/N0(경어)' 선택 시 경어 문서 모드 강제
    force_plain:    수동 실행 무표기 N1/N0 선택 시 통상문서(논술체) 강제 (경어 굴림 생략)
    """
    use_rss = label in RSS_LEVELS
    keigo_business = False

    # KEIGO_LEVELS: 비즈니스 주제 강제 → 경어 발동 보장
    # JLPT N1/N0: 75% (BJT 대응 강화), JPT 800/900: 50%
    _bjt_levels = {"JLPT N1", "JLPT N0"}
    _keigo_threshold = 0.75 if label in _bjt_levels else 0.5
    if (label in KEIGO_LEVELS and not force_plain
            and (force_business or random.random() < _keigo_threshold)):
        selected_title, selected_url = pick_topic(label)
        title_pairs = [(selected_title, selected_url)]
        _how = "수동 강제" if force_business else f"확률 {int(_keigo_threshold*100)}%"
        _rlog(f"[경어 모드] 비즈니스 주제 선택({_how}): {selected_title}")
        use_rss = False
        keigo_business = True

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
    elif not keigo_business:
        selected_title, selected_url = pick_topic(label)
        title_pairs = [(selected_title, selected_url)]

    _rlog(f"테마 확정: {selected_title}")

    sentences = []
    tried_titles = {selected_title}

    _MAX_ATTEMPTS = 4
    for attempt in range(_MAX_ATTEMPTS):
        raw_lines = write_story_with_gemini(selected_title, label, attempt=attempt,
                                            business_doc=keigo_business)

        if raw_lines:
            sentences = validate_sentences(raw_lines, label, keigo_doc=keigo_business)
            if sentences:
                return selected_title, selected_url, sentences

            if use_rss and len(title_pairs) > 1:
                remaining = [(t, u) for t, u in title_pairs if t not in tried_titles]
                if remaining:
                    selected_title, selected_url = random.choice(remaining)
                    tried_titles.add(selected_title)
                    print(f"[안전필터 차단 의심] 새 제목으로 교체: {selected_title}")
                    continue
        else:
            print("[중단] Gemini 응답 없음 — 다른 주제로 재시도")

        selected_title = _retry_theme(label, tried_titles)
        selected_url = ""
        if label in KEIGO_LEVELS and not force_plain:
            keigo_business = True
        _rlog(f"[재시도 {attempt + 1}/{_MAX_ATTEMPTS}] 새 주제: {selected_title}")

    return selected_title, selected_url, sentences

# ── 레벨 선택: 주간 셔플 백 (랜덤성 + 순환 보장) ──────
def _plan_for_date(dt: datetime.date) -> list:
    """해당 날짜의 주 모드에 따른 레벨 plan.
    주 단위 JPT/JLPT 구분은 get_week_of_month 홀짝으로 확정적 (근간 — 불변)."""
    return JPT_PLAN[:] if get_week_of_month(dt) % 2 == 1 else JLPT_PLAN[:]

def _weekly_bag(dt: datetime.date) -> list:
    """그 주 월요일 날짜를 시드로 plan을 셔플한 '이번 주 레벨 순서'.
    같은 주에는 순서가 고정되고, 주가 바뀌면 새로 섞인다 (상태 저장 불필요)."""
    monday = dt - datetime.timedelta(days=dt.weekday())
    bag = _plan_for_date(dt)
    random.Random(monday.toordinal()).shuffle(bag)
    return bag

def pick_level(today: datetime.date) -> str:
    """주간 셔플 백 방식 레벨 선택.
    - JPT주(7레벨/7일): 한 주에 모든 레벨이 정확히 1회, 순서는 주마다 랜덤
    - JLPT주(5레벨/7일): 5레벨 전부 등장 + 2개 레벨만 2회 (어느 레벨인지도 주마다 랜덤)
    - 요일-레벨 고정 패턴 없음 (단순 날짜 순환의 패턴화 문제 해결)
    - 주 경계 가드: 지난주 마지막 날과 같은 레벨로 이번 주가 시작되면 앞 2개 스왑
      (가드 계산이 요일과 무관하게 주 단위로 동일 → 주 내 순열 일관성 유지)
    """
    bag = _weekly_bag(today)
    if len(bag) > 1:
        monday = today - datetime.timedelta(days=today.weekday())
        prev_sunday = monday - datetime.timedelta(days=1)
        prev_bag = _weekly_bag(prev_sunday)
        prev_label = prev_bag[prev_sunday.weekday() % len(prev_bag)]
        if bag[0] == prev_label:
            bag[0], bag[1] = bag[1], bag[0]
    return bag[today.weekday() % len(bag)]

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
        if line in _SECTION_HEADERS:
            pdf.ln(2)
            pdf.set_fill_color(234, 240, 255)
            pdf.set_font("JP", size=12)
            pdf.cell(0, 8, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
            pdf.set_font("JP", size=11)
            pdf.ln(1)
            continue
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
        if MANUAL_RUN:
            # 수동 실행: hexartrion@gmail.com 단독 수신
            recipients = [MANUAL_MAIL_TO]
        else:
            recipients = [GMAIL_ADDRESS]
            for addr in re.split(r"[,;\s]+", EMAIL_RECIPIENTS):
                addr = addr.strip()
                if addr and addr not in recipients:
                    recipients.append(addr)
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = ", ".join(recipients)
        _test_tag = " [TEST]" if MANUAL_RUN else ""
        msg["Subject"] = f"[Japanese Study]{_test_tag} {date_str} — {label}"
        # 본문은 기존 형태 유지. 수동 실행 시 테스트 안내 한 줄만 추가
        # (레벨·주제 상세와 생성 로그는 결과 알림 메일(kwonyh000@naver.com) 전용)
        body = f"Today's Japanese study material.\nLevel: {label}\nMode: {mode}"
        if MANUAL_RUN:
            body += "\n이 메일은 테스트용 메일 입니다"
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
        print(f"Email sent → {', '.join(recipients)}")
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

    force_level = os.environ.get("FORCE_LEVEL", "").strip()
    level_choice = os.environ.get("LEVEL_CHOICE", "").strip()
    all_levels = JPT_PLAN + JLPT_PLAN
    force_business = False
    force_plain = False

    # 이전 실행 로그/메타 파일 초기화
    for _p in (RUN_LOG_FILE, RUN_META_FILE):
        try:
            open(_p, "w", encoding="utf-8").close()
        except OSError:
            pass

    if MANUAL_RUN and level_choice in ("JLPT N1(경어)", "JLPT N0(경어)"):
        # 수동 실행: 경어 전용 카테고리 → 비즈니스 상황에서만 경어 문서 생성
        label = level_choice.replace("(경어)", "")
        force_business = True
        _rlog(f"[수동 실행] 경어 카테고리 지정: {level_choice} → 기준 레벨 {label}, 비즈니스 경어 강제")
    elif MANUAL_RUN and level_choice in all_levels:
        # 수동 실행: 기존 로직 무시하고 지정 레벨 사용
        label = level_choice
        # N1/N0(무표기)는 경어 확률 굴림 없이 항상 통상문서(논술체)로 생성
        force_plain = label in KEIGO_LEVELS
        _rlog(f"[수동 실행] 레벨 지정: {label} (주간 로직 무시"
              + (", 통상문서 강제" if force_plain else "") + ")")
    elif force_level in all_levels:
        label = force_level
        print(f"[FORCE_LEVEL] {label} 강제 지정")
    else:
        # 주간 셔플 백: 주 모드(JPT/JLPT)는 위에서 확정, 주 안에서만 랜덤 순환
        label = pick_level(today)
    print(f"Today: {label} | {week_label}")

    title, url, sentences = fetch_study_lines(label, force_business=force_business,
                                              force_plain=force_plain)

    if not sentences:
        raise RuntimeError(
            "[중단] 유효한 문장이 없어 PDF/이메일 전송을 건너뜁니다."
        )

    print(f"Lines validated: {len(sentences)}")

    # 수동 실행 + 경어 카테고리: 메일 표기는 "JLPT N1[경어]", PDF 헤더는 "JLPT N1[敬語]"
    _is_keigo_cat = MANUAL_RUN and force_business
    mail_label = f"{label}[경어]" if _is_keigo_cat else label
    pdf_label = f"{label}[敬語]" if _is_keigo_cat else label

    # 수동 실행: 테스트 정보 블록 기록 → 결과 알림 메일(kwonyh000@naver.com)에 첨부
    if MANUAL_RUN:
        try:
            with open(RUN_META_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join([
                    f"지정 레벨    : {level_choice or '(자동 — 기존 로직)'}",
                    f"적용 레벨    : {mail_label}",
                    f"세부 카테고리 : {LEVEL_DESC.get(label, {}).get('desc', label)}",
                    f"모드         : {'비즈니스 경어' if force_business else mode}",
                    f"주제         : {title}",
                ]) + "\n")
        except OSError:
            pass

    build_pdf(pdf_label, title, url, sentences, date_str, week_label, mode)
    send_email(date_str, mail_label, mode)
    print("Done!")

if __name__ == "__main__":
    main()
