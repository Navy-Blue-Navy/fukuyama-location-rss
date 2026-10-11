import requests
import re
import time
import hashlib
import xml.etree.ElementTree as ET

from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime


SOURCE_URL = "https://www.fukuyama-kanko.com/filmcommission/jiseki/"
OUTPUT = Path(__file__).parent / "fukuyama_location.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}

MAX_RETRIES = 3


# --------------------------------------------------
# HTTP取得
# --------------------------------------------------

def fetch(url):

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            print(f"取得 {attempt}/{MAX_RETRIES}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )

            print("HTTP:", response.status_code)

            response.raise_for_status()

            return response

        except requests.RequestException as e:

            print("取得失敗:", e)

            if attempt < MAX_RETRIES:

                print("10秒待って再試行します。")
                time.sleep(10)

    return None


# --------------------------------------------------
# 既存RSS
# --------------------------------------------------

old_items = {}

if OUTPUT.exists():

    try:

        tree = ET.parse(OUTPUT)

        for item in tree.getroot().findall("./channel/item"):

            guid = item.findtext("guid", "")

            if not guid:
                continue

            old_items[guid] = {
                "title": item.findtext("title", ""),
                "link": item.findtext("link", ""),
                "description": item.findtext("description", ""),
                "pubDate": item.findtext("pubDate", ""),
                "guid": guid,
            }

    except Exception as e:

        print("既存RSS読み込みエラー:", e)
        print("安全のため今回は更新しません。")

        raise SystemExit(0)


print("既存RSS件数:", len(old_items))


# --------------------------------------------------
# 公式ページ取得
# --------------------------------------------------

response = fetch(SOURCE_URL)

if response is None:

    print("サイトを取得できないため、既存XMLを維持します。")
    raise SystemExit(0)


soup = BeautifulSoup(
    response.content,
    "html.parser"
)


container = soup.select_one("#jiseki_list")

if container is None:

    print("#jiseki_list が見つかりません。")
    print("サイト構造変更の可能性があるため、既存XMLを維持します。")

    raise SystemExit(0)


blocks = container.select("div.unit.clearfix")

print("ロケ実績候補:", len(blocks), "件")


if len(blocks) < 50:

    print("取得件数が異常に少ないため、既存XMLを維持します。")
    raise SystemExit(0)


# --------------------------------------------------
# タイトル抽出
# --------------------------------------------------

TITLE_PATTERNS = [
    r"番組名[：:]\s*(.+)",
    r"タイトル[：:]\s*(.+)",
    r"CM名[：:]\s*(.+)",
    r"会報誌タイトル[：:]\s*(.+)",
    r"PR動画タイトル[：:]\s*(.+)",
    r"ページタイトル[：:]\s*(.+)",
]


def extract_title(block, number):

    # まず画像altを利用
    image = block.find("img")

    if image:

        alt = image.get("alt", "").strip()

        if alt:
            return alt


    # 画像altがない場合は本文から取得
    lines = [
        re.sub(r"\s+", " ", x).strip()
        for x in block.stripped_strings
    ]

    for line in lines:

        for pattern in TITLE_PATTERNS:

            match = re.search(
                pattern,
                line
            )

            if match:

                title = match.group(1).strip()

                if title:
                    return title


    return f"ロケ実績 No.{number}"


# --------------------------------------------------
# 日付抽出
# --------------------------------------------------

DATE_PATTERNS = [
    r"放送日[：:]\s*(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
    r"公開日[：:]\s*(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
    r"配信開始日[：:]\s*(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
    r"撮影日[：:]\s*(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
    r"収録日[：:]\s*(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
]


def extract_date(text):

    # 全角数字対策
    normalized = text.translate(
        str.maketrans(
            "０１２３４５６７８９",
            "0123456789"
        )
    )

    for pattern in DATE_PATTERNS:

        match = re.search(
            pattern,
            normalized
        )

        if not match:
            continue

        try:

            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                12,
                0,
                0,
                tzinfo=JST
            )

        except ValueError:
            continue

    return None


# --------------------------------------------------
# 各ロケ実績解析
# --------------------------------------------------

current_items = []


