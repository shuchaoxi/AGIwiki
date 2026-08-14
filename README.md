# AGIWiki

[![CI](https://github.com/shuchaoxi/AGIwiki/actions/workflows/ci.yml/badge.svg)](https://github.com/shuchaoxi/AGIwiki/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/shuchaoxi/AGIwiki/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

> AGI 时代的个人 Wiki：把你的资料整理成多个 Agent 都能调用的事实记忆。

AGIWiki 是一个开源、本地优先、面向个人的事实记忆工具。用户选择的 Agent 可通过
`agiwiki-author-memory` Skill 阅读 PDF、手册、网页导出、代码和笔记，将知识写入普通
JSON Workspace；AGIWiki 负责机械校验、
构建不可变 Memory Pack、安装到个人 Home、建立可重建的本地索引，并通过 CLI 或 stdio
MCP 提供给其他 Agent。

```text
资料 + 用户选择的 Agent
          ↓
可编辑 JSON Workspace
          ↓ validate / build
不可变 Memory Pack
          ↓ install / activate
AGIWiki Home
          ↓
CLI + stdio MCP
```

## 产品边界

首版只做：

- JSON Workspace；
- Source、Fact、Concept、Procedure、Troubleshooting；
- 可重现的 Pack identity 和完整性校验；
- 本地 SQLite FTS 缓存；
- 多 Pack 安装与精确激活；
- `find_memory` / `get_memory`；
- 一个 provider-neutral Agent Skill。

首版不提供网站、HTML Wiki、HTTP 服务、账号、社区、联邦、公共审核、企业权限、云同步
或动态对话记忆。原始资料和 Workspace 默认不离开用户设备。

## 编辑边界

人和 Agent 修改的是 Workspace 中的 `entries/*.json`，不是已安装 Pack：

```text
修改 Workspace JSON → validate → build 新 Pack → 显式 activate
```

直接修改 installed Pack 会使摘要失配，读取必须失败。

## 当前状态

个人版最小闭环已经可以本地运行。接口在 `0.x` 阶段可能发生不兼容变化；不要把它
宣传为已经稳定发布的公共知识平台，也不要把示例词条当作经过事实审定的知识。

## 五分钟本地试用

先在隔离环境中安装当前 checkout：

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -e .
```

随后运行（不需要激活虚拟环境）：

```bash
# 0. 初始化自己的空 Workspace（不会覆盖现有目录）
./.venv/bin/agiwiki workspace init ./my-memory \
  --slug my-memory --title "我的事实记忆" --locale zh-CN

# 1. 校验人或 Agent 编写的 JSON Workspace；示例已经包含可校验内容
./.venv/bin/agiwiki workspace validate examples/minimal-memory

# 2. 构建纯 JSON、不可变的 Memory Pack
./.venv/bin/agiwiki pack build examples/minimal-memory ./demo.memory-pack

# 3. 初始化个人 Home，安装并查看 Pack ID
./.venv/bin/agiwiki home init
PACK_ID="$(./.venv/bin/agiwiki home install ./demo.memory-pack | \
  ./.venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["pack_id"])')"
./.venv/bin/agiwiki home list

# 4. 将上一步返回的精确 Pack 显式激活
./.venv/bin/agiwiki home activate "$PACK_ID"

# 5. 搜索并精确读取词条
./.venv/bin/agiwiki memory find "规范化 JSON"
./.venv/bin/agiwiki memory get entry_44444444444444444444444444444444 \
  --pack-id "$PACK_ID"
```

Core 全程无网络、不会调用模型。默认 Home 位于平台的用户数据目录；测试或隔离实例可用
全局参数 `--home /some/private/path` 覆盖。

## Agent 接入

安装 MCP 可选依赖后，在支持 stdio MCP 的 Agent 中运行：

```bash
./.venv/bin/python -m pip install -e '.[mcp]'
./.venv/bin/agiwiki-mcp
```

MCP 只有一个资源和两个内容只读工具：

- `agiwiki://catalog`
- `find_memory`
- `get_memory`

查询不会修改 Workspace 或 Pack，也没有管理工具；运行时可能创建可丢弃索引，或把完整性
失败的安装包标成 `BROKEN` 并从激活集合隔离。

构建、安装和激活不会暴露给 Agent。可复制
[`skills/agiwiki-memory`](https://github.com/shuchaoxi/AGIwiki/tree/main/skills/agiwiki-memory) 作为 Agent 的只读使用说明；
复制 [`skills/agiwiki-author-memory`](https://github.com/shuchaoxi/AGIwiki/tree/main/skills/agiwiki-author-memory) 可让具备本地
文件权限的 Agent 把用户明确选定的资料编译成 Workspace。作者 Skill 不会进入只读 MCP。
必须复制完整 Skill 目录，因为作者 Skill 还引用 `references/`。两个 Skill 只作为 GitHub
源码仓库和源码发行包的一部分交付，不会随 runtime wheel 自动写入任何 Agent 的配置目录。
MCP 和 Skill 的接入模板见 [`docs/agent-integration.md`](https://github.com/shuchaoxi/AGIwiki/blob/main/docs/agent-integration.md)。

## 数据模型

- `agiwiki.json`：Workspace 身份和版本；
- `sources/*.json`：原资料的版本、内容摘要（digest）和可移植定位；
- `entries/*.json`：事实、概念、教程或排障记忆；
- `pack.json + sources.json + entries/*.json`：可移动的不可变 Pack；
- `Home/indexes/*.sqlite3`：可删除、可重建的本地搜索缓存，不属于 Pack。

详细边界见 [`docs/architecture.md`](https://github.com/shuchaoxi/AGIwiki/blob/main/docs/architecture.md) 与
[`docs/security-model.md`](https://github.com/shuchaoxi/AGIwiki/blob/main/docs/security-model.md)。

示例 Workspace 引用仓库内一份可复算 SHA-256 的二级证据笔记。它用于演示引用和安全
实践，不冒充 Python 官方资料镜像；运行时行为仍应核对匹配版本的官方文档。

## 支持范围

0.1 的完整闭环和私有文件权限只在 Linux 上验收。Python 3.12–3.14 由 CI 覆盖；macOS
和 Windows 路径代码仍是实验性支持，Windows 的 `chmod` 不等同于独立 ACL 保证。
当前实现以完整性优先，每次读取会验证 Pack 和可重建索引；面向的是小到中型个人记忆包，
Schema 的安全上限不是性能承诺。

## 开发

需要 Python 3.12+：

```bash
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check .
```

迁移来源和未迁移边界见 [`ORIGIN.md`](https://github.com/shuchaoxi/AGIwiki/blob/main/ORIGIN.md) 与
[`MIGRATION.md`](https://github.com/shuchaoxi/AGIwiki/blob/main/MIGRATION.md)。
