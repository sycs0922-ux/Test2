"""쇼핑 리스트 Streamlit 앱 (PRD.md 참고)."""

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

EMPTY_MESSAGE = "쇼핑 리스트가 비어 있어요. 아이템을 추가해 보세요!"
EMPTY_NAME_WARNING = "아이템 이름을 입력해 주세요."
FILTER_ALL = "전체"
FILTER_REMAINING = "남은 물건"
FILTER_DONE = "구매 완료"

ITEM_MISSING_WARNING = "다른 화면에서 이미 삭제된 아이템이에요."

# 아이템을 저장하는 파일. 새로고침하거나 앱을 다시 켜도 목록이 유지된다.
DATA_FILE = Path(os.environ.get("SHOPPING_LIST_FILE", Path(__file__).parent / "shopping_list.json"))
# 여러 탭/사용자(=여러 스레드)가 동시에 파일을 고치지 않도록 하는 잠금
_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 저장소 (JSON 파일)
# ---------------------------------------------------------------------------
def _read_file():
    """파일에서 아이템 목록을 읽는다. 잠금을 잡은 상태에서만 호출한다."""
    if not DATA_FILE.exists():
        return []
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("목록 형식이 아님")
        return data
    except (OSError, ValueError):
        # 손상된 파일은 덮어쓰지 않도록 백업해 두고 빈 목록으로 시작한다.
        backup = DATA_FILE.with_name(
            f"{DATA_FILE.stem}.broken-{datetime.now():%Y%m%d%H%M%S}{DATA_FILE.suffix}"
        )
        try:
            DATA_FILE.replace(backup)
        except OSError:
            pass
        return []


def _write_file(items):
    """아이템 목록을 파일에 저장한다. 임시 파일에 쓴 뒤 교체해서 중간에 깨지지 않게 한다."""
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


def load_items():
    with _file_lock:
        return _read_file()


def mutate(change):
    """파일을 새로 읽고 → change(items)로 고치고 → 저장하는 과정을 잠금 안에서 한 번에 한다.

    화면에 그려 둔 예전 목록이 아니라 항상 최신 파일 내용을 고치므로,
    다른 탭이나 다른 사용자가 그사이 바꾼 내용을 덮어쓰지 않는다.
    change는 items 리스트를 직접 고치고 결과값을 반환한다.
    """
    with _file_lock:
        items = _read_file()
        result = change(items)
        _write_file(items)
    st.session_state["items"] = items
    # 목록이 바뀌면 띄워 둔 '전체 삭제' 확인창은 닫는다 (확인 대상이 달라졌으므로).
    st.session_state["confirm_clear"] = False
    return result


def _index_of(items, item_id):
    for i, item in enumerate(items):
        if item["id"] == item_id:
            return i
    return None


