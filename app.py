from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from pypinyin import Style, lazy_pinyin


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "student_phone_batches.db"


@dataclass
class BatchOption:
    batch_id: str
    label: str


def ensure_storage() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                upload_label TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                id_ranges_json TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                batch_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                student_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                name_initials TEXT NOT NULL,
                name_full_pinyin TEXT NOT NULL,
                searchable_text TEXT NOT NULL,
                PRIMARY KEY (batch_id, student_id, phone, student_name),
                FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
            )
            """
        )
        conn.commit()


def normalize_digits(value: object) -> str:
    return "".join(ch for ch in str(value).strip() if ch.isdigit())


def normalize_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value).strip())


def build_pinyin_fields(name: str) -> tuple[str, str]:
    if not name:
        return "", ""
    syllables = lazy_pinyin(name)
    initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER))
    return initials.lower(), "".join(syllables).lower()


def make_searchable_text(student_id: str, name: str, phone: str, initials: str, full_pinyin: str) -> str:
    return " ".join(
        part for part in [student_id, name, phone, initials, full_pinyin] if part
    ).lower()


def read_excel(uploaded_file) -> pd.DataFrame:
    raw = pd.read_excel(uploaded_file, header=None)
    raw = raw.dropna(how="all").copy()
    if raw.shape[1] < 3:
        raise ValueError("Excel 至少需要 3 列：编号、姓名、手机号。")

    raw = raw.iloc[:, :3].copy()
    raw.columns = ["student_id", "student_name", "phone"]
    raw["student_id"] = raw["student_id"].map(normalize_digits)
    raw["student_name"] = raw["student_name"].map(normalize_name)
    raw["phone"] = raw["phone"].map(normalize_digits)
    raw = raw[
        (raw["student_id"] != "")
        & (raw["student_name"] != "")
        & (raw["phone"] != "")
    ].copy()

    raw = raw.drop_duplicates(subset=["student_id", "student_name", "phone"]).reset_index(drop=True)
    if raw.empty:
        raise ValueError("没有读到有效数据，请确认前 3 列分别是编号、姓名、手机号。")

    raw["student_id"] = raw["student_id"].str.zfill(4)
    raw["name_initials"], raw["name_full_pinyin"] = zip(
        *raw["student_name"].map(build_pinyin_fields)
    )
    raw["searchable_text"] = raw.apply(
        lambda row: make_searchable_text(
            row["student_id"],
            row["student_name"],
            row["phone"],
            row["name_initials"],
            row["name_full_pinyin"],
        ),
        axis=1,
    )
    return raw


def compute_id_ranges(student_ids: pd.Series) -> list[dict[str, int | str]]:
    numeric_ids = sorted({int(value) for value in student_ids if str(value).isdigit()})
    if not numeric_ids:
        return []

    ranges: list[dict[str, int | str]] = []
    start = prev = numeric_ids[0]
    for current in numeric_ids[1:]:
        if current == prev + 1:
            prev = current
            continue
        ranges.append(
            {
                "start": f"{start:04d}",
                "end": f"{prev:04d}",
                "count": prev - start + 1,
            }
        )
        start = prev = current
    ranges.append(
        {"start": f"{start:04d}", "end": f"{prev:04d}", "count": prev - start + 1}
    )
    ranges.sort(key=lambda item: (-int(item["count"]), item["start"]))
    return ranges


def save_batch(uploaded_file, df: pd.DataFrame, upload_label: str) -> str:
    content = uploaded_file.getvalue()
    content_hash = hashlib.sha256(content).hexdigest()
    with sqlite3.connect(DB_PATH) as conn:
        batch_id = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        extension = Path(uploaded_file.name).suffix or ".xlsx"
        stored_filename = f"{batch_id}{extension}"
        stored_path = UPLOADS_DIR / stored_filename
        stored_path.write_bytes(content)

        id_ranges = compute_id_ranges(df["student_id"])
        uploaded_at = datetime.now().isoformat(timespec="seconds")

        conn.execute(
            """
            INSERT INTO batches (
                batch_id, upload_label, uploaded_at, original_filename,
                stored_filename, row_count, id_ranges_json, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                upload_label,
                uploaded_at,
                uploaded_file.name,
                stored_filename,
                int(len(df)),
                json.dumps(id_ranges, ensure_ascii=False),
                content_hash,
            ),
        )
        conn.executemany(
            """
            INSERT INTO records (
                batch_id, student_id, student_name, phone,
                name_initials, name_full_pinyin, searchable_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    batch_id,
                    row.student_id,
                    row.student_name,
                    row.phone,
                    row.name_initials,
                    row.name_full_pinyin,
                    row.searchable_text,
                )
                for row in df.itertuples(index=False)
            ],
        )
        conn.commit()
    return batch_id


def list_batches() -> list[BatchOption]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT batch_id, upload_label, uploaded_at, row_count
            FROM batches
            ORDER BY uploaded_at DESC
            """
        ).fetchall()
    return [
        BatchOption(
            batch_id=row[0],
            label=f"{row[1]} | {row[2][:16].replace('T', ' ')} | {row[3]}条 | {row[0][-4:]}  ",
        )
        for row in rows
    ]