for block in blocks:

    no_element = block.select_one("span.no")

    if no_element is None:
        continue


    no_text = no_element.get_text(
        " ",
        strip=True
    )


    match = re.fullmatch(
        r"No\.?\s*(\d+)",
        no_text,
        flags=re.I
    )

    if not match:
        continue


    number = int(match.group(1))


    text = block.get_text(
        " ",
        strip=True
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()


    title = extract_title(
        block,
        number
    )


    # --------------------------------------------------
    # 種別
    # No.の直後にある文字列を取得
    # --------------------------------------------------

    category = ""

    strings = [
        re.sub(r"\s+", " ", x).strip()
        for x in block.stripped_strings
    ]


    for i, value in enumerate(strings):

        if re.fullmatch(
            rf"No\.?\s*{number}",
            value,
            flags=re.I
        ):

            if i + 1 < len(strings):

                possible_category = strings[i + 1]

                if len(possible_category) <= 30:
                    category = possible_category

            break


    # --------------------------------------------------
    # リンク
    #
    # 各作品に固有ページがないので
    # RSSからは公式ロケ実績ページへ戻す
    # --------------------------------------------------

    link = SOURCE_URL


    # --------------------------------------------------
    # 日付
    #
    # 日付があるものは表示用として利用。
    # 日付がない古い記事にも対応。
    # --------------------------------------------------

    real_date = extract_date(text)


    # RSSではNo.順を絶対に維持したい。
    #
    # 実際の日付をpubDateにすると
    # 撮影日・放送日・公開日の違いで
    # No.順が崩れる。
    #
    # そのため固定基準日からNo.に応じた
    # 仮想日時を割り当てる。
    #
    # No.87 > No.86 > No.85 ...
    # が必ず維持される。
    # --------------------------------------------------

    base_date = datetime(
        2000,
        1,
        1,
        12,
        0,
        0,
        tzinfo=JST
    )

    pub_dt = base_date + timedelta(
        days=number
    )


    # --------------------------------------------------
    # GUID
    # No.そのものを一意IDとして固定
    # --------------------------------------------------

    guid_source = (
        f"fukuyama-location|{number}"
    )

    guid = hashlib.sha256(
        guid_source.encode("utf-8")
    ).hexdigest()


    # --------------------------------------------------
    # 説明文
    # --------------------------------------------------

    description_parts = [
        f"No.{number}"
    ]


    if category:
        description_parts.append(
            f"種別：{category}"
        )


    if real_date is not None:

        description_parts.append(
            "作品情報の日付："
            + real_date.strftime("%Y年%m月%d日")
        )


    # 本文も残す
    clean_text = re.sub(
        rf"^No\.?\s*{number}\s*",
        "",
        text,
        flags=re.I
    ).strip()


    if clean_text:

        description_parts.append(
            clean_text
        )


    description = "\n".join(
        description_parts
    )


    current_items.append({
        "number": number,
        "title": f"No.{number} {title}",
        "link": link,
        "description": description,
        "pubDate": format_datetime(pub_dt),
        "guid": guid,
    })


# --------------------------------------------------
# 安全確認
# --------------------------------------------------

if len(current_items) < 50:

    print(
        "有効なロケ実績が異常に少ないため、"
        "既存XMLを維持します。"
    )

    raise SystemExit(0)


# --------------------------------------------------
# No.重複除去
# --------------------------------------------------

unique = {}

for item in current_items:

    unique[item["number"]] = item


current_items = list(
    unique.values()
)


current_items.sort(
    key=lambda x: x["number"],
    reverse=True
)


print(
    "有効なロケ実績:",
    len(current_items),
    "件"
)


print(
    "No.範囲:",
    f"No.{current_items[-1]['number']}",
    "～",
    f"No.{current_items[0]['number']}"
)


# --------------------------------------------------
# 既存RSSと統合
# --------------------------------------------------

all_items = []
seen = set()


for item in current_items:

    all_items.append(item)
    seen.add(item["guid"])


for guid, item in old_items.items():

    if guid in seen:
        continue

    # 古いRSS項目にはnumberがないので
    # 並び替え用にタイトルからNo.を取得
    match = re.search(
        r"No\.?\s*(\d+)",
        item["title"]
    )

    if match:
        number = int(match.group(1))
    else:
        number = 0


    item["number"] = number

    all_items.append(item)
    seen.add(guid)


all_items.sort(
    key=lambda x: x["number"],
    reverse=True
)


# --------------------------------------------------
# RSS作成
# --------------------------------------------------

rss = ET.Element(
    "rss",
    version="2.0"
)

channel = ET.SubElement(
    rss,
    "channel"
)


ET.SubElement(
    channel,
    "title"
).text = (
    "福山観光コンベンション協会 ロケ実績"
)


ET.SubElement(
    channel,
    "link"
).text = SOURCE_URL


ET.SubElement(
    channel,
    "description"
).text = (
    "福山観光コンベンション協会 "
    "フィルムコミッション「ロケ実績」の更新情報"
)


ET.SubElement(
    channel,
    "language"
).text = "ja"


# --------------------------------------------------
# item
# --------------------------------------------------

for item in all_items:

    element = ET.SubElement(
        channel,
        "item"
    )


    ET.SubElement(
        element,
        "title"
    ).text = item["title"]


    ET.SubElement(
        element,
        "link"
    ).text = item["link"]


    ET.SubElement(
        element,
        "description"
    ).text = item["description"]


    ET.SubElement(
        element,
        "pubDate"
    ).text = item["pubDate"]


    guid_element = ET.SubElement(
        element,
        "guid"
    )

    guid_element.set(
        "isPermaLink",
        "false"
    )

    guid_element.text = item["guid"]


# --------------------------------------------------
# XML保存
# --------------------------------------------------

tree = ET.ElementTree(rss)

ET.indent(
    tree,
    space="  "
)

tree.write(
    OUTPUT,
    encoding="utf-8",
    xml_declaration=True
)


# --------------------------------------------------
# 結果
# --------------------------------------------------

print()
print("=" * 70)
print("RSS作成成功")
print("=" * 70)

print(
    "現在サイトから取得:",
    len(current_items),
    "件"
)

print(
    "RSS保存件数:",
    len(all_items),
    "件"
)

print(
    "保存先:",
    OUTPUT
)

print()
print("最新20件:")


for i, item in enumerate(
    all_items[:20],
    start=1
):

    print()

    print(
        f"[{i}] "
        f"{item['title']}"
    )

    lines = item["description"].splitlines()

    for line in lines[:4]:

        print(
            "    ",
            line
        )