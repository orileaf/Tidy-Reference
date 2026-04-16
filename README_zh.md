# tidy-reference

参考文献格式化与核验工具。只需扔进去一个满是原始引用文本的 `.docx` 或 `.txt` 文件，工具即可完成：引用解析、Crossref / Semantic Scholar 查证、LLM 质量评估、人工审核，最终输出标准 BibTeX 和 GB/T 7714-2015 格式的参考文献。

---

## 它能做什么

比如你有这样一份参考文献列表：

```
[1] J. Smith, "Deep learning in optics," Nature Photonics, vol. 15, pp. 312-320, 2021.
[2] Li, Wei, and Y. Zhang. "Quantum cascade lasers: a review." IEEE J. Sel. Top. Quantum Electron. 28, 2022.
```

这个工具可以帮你把它转换成经过 API 核实、结构完整、格式规范的参考文献——全程自动，疑点由你把关。

---

## 功能亮点

- **解析**：从 `.docx` / `.txt` 中提取原始引用（支持 `[1]`、`1.`、纯数字编号等多种格式）
- **LLM 结构化**：将引用文本解析为 title、authors、journal、year、volume、issue、pages、doi、publisher、location、edition 等字段（支持所有 OpenAI 兼容 API）
- **4 步查证链**：DOI 精确查找 → 标题+期刊模糊匹配 → 期刊+卷期页结构化查找 → MCP 网络搜索兜底
- **LLM 质量评估**：自动将每条结果分为高/中/低可信度
- **人工审核**：中低可信度条目由你逐条确认或修正后再导出；可修正字段包括 title、authors、journal、year、volume、pages、doi、type 及图书专用字段 publisher、location、edition
- **双格式输出**：BibTeX（`.bib`）+ GB/T 7714-2015 纯文本
- **按类型检查必填字段**：例如图书检查 edition/publisher/location，期刊文章检查 pages 或 doi
- **防崩溃**：结果每 20 条即时落盘，中断不会丢失数据

---

## 环境要求

### Python

Python 3.10 或更高版本。

### API 密钥

只需要两个密钥：

