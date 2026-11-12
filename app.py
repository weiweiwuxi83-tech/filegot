import io
import re
import time
import datetime

import requests
import streamlit as st
from pypdf import PdfReader

API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
HEADERS = {"User-Agent": "Mozilla/5.0"}


# ---------- 工具函数 ----------
@st.cache_data(ttl=86400, show_spinner=False)
def load_stock_list():
    """从巨潮公开的列表里取 代码/orgId/名称（沪深两份，取不到的跳过）"""
    stocks = []
    for name in ["szse_stock", "sse_stock"]:
        try:
            r = requests.get(
                f"http://www.cninfo.com.cn/new/data/{name}.json",
                headers=HEADERS, timeout=30,
            )
            stocks += r.json().get("stockList", [])
        except Exception:
            pass
    return stocks


def find_stocks(query, stocks):
    q = query.strip()
    if not q:
        return []
    out = []
    for s in stocks:
        if (
            s.get("code") == q
            or q in (s.get("zwjc") or "")
            or q.lower() == (s.get("pinyin") or "").lower()
        ):
            out.append(s)
    return out[:20]


def clean(s):
    return re.sub(r"<[^>]+>", "", s or "")


def fetch_all(code, org_id, tab, keyword, se_date):
    column = "sse" if code.startswith("6") else "szse"
    headers = dict(HEADERS)
    headers["Referer"] = (
        f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}&orgId={org_id}"
    )
    items, page = [], 1
    while True:
        data = {
            "pageNum": str(page), "pageSize": "30", "column": column,
            "tabName": tab, "plate": "", "stock": f"{code},{org_id}",
            "searchkey": keyword, "secid": "", "category": "", "trade": "",
            "seDate": se_date, "sortName": "", "sortType": "", "isHLtitle": "true",
        }
        r = requests.post(API, data=data, headers=headers, timeout=30)
        r.raise_for_status()
        batch = r.json().get("announcements") or []
        items += batch
        if len(batch) < 30:
            break
        page += 1
        time.sleep(0.5)
    return items, headers


def only_pdfs(items):
    seen, pdfs = set(), []
    for i in items:
        u = i.get("adjunctUrl") or ""
        if u.lower().endswith(".pdf") and u not in seen:
            seen.add(u)
            pdfs.append(i)

    # 按公告时间倒序排列：最新日期在最前面
    pdfs.sort(
        key=lambda i: i.get("announcementTime") or 0,
        reverse=True
    )

    return pdfs



def ann_date(i):
    return datetime.datetime.fromtimestamp(i["announcementTime"] / 1000).strftime("%Y-%m-%d")


# ---------- 页面 ----------
st.set_page_config(page_title="巨潮公告批量提取", page_icon="📄")
st.title("📄 巨潮公告批量提取")
st.caption("输入股票代码或名称，批量下载公告 PDF，提取文字并合并成一个 txt。")

stocks = load_stock_list()

query = st.text_input("股票代码或名称", placeholder="如 002475 或 立讯精密")
tab_label = st.radio("栏目", ["调研", "全部公告"], horizontal=True)
tab = "relation" if tab_label == "调研" else "fulltext"
keyword = st.text_input("标题关键词（可选）", placeholder="如 投资者关系活动记录表")

use_date = st.checkbox("限定日期范围")
se_date = ""
if use_date:
    today = datetime.date.today()
    rng = st.date_input("日期范围", value=(today - datetime.timedelta(days=365), today))
    if isinstance(rng, tuple) and len(rng) == 2:
        se_date = f"{rng[0]}~{rng[1]}"

chosen = None
matches = find_stocks(query, stocks) if query else []
if len(matches) == 1:
    chosen = matches[0]
elif len(matches) > 1:
    chosen = st.selectbox(
        "找到多个，请选择", matches,
        format_func=lambda s: f"{s.get('zwjc', '')} {s.get('code', '')}",
    )
elif query:
    st.warning("在股票列表里没找到。可以在下面手动填写代码和 orgId。")

with st.expander("找不到？手动填写代码和 orgId"):
    m_code = st.text_input("股票代码", key="m_code")
    m_org = st.text_input("orgId（从该公司巨潮页面网址里复制）", key="m_org")
    if m_code and m_org:
        chosen = {"code": m_code.strip(), "orgId": m_org.strip(), "zwjc": m_code.strip()}

if chosen:
    code, org_id = chosen["code"], chosen["orgId"]
    st.info(f"当前标的：{chosen.get('zwjc', '')} {code}（orgId {org_id}）")

    if st.button("获取列表"):
        with st.spinner("正在获取列表…"):
            try:
                items, _ = fetch_all(code, org_id, tab, keyword, se_date)
                st.session_state["pdfs"] = only_pdfs(items)
                st.session_state["raw_count"] = len(items)
                st.session_state["list_code"] = code
                st.session_state.pop("merged", None)
            except Exception as e:
                st.error(f"获取失败：{e}")

    if st.session_state.get("list_code") == code and "pdfs" in st.session_state:
        pdfs = st.session_state["pdfs"]
        st.success(f"接口返回 {st.session_state['raw_count']} 条，其中 PDF {len(pdfs)} 份")
        if pdfs:
            st.dataframe(
                [{"日期": ann_date(i), "标题": clean(i.get("announcementTitle"))} for i in pdfs],
                use_container_width=True, hide_index=True,
            )
            limit = st.number_input(
                "最多处理份数（每份约需 2~5 秒；份数多时建议先限定日期）",
                min_value=1, max_value=len(pdfs), value=min(len(pdfs), 80),
            )
            if st.button("下载并合并"):
                headers = dict(HEADERS)
                headers["Referer"] = (
                    f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}&orgId={org_id}"
                )
                todo = pdfs[: int(limit)]
                parts, failed, empty = [], [], []
                bar = st.progress(0.0)
                status = st.empty()
                for n, i in enumerate(todo, 1):
                    title = clean(i.get("announcementTitle"))
                    status.write(f"处理中 {n}/{len(todo)}：{title}")
                    try:
                        r = requests.get(
                            "http://static.cninfo.com.cn/" + i["adjunctUrl"],
                            headers=headers, timeout=60,
                        )
                        r.raise_for_status()
                        reader = PdfReader(io.BytesIO(r.content))
                        text = "\n".join(p.extract_text() or "" for p in reader.pages)
                        if not text.strip():
                            empty.append(title)
                            text = "（未提取到文字，可能是扫描版）"
                        parts.append(f"===== {ann_date(i)}  {title} =====\n\n{text}\n")
                        time.sleep(1)
                    except Exception as e:
                        failed.append((title, str(e)))
                    bar.progress(n / len(todo))
                status.write("处理完成")
                st.session_state["merged"] = "\n".join(parts)
                st.session_state["merged_code"] = code
                st.session_state["summary"] = (len(parts), len(failed), len(empty), failed)

    if st.session_state.get("merged_code") == code and st.session_state.get("merged"):
        ok, nfail, nempty, failed = st.session_state["summary"]
        st.success(f"成功 {ok} 份，失败 {nfail} 份，无文字 {nempty} 份")
        if failed:
            st.write("失败列表：", failed)
        st.download_button(
            "下载合并后的 txt",
            data=st.session_state["merged"],
            file_name=f"merged_{code}.txt",
            mime="text/plain",
        )

st.caption("数据来自巨潮资讯网公开披露信息，仅供个人研究使用，请勿高频请求。")
