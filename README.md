# GraphRAG-Augmented Godot 4 Assistant
# 基于 GraphRAG 与符号校验的 Godot 4 领域代码生成助手

![image-20261017011217302](https://cdn.jsdelivr.net/gh/KuiMian/NoteImage@master/2026/10/upgit_20261017_image-20261017011217302.png)

[English](#english) | [中文说明](#chinese)

---

<a name="english"></a>
## English

An intelligent, neuro-symbolic coding assistant plugin for Godot 4. Backed by **GraphRAG** (symbolic knowledge graph retrieval) and a **closed-loop static semantic verifier**, it drastically suppresses LLM API hallucinations and version discrepancies (Godot 3 vs 4) for domain-specific game development.

### 🌟 Key Features

* **Knowledge-Grounded Generation (GraphRAG):** Extracts Godot 4 class inheritance trees, singletons, and core API signatures to ground LLM code synthesis in ground-truth engine specs.
* **Closed-Loop Static Self-Correction:** Incorporates an in-situ AST/symbol analyzer that automatically catches unverified symbols, deprecated syntax (`export(...)`), and type issues, feeding compiler-like diagnostics back to the LLM for automated repair.
* **Contracted Structural Outputs:** Segregates generated outputs into three distinct semantics (`<approach>`, `<gdscript>`, and `<key_points>`), keeping your editor view clean, organized, and free from internal LLM chatter.
* **Asynchronous, Multi-Threaded Dock:** Non-blocking background worker threads handle inter-process communication (IPC) via pipes without freezing the Godot editor main thread.

### 🛠️ Prerequisites

* **Godot 4.x** (Standard version)
* **Python 3.10+**
* **uv** (recommended for blazing-fast environment management):
  ```bash
  # Windows (PowerShell)
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
* A **Gemini API Key** from [Google AI Studio](https://aistudio.google.com/).

### 🚀 Quick Start Guide

#### Step 1: Dump Godot 4 API Documentation
Before running the backend, dump the engine API docs directly from your Godot 4 executable to initialize the knowledge base:
```bash
# Run from your terminal (replace `godot` with your Godot executable name/path if needed)
godot --doctool . --dump-extension-api
```
Move the generated API/documentation dumps into the backend assets directory:
```text
assets/
├── godot4_api_graph.json
└── godot_enriched_kb.json
```
*(If you are building the graph from scratch, execute your graph construction script located in `pipeline/` to generate these artifacts).*

#### Step 2: Install Python Dependencies
Navigate to the plugin backend directory and sync the environment:
```bash
cd addons/graphrag_assistant/backend/
uv sync
```
*(Alternatively, `uv run` will automatically set up the virtual environment on its first invocation inside Godot).*

#### Step 3: Enable the Plugin in Godot
1. Place the `graphrag_assistant` folder under your project's `res://addons/` directory:
    (Here, the backend is the root directory. It is recommended to package the Backend into the addons plugin directory.)
   ```text
   res://
   └── addons/
       └── graphrag_assistant/
           ├── dock/
           ├── backend/
           ├── assets/
           └── plugin.cfg
   ```
2. Open Godot, navigate to **Project -> Project Settings -> Plugins**.
3. Locate **GraphRAG GDScript Assistant** and toggle the status to **Enable**.
4. The **Assistant** dock will appear in your editor dock panels.

#### Step 4: Configure API Key
1. Switch to the **Settings (设置)** tab in the Assistant dock.
2. Enter your `Gemini API Key`.
3. Set your preferred thinking budget and interface language (`zh` or `en`).
4. Click **Save Configuration (保存设置)**.

---

<a name="chinese"></a>
## 中文说明

面向 Godot 4 游戏引擎的智能神经符号（Neuro-Symbolic）编程辅助插件。通过**符号知识图谱检索（GraphRAG）**与**闭环静态语义校验器**，大幅降低大模型在领域特定语言（GDScript）中的 API 幻觉率，并彻底规避 Godot 3 到 4 的跨版本语法断代问题。

### 🌟 核心特性

* **图谱增强检索生成 (GraphRAG)：** 抽取引擎层类继承树、内置单例及核心 API 签名，为大模型代码生成提供真实的 Ground Truth 规格约束。
* **闭环静态自愈修正 (Self-Correction)：** 内置轻量级 AST/符号分析器，自动捕获未定义符号、废弃语法（如 `export(...)`）及类型错误，将类似编译器的诊断反馈回模型进行全自动修复。
* **契约化结构输出：** 严格按照 `<approach>`（思路）、`<gdscript>`（源码）与 `<key_points>`（要点）三种语义分流，彻底杜绝自省废话与代码杂糅。
* **异步多线程面板：** 通过专属后台 Worker 线程管理管道进程通信（IPC），流式推送执行状态，保障 Godot 编辑器主线程丝滑流畅。

### 🛠️ 环境要求

* **Godot 4.x**（标准版）
* **Python 3.10+**
* **uv**（推荐的现代轻量 Python 环境管理工具）：
  ```bash
  # Windows (PowerShell)
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
* 从 [Google AI Studio](https://aistudio.google.com/) 获取的 **Gemini API Key**。

### 🚀 快速上手指南

#### 步骤 1：导出 Godot 4 官方文档与 API
在运行后端服务前，通过命令行从你的 Godot 4 可执行文件中导出最新的 API 描述与文档，用于初始化本地知识库：
```bash
# 在终端中执行（根据实际情况替换 godot 的可执行程序名或绝对路径）
godot --doctool . --dump-extension-api
```
将生成与提炼出的图谱文件放置于 `assets/` 目录下：
```text
assets/
├── godot4_api_graph.json
└── godot_enriched_kb.json
```
*(若需从头构建图谱数据，可直接运行 `pipeline/` 目录下的图谱预处理脚本)*。

#### 步骤 2：安装 Python 依赖
打开终端，进入插件的后端目录一键同步依赖(这里的后端为根目录，推荐将backend打包至addons的插件目录下)：
```bash
uv sync
```
*(注：`uv run` 具备环境感知能力，初次在 Godot 中拉起时也会自动按需完成虚拟环境构建)*。

#### 步骤 3：在 Godot 中启用插件
1. 确保整个插件文件夹放置在项目根目录的 `res://addons/` 下：
   ```text
   res://
   └── addons/
       └── graphrag_assistant/
           ├── dock/
           ├── backend/
           ├── assets/
           └── plugin.cfg
   ```
2. 打开 Godot 编辑器，点击 **项目 -> 项目设置 -> 插件 (Plugins)**。
3. 找到 **GraphRAG GDScript Assistant**，勾选 **启用 (Enable)**。
4. 插件面板（Dock）将自动挂载到编辑器面板区域。

#### 步骤 4：配置 API Key
1. 点击面板顶部的 **设置 (Settings)** 选项卡。
2. 输入你的 `Gemini API Key`。
3. 按需设置思考预算（Thinking Budget）和界面语言（中文或英文）。
4. 点击 **保存设置 (Save Configuration)**。


## 📄 License / 开源协议

本项目基于 [MIT License](LICENSE) 协议开源。