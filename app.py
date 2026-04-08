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
                remark TEXT DEFAULT '',
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
                name_initials, name_full_pinyin, searchable_text, remark
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    ""
                )
                for row in df.itertuples(index=False)
            ],
        )
        conn.commit()
    return batch_id


@st.cache_data(show_spinner=False, ttl=3600)  # 缓存1小时
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
            label=f"{row[1]} | {row[2][:16].replace('T', ' ')} | {row[3]}条 | {row[0][-4:]}",
        )
        for row in rows
    ]


@st.cache_data(show_spinner=False, ttl=3600)  # 缓存1小时
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
            SELECT student_id, student_name, phone, name_initials, name_full_pinyin, remark
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
    batch_id = meta["batch_id"]
    # 获取当前批次的实际数据量
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM records WHERE batch_id = ?",
            (batch_id,)
        )
        actual_count = cursor.fetchone()[0]
    
    # 获取当前批次的所有学生ID，重新计算号段
    records_df = load_batch_records(batch_id)
    ranges = compute_id_ranges(records_df["student_id"])
    
    st.markdown("### 本期信息")
    st.markdown(f"本期数量：**{actual_count} 条**")
    st.markdown(f"上传日期：**{meta['upload_label']}**")

    if ranges:
        st.markdown("连续号段：")
        for index, item in enumerate(ranges, start=1):
            st.markdown(
                f"{index}. **{item['start']} - {item['end']}**，共 **{item['count']}** 个"
            )
    else:
        st.markdown("连续号段：**未识别到连续号段**")


def render_results(df: pd.DataFrame) -> None:
    if df.empty:
        st.warning("没有匹配到结果，请试试编号、姓名、拼音首字母或手机号片段。")
        return

    # 限制显示结果数量，避免手机端渲染过多内容
    max_results = 50
    if len(df) > max_results:
        df = df.head(max_results)
        st.info(f"只显示前 {max_results} 条结果")

    # 为每条记录显示卡片和按钮
    for i, row in enumerate(df.itertuples(index=False)):
        # 生成唯一的键
        record_key = f"{row.student_id}_{row.student_name}_{row.phone}"
        
        # 显示记录卡片
        st.markdown(
            f"""
            <div class='result-card'>
                <div class='id-badge'>{row.student_id}</div>
                <div class='result-main'>
                    <div class='student-name'>{row.student_name}</div>
                    <div class='student-phone'>{row.phone}</div>
                    {'<div class="student-remark">' + row.remark + '</div>' if row.remark else ''}
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        # 显示操作按钮
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button(f"已取走", key=f"take_{record_key}"):
                # 添加备注
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new_remark = f"{row.remark} 已于 {current_time} 取走" if row.remark else f"已于 {current_time} 取走"
                # 更新数据库
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute(
                        """
                        UPDATE records
                        SET remark = ?
                        WHERE student_id = ? AND student_name = ? AND phone = ?
                        """,
                        (new_remark, row.student_id, row.student_name, row.phone)
                    )
                    conn.commit()
                # 清除缓存
                load_batch_records.clear()
                st.success(f"已标记 {row.student_name} 为取走")
        
        with col2:
            if st.button(f"已存回", key=f"return_{record_key}"):
                # 添加备注
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new_remark = f"{row.remark} 已于 {current_time} 存回" if row.remark else f"已于 {current_time} 存回"
                # 更新数据库
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute(
                        """
                        UPDATE records
                        SET remark = ?
                        WHERE student_id = ? AND student_name = ? AND phone = ?
                        """,
                        (new_remark, row.student_id, row.student_name, row.phone)
                    )
                    conn.commit()
                # 清除缓存
                load_batch_records.clear()
                st.success(f"已标记 {row.student_name} 为存回")
        
        st.markdown("---")


def main() -> None:
    st.set_page_config(page_title="学生手机查询", page_icon="📱", layout="wide")
    ensure_storage()

    st.markdown(
        """
        <style>
        /* 简化背景样式 */
        .stApp { background-color: #f8f5ee; }
        .block-container { padding-top: 1.5rem; padding-bottom: 1.5rem; max-width: 1000px; }
        
        /* 简化hero样式 */
        .hero {
            background: linear-gradient(135deg, #1d3557 0%, #274c77 100%);
            border-radius: 16px; padding: 20px 24px; color: white; margin-bottom: 1rem;
            box-shadow: 0 8px 20px rgba(29, 53, 87, 0.15);
        }
        .hero h1 { margin: 0; font-size: 1.8rem; }
        
        /* 简化结果样式 */
        .result-wrap { display: grid; gap: 10px; margin-top: 8px; }
        .result-card {
            display: flex; gap: 12px; align-items: center; padding: 14px 16px;
            background: white; border-radius: 12px; border: 1px solid #e8e2d6;
            box-shadow: 0 4px 12px rgba(39, 76, 119, 0.05);
        }
        .id-badge {
            min-width: 90px; text-align: center; font-weight: 900; font-size: 1.4rem;
            color: #111; background: #ffe08a; border: 2px solid #101010; border-radius: 10px;
            padding: 8px 6px; letter-spacing: 1px;
        }
        .student-name { font-size: 1.2rem; font-weight: 700; color: #1f2937; }
        .student-phone { font-size: 1rem; margin-top: 2px; color: #b45309; font-weight: 600; }
        .student-remark { font-size: 0.9rem; margin-top: 4px; color: #6b7280; font-style: italic; }
        
        /* 简化响应式样式 */
        @media (max-width: 768px) {
            .block-container { padding: 1rem 0.8rem; }
            .hero { padding: 16px 16px; border-radius: 12px; }
            .hero h1 { font-size: 1.4rem; }
            .result-card { flex-direction: column; align-items: stretch; gap: 8px; padding: 12px 12px; border-radius: 10px; }
            .id-badge { min-width: auto; width: 100%; font-size: 1.2rem; padding: 6px 4px; }
            .student-name { font-size: 1.1rem; }
            .student-phone { font-size: 0.95rem; word-break: break-all; }
            .student-remark { font-size: 0.85rem; margin-top: 3px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="hero">
            <h1>学生手机查询系统</h1>
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

    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input(
            "开始查询",
            placeholder="输入编号 / 姓名 / 单字 / 拼音首字母 / 全拼 / 手机号任意连续3位以上",
        )
    with col2:
        search_button = st.button("查询", type="primary")

    records_df = load_batch_records(selected_batch_id)
    
    if search_button:
        result_df = search_records(records_df, query)
        st.write(f"匹配结果：{len(result_df)} 条")
        render_results(result_df)

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
                            # 提取编号、姓名、手机号三项
                            student_id = normalize_digits(parts[0])
                            student_name = normalize_name(parts[1])
                            phone = normalize_digits(parts[2])
                            if student_id and student_name and phone:
                                # 确保编号是四位数字
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
                                    name_initials, name_full_pinyin, searchable_text, remark
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                [record + ("",) for record in new_records]
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
