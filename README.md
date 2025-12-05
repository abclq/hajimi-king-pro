MARKDOWN
🎪 Hajimi King Pro 🏆
人人都是【超级】哈基米大王 👑

🙏 特别感谢
本项目基于 @GakkiNoOne 的原始项目进行开发和增强。

原仓库地址: https://github.com/GakkiNoOne/hajimi-king

感谢原作者的开源贡献！🎉

注意： 本项目正处于beta期间，所以功能、结构、接口等等都有可能变化，不保证稳定性，请自行承担风险。

🚀 核心功能
- **GitHub搜索Gemini Key 🔍** - 基于自定义查询表达式搜索GitHub代码中的API密钥
- **双认证模式 🔐** - 支持Token模式（API）和Web模式（Cookie），多账号轮询
- **代理支持 🌐** - 支持多代理轮换，提高访问稳定性和成功率
- **增量扫描 📊** - 支持断点续传，避免重复扫描已处理的文件
- **智能过滤 🚫** - 自动过滤文档、示例、测试文件，专注有效代码
- **外部同步 🔄** - 支持向Gemini-Balancer和GPT-Load同步发现的密钥
- **付费key检测 💰** - 检查到有效key时自动再次检查是否为付费key
- **数据库存储 💾** - 支持SQLite/PostgreSQL/MySQL数据库存储，自动从文本文件迁移

🔮 待开发功能 (TODO)
-  API、可视化展示抓取的key列表 📊 - 提供API接口和可视化界面获取已抓取的密钥列表
-  多线程支持 🛠️ - 支持多线程并发处理，提高处理效率

📋 Wiki 🗂️
- [部署文档]()
- [本地部署 🏠]()
- [Docker部署 🐳]()
- [AI wiki  AI 维基]()
- AI wiki（由AI生成，非实时更新）

---

### 🖥️ 前台监控面板 (Web Console) - 官方推荐

为了方便实时监控挖矿程序的成果，我们额外提供了一个基于 Streamlit 的独立网页控制台。它作为一个完全独立的只读监控面板，与后台挖棺程序解耦，通过读取数据库和日志文件来展示信息，安全且稳定。

> **部署方式说明**: 本部署方案采用**原生本地部署** (Native Local Deployment) 方式。这种方式对于此监控面板来说，具有**简单、透明、可靠**的优点，避免了为单个轻量级应用引入 Docker 的额外复杂性，使得问题排查更为直接。

#### 面板核心功能
- **📊 仪表盘总览** - 清晰展示付费、有效、受限三类密钥的总数。
- **📋 密钥列表展示** - 分类展示所有密钥，并提供单个密钥的复制按钮。
- **🖱️ 可靠的批量复制** - 提供一个**绝对可靠**的只读文本框，用于全选 (`Ctrl+A`) 并复制 (`Ctrl+C`) 当前分类下的所有密钥。
- **🚀 一键批量推送** - 可一键将当前数据库中**所有**的“付费”和“有效”密钥，以纯文本列表的形式，通过钉钉机器人推送给自己。
- **📜 实时日志查看** - 直接在网页上查看后台 `miner.log` 的最新日志内容。

#### 部署步骤

