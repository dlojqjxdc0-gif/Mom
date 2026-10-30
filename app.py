"""장보기 알림메모 — 실행: streamlit run app.py
배포: GitHub에 app.py와 requirements.txt 업로드 → Streamlit Community Cloud 연결.
선택: Streamlit Secrets에 APP_URL = "https://your-app.streamlit.app" 설정.
SQLite는 실행 서버에 저장됩니다. Community Cloud의 재배포/절전/인스턴스 교체 시
데이터 보존을 보장하지 않습니다. QR 링크 보유자는 같은 목록을 열고 수정할 수 있습니다.
"""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
import io
import json
import math
import os
import sqlite3
import uuid

import qrcode
import streamlit as st

st.set_page_config(page_title="장보기 알림메모", page_icon="🛒", layout="centered", initial_sidebar_state="collapsed")
DB_PATH = Path(os.environ.get("SHOPPING_DB_PATH", "shopping.sqlite3"))
CATEGORIES = ["🥬 야채", "🍎 과일", "🥩 정육·달걀", "🐟 수산", "🥛 유제품", "🍚 쌀·잡곡", "🥫 가공식품", "🧊 냉동식품", "🧴 생활용품", "📦 기타"]
PRIORITIES = {1: "꼭 구매", 2: "가격 보고", 3: "여유 되면"}
PAGE_SIZE = 2

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700&display=swap');
html, body, [class*="css"], .stApp {font-family:'Noto Sans KR',sans-serif;}
.stApp {background:#f7f9f5;color:#193329;}
.block-container {max-width:520px;padding:0.65rem 0.85rem 0.5rem!important;}
header[data-testid="stHeader"] {display:none;}
[data-testid="stToolbar"], #MainMenu, footer {display:none;}
[data-testid="stVerticalBlock"] {gap:0.40rem;}
[data-testid="stHorizontalBlock"] {gap:0.45rem;flex-wrap:nowrap!important;}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width:0!important;}
.stButton > button {min-height:2.35rem;border-radius:12px;font-size:0.88rem;padding:.25rem .35rem;}
.stButton > button[kind="primary"] {background:#287951;border-color:#287951;}
[data-testid="stMetric"] {background:white;border-radius:14px;padding:.35rem .5rem;}
[data-testid="stMetricLabel"] {font-size:.75rem;}
[data-testid="stMetricValue"] {font-size:1.2rem;}
h3 {font-size:1.15rem!important;padding:.1rem 0!important;}
p {margin-bottom:.15rem;}
[data-testid="stCaptionContainer"] {font-size:.72rem;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius:14px!important;}
[data-testid="stDialog"] [role="dialog"] {max-height:90dvh;overflow-y:auto;}
@media(max-height:650px) {
 .block-container {padding-top:.3rem!important;}
 [data-testid="stVerticalBlock"] {gap:.23rem;}
 .stButton > button {min-height:2rem;}
}
</style>""", unsafe_allow_html=True)


def connection():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=20000")
    return con


with connection() as con:
    con.execute("""CREATE TABLE IF NOT EXISTS items (
        id TEXT PRIMARY KEY, list_id TEXT NOT NULL, name TEXT NOT NULL,
        category TEXT NOT NULL, priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 3),
        target INTEGER, status TEXT NOT NULL DEFAULT 'todo',
        unit_price INTEGER, quantity TEXT, created TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_list ON items(list_id, status)")

raw_id = st.query_params.get("list", "")
try:
    LIST_ID = str(uuid.UUID(raw_id))
except (ValueError, TypeError, AttributeError):
    LIST_ID = str(uuid.uuid4())
    st.query_params["list"] = LIST_ID


def rows():
    with connection() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM items WHERE list_id=? ORDER BY priority, created", (LIST_ID,))]


def update(item_id, **values):
    allowed = {"name", "category", "priority", "target", "status", "unit_price", "quantity"}
    if not values or not set(values).issubset(allowed):
        raise ValueError("올바르지 않은 수정 항목입니다.")
    with connection() as con:
        con.execute("UPDATE items SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=? AND list_id=?",
                    (*values.values(), item_id, LIST_ID))


def amount(item):
    return int((Decimal(str(item.get("unit_price") or 0)) * Decimal(str(item.get("quantity") or 0)))
               .quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def won(value):
    return f"{value:,}원"


def refresh(message=None):
    if message:
        st.session_state["notice"] = message
    st.rerun()


@st.dialog("📝 장볼 상품", width="small")
def item_dialog(item=None):
    item = item or {}
    with st.form("item_form"):
        category = st.selectbox("카테고리", CATEGORIES,
                               index=CATEGORIES.index(item.get("category", CATEGORIES[0])))
        name = st.text_input("상품명", value=item.get("name", ""), max_chars=50, placeholder="예: 애호박 2개 / 우유 1L")
        priority = st.radio("중요도", [1, 2, 3], index=item.get("priority", 2) - 1,
                            format_func=lambda p: f"{p} {PRIORITIES[p]}", horizontal=True)
        target = st.number_input("살 만한 최대 단가 (원 · 0이면 미설정)", min_value=0,
                                 max_value=100000000, value=int(item.get("target") or 0), step=100)
        st.caption("2·3순위는 비싸면 보류하세요. 최대 단가는 입력한 수량 단위 1개 기준입니다.")
        save = st.form_submit_button("저장", type="primary", use_container_width=True)
    if save:
        if not name.strip():
            st.error("상품명을 입력해 주세요.")
            return
        if item.get("id"):
            update(item["id"], name=name.strip(), category=category, priority=priority, target=target or None)
        else:
            with connection() as con:
                con.execute("INSERT INTO items (id,list_id,name,category,priority,target,created) VALUES (?,?,?,?,?,?,?)",
                            (str(uuid.uuid4()), LIST_ID, name.strip(), category, priority, target or None,
                             datetime.now(timezone.utc).isoformat()))
        refresh("메모를 저장했어요")
    if item.get("id"):
        confirm = st.checkbox("이 메모를 완전히 삭제합니다", key="delete_confirm")
        if st.button("메모 삭제", disabled=not confirm, use_container_width=True):
            with connection() as con:
                con.execute("DELETE FROM items WHERE id=? AND list_id=?", (item["id"], LIST_ID))
            refresh("메모를 삭제했어요")


@st.dialog("🛒 가격·수량 확인", width="small")
def purchase_dialog(item):
    st.write(f"**{item['name']}** · {item['priority']}순위 {PRIORITIES[item['priority']]}")
    st.caption("수량은 개·팩·kg 등 원하는 단위로 입력하고, 단가는 같은 단위의 가격을 적으세요.")
    unit = st.number_input("단가 (원)", min_value=0, max_value=100000000,
                            value=int(item.get("unit_price") or 0), step=100, key=f"purchase_price_{item['id']}")
    qty = st.number_input("수량 (소수 가능)", min_value=0.01, max_value=100000.0,
                           value=float(item.get("quantity") or 1), step=0.1, format="%.2f", key=f"purchase_quantity_{item['id']}")
    total = amount({"unit_price": unit, "quantity": str(qty)})
    st.metric("이 상품 합계", won(total))
    over = item.get("target") and unit > item["target"]
    if over:
        st.warning(f"정해둔 단가 {won(item['target'])}보다 비싸요. 구매하지 않고 보류할 수 있어요.")
    elif item["priority"] >= 2:
        st.info("지금 비싸게 느껴지면 보류해도 괜찮아요.")
    a, b = st.columns(2)
    if a.button("담기 완료" if item["status"] != "cart" else "금액 수정", type="primary", use_container_width=True):
        update(item["id"], status="cart", unit_price=int(unit), quantity=str(qty))
        refresh("카트 금액을 반영했어요")
    if b.button("비싸서 보류", use_container_width=True):
        update(item["id"], status="hold", unit_price=None, quantity=None)
        refresh("메모를 남기고 보류했어요")
    st.caption("합계는 원 단위 반올림입니다. 0원 상품도 기록할 수 있습니다.")


@st.dialog("🔗 내 목록 QR · 백업", width="small")
def share_dialog():
    st.warning("이 링크나 QR을 가진 사람은 목록을 보고 수정할 수 있어요. 공개 게시하지 마세요.")
    try:
        default_url = st.secrets.get("APP_URL", "")
    except Exception:
        default_url = ""
    base = st.text_input("배포된 앱 주소", value=st.session_state.get("public_url", default_url),
                         placeholder="https://my-shopping.streamlit.app")
    if base:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parsed = urlsplit(base.strip())
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            st.error("https://로 시작하는 올바른 앱 주소를 넣어 주세요.")
        else:
            st.session_state["public_url"] = base.strip()
            params = dict(parse_qsl(parsed.query))
            params["list"] = LIST_ID
            link = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ""))
            image = qrcode.make(link)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            st.image(buf.getvalue(), width=200)
            st.code(link, language=None)
            st.download_button("QR 이미지 저장", buf.getvalue(), "shopping-qr.png", "image/png", use_container_width=True)
    data = json.dumps({"version": 1, "items": rows()}, ensure_ascii=False, indent=2)
    st.download_button("목록 백업(JSON)", data, "shopping-backup.json", "application/json", use_container_width=True)
    st.caption("서버 임시 저장입니다. 재배포·서버 교체 시 사라질 수 있어 중요한 기록은 백업하세요.")
    uploaded = st.file_uploader("백업 복원 (현재 목록에 추가)", type=["json"])
    if uploaded and st.button("백업 복원", use_container_width=True):
        try:
            if uploaded.size > 2_000_000:
                raise ValueError("백업 파일은 2MB 이하만 가능합니다.")
            data = json.load(uploaded)
            restored = data["items"]
            if not isinstance(restored, list) or len(restored) > 1000:
                raise ValueError("최대 1,000개 항목까지 복원할 수 있습니다.")
            validated = []
            for r in restored:
                name = str(r["name"]).strip()
                category = r["category"]
                priority = int(r["priority"])
                status = r["status"]
                target = int(r.get("target") or 0)
                price = int(r.get("unit_price") or 0)
                quantity = Decimal(str(r.get("quantity") or 1))
                if (not 1 <= len(name) <= 50 or category not in CATEGORIES or priority not in PRIORITIES
                    or status not in {"todo", "hold", "cart"} or not 0 <= target <= 100000000
                    or not 0 <= price <= 100000000 or not quantity.is_finite() or not Decimal(".01") <= quantity <= 100000):
                    raise ValueError("백업에 허용되지 않은 값이 있습니다.")
                validated.append((str(uuid.uuid4()), LIST_ID, name, category, priority, target or None,
                                  status, price if status == "cart" else None,
                                  str(quantity) if status == "cart" else None, datetime.now(timezone.utc).isoformat()))
            with connection() as con:
                con.executemany("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?)", validated)
            refresh("백업을 추가했어요")
        except (ValueError, KeyError, TypeError, ArithmeticError):
            st.error("올바른 장보기 백업 파일인지 확인해 주세요.")