# ---------------------------------------------------------------------------
# 상태
# ---------------------------------------------------------------------------
def init_state():
    """세션 상태를 초기화한다.

    items: 아이템 리스트 (매 실행마다 파일에서 다시 읽는다). 각 아이템 구조는
        {"id": str, "name": str, "quantity": int, "checked": bool, "created_at": str}
    editing_id: 현재 수정 중인 아이템 id (없으면 None)
    confirm_clear: 전체 삭제 확인 단계 표시 여부
    flashes: 다음 렌더링에서 한 번 보여줄 알림 목록 [(st 함수 이름, args, kwargs)]
    """
    st.session_state["items"] = load_items()
    defaults = {
        "editing_id": None,
        "confirm_clear": False,
        "flashes": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    items = st.session_state["items"]
    # 수정 중이던 아이템이 다른 곳에서 삭제됐으면 수정 모드를 끝낸다.
    if st.session_state["editing_id"] and _index_of(items, st.session_state["editing_id"]) is None:
        st.session_state["editing_id"] = None
    # 목록이 비면 '전체 삭제' 확인창도 필요 없다.
    if not items:
        st.session_state["confirm_clear"] = False


def find_item(item_id):
    items = st.session_state["items"]
    index = _index_of(items, item_id)
    return None if index is None else items[index]


def flash(kind, *args, **kwargs):
    """콜백 안에서는 바로 그릴 수 없는 알림(warning, toast, balloons)을 다음 렌더링으로 미룬다."""
    st.session_state["flashes"].append((kind, args, kwargs))


def with_particle(word, batchim, no_batchim):
    """한글 조사 선택: 마지막 글자에 받침이 있으면 batchim(을), 없으면 no_batchim(를).

    마지막 글자가 한글이 아니면 '을(를)'처럼 둘 다 표시한다.
    """
    last = word[-1] if word else ""
    if "가" <= last <= "힣":
        has_batchim = (ord(last) - ord("가")) % 28 != 0
        return word + (batchim if has_batchim else no_batchim)
    return f"{word}{batchim}({no_batchim})"


# ---------------------------------------------------------------------------
# 데이터 변경 (콜백에서 호출). 모두 mutate()로 최신 파일 내용을 고친다.
# ---------------------------------------------------------------------------
OK, EMPTY, MISSING = "ok", "empty", "missing"


def add_item(name, quantity):
    """아이템을 추가한다. 이름이 비어 있으면 추가하지 않고 EMPTY를 반환한다."""
    name = (name or "").strip()
    if not name:
        return EMPTY
    new_item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": max(1, int(quantity)),
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    mutate(lambda items: items.append(new_item))
    return OK


def update_item(item_id, name, quantity):
    """아이템의 이름과 수량을 수정한다."""
    name = (name or "").strip()
    if not name:
        return EMPTY

    def change(items):
        index = _index_of(items, item_id)
        if index is None:
            return MISSING
        items[index]["name"] = name
        items[index]["quantity"] = max(1, int(quantity))
        return OK

    return mutate(change)


def delete_item(item_id):
    """아이템을 삭제하고 삭제한 아이템을 반환한다 (이미 없으면 None)."""

    def change(items):
        index = _index_of(items, item_id)
        return None if index is None else items.pop(index)

    removed = mutate(change)
    if st.session_state["editing_id"] == item_id:
        st.session_state["editing_id"] = None
    return removed


def set_checked(item_id, checked):
    """아이템의 구매 완료 상태를 정한다."""

    def change(items):
        index = _index_of(items, item_id)
        if index is None:
            return MISSING
        items[index]["checked"] = checked
        return OK

    return mutate(change)


def delete_checked_items():
    """구매 완료 아이템을 모두 삭제하고 삭제한 개수를 반환한다."""

    def change(items):
        checked_ids = {item["id"] for item in items if item["checked"]}
        items[:] = [item for item in items if item["id"] not in checked_ids]
        return checked_ids

    checked_ids = mutate(change)
    if st.session_state["editing_id"] in checked_ids:
        st.session_state["editing_id"] = None
    return len(checked_ids)


def clear_all_items():
    mutate(lambda items: items.clear())
    st.session_state["editing_id"] = None


# ---------------------------------------------------------------------------
# 콜백
# ---------------------------------------------------------------------------
def on_add_submit():
    name = st.session_state["new_name"]
    if add_item(name, st.session_state["new_quantity"]) == OK:
        flash("toast", f"{with_particle(escape_markdown(name.strip()), '을', '를')} 추가했어요", icon="➕")
    else:
        flash("warning", EMPTY_NAME_WARNING, icon="⚠️")


def on_toggle(item_id):
    if set_checked(item_id, st.session_state[f"check_{item_id}"]) == MISSING:
        flash("warning", ITEM_MISSING_WARNING, icon="⚠️")
        return
    items = st.session_state["items"]
    if items and all(item["checked"] for item in items):
        flash("balloons")


def on_delete(item_id):
    removed = delete_item(item_id)
    if removed is None:
        flash("warning", ITEM_MISSING_WARNING, icon="⚠️")
        return
    flash("toast", f"{with_particle(escape_markdown(removed['name']), '을', '를')} 삭제했어요", icon="🗑️")


def on_delete_checked():
    count = delete_checked_items()
    flash("toast", f"구매 완료한 {count}개를 삭제했어요", icon="🧹")


def on_clear_confirm():
    clear_all_items()
    flash("toast", "목록을 모두 비웠어요", icon="🗑️")


def on_edit_start(item_id):
    item = find_item(item_id)
    if item is None:
        flash("warning", ITEM_MISSING_WARNING, icon="⚠️")
        return
    st.session_state["editing_id"] = item_id
    st.session_state[f"edit_name_{item_id}"] = item["name"]
    st.session_state[f"edit_quantity_{item_id}"] = item["quantity"]


def on_edit_save(item_id):
    result = update_item(
        item_id,
        st.session_state[f"edit_name_{item_id}"],
        st.session_state[f"edit_quantity_{item_id}"],
    )
    if result == OK:
        st.session_state["editing_id"] = None
        flash("toast", "수정했어요", icon="✏️")
    elif result == MISSING:
        st.session_state["editing_id"] = None
        flash("warning", ITEM_MISSING_WARNING, icon="⚠️")
    else:
        flash("warning", EMPTY_NAME_WARNING, icon="⚠️")


def on_edit_cancel():
    st.session_state["editing_id"] = None


def on_clear_request():
    st.session_state["confirm_clear"] = True


def on_clear_cancel():
    st.session_state["confirm_clear"] = False


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
/* 폭을 적당히 좁히고 위쪽 여백을 줄인다 */
.block-container { max-width: 680px; padding-top: 2.5rem; padding-bottom: 3rem; }

/* 버튼 크기와 간격 통일 */
.stButton button, .stFormSubmitButton button {
    min-height: 2.5rem;
    padding: 0 0.6rem;
    white-space: nowrap;
}

/* 아이템 행과 일괄 작업 버튼: 휴대폰에서도 열이 아래로 떨어지지 않고 한 줄에 놓이게 한다 */
.st-key-progress_card [data-testid="stHorizontalBlock"],
[data-testid="stForm"] [data-testid="stHorizontalBlock"],
.st-key-item_list [data-testid="stHorizontalBlock"],
.st-key-bulk_actions [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap;
    gap: 0.5rem;
}
.st-key-progress_card [data-testid="stColumn"],
[data-testid="stForm"] [data-testid="stColumn"],
.st-key-item_list [data-testid="stColumn"],
.st-key-bulk_actions [data-testid="stColumn"] {
    min-width: 0;
}
.st-key-item_list .stCheckbox p { font-size: 1.05rem; word-break: break-all; }
/* 이름 속 URL·이메일이 자동으로 링크가 되어도 일반 글자처럼 보이고, 누르면 체크만 되게 한다 */
.st-key-item_list .stCheckbox a {
    pointer-events: none;
    color: inherit;
    text-decoration: inherit;
}

/* 휴대폰 화면에서는 수정/삭제 버튼을 아이콘만 보여준다 */
@media (max-width: 640px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; padding-top: 1.5rem; }
    /* display:none 대신 화면에서만 숨겨서 스크린 리더는 "수정", "삭제"를 읽을 수 있게 한다 */
    [class*="st-key-edit_"] button p,
    [class*="st-key-delete_"] button p {
        position: absolute; width: 1px; height: 1px; margin: -1px;
        overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap;
    }
    [class*="st-key-edit_"] button,
    [class*="st-key-delete_"] button { padding: 0; }
    .st-key-progress_card [data-testid="stMetricValue"] { font-size: 1.6rem; }
}
</style>
"""


def escape_markdown(text):
    """사용자가 입력한 이름이 마크다운으로 해석되지 않도록 특수문자를 이스케이프한다."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>:$])", r"\\\1", text)