**1. 创建独立目录并进入**
```bash
mkdir ~/web_console && cd ~/web_console
2. 安装核心依赖

BASH
pip3 install streamlit pandas dingtalkchatbot st-copy-to-clipboard
3. 创建代码文件 (web_app.py)
执行下面的整块命令，它会自动创建并写入一个已去除敏感信息的代码模板。

BASH
cat << 'EOF' > web_app.py
import streamlit as st
import sqlite3
import pandas as pd
import os
import subprocess
import logging
from dingtalkchatbot.chatbot import DingtalkChatbot
from st_copy_to_clipboard import st_copy_to_clipboard

# --- 配置区 (V12) ---
# 注意：此处的 '~/hajimi-king-pro' 路径是硬编码的，确保你的后台程序路径与此一致
BASE_DIR = os.path.expanduser('~/hajimi-king-pro')
DB_PATH = os.path.join(BASE_DIR, 'data/keys.db')
LOG_PATH = os.path.join(BASE_DIR, 'miner.log')

# --- 核心安全与通知配置 (请在此处填入你的信息) ---
APP_PASSWORD = "YOUR_APP_PASSWORD"  # 在这里填入你的访问密码
DINGTALK_WEBHOOK = "YOUR_DINGTALK_WEBHOOK_URL"  # 在这里填入你的钉钉机器人 Webhook 地址

# --- 日志与函数库 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def send_dingtalk_notification(message):
    if not DINGTALK_WEBHOOK or "YOUR_DINGTALK_WEBHOOK_URL" in DINGTALK_WEBHOOK:
        st.toast("钉钉 Webhook 未配置!", icon="⚠️")
        return
    try:
        full_message = {
            "msgtype": "markdown",
            "markdown": {
                "title":"Hajimi控制台通知",
                "text": f"### 【Hajimi控制台】\n\n{message}"
            }
        }
        xiaoding = DingtalkChatbot(DINGTALK_WEBHOOK)
        xiaoding.send_markdown(full_message['markdown']['title'], full_message['markdown']['text'])
        st.toast("🎉 通知已成功发送!", icon="✅")
    except Exception as e:
        st.error(f"发送失败: {e}")

def get_keys_by_type(key_type):
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(), f"数据库文件不存在: {DB_PATH}"
    try:
        conn_str = f'file:{DB_PATH}?mode=ro'
        conn = sqlite3.connect(conn_str, uri=True)
        query = f"SELECT id, api_key, repo_name, file_url, created_at FROM keys WHERE key_type = '{key_type}' ORDER BY created_at DESC"
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df, None
    except Exception as e:
        return pd.DataFrame(), f"读取数据库时发生未知错误: {e}"

def display_key_table(df, table_identifier):
    if df.empty:
        st.info(f"暂无数据。")
        return

    for index, row in df.iterrows():
        col1, col2 = st.columns([4, 1])
        with col1:
            st.code(row['api_key'], language=None)
        with col2:
            st_copy_to_clipboard(text=row['api_key'], before_copy_label="复制", after_copy_label="已复制!", key=f"copy_{table_identifier}_{row['id']}")

# --- 页面渲染 (V12 · 可靠版) ---
st.set_page_config(page_title="Hajimi King Pro 控制台", layout="wide")
st.title("👑 Hajimi King Pro 控制台 (V12 · 可靠版)")

# 密码验证
st.sidebar.title("身份验证")
password = st.sidebar.text_input("请输入访问密码:", type="password")
if password != APP_PASSWORD:
    st.error("❌ 密码错误，禁止访问！")
    st.stop()
st.sidebar.success("✅ 验证通过")

# 手动刷新按钮
st.sidebar.markdown("---")
if st.sidebar.button("🔄 刷新数据 (强制重载)"):
    st.rerun()

# 心跳监控
if 'heartbeat_sent' not in st.session_state:
    send_dingtalk_notification("网页控制台服务已启动 (V12 · 可靠版)。")
    st.session_state.heartbeat_sent = True

# 数据加载
df_paid, err_paid = get_keys_by_type('paid')
df_valid, err_valid = get_keys_by_type('valid')
df_limited, err_limited = get_keys_by_type('rate_limited')

# 总览仪表盘
st.header("📊 总览仪表盘")
col1, col2, col3 = st.columns(3)

def create_dashboard_card(df, title, key_type):
    with st.expander(f"{title}", expanded=True):
        st.metric("总数", len(df))
        if not df.empty:
            all_keys = "\n".join(df['api_key'])
            st.info("⬇️ 全选 (Ctrl+A) 并复制 (Ctrl+C)")
            st.text_area(
                "all_keys_textarea", 
                value=all_keys, 
                height=200, 
                disabled=True, 
                label_visibility="collapsed",
                key=f"textarea_all_{key_type}"
            )
        with st.container(height=300):
             display_key_table(df, key_type)

with col1:
    create_dashboard_card(df_paid, "💎 付费密钥", "paid")
with col2:
    create_dashboard_card(df_valid, "✅ 有效密钥", "valid")
with col3:
    create_dashboard_card(df_limited, "⚠️ 受限密钥", "limited")

st.markdown("---") 

# 功能区 (日志和手动通知)
tab_log, tab_notify = st.tabs(["挖矿日志 📜", "手动通知 🔔"])
with tab_log:
    st.header("📜 后台挖矿程序实时日志")
    log_lines = st.slider("选择日志行数:", 10, 500, 50)
    if os.path.exists(LOG_PATH):
        result = subprocess.run(['tail', '-n', str(log_lines), LOG_PATH], capture_output=True, text=True)
        st.code(result.stdout, language='log')
    else:
        st.warning(f"日志文件不存在: {LOG_PATH}")
with tab_notify:
    st.header("🔔 手动发送钉钉通知")
    with st.form("manual_notify_form"):
        message_content = st.text_area("输入消息内容:", key="manual_message")
        submitted = st.form_submit_button("发送到钉钉")
        if submitted and message_content:
            send_dingtalk_notification(message_content)

# 批量推送功能区
st.sidebar.markdown("---")
st.sidebar.title("🚨 批量推送")

def push_all_available_keys():
    df_paid, err_paid = get_keys_by_type('paid')
    df_valid, err_valid = get_keys_by_type('valid')
    
    all_keys_df = pd.concat([df_paid, df_valid], ignore_index=True)

    if err_paid or err_valid:
        st.sidebar.error("查询数据库时出错。")
        return
    
    if all_keys_df.empty:
        st.sidebar.warning("当前无任何付费或有效密钥可推送。")
        return

    keys_list = all_keys_df['api_key'].tolist()
    num_keys = len(keys_list)
    keys_string = "\n".join(keys_list)
    
    message = (
        f"**发现 {num_keys} 个可用密钥 (付费+有效):**\n\n"
        f"```\n"
        f"{keys_string}\n"
        f"```"
    )
    
    send_dingtalk_notification(message)

