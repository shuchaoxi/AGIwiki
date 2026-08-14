# AGIWiki JSON 示例证据笔记

这是一份随仓库发布的、由 AGIWiki 项目维护者编写的二级资料，只用于演示
Source、Locator 和 Entry 的绑定。它不是 Python 官方文档的镜像，也不应被当作
独立的权威规范。Python 行为应以匹配版本的官方文档和运行时为准：
<https://docs.python.org/3.12/library/json.html>。

## Python json 模块事实

在 Python 3.12 的 `json.dumps` 中，`ensure_ascii=False` 会让非 ASCII 字符以
原字符形式出现在返回的字符串中；默认的 `True` 会转义这些字符。

`JSONDecodeError` 提供 `pos`、`lineno` 和 `colno` 等位置信息，可用于定位解析失败
的位置。位置只能指出解析器停止的位置，不能单独证明根因。

## 规范化 JSON（AGIWiki 约定）

AGIWiki 所说的“规范化 JSON”是项目自己的确定性编码约定：对象键稳定排序、UTF-8、
紧凑分隔符、拒绝 NaN/Infinity 和重复键。相同的 JSON 语义在该约定下应产生相同字节，
从而可以计算稳定摘要。它不替代 JSON Schema，也不证明内容真实。

## AGIWiki 安全写入建议

若要生成便于人阅读、保留中文的 JSON，可先用
`json.dumps(value, ensure_ascii=False, indent=2)` 生成文本，再用 `json.loads` 回读并
比较语义。写文件时明确使用 UTF-8，优先写入新文件，并在替换原文件前完成回读验证。
不要把密码、令牌或私钥写入输出；不要盲目覆盖现有文件或跟随未经确认的符号链接。

## JSONDecodeError 排障建议

先保留失败输入的只读副本，确认输入确实应为单个 JSON 文档，而不是日志或 JSON Lines。
读取异常给出的行、列和字符位置：文件开头的失败可提示空文件、BOM 或编码问题；对象或
数组内部的失败常需要检查引号、逗号、括号或尾逗号。只在副本中修正，并用 `json.loads`
重新解析、检查预期类型和字段。输入可能含个人信息或凭据，不应整段复制到公开日志。
