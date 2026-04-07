import streamlit as st
import pandas as pd
from datetime import datetime
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import re

# -------------------------- 配置 & 数据结构 --------------------------
st.set_page_config(page_title="数据查询系统", page_icon="📊", layout="wide")

# 固定管理员密码
ADMIN_PASSWORD = "523626"

@dataclass
class BatchOption:
    batch_id: str
    label: str
    uploaded_at: str

# 模拟存储（实际可替换为数据库/文件存储）
if "batches" not in st.session_state:
    st.session_state.batches = []
if "data_map" not in st.session_state:
    st.session_state.data_map = {}
# 搜索状态持久化
if "search_key" not in st.session_state:
    st.session_state.search_key = ""

# -------------------------- 核心工具函数 --------------------------
def list_batches() -> List[BatchOption]:
    """获取所有批次列表"""
    return st.session_state.batches

@st.cache_data(show_spinner=False)
def list_batches_cached() -> List[BatchOption]:
    """缓存批次列表，上传后清除缓存"""
    return list_batches()

def save_batch_data(df: pd.DataFrame, upload_label: str) -> None:
    """保存上传的批次数据"""
    batch_id = f"batch_{int(datetime.now().timestamp())}"
    uploaded_at = datetime.now().isoformat()
    st.session_state.batches.append(
        BatchOption(batch_id=batch_id, label=upload_label, uploaded_at=uploaded_at)
    )
    st.session_state.data_map[batch_id] = df

def append_batch_data(batch_id: str, new_rows: List[Dict]) -> bool:
    """向指定批次追加数据（支持重复姓名电话，不同编号直接新增）"""
    if batch_id not in st.session_state.data_map:
        return False
    df = st.session_state.data_map[batch_id]
    new_df = pd.DataFrame(new_rows)
    # 合并数据（允许重复，不去重）
    df_updated = pd.concat([df, new_df], ignore_index=True)
    st.session_state.data_map[batch_id] = df_updated
    return True

def get_batch_data(batch_id: str) -> Optional[pd.DataFrame]:
    """根据批次ID获取数据"""
    return st.session_state.data_map.get(batch_id)

def get_batch_meta(batch_id: str) -> Dict[str, Any]:
    """获取批次元信息"""
    df = get_batch_data(batch_id)
    if df is None:
        return {"row_count": 0, "upload_label": "", "uploaded_at": "", "id_ranges": []}
    
    row_count = len(df)
    upload_label = next((b.label for b in st.session_state.batches if b.batch_id == batch_id), "")
    uploaded_at = next((b.uploaded_at for b in st.session_state.batches if b.batch_id == batch_id), "")
    
    return {
        "row_count": row_count,
        "upload_label": upload_label,
        "uploaded_at": uploaded_at,
        "id_ranges": []
    }

def parse_text_data(text: str) -> List[Dict]:
    """
    解析文本框数据：
    1. 一行一条
    2. 支持逗号/空格分隔
    3. 自动忽略行首序号
    4. 只提取：id(编号), name(姓名), phone(电话)
    """
    rows = []
    lines = text.strip().split("\n")
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 1. 忽略行首序号（如 1、1.、1：、① 等）
        line = re.sub(r'^[\d\.、：\s]+', '', line)
        # 2. 按逗号/空格拆分
        parts = re.split(r'[,，\s]+', line)
        parts = [p.strip() for p in parts if p.strip()]
        
        # 3. 必须至少3个字段：编号、姓名、电话
        if len(parts) >= 3:
            user_id = parts[0]
            name = parts[1]
            phone = parts[2]
            rows.append({
                "id": user_id,
                "name": name,
                "phone": phone
            })
    return rows

# -------------------------- 页面渲染函数 --------------------------
def render_stats(meta: dict[str, Any]) -> None:
    """渲染统计信息"""
    ranges = meta["id_ranges"]
    st.markdown("### 本期信息")
    st.markdown(f"本期数量：**{meta['row_count']} 条**")
    st.markdown(f"数据日期：**{meta['upload_label']}**")
    upload_time = meta['uploaded_at'].replace('T', ' ')[:16]
    st.markdown(f"上传时间：**{upload_time}**")
    
    if ranges:
        st.markdown("主要连续号段：")
        for index, item in enumerate(ranges[:2], start=1):
            st.markdown(
                f"{index}. **{item['start']} - {item['end']}**，共 **{item['count']}** 个"
            )
    else:
        st.markdown("主要连续号段：**未识别到连续号段**")

def render_results(df: pd.DataFrame, search_key: str = "") -> None:
    """渲染结果列表（支持搜索过滤，点击按钮后触发）"""
    st.markdown("### 数据列表")
    
    # 搜索过滤
    if search_key:
        df = df[
            df["id"].astype(str).str.contains(search_key, na=False) |
            df["name"].astype(str).str.contains(search_key, na=False) |
            df["phone"].astype(str).str.contains(search_key, na=False)
        ]
    
    if df.empty:
        st.info("暂无匹配数据")
        return
    
    styled_html = []
    for _, row in df.iterrows():
        phone = str(row.get('phone', ''))
        name = str(row.get('name', ''))
        user_id = str(row.get('id', ''))
        
        card_html = f"""
        <div class="result-card">
            <div class="id-badge">{user_id}</div>
            <div class="result-main">
                <div class="student-name">{name}</div>
                <div class="student-phone">{phone}</div>
            </div>
        </div>
        """
        styled_html.append(card_html)
    
    st.markdown(f'<div class="result-wrap">{"".join(styled_html)}</div>', unsafe_allow_html=True)