if st.sidebar.button("🚀 推送全部可用密钥 (付费+有效)"):
    push_all_available_keys()

EOF
4. 查找 Streamlit 的绝对路径 (必须)
为了确保 nohup 命令能稳定运行，我们必须使用绝对路径。

BASH
which streamlit
# 这会输出一个路径，例如 /home/your_user/.local/bin/streamlit，请复制它
5. 启动网页服务
使用上一步查到的绝对路径，将网页服务放入后台 7x24 小时运行。

BASH
# ‼️ 警告：这里的 [YOUR_STREAMLIT_ABSOLUTE_PATH] 必须替换成你上一步查到的真实路径
nohup [YOUR_STREAMLIT_ABSOLUTE_PATH] run web_app.py > web.log 2>&1 &
部署完成后，通过 http://[你的服务器IP]:8501 即可访问你的专属监控面板。

⚙️ 配置变量说明 📖
以下是后台挖矿程序 (hajimi-king-pro) 的所有可配置的环境变量，在 .env 文件中设置：

GitHub认证配置
变量名	默认值	说明	示例值
GITHUB_AUTH_MODE	token	认证模式：token=API / web=Cookie	token 或 web
GITHUB_TOKENS	空	GitHub API令牌（多个用逗号分隔），Token模式使用	ghp_token1,ghp_token2
GITHUB_SESSION	空	GitHub Session Cookie（多个用逗号分隔），Web模式使用	session1,session2
基础配置
变量名	默认值	说明
DATA_PATH	/app/data	数据存储目录
QUERIES_FILE	queries.txt	搜索查询配置文件（相对于DATA_PATH）
LANGUAGE	zh_cn	界面语言（zh_cn/en）
PROXY	空	代理服务器（支持HTTP/HTTPS/SOCKS5，多个用逗号分隔）<br>格式: http://host:port, socks5://user:pass@host:port
DATE_RANGE_DAYS	730	仓库年龄过滤（天数）
FILE_PATH_BLACKLIST	readme,docs,...	文件路径黑名单（逗号分隔）
存储配置
变量名	默认值	说明
STORAGE_TYPE	sql	存储方式（sql/text，推荐sql）
DB_TYPE	sqlite	数据库类型（sqlite/postgresql/mysql）
SQLITE_DB_PATH	keys.db	SQLite数据库文件路径
POSTGRESQL_HOST	localhost	PostgreSQL主机
POSTGRESQL_PORT	5432	PostgreSQL端口
POSTGRESQL_DATABASE	hajimi_keys	PostgreSQL数据库名
POSTGRESQL_USER	postgres	PostgreSQL用户名
POSTGRESQL_PASSWORD	空	PostgreSQL密码
MYSQL_HOST	localhost	MySQL主机
MYSQL_PORT	3306	MySQL端口
MYSQL_DATABASE	hajimi_keys	MySQL数据库名
MYSQL_USER	root	MySQL用户名
MYSQL_PASSWORD	空	MySQL密码
💡 首次启用SQL存储时，系统会自动迁移历史文本文件到数据库		
模型配置
变量名	默认值	说明
HAJIMI_CHECK_MODEL	gemini-2.5-flash	密钥验证模型
HAJIMI_PAID_MODEL	gemini-2.5-pro-preview-03-25	付费密钥验证模型
密钥同步配置
变量名	默认值	说明
GEMINI_BALANCER_SYNC_ENABLED	false	Gemini Balancer同步开关
GEMINI_BALANCER_URL	空	Gemini Balancer服务地址
GEMINI_BALANCER_AUTH	空	Gemini Balancer认证密码
GPT_LOAD_SYNC_ENABLED	false	GPT-Load同步开关
GPT_LOAD_URL	空	GPT-Load服务地址
GPT_LOAD_AUTH	空	GPT-Load认证Token
GPT_LOAD_GROUP_NAME	空	GPT-Load目标组名（多个用逗号分隔）
GPT_LOAD_PAID_SYNC_ENABLED	false	付费密钥独立同步开关
GPT_LOAD_PAID_GROUP_NAME	空	付费密钥分组名
RATE_LIMITED_HANDLING	save_only	429密钥处理策略
GPT_LOAD_RATE_LIMITED_GROUP_NAME	空	429密钥分组名
💡 429密钥处理策略： discard=丢弃 / save_only=仅保存 / sync=正常同步 / sync_separate=单独分组		
SHA清理配置
变量名	默认值	说明
SHA_CLEANUP_ENABLED	true	是否启用自动清理
SHA_CLEANUP_DAYS	7	清理N天前的SHA记录
SHA_CLEANUP_INTERVAL_LOOPS	10	每N轮执行一次清理
💡 定期清理旧SHA记录可以重新扫描仓库，避免错过新密钥		
强制冷却配置
变量名	默认值	说明
FORCED_COOLDOWN_ENABLED	false	是否启用强制冷却
FORCED_COOLDOWN_HOURS_PER_QUERY	0	每个查询后冷却时间（小时）
FORCED_COOLDOWN_HOURS_PER_LOOP	0	每轮搜索后冷却时间（小时）
💡 支持固定值（如1）或范围（如1-3或0.5-1.5）		
文本存储配置（仅STORAGE_TYPE=text）
变量名	默认值	说明
VALID_KEY_PREFIX	keys/keys_valid_	有效密钥文件前缀
RATE_LIMITED_KEY_PREFIX	keys/key_429_	429密钥文件前缀
PAID_KEY_PREFIX	keys/keys_paid_	付费密钥文件前缀
KEYS_SEND_PREFIX	keys/keys_send_	已发送密钥文件前缀
VALID_KEY_DETAIL_PREFIX	logs/keys_valid_detail_	有效密钥日志前缀
RATE_LIMITED_KEY_DETAIL_PREFIX	logs/key_429_detail_	429密钥日志前缀
PAID_KEY_DETAIL_PREFIX	logs/keys_paid_detail_	付费密钥日志前缀
KEYS_SEND_DETAIL_PREFIX	logs/keys_send_detail_	已发送密钥日志前缀
配置示例 📝
DOTENV
# GitHub认证（二选一）
GITHUB_AUTH_MODE=token  # 或 web
GITHUB_TOKENS=ghp_xxxx,ghp_yyyy  # Token模式
# GITHUB_SESSION=session1,session2  # Web模式

# 基础配置
DATA_PATH=/app/data
STORAGE_TYPE=sql
DB_TYPE=sqlite
PROXY=http://proxy.example.com:8080  # 可选
查询配置文件 🔍
编辑 queries.txt 文件自定义搜索规则：

⚠️ 重要提醒：query 是本项目的核心！好的表达式可以让搜索更高效，需要发挥自己的想象力！🧠💡

TEXT
# GitHub搜索查询配置文件
# 每行一个查询语句，支持GitHub搜索语法
# 以#开头的行为注释，空行会被忽略

# 基础搜索
AIzaSy in:file
AizaSy in:file filename:.env
📖 搜索语法参考： GitHub Code Search Syntax 📚

🎯 核心提示：创造性的查询表达式是成功的关键，多尝试不同的组合！

🔒 安全注意事项
GitHub Token/Session权限最小化，定期轮换
不要将 .env 文件提交到版本控制
定期检查和清理发现的密钥
数据库密码使用强密码并妥善保管
💖 享受使用 Hajimi King Pro的快乐时光！ 🎉