def render_flashes():
    for kind, args, kwargs in st.session_state["flashes"]:
        getattr(st, kind)(*args, **kwargs)
    st.session_state["flashes"] = []


def render_progress():
    """F5: 진행 현황 카드."""
    items = st.session_state["items"]
    if not items:
        return
    total = len(items)
    done = sum(1 for item in items if item["checked"])

    with st.container(border=True, key="progress_card"):
        col_total, col_left, col_done = st.columns(3)
        col_total.metric("전체", f"{total}개")
        col_left.metric("남은 물건", f"{total - done}개")
        col_done.metric("구매 완료", f"{done}개")
        st.progress(done / total, text=f"전체 {total}개 중 {done}개 구매 완료")

    if done == total:
        st.success("모든 물건을 샀어요! 장보기 완료", icon="🎉")


def render_input_form():
    """F1: 아이템 입력 폼."""
    with st.form("add_form", clear_on_submit=True, border=True):
        col_name, col_qty = st.columns([0.72, 0.28], vertical_alignment="bottom")
        col_name.text_input("아이템 이름", key="new_name", placeholder="예: 우유")
        col_qty.number_input("수량", key="new_quantity", min_value=1, value=1, step=1)
        st.form_submit_button(
            "추가", icon="➕", type="primary", on_click=on_add_submit, width="stretch"
        )