# -------------------------- 页面样式（完全保留原样式） --------------------------
st.markdown("""
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
.result-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.id-badge {
    min-width: 108px; text-align: center; font-weight: 900; font-size: 1.6rem;
    color: #111; background: #ffe08a; border: 3px solid #101010; border-radius: 14px;
    padding: 12px 10px; letter-spacing: 1px;
}
.student-name { font-size: 1.35rem; font-weight: 800; color: #1f2937; }
.student-phone { font-size: 1.05rem; margin-top: 4px; color: #b45309; font-weight: 700; }
/* 搜索按钮样式优化 */
.stButton > button {
    border-radius: 12px;
    font-weight: 700;
    height: 48px;
}
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
""", unsafe_allow_html=True)

# -------------------------- 主页面 --------------------------
def main():
    # 顶部标题
    st.markdown("""
    <div class="hero">
        <h1>📊 数据查询管理系统</h1>
        <p>上传Excel / 文本追加 / 全局搜索 一体化管理</p>
    </div>
    """, unsafe_allow_html=True)

    # ====================== 【查找功能置顶 + 新增搜索按钮】 ======================
    st.markdown("### 🔍 全局搜索")
    # 搜索输入框 + 按钮布局
    col_search, col_btn = st.columns([8, 1], gap="small")
    with col_search:
        search_input = st.text_input(
            "输入编号/姓名/电话搜索",
            value=st.session_state.search_key,
            placeholder="请输入搜索内容",
            label_visibility="collapsed"
        )
    with col_btn:
        if st.button("🔍 搜索", type="primary", use_container_width=True):
            st.session_state.search_key = search_input.strip()
    st.divider()

    # 左右分栏
    left, right = st.columns([1, 2], gap="large")

    with left:
        st.markdown("### 📁 Excel数据上传")
        # 【新增：Excel上传密码验证】
        upload_password = st.text_input(
            "请输入管理员密码",
            type="password",
            placeholder="请输入管理员密码"
        )
        uploaded_file = st.file_uploader("选择Excel文件", type=["xlsx", "xls"])
        if uploaded_file:
            try:
                df = pd.read_excel(uploaded_file)
                required_cols = ["id", "name", "phone"]
                if not all(col in df.columns for col in required_cols):
                    st.error(f"文件必须包含列：{required_cols}")
                else:
                    upload_label = st.text_input("数据日期标识", value=datetime.now().strftime("%Y-%m-%d"))
                    if st.button("✅ 确认上传", type="primary"):
                        # 先验证密码，正确才允许上传
                        if upload_password != ADMIN_PASSWORD:
                            st.error("❌ 密码错误，无法上传！")
                        else:
                            save_batch_data(df, upload_label)
                            list_batches_cached.clear()
                            st.success("Excel上传成功！")
            except Exception as e:
                st.error(f"文件解析失败：{str(e)}")

        st.divider()
        # ====================== 【文本追加数据 + 密码隐藏优化】 ======================
        st.markdown("### ➕ 文本追加数据")
        # 【修复：密码彻底隐藏，不显示明文】
        append_password = st.text_input(
            "请输入管理员密码",
            type="password",
            placeholder="请输入管理员密码"
        )
        text_input = st.text_area(
            "粘贴数据（一行一条，逗号/空格分隔，自动忽略序号）",
            height=150,
            placeholder="示例：\n1,张三,13800138000\n2 李四 13900139000\n3.王五,13700137000"
        )
        if st.button("💾 保存追加数据", type="primary"):
            # 1. 密码校验
            if append_password != ADMIN_PASSWORD:
                st.error("❌ 密码错误，无法保存！")
            # 2. 数据校验
            elif not text_input.strip():
                st.warning("⚠️ 请输入要追加的数据")
            # 3. 批次校验
            elif not selected_batch_id:
                st.warning("⚠️ 请先选择数据批次")
            else:
                # 4. 解析数据
                parsed_rows = parse_text_data(text_input)
                if not parsed_rows:
                    st.error("❌ 未解析到有效数据，请检查格式")
                else:
                    # 5. 追加保存
                    success = append_batch_data(selected_batch_id, parsed_rows)
                    if success:
                        st.success(f"✅ 追加成功！共添加 {len(parsed_rows)} 条数据")
                        list_batches_cached.clear()
                    else:
                        st.error("❌ 追加失败，批次不存在")

        st.divider()
        # 批次选择
        batches = list_batches_cached()
        selected_batch_id = None
        if not batches:
            st.info("还没有批次数据，请先上传 Excel。")
        else:
            selected_label = st.selectbox(
                "查询数据批次",
                options=[item.label for item in batches],
                index=0,
            )
            batch_lookup = {item.label: item.batch_id for item in batches}
            selected_batch_id = batch_lookup[selected_label]

    with right:
        if not selected_batch_id:
            st.info("👈 请先上传数据并选择批次")
        else:
            # 渲染数据 + 搜索过滤（使用session_state中的搜索词）
            meta = get_batch_meta(selected_batch_id)
            df = get_batch_data(selected_batch_id)
            render_stats(meta)
            st.divider()
            render_results(df, st.session_state.search_key)

if __name__ == "__main__":
    main()