# GenUI Project

GenUI 是一个面向工业/HMI 场景的生成式 UI 原型项目。  
核心流程是把自然语言需求转换为可验证的 DSL/HTML，并通过约束验证与修复机制降低风险。

## 核心能力

- 自然语言到 UI 生成：支持 LLM 驱动的界面生成。
- 多阶段 Pipeline：包含 Phase0（Baseline）、Phase1（DSL）、Phase2（约束增强与闭环修复）。
- 安全验证与自动修复：对控件参数进行规则校验，并可自动修复违规项。
- 运行产物可追踪：每次运行都会写入 `runs/<run_id>/`，便于复现和审计。
- Web 可视化入口：基于 Streamlit 的交互界面。

## 项目结构

```text
GenUI_Project/
├── app.py                      # Streamlit 主入口
├── demo_simple.py              # 离线/最小演示（验证+修复+渲染）
├── start.sh                    # 启动脚本（macOS/Linux）
├── requirements.txt            # Python 依赖
├── src/
│   ├── core/                   # Phase0/1/2 核心流程
│   ├── modules/
│   │   ├── rag/                # 文档解析与检索
│   │   ├── verifier/           # 约束验证与修复
│   │   ├── renderer/           # HTML 渲染
│   │   └── runtime/            # 运行时监控/事件记录
│   ├── models/                 # Pydantic 数据模型
│   ├── agents/                 # Prompt 和工具层
│   └── utils/                  # 通用工具（run artifacts 等）
├── scripts/                    # 校验、导出、指标与实验脚本
├── resources/                  # 约束/DSL 等 schema 与基准数据
├── docs/                       # 指标与实验文档
└── runs/                       # 运行产物（已包含一次 sample 演示）
```

## 环境要求

- Python 3.10+（建议 3.11）
- macOS / Linux / Windows（Windows 建议使用 WSL 或手动执行等价命令）
- 可选：Gemini API Key（`GOOGLE_API_KEY`）

## 快速开始

1. 克隆仓库并进入目录

```bash
git clone https://github.com/KKKKJ687/GenUI_Project.git
cd GenUI_Project
```

2. 创建虚拟环境并安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. 配置 API Key（可选）

```bash
export GOOGLE_API_KEY="your_key_here"
```

4. 启动 Web 应用

```bash
streamlit run app.py
```

也可以使用启动脚本：

```bash
chmod +x start.sh
./start.sh
```

## 已包含的 Sample 演示

仓库已包含一次完整运行产物（可复现检查）：

- `runs/20260301_055331_288_t1tms5/`
- `runs/20260301_055331_288_t1tms5/final.html`：最终渲染页面
- `runs/20260301_055331_288_t1tms5/verifier_report.json`：验证报告
- `runs/20260301_055331_288_t1tms5/metrics.json`：运行指标
- `runs/20260301_055331_288_t1tms5/session_log.json`：运行日志
- `runs/20260301_055331_288_t1tms5/intermediate/`：中间轮次产物

## 常用脚本

1. 离线健康检查（推荐先跑）

```bash
python scripts/health_check.py --mock-llm
```

2. 在线健康检查（需要 API Key）

```bash
python scripts/health_check.py --api-key "$GOOGLE_API_KEY"
```

3. 简化演示（验证器 + 自动修复 + 渲染）

```bash
python demo_simple.py
```

4. 渲染确定性检查

```bash
python scripts/render_example.py
```

## 运行产物说明（run artifacts）

每次 Pipeline 运行会生成独立目录：`runs/<run_id>/`。常见文件：

- `input.json`：输入与配置快照
- `model_raw.txt`：模型原始输出
- `dsl_raw*.txt` / `dsl_validated.json`：DSL 生成与修复结果
- `final.html`：最终输出
- `verifier_report.json`：约束校验结论
- `timing.json` / `metrics.json`：性能与质量指标
- `runtime_events.jsonl` / `session_log.json`：运行时事件与审计日志

## 常见问题

1. 提示缺少 `GOOGLE_API_KEY`
- 先执行 `export GOOGLE_API_KEY=...`
- 或在 Web UI 侧边栏输入 API Key
- 或使用 `--mock-llm` 离线模式

2. 启动脚本无执行权限

```bash
chmod +x start.sh
```

3. 依赖安装慢或失败
- 先升级 pip：`python -m pip install -U pip`
- 然后重新安装：`pip install -r requirements.txt`

## 开发与贡献

- 提交前建议运行：

```bash
python scripts/health_check.py --mock-llm
```

- 建议通过 Pull Request 方式提交改动，并附上关键运行截图或 `runs/<run_id>/metrics.json` 结果。
