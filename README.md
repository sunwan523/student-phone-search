# 学生手机查询系统

一个适合部署到 `streamlit.app` 的 Streamlit 小项目，专门用于查询学生编号、姓名、拼音首字母和手机号。

## 功能

- 上传 Excel 文件，按“上传日期”保存为独立批次
- 默认查询最近一次上传的数据
- 查询支持：
  - 四位编号
  - 姓名全称
  - 姓名单字
  - 拼音首字母
  - 部分拼音首字母
  - 全拼
  - 手机号
  - 手机号任意连续片段（3 位及以上）
- 查询结果把四位编号做粗黑高亮展示
- 多条匹配结果会完整列表显示
- 自动统计当期总数和主要连续号段
- 上传时必须输入密码 `523626`

## Excel 格式

默认读取前 3 列：

1. 编号
2. 姓名
3. 手机号

不需要表头；如果有表头，建议单独去掉后再上传。

## 本地运行

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 部署到 Streamlit Community Cloud

1. 把整个项目上传到 GitHub
2. 在 `streamlit.app` 里选择这个仓库
3. Main file path 填 `app.py`
4. 部署即可

## 重要说明

当前版本把上传批次保存在应用本地 `data/` 目录，适合本地长期使用，也适合先完成 GitHub/Streamlit 部署验证。

如果你准备把它作为正式线上工具长期每周上传，建议下一步把“批次存储”改成外部持久化方案，例如：

- Supabase / PostgreSQL
- Google Sheets / Google Drive
- 你自己的服务器数据库

因为 `streamlit.app` 的本地文件存储不是长期稳定持久化的，应用重启后历史上传数据可能丢失。