@st.dialog("✅ 장보기 마무리", width="small")
def finish_dialog():
    cart = [r for r in rows() if r["status"] == "cart"]
    st.metric("이번 카트 합계", won(sum(amount(r) for r in cart)))
    st.caption("카트 기록만 비웁니다. 미구매·보류 메모는 남습니다. 보관하려면 먼저 QR·백업에서 내려받으세요.")
    confirm = st.checkbox("구매 기록을 비우겠습니다")
    if st.button("카트 비우기", type="primary", disabled=not confirm, use_container_width=True):
        with connection() as con:
            con.execute("DELETE FROM items WHERE list_id=? AND status='cart'", (LIST_ID,))
        refresh("장보기를 마쳤어요")


if "notice" in st.session_state:
    st.toast(st.session_state.pop("notice"))
all_items = rows()
cart = [r for r in all_items if r["status"] == "cart"]
remaining = [r for r in all_items if r["status"] == "todo"]
held = [r for r in all_items if r["status"] == "hold"]
a, b = st.columns([3, 1])
a.markdown("### 🛒 장보기 알림메모")
if b.button("QR·백업", use_container_width=True):
    share_dialog()
a, b, c = st.columns([1.6, 1, 1])
a.metric("현재 카트 총액", won(sum(amount(r) for r in cart)))
b.metric("담은 상품", f"{len(cart)}종")
c.metric("남은 메모", f"{len(remaining)}개")
nav, add = st.columns([3, 1])
with nav:
    section = st.radio("보기", ["메모", "카트", "보류"], horizontal=True, label_visibility="collapsed")