def get_batch_meta(batch_id: str) -> dict[str, object] | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT batch_id, upload_label, uploaded_at, original_filename, row_count, id_ranges_json
            FROM batches
            WHERE batch_id = ?
            """,
            (batch_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "batch_id": row[0],
        "upload_label": row[1],
        "uploaded_at": row[2],
        "original_filename": row[3],
        "row_count": row[4],
        "id_ranges": json.loads(row[5]),
    }


@st.cache_data(show_spinner=False)
def load_batch_records(batch_id: str) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT student_id, student_name, phone, name_initials, name_full_pinyin
            FROM records
            WHERE batch_id = ?
            ORDER BY CAST(student_id AS INTEGER), student_name
            """,
            conn,
            params=(batch_id,),
        )
    return df


def search_records(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    keyword = keyword.strip().lower()
    if not keyword:
        return df

    compact = re.sub(r"\s+", "", keyword)
    digits_only = "".join(ch for ch in compact if ch.isdigit())

    id_match = df["student_id"].str.contains(compact, case=False, na=False)
    name_match = df["student_name"].str.contains(keyword, case=False, na=False)
    initials_match = df["name_initials"].str.contains(compact, case=False, na=False)
    pinyin_match = df["name_full_pinyin"].str.contains(compact, case=False, na=False)
    phone_match = False
    if len(digits_only) >= 3:
        phone_match = df["phone"].str.contains(digits_only, case=False, na=False)

    mask = id_match | name_match | initials_match | pinyin_match | phone_match
    return df[mask].copy()


def render_stats(meta: dict[str, object]) -> None:
    ranges = meta["id_ranges"]
    st.markdown("### 本期信息")
    st.markdown(f"本期数量：**{meta['row_count']} 条**")
    st.markdown(f"上传日期：**{meta['upload_label']}**")

    if ranges:
        st.markdown("主要连续号段：")
        for index, item in enumerate(ranges[:2], start=1):
            st.markdown(
                f"{index}. **{item['start']} - {item['end']}**，共 **{item['count']}** 个"
            )
    else:
        st.markdown("主要连续号段：**未识别到连续号段**")


def render_results(df: pd.DataFrame) -> None:
    if df.empty:
        st.warning("没有匹配到结果，请试试编号、姓名、拼音首字母或手机号片段。")
        return

    styled_rows = []
    for row in df.itertuples(index=False):
        styled_rows.append(
            f"""
            <div class="result-card">
                <div class="id-badge">{row.student_id}</div>
                <div class="result-main">
                    <div class="student-name">{row.student_name}</div>
                    <div class="student-phone">{row.phone}</div>
                </div>
            </div>
            """
        )
    st.markdown(
        f"<div class='result-wrap'>{''.join(styled_rows)}</div>",
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="学生手机查询", page_icon="📱", layout="wide")
    ensure_storage()

    st.markdown(
        """
        <style>
        .stApp { background: linear-gradient(180deg, #f8f5ee 0%, #fffdfa 100%); }
        .block-container { padding-top: 2rem; padding-bottom: 2rem; max-width: 1100px; }
        .hero {
            background: linear-gradient(135deg, #1d3557 0%, #274c77 55%, #457b9d 100%);
            border-radius: 22px; padding: 28px 30px; color: white; margin-bottom: 1rem;
            box-shadow: 0 18px 45px rgba(29, 53, 87, 0.18);
        }
        .hero h1 { margin: 0 0 8px 0; font-size: 2.1rem; }
        .hero p { margin: 0; font-size: 1rem; opacity: 0.92; }
        .result-wrap { display: grid; gap: 14px; margin-top: 8px; }
        .result-card {
            display: flex; gap: 16px; align-items: center; padding: 18px 20px;
            background: white; border-radius: 18px; border: 1px solid #e8e2d6;
            box-shadow: 0 10px 28px rgba(39, 76, 119, 0.08);
        }
        .id-badge {
            min-width: 108px; text-align: center; font-weight: 900; font-size: 1.6rem;
            color: #111; background: #ffe08a; border: 3px solid #101010; border-radius: 14px;
            padding: 12px 10px; letter-spacing: 1px;
        }
        .student-name { font-size: 1.35rem; font-weight: 800; color: #1f2937; }
        .student-phone { font-size: 1.05rem; margin-top: 4px; color: #b45309; font-weight: 700; }
        @media (max-width: 768px) {
            .block-container { padding-top: 1rem; padding-bottom: 1.25rem; padding-left: 0.8rem; padding-right: 0.8rem; }
            .hero { padding: 20px 18px; border-radius: 18px; }
            .hero h1 { font-size: 1.55rem; line-height: 1.2; }
            .hero p { font-size: 0.95rem; line-height: 1.5; }
            .result-card { flex-direction: column; align-items: stretch; gap: 12px; padding: 14px 14px; border-radius: 16px; }
            .id-badge { min-width: auto; width: 100%; font-size: 1.45rem; padding: 10px 8px; }
            .student-name { font-size: 1.2rem; }
            .student-phone { font-size: 1rem; word-break: break-all; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="hero">
            <h1>学生手机查询系统</h1>
            <p>支持编号、姓名、单字、拼音首字母、手机号片段查询。每次上传独立保存，默认查询最近一次上传的数据。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 顶部查询区域
    st.subheader("查询")
    batches = list_batches()
    if not batches:
        st.info("还没有批次数据，请先上传 Excel。")
        return

    selected_label = st.selectbox(
        "查询哪次上传的数据",
        options=[item.label for item in batches],
        index=0,
    )
    batch_lookup = {item.label: item.batch_id for item in batches}
    selected_batch_id = batch_lookup[selected_label]
    
    meta = get_batch_meta(selected_batch_id)
    if meta is None:
        st.error("批次信息不存在。")
        return

    query = st.text_input(
        "开始查询",
        placeholder="输入编号 / 姓名 / 单字 / 拼音首字母 / 全拼 / 手机号任意连续3位以上",
    )

    records_df = load_batch_records(selected_batch_id)
    result_df = search_records(records_df, query)
    st.write(f"匹配结果：{len(result_df)} 条")
    render_results(result_df)

    with st.expander("查看本期原始数据"):
        st.dataframe(
            records_df[["student_id", "student_name", "phone"]],
            use_container_width=True,
            hide_index=True,
        )

    st.divider()

    # 左侧上传和补充信息区域
    left, right = st.columns([1.05, 1.95], gap="large")

    with left:
        st.subheader("上传新批次")
        upload_label = st.date_input("这次数据日期", value=datetime.now().date(), format="YYYY-MM-DD")
        upload_password = st.text_input("上传密码", type="password", placeholder="请输入上传密码")
        uploaded_file = st.file_uploader("上传 Excel 文件", type=["xlsx", "xls"])
        if st.button("保存本次上传", type="primary", use_container_width=True):
            if upload_password != "523626":
                st.error("上传密码错误，未接受数据，也不会进行整理。")
            elif uploaded_file is None:
                st.error("请先选择一个 Excel 文件。")
            else:
                try:
                    df = read_excel(uploaded_file)
                    batch_id = save_batch(uploaded_file, df, str(upload_label))
                    load_batch_records.clear()
                    st.success(f"上传完成，已保存为独立批次：{batch_id}")
                except Exception as exc:
                    st.error(f"上传失败：{exc}")

        st.caption("Excel 默认读取前 3 列：编号、姓名、手机号。每次上传的数据互相独立，不会混在一起。")

        st.divider()
        
        st.subheader("补充信息")
        supplement_password = st.text_input("补充密码", type="password", placeholder="请输入密码")
        supplement_text = st.text_area("粘贴补充信息", placeholder="一行一条信息，用逗号或空格分隔，格式：编号 姓名 手机号\n例如：0001 张三 13800138000")
        if st.button("保存补充信息", type="primary", use_container_width=True):
            if supplement_password != "523626":
                st.error("密码错误，无法保存补充信息。")
            elif not supplement_text.strip():
                st.error("请输入补充信息。")
            else:
                try:
                    # 解析补充信息
                    lines = supplement_text.strip().split('\n')
                    new_records = []
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        # 忽略序号
                        line = re.sub(r'^\d+\s*[.:、]\s*', '', line)
                        # 用逗号或空格分隔
                        parts = re.split(r'[\s,，]+', line)
                        # 过滤空字符串
                        parts = [p.strip() for p in parts if p.strip()]
                        if len(parts) >= 3:
                            student_id = normalize_digits(parts[0])
                            student_name = normalize_name(parts[1])
                            phone = normalize_digits(parts[2])
                            if student_id and student_name and phone:
                                student_id = student_id.zfill(4)
                                name_initials, name_full_pinyin = build_pinyin_fields(student_name)
                                searchable_text = make_searchable_text(student_id, student_name, phone, name_initials, name_full_pinyin)
                                new_records.append((selected_batch_id, student_id, student_name, phone, name_initials, name_full_pinyin, searchable_text))
                    
                    if new_records:
                        # 保存到数据库
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.executemany(
                                """
                                INSERT OR IGNORE INTO records (
                                    batch_id, student_id, student_name, phone,
                                    name_initials, name_full_pinyin, searchable_text
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                new_records
                            )
                            conn.commit()
                        load_batch_records.clear()
                        st.success(f"补充信息保存成功，共 {len(new_records)} 条。")
                    else:
                        st.warning("没有有效的补充信息。")
                except Exception as exc:
                    st.error(f"保存失败：{exc}")

        st.caption("支持逗号或空格分隔，忽略序号，格式：编号 姓名 手机号")

    with right:
        render_stats(meta)
        st.caption(
            f"当前批次文件：{meta['original_filename']} | 上传时间：{str(meta['uploaded_at']).replace('T', ' ')}"
        )


if __name__ == "__main__":
    main()