| 变量 | 是否必需 | 获取方式 |
|------|---------|---------|
| **任意 OpenAI 兼容 API 密钥** | 必须 | DashScope、OpenAI、Groq、Ollama 等，见下方 [LLM 配置](#llm-配置) |
| `CROSSREF_MAILTO` | 必须 | 任意有效邮箱（Crossref 服务条款要求） |
| `MINIMAX_API_KEY` | **可选** | 仅用于 MCP 第 4 步网络搜索兜底，见下方说明 |
| `SEMANTIC_SCHOLAR_API_KEY` | 可选 | [Semantic Scholar](https://www.semanticscholar.org/product/api)（无需密钥也能用，有频率限制） |

---

## LLM 配置

本工具使用 **OpenAI SDK**，支持所有兼容 OpenAI API 格式的提供商：

| 提供商 | API 密钥变量 | Base URL |
|--------|------------|---------|
| [阿里云 DashScope](https://dashscope.console.aliyun.com/) | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| [OpenAI](https://platform.openai.com/) | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| [Groq](https://console.groq.com/) | `OPENAI_API_KEY` | `https://api.groq.com/openai/v1` |
| [Ollama](https://ollama.com/)（本地） | `OPENAI_API_KEY`（任意值） | `http://localhost:11434/v1` |
| 其他兼容 API | `OPENAI_API_KEY` | 提供商地址 |

优先级：`DASHSCOPE_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY`

在 `config.env` 中配置（见下方[安装步骤](#安装步骤)）。

### MCP 第 4 步（可选）

MCP 是**网络搜索兜底机制**——仅当前 3 步（DOI、标题、期刊）都查不到结果时才会启用，完全可选。

启用方法：
1. 将 `.mcp.json.example` 复制为 `.mcp.json`
2. 打开 `.mcp.json`，将 `/path/to/your/tidy-reference` 替换为你的实际项目路径
3. 安装 MiniMax MCP：`pip install mcp`（或 `uvx minimax-coding-plan-mcp -y -`）
4. 在 `config.env` 中设置 `MINIMAX_API_KEY`

**不使用 MCP**（推荐大多数用户）：
```bash
python -m src.skill search --no-mcp
# 或在 config.env 中设置 DISABLE_MCP=1
```

---

## 安装步骤

```bash
# 1. 克隆仓库
git clone <your-repo-url>
cd tidy-reference

# 2. 安装 Python 依赖
pip install python-docx requests tqdm openai

# 3. 配置 API 密钥
cp config.env.example config.env
# 编辑 config.env，至少填写：
#   OPENAI_API_KEY（或 DASHSCOPE_API_KEY）
#   OPENAI_BASE_URL
#   OPENAI_MODEL
#   CROSSREF_MAILTO
```

> 如果系统 `python3` 不在 PATH 上，打开 `config.env`，找到 `PIPTHON` 行，填入你的 Python 路径。

---

## 快速上手

### 第 0 步 — 准备输入文件

把你的 `.docx` 或 `.txt` 参考文献文件放在任意位置（推荐放在项目根目录，方便引用）。项目默认读取 `1.docx`，但也可以指定任意路径：

```bash
# 默认读取项目根目录下的 1.docx
python -m src.skill run

# 也可以直接指定文件路径
python -m src.skill run /绝对路径/你的参考文献.docx
python -m src.skill run ./my_references.txt
```

文件格式：每行一条引用，自动识别常见编号格式（`[1]`、`1.`、`1 ` 等）。

### 第 1 步 — 运行完整流程

```bash
python -m src.skill run your_refs.docx
```

所有阶段自动连续运行。质量评估结束后，流程会暂停，提示：

```
Next steps:
  python -m src.skill review --review   # 审核不确定条目
  python -m src.skill review --approve   # 合并并生成报告
  python -m src.skill export             # 导出参考文献
```

### 第 2 步 — 审核不确定条目

中低可信度的条目需要你判断。运行交互式审核：

```bash
python -m src.skill review --review
```

工具会逐条展示原始引用与检索结果，按 `a` 批准、`e` 修正字段、`d` 跳过、`q` 保存退出。

### 第 3 步 — 导出

```bash
python -m src.skill export
```

输出文件位于 `data/05_export/`：
- `references.bib` — BibTeX 格式
- `references_gb.txt` — GB/T 7714-2015 格式
- `bib_export_report.md` — 每条导出条目的警告与备注

---

## 分步命令参考

流程由 5 个独立阶段组成，可分开运行以便精细控制：

```bash
# 阶段 1 — 解析：从文档中提取原始引用文本
python -m src.skill parse your_refs.docx
# 输出：data/01_raw/refs_raw.json

# 阶段 2 — LLM 结构化提取
python -m src.skill llm
# 输出：data/02_llm/llm_results.json

# 阶段 3 — 查证链（不含 MCP）
python -m src.skill search --no-mcp
# 输出：data/03_search/search_results.json

# 阶段 4 — LLM 质量评估
python -m src.skill review
# 输出：data/04_quality/qa_results.json + qa_review.json

# 阶段 5 — 合并 + 导出
python -m src.skill review --approve   # 先合并已批准条目
python -m src.skill export             # 再导出
```

或使用快捷脚本：

```bash
bash scripts/run_all.sh              # 阶段 1–4，运行到审核步骤后暂停
bash scripts/run_review.sh --review   # 交互式审核
bash scripts/run_review.sh --approve  # 合并已批准条目并生成报告
bash scripts/run_export.sh            # 导出参考文献
```

---

## 输出文件说明

| 文件 | 内容 |
|------|------|
| `data/01_raw/refs_raw.json` | 从文档中提取的原始引用文本 |
| `data/02_llm/llm_results.json` | LLM 解析出的结构化字段 |
| `data/03_search/search_results.json` | API 查证结果（按渠道：Crossref、Semantic Scholar、MCP） |
| `data/04_quality/qa_approved.json` | 已批准、可导出的条目（含图书的 type、publisher、location、edition） |
| `data/05_export/references.bib` | BibTeX 格式参考文献 |
| `data/05_export/references_gb.txt` | GB/T 7714-2015 格式参考文献 |
| `data/05_export/bib_export_report.md` | 按类型分类的警告：缺失必填字段、类型冲突、MCP URL 检查、作者格式异常 |

---

## 人工审核快捷键

在 `review --review` 界面中，各条目显示原始引用与检索结果对照：

| 按键 | 作用 |
|------|------|
| `a` | 直接批准 |
| `e` | 编辑（修正某个字段——修正后自动批准） |
| `p` | 预览修改 |
| `d` | 跳过（不导出此条目） |
| `q` | 保存并退出 |

可修正字段（`e`）：title, authors, journal, year, volume, pages, doi, type, **publisher**, **location**, **edition**。
修正后字段优先级最高，会覆盖其他所有数据来源。

**对于无法查到的条目**（审核时跳过），在 `data/04_quality/manual_research.json` 中填写 `research_text`，然后运行
`python -m src.skill review --manual` 重新解析并质检。
