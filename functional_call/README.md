# Function Call - AMR机器人控制系统（多代理 + 事件流）

基于 **通义千问（DashScope）** 的 AMR（自主移动机器人）控制系统，通过自然语言与机器人交互。
支持 **多代理（multi-agent）** 与 **事件流（进度播报）**：长任务（导航/动作/充电等待）会持续输出可播报事件，适配语音系统的“后台事件消费循环”。

## 功能特性

- 🤖 自然语言控制AMR机器人
- 🔌 通过Modbus协议与机器人通信
- 📊 实时获取机器人状态、电池信息、任务统计等
- 🚀 基于FastAPI的HTTP服务器
- 🧠 使用通义千问模型进行智能决策（DashScope 原生协议，可通过配置更换模型）

## 环境要求

- Python 3.10+
- Conda环境管理器

## 安装步骤

1. 克隆仓库：
```bash
git clone git@github.com:fudashennb/functional_call.git
cd functional_call
```

2. 创建并激活conda环境：
```bash
conda create -n text_to_speech python=3.10
conda activate text_to_speech
```

3. 安装依赖：
```bash
pip install -r requirements.txt  # 如果有的话
```

4. 配置环境变量：
```bash
cp .env.example .env
# 编辑 .env 文件，填入您的Gemini API密钥
```

## 配置说明

### API密钥配置（DashScope）

您需要获取Gemini API密钥并配置：

1. 登录阿里云账号，并进入 DashScope 控制台：`https://dashscope.console.aliyun.com/overview`
2. 首次使用请先“开通服务”
3. 进入 “API-KEY 管理” 创建新的 API Key（请妥善保存）
4. 创建 `.env` 文件（可以复制 `.env.example`）
5. 在 `.env` 文件中设置：
```bash
DASHSCOPE_API_KEY=your_actual_api_key_here
# 可选：模型名，默认 qwen-plus
QWEN_MODEL=qwen-plus
# 可选：OpenAI兼容接口 base_url（默认已是DashScope compatible-mode）
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

> 说明：本仓库由于安全策略不直接提供 `.env.example` 文件，你可以参考 `env.example` 复制为 `.env`。

#### 快速验证 API Key 是否可用（可选）
在服务端机器上执行：
```bash
export DASHSCOPE_API_KEY=xxxxx
python -c "import requests,os; print('key ok' if os.getenv('DASHSCOPE_API_KEY') else 'missing')"
```

### Modbus配置

如果需要通过SSH隧道连接到远程AGV设备：

```bash
ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218
```

## 使用方法

### 方式1：使用启动脚本（推荐）

启动脚本会自动检查并建立SSH隧道：

```bash
./start_server.sh
```

### 方式2：手动启动

1. 建立SSH隧道（如果尚未建立）：
```bash
ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218
```

2. 启动服务器：
```bash
conda activate text_to_speech
python3 voice_server.py
```

服务器将在 `http://0.0.0.0:8766` 启动（可用 `FC_SERVER_PORT` 修改端口）。

### 启用本地路由模型（BART + AdaptiveClassifier，复用 agenticSeek 思路）
本项目默认使用“规则 + 运行态优先”的路由。若你希望进一步采用本地模型增强（BART zero-shot + AdaptiveClassifier 投票），请：

1. 安装依赖（首次启用会下载模型权重，耗时较长）：
```bash
pip install -r requirements.txt
```
2. 启用开关：
```bash
export ENABLE_LOCAL_ROUTER_MODELS=true
```
3. 启动服务后即可生效（日志会输出本地路由投票详情）。

### API端点（语音系统对接）

- `POST /v1/voice/query` - 发送对话请求（兼容你当前语音系统的 `{"query": "..."}`
  ```json
  {
    "query": "导航到站点一",
    "session_id": "optional-session-id"
  }
  ```

  - 短任务：HTTP 200，直接返回 `resultMsg`
  - 长任务：HTTP 202，返回 `request_id` + 第一条 `resultMsg`（例如“收到指令，开始执行。”）

- `GET /v1/voice/events/{request_id}?after=0&limit=200` - 轮询获取事件流
  - 每条事件包含 `speak_text`（可直接播报）

## 项目结构

```
function_call/
├── agent/
│   ├── config.py           # 配置文件
│   ├── gemini_agent.py     # 旧实现（保留）
│   ├── gemini_server.py    # 旧服务端（保留）
│   └── modbus_ai_cmd.py    # Modbus通信接口
├── src/
│   ├── sr_modbus_model.py  # Modbus数据模型
│   └── sr_modbus_sdk.py    # Modbus SDK
├── log_config.py           # 日志配置（含行号 + trace字段）
├── voice_server.py         # 新：语音服务端（多代理 + 事件流）
├── core/                   # 新：基础设施层（配置/语言/事件/Job）
├── routing/                # 新：路由系统
├── agents/                 # 新：多代理
├── tools/                  # 新：工具系统（RobotClient 等）
├── llm/                    # 新：DashScope Provider
├── memory/                 # 新：会话与运行态存储
├── .env.example            # 环境变量示例
└── .gitignore              # Git忽略文件
```

## 机器人控制指令

系统支持以下功能：

### 基础控制
- 移动到指定站点：`导航到站点一`
- 执行动作任务：`顶升到50`
- 获取电池信息：`查看电池状态`

### 信息查询
- AGV基本信息、任务状态
- 性能统计、故障信息
- 充电统计、工单信息
- 各种趋势数据

## 注意事项

⚠️ **安全提醒**：
- 不要将 `.env` 文件提交到Git仓库
- 不要在代码中硬编码API密钥
- 定期更换API密钥

## 许可证

[添加您的许可证信息]

## 联系方式

[添加您的联系方式]