def render_filter():
    """F8: 필터."""
    return st.radio(
        "보기",
        [FILTER_ALL, FILTER_REMAINING, FILTER_DONE],
        key="filter",
        horizontal=True,
        label_visibility="collapsed",
    )


def item_label(item):
    """체크박스 라벨: 이름 + 수량 배지. 구매 완료면 취소선과 회색 글자."""
    name = escape_markdown(item["name"])
    if item["checked"]:
        return f":gray[~~{name}~~] :gray-badge[x{item['quantity']}]"
    return f"{name} :green-badge[x{item['quantity']}]"


def render_edit_row(item_id):
    """F2: 수정 모드."""
    with st.container(border=True):
        col_name, col_qty = st.columns([0.72, 0.28], vertical_alignment="bottom")
        col_name.text_input("이름", key=f"edit_name_{item_id}")
        col_qty.number_input("수량", key=f"edit_quantity_{item_id}", min_value=1, step=1)
        col_save, col_cancel = st.columns(2)
        col_save.button(
            "저장", icon="💾", key=f"save_{item_id}", type="primary",
            on_click=on_edit_save, args=(item_id,), width="stretch",
        )
        col_cancel.button(
            "취소", key=f"cancel_{item_id}", on_click=on_edit_cancel, width="stretch",
        )


def render_item_row(item):
    item_id = item["id"]
    if st.session_state["editing_id"] == item_id:
        render_edit_row(item_id)
        return

    col_check, col_edit, col_delete = st.columns(
        [0.66, 0.17, 0.17], vertical_alignment="center"
    )
    # F4: 체크. 위젯이 예전 값을 기억하고 있으면 파일의 최신 값을 무시하므로,
    # 그리기 전에 위젯 상태를 실제 데이터 값으로 맞춘다 (다른 탭에서 바꾼 값 반영).
    check_key = f"check_{item_id}"
    st.session_state[check_key] = item["checked"]
    col_check.checkbox(
        item_label(item),
        key=check_key,
        on_change=on_toggle,
        args=(item_id,),
    )
    col_edit.button(
        "수정", icon="✏️", key=f"edit_{item_id}",
        on_click=on_edit_start, args=(item_id,), width="stretch",
    )
    # F3: 삭제
    col_delete.button(
        "삭제", icon="🗑️", key=f"delete_{item_id}",
        on_click=on_delete, args=(item_id,), width="stretch",
    )


def render_item_list(filter_value):
    """아이템 목록."""
    items = st.session_state["items"]
    if not items:
        st.info(EMPTY_MESSAGE, icon="🧺")
        return

    if filter_value == FILTER_REMAINING:
        visible = [item for item in items if not item["checked"]]
    elif filter_value == FILTER_DONE:
        visible = [item for item in items if item["checked"]]
    else:
        visible = items

    with st.container(key="item_list"):
        if not visible:
            st.caption("해당하는 아이템이 없어요.")
            return
        for item in visible:
            render_item_row(item)


def render_bulk_actions():
    """F6: 체크 항목 일괄 삭제, F7: 전체 삭제."""
    items = st.session_state["items"]
    if not items:
        return

    st.divider()
    with st.container(key="bulk_actions"):
        if st.session_state["confirm_clear"]:
            st.warning(f"아이템 {len(items)}개를 모두 삭제할까요? 되돌릴 수 없어요.", icon="⚠️")
            col_yes, col_no = st.columns(2)
            col_yes.button("전체 삭제", key="clear_yes", type="primary",
                           on_click=on_clear_confirm, width="stretch")
            col_no.button("취소", key="clear_no", on_click=on_clear_cancel, width="stretch")
            return

        has_checked = any(item["checked"] for item in items)
        col_checked, col_all = st.columns(2)
        col_checked.button(
            "체크 항목 삭제", icon="🧹", on_click=on_delete_checked,
            disabled=not has_checked, width="stretch",
        )
        col_all.button("전체 삭제", icon="🗑️", on_click=on_clear_request, width="stretch")


def main():
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    init_state()
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    st.title("🛒 쇼핑 리스트")
    st.caption("살 물건을 적어 두고, 장 보면서 하나씩 체크하세요.")
    render_progress()
    render_input_form()
    render_flashes()

    st.subheader("목록")
    filter_value = render_filter() if st.session_state["items"] else FILTER_ALL
    render_item_list(filter_value)
    render_bulk_actions()


if __name__ == "__main__":
    main()