if add.button("＋ 추가", type="primary", use_container_width=True):
    item_dialog()
filtered = {"메모": remaining, "카트": cart, "보류": held}[section]
page_key = f"page_{section}"
page_count = max(1, math.ceil(len(filtered) / PAGE_SIZE))
page = min(max(st.session_state.get(page_key, 0), 0), page_count - 1)
st.session_state[page_key] = page
st.caption({"메모": "1 꼭 구매 · 2 가격 보고 · 3 여유 되면", "카트": "합계는 단가 × 수량 · 수정/되돌리기 가능", "보류": "비싸서 미룬 상품 · 다음 장보기에 다시 확인"}[section])
if not filtered:
    with st.container(border=True):
        st.write({"메모": "🌱 필요한 상품을 추가해 보세요.", "카트": "🛒 담은 상품이 아직 없어요.", "보류": "👌 보류한 상품이 없어요."}[section])
        st.caption("상품을 담으면 가격과 수량이 함께 기록됩니다.")
for item in filtered[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
    with st.container(border=True):
        # Text is rendered as text, not interpolated into unsafe HTML.
        st.text(item["name"])
        if section == "카트":
            st.caption(f"{item['category']} · {won(item['unit_price'])} × {item['quantity']} = {won(amount(item))}")
        else:
            limit = f" · ≤ {won(item['target'])}" if item["target"] else ""
            st.caption(f"{item['category']} · {item['priority']} {PRIORITIES[item['priority']]}{limit}")
        x, y, z = st.columns([1.3, 1, 1])
        if section == "카트":
            if x.button("금액 수정", key=f"price_{item['id']}", use_container_width=True):
                purchase_dialog(item)
            if y.button("되돌리기", key=f"undo_{item['id']}", use_container_width=True):
                update(item["id"], status="todo", unit_price=None, quantity=None)
                refresh("메모로 되돌렸어요")
            if z.button("상품 수정", key=f"editcart_{item['id']}", use_container_width=True):
                item_dialog(item)
        else:
            if x.button("카트 담기", key=f"buy_{item['id']}", type="primary", use_container_width=True):
                purchase_dialog(item)
            label = "다시 메모" if section == "보류" else "보류"
            if y.button(label, key=f"hold_{item['id']}", use_container_width=True):
                update(item["id"], status="todo" if section == "보류" else "hold")
                refresh()
            if z.button("수정·삭제", key=f"edit_{item['id']}", use_container_width=True):
                item_dialog(item)
a, b, c = st.columns([1, 2, 1])
if a.button("◀", disabled=page == 0, use_container_width=True):
    st.session_state[page_key] = page - 1
    st.rerun()
b.caption(f"{page + 1} / {page_count} 페이지 · {len(filtered)}개")
if c.button("▶", disabled=page >= page_count - 1, use_container_width=True):
    st.session_state[page_key] = page + 1
    st.rerun()
if section == "카트" and cart:
    if st.button("✅ 장보기 마무리", use_container_width=True):
        finish_dialog()
st.caption("개인정보는 적지 마세요 · QR 링크는 목록 열쇠입니다")
