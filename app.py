
import io
import re
import time
import datetime
import html

import requests
import pandas as pd
import streamlit as st
from pypdf import PdfReader
from docx import Document

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak
)


API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
HEADERS = {"User-Agent": "Mozilla/5.0"}


# ---------- 工具函数 ----------

@st.cache_data(ttl=86400, show_spinner=False)
def load_stock_list():
    """获取沪深股票列表"""
    stocks = []
    for name in ["szse_stock", "sse_stock"]:
        try:
            r = requests.get(
                f"https://www.cninfo.com.cn/new/data/{name}.json",
                headers=HEADERS,
                timeout=30,
            )
            r.raise_for_status()
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
        f"https://www.cninfo.com.cn/new/disclosure/"
        f"stock?stockCode={code}&orgId={org_id}"
    )

    items = []
    page = 1

    while True:
        data = {
            "pageNum": str(page),
            "pageSize": "30",
            "column": column,
            "tabName": tab,
            "plate": "",
            "stock": f"{code},{org_id}",
            "searchkey": keyword,
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": se_date,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }

        r = requests.post(
            API,
            data=data,
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()

        batch = r.json().get("announcements") or []
        items += batch

        if len(batch) < 30:
            break

        page += 1
        time.sleep(0.5)

    return items, headers


def only_pdfs(items):
    """去重并按照公告时间倒序排列"""
    seen = set()
    pdfs = []

    for i in items:
        u = i.get("adjunctUrl") or ""

        if u.lower().endswith(".pdf") and u not in seen:
            seen.add(u)
            pdfs.append(i)

    pdfs.sort(
        key=lambda i: i.get("announcementTime") or 0,
        reverse=True,
    )

    return pdfs


def ann_date(i):
    return datetime.datetime.fromtimestamp(
        i["announcementTime"] / 1000
    ).strftime("%Y-%m-%d")


def extract_pdf_text(code, org_id, todo, headers):
    """下载 PDF 并提取文字"""
    parts = []
    failed = []
    empty = []

    bar = st.progress(0.0)
    status = st.empty()

    for n, i in enumerate(todo, 1):
        title = clean(i.get("announcementTitle"))
        status.write(f"处理中 {n}/{len(todo)}：{title}")

        try:
            url = "https://static.cninfo.com.cn/" + i["adjunctUrl"]

            r = requests.get(
                url,
                headers=headers,
                timeout=60,
            )
            r.raise_for_status()

            reader = PdfReader(io.BytesIO(r.content))

            text = "\n".join(
                p.extract_text() or ""
                for p in reader.pages
            )

            if not text.strip():
                empty.append(title)
                text = "（未提取到文字，可能是扫描版）"

            parts.append({
                "date": ann_date(i),
                "title": title,
                "text": text,
            })

        except Exception as e:
            failed.append((title, str(e)))

        bar.progress(n / len(todo))
        time.sleep(1)

    status.write("处理完成")

    return parts, failed, empty


def make_txt(parts):
    """生成 TXT"""
    content = []

    for p in parts:
        content.append(
            f"===== {p['date']}  {p['title']} =====\n\n"
            f"{p['text']}\n"
        )

    return "\n".join(content).encode("utf-8-sig")


def make_docx(parts):
    """生成 Word 文档"""
    doc = Document()

    doc.add_heading("上市公司公告汇总", 0)

    doc.add_paragraph(
        f"公告数量：{len(parts)}"
    )

    doc.add_paragraph(
        f"生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    doc.add_page_break()

    for index, p in enumerate(parts):
        doc.add_heading(
            f"{p['date']}  {p['title']}",
            level=1,
        )

        for line in p["text"].splitlines():
            if line.strip():
                doc.add_paragraph(line)

        if index < len(parts) - 1:
            doc.add_page_break()

    output = io.BytesIO()
    doc.save(output)

    return output.getvalue()


def make_pdf(parts):
    """生成支持中文的 PDF"""
    output = io.BytesIO()

    pdfmetrics.registerFont(
        UnicodeCIDFont("STSong-Light")
    )

    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=45,
        leftMargin=45,
        topMargin=45,
        bottomMargin=45,
        title="上市公司公告汇总",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ChineseTitle",
        parent=styles["Title"],
        fontName="STSong-Light",
        fontSize=18,
        leading=25,
        alignment=TA_CENTER,
    )

    heading_style = ParagraphStyle(
        "ChineseHeading",
        parent=styles["Heading2"],
        fontName="STSong-Light",
        fontSize=12,
        leading=18,
        spaceBefore=10,
        spaceAfter=10,
        wordWrap="CJK",
    )

    body_style = ParagraphStyle(
        "ChineseBody",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=9,
        leading=14,
        wordWrap="CJK",
        spaceAfter=3,
    )

    story = [
        Paragraph("上市公司公告汇总", title_style),
        Spacer(1, 15),
        Paragraph(f"公告数量：{len(parts)}", body_style),
        Paragraph(
            "生成时间：" +
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            body_style,
        ),
        Spacer(1, 15),
    ]

    for index, p in enumerate(parts):
        heading = html.escape(
            f"{p['date']}  {p['title']}"
        )

        story.append(
            Paragraph(heading, heading_style)
        )

        for line in p["text"].splitlines():
            if line.strip():
                safe_line = html.escape(line)
                story.append(
                    Paragraph(safe_line, body_style)
                )

        story.append(Spacer(1, 12))

        if index < len(parts) - 1:
            story.append(PageBreak())

    doc.build(story)

    return output.getvalue()


# ---------- 页面 ----------

st.set_page_config(
    page_title="巨潮公告批量提取",
    page_icon="📄",
    layout="wide",
)

st.title("📄 巨潮公告批量提取")

st.caption(
    "输入股票代码或名称，获取巨潮公告，"
    "自主选择公告并合并导出。"
)

stocks = load_stock_list()

query = st.text_input(
    "股票代码或名称",
    placeholder="如 002475 或 立讯精密",
)

tab_label = st.radio(
    "栏目",
    ["调研", "全部公告"],
    horizontal=True,
)

tab = "relation" if tab_label == "调研" else "fulltext"

keyword = st.text_input(
    "标题关键词（可选）",
    placeholder="如 投资者关系活动记录表",
)

use_date = st.checkbox("限定日期范围")

se_date = ""

if use_date:
    today = datetime.date.today()

    rng = st.date_input(
        "日期范围",
        value=(
            today - datetime.timedelta(days=365),
            today,
        ),
    )

    if isinstance(rng, tuple) and len(rng) == 2:
        se_date = f"{rng[0]}~{rng[1]}"


# ---------- 股票选择 ----------

chosen = None

matches = find_stocks(query, stocks) if query else []

if len(matches) == 1:
    chosen = matches[0]

elif len(matches) > 1:
    chosen = st.selectbox(
        "找到多个，请选择",
        matches,
        format_func=lambda s: (
            f"{s.get('zwjc', '')} {s.get('code', '')}"
        ),
    )

elif query:
    st.warning(
        "在股票列表里没找到，可以手动填写代码和 orgId。"
    )


with st.expander("找不到？手动填写代码和 orgId"):
    m_code = st.text_input("股票代码", key="m_code")
    m_org = st.text_input("orgId", key="m_org")

    if m_code and m_org:
        chosen = {
            "code": m_code.strip(),
            "orgId": m_org.strip(),
            "zwjc": m_code.strip(),
        }


# ---------- 获取公告 ----------

if chosen:
    code = chosen["code"]
    org_id = chosen.get("orgId") or chosen.get("orgId".lower())

    st.info(
        f"当前标的：{chosen.get('zwjc', '')} "
        f"{code}（orgId {org_id}）"
    )

    if st.button("获取公告列表", type="primary"):
        with st.spinner("正在获取公告列表..."):
            try:
                items, _ = fetch_all(
                    code,
                    org_id,
                    tab,
                    keyword,
                    se_date,
                )

                pdfs = only_pdfs(items)

                st.session_state["pdfs"] = pdfs
                st.session_state["raw_count"] = len(items)
                st.session_state["list_code"] = code

                # 新列表默认全部选中
                st.session_state["selection_data"] = [
                    {
                        "选择": True,
                        "日期": ann_date(i),
                        "标题": clean(i.get("announcementTitle")),
                    }
                    for i in pdfs
                ]

                st.session_state["selection_version"] = (
                    st.session_state.get("selection_version", 0) + 1
                )

                st.session_state.pop("merged_parts", None)
                st.session_state.pop("export_data", None)

            except Exception as e:
                st.error(f"获取失败：{e}")


# ---------- 公告勾选与导出 ----------

if (
    chosen
    and st.session_state.get("list_code") == chosen["code"]
    and "pdfs" in st.session_state
):

    code = chosen["code"]
    org_id = chosen.get("orgId") or chosen.get("orgId".lower())
    pdfs = st.session_state["pdfs"]

    st.success(
        f"接口返回 {st.session_state['raw_count']} 条，"
        f"其中 PDF {len(pdfs)} 份"
    )

    if pdfs:

        st.subheader("一、选择需要合并的公告")

        selection_data = st.session_state["selection_data"]

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("全选", use_container_width=True):
                for row in selection_data:
                    row["选择"] = True

                st.session_state["selection_data"] = selection_data
                st.session_state["selection_version"] += 1
                st.rerun()

        with col2:
            if st.button("全不选", use_container_width=True):
                for row in selection_data:
                    row["选择"] = False

                st.session_state["selection_data"] = selection_data
                st.session_state["selection_version"] += 1
                st.rerun()

        with col3:
            selected_count = sum(
                1 for row in selection_data if row["选择"]
            )
            st.metric("已选择公告", f"{selected_count} / {len(pdfs)}")

        editor_key = (
            f"announcement_editor_{code}_"
            f"{st.session_state['selection_version']}"
        )

        edited_df = st.data_editor(
            pd.DataFrame(selection_data),
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            disabled=["日期", "标题"],
            column_config={
                "选择": st.column_config.CheckboxColumn(
                    "合并",
                    default=True,
                ),
                "日期": st.column_config.TextColumn("公告日期"),
                "标题": st.column_config.TextColumn(
                    "公告标题",
                    width="large",
                ),
            },
            key=editor_key,
        )

        selected_indices = edited_df.index[
            edited_df["选择"]
        ].tolist()

        st.session_state["selection_data"] = (
            edited_df.to_dict("records")
        )

        st.info(
            f"当前勾选 {len(selected_indices)} 份公告，"
            "将按照日期从近到远的顺序合并。"
        )

        st.divider()

        # ---------- 导出格式 ----------

        st.subheader("二、选择导出格式")

        export_format = st.radio(
            "文件格式",
            ["TXT", "DOCX（Word）", "PDF"],
            horizontal=True,
        )

        st.caption(
            "TXT 适合 AI 分析；Word 方便编辑；"
            "PDF 方便阅读和归档。"
        )

        st.divider()

        # ---------- 执行合并 ----------

        if st.button(
            "下载并合并所选公告",
            type="primary",
            use_container_width=True,
            disabled=len(selected_indices) == 0,
        ):

            todo = [pdfs[i] for i in selected_indices]

            headers = dict(HEADERS)
            headers["Referer"] = (
                f"https://www.cninfo.com.cn/new/disclosure/"
                f"stock?stockCode={code}&orgId={org_id}"
            )

            with st.spinner("正在下载和提取公告..."):
                parts, failed, empty = extract_pdf_text(
                    code,
                    org_id,
                    todo,
                    headers,
                )

            if parts:
                # 保证最终结果仍然按日期倒序
                parts.sort(
                    key=lambda p: p["date"],
                    reverse=True,
                )

                try:
                    if export_format == "TXT":
                        export_data = make_txt(parts)
                        filename = f"merged_{code}.txt"
                        mime = "text/plain"

                    elif export_format == "DOCX（Word）":
                        export_data = make_docx(parts)
                        filename = f"merged_{code}.docx"
                        mime = (
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        )

                    else:
                        export_data = make_pdf(parts)
                        filename = f"merged_{code}.pdf"
                        mime = "application/pdf"

                    st.session_state["merged_parts"] = parts
                    st.session_state["export_data"] = export_data
                    st.session_state["export_filename"] = filename
                    st.session_state["export_mime"] = mime
                    st.session_state["summary"] = (
                        len(parts),
                        len(failed),
                        len(empty),
                        failed,
                    )

                except Exception as e:
                    st.error(f"生成导出文件失败：{e}")

            else:
                st.error("没有成功提取到任何公告。")

        # ---------- 下载结果 ----------

        if (
            st.session_state.get("export_data") is not None
            and st.session_state.get("list_code") == code
        ):

            ok, nfail, nempty, failed = st.session_state["summary"]

            st.divider()
            st.subheader("三、导出结果")

            st.success(
                f"成功 {ok} 份，失败 {nfail} 份，"
                f"无文字 {nempty} 份"
            )

            if failed:
                with st.expander("查看下载失败列表"):
                    for title, reason in failed:
                        st.write(f"- {title}：{reason}")

            st.download_button(
                label="📥 下载合并文件",
                data=st.session_state["export_data"],
                file_name=st.session_state["export_filename"],
                mime=st.session_state["export_mime"],
                use_container_width=True,
                type="primary",
            )

    else:
        st.warning("没有找到 PDF 公告。")


st.caption(
    "数据来自巨潮资讯网公开披露信息，仅供个人研究使用，"
    "请勿高频请求。"
)
