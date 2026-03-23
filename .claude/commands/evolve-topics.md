你是本知识库的"主题进化引擎"。你的任务是分析当前知识库的内容覆盖度、深度、视觉资料完整性，找出知识空白和不足，自动生成下一轮需要调研的主题列表。

## 知识架构

读取 `project.json` 了解知识层级结构、目录布局和领域上下文。

## 执行步骤

### 第一步：扫描知识库现状

运行以下诊断命令（一次性全部执行）：

```bash
# 读取项目配置
VAULT_DIR="$(pwd)"
PROJECT="$VAULT_DIR/project.json"
RAW_DIR=$(jq -r '.project.vault_dirs.raw_materials // "99-原始资料"' "$PROJECT")

echo "=== 1. 各目录笔记分布 ==="
for dir in [0-9]*-* ; do
  [ -d "$dir" ] && echo "$dir: $(find "$dir" -name "*.md" | wc -l) 篇"
done

echo ""
echo "=== 2. 所有笔记标题 ==="
find . -name "*.md" -not -path "./.claude/*" -not -path "./.git/*" -not -path "./${RAW_DIR}/*" \
  -exec grep -l "^---" {} \; | while read f; do
  title=$(grep "^title:" "$f" | head -1 | sed 's/title: *//;s/"//g')
  layer=$(grep "^knowledge_layer:" "$f" | head -1 | sed 's/knowledge_layer: *//;s/"//g')
  echo "  [$layer] $title  ($f)"
done

echo ""
echo "=== 3. 断链分析（wikilink 指向不存在的笔记）==="
grep -roh "\[\[[^]|]*" --include="*.md" | sed 's/\[\[//' | sort -u | while read name; do
  found=$(find . -name "$name.md" -not -path "./.git/*" 2>/dev/null | head -1)
  [ -z "$found" ] && echo "  缺失: [[$name]]"
done

echo ""
echo "=== 4. 视觉资料完整性 ==="
has_diagram=$(grep -rl "mermaid\|\.svg\|image-ref" --include="*.md" | wc -l)
total=$(find . -name "*.md" -not -path "./.claude/*" -not -path "./.git/*" -not -path "./${RAW_DIR}/*" | wc -l)
echo "  含示意图/图片引用的笔记: $has_diagram / $total"
echo "  无任何视觉资料的笔记:"
find . -name "*.md" -not -path "./.claude/*" -not -path "./.git/*" -not -path "./${RAW_DIR}/*" | while read f; do
  if ! grep -q "mermaid\|\.svg\|image-ref\|!\\[\\[" "$f"; then
    echo "    $f"
  fi
done

echo ""
echo "=== 5. 参数表完整性 ==="
has_params=$(grep -rl "参数速查表\|Parameter.*Table\|Reference Table" --include="*.md" | wc -l)
echo "  含参数速查表: $has_params / $total"

echo ""
echo "=== 6. wikilink 密度 ==="
total_links=$(grep -roh "\[\[[^]]*\]\]" --include="*.md" | wc -l)
echo "  总笔记: $total | 总链接: $total_links | 密度: $(echo "scale=1; $total_links / ($total + 1)" | bc) links/篇"

echo ""
echo "=== 7. 已下载的参考资料 ==="
find "${RAW_DIR}" -type f -not -name "*.txt" -not -name "*.md" 2>/dev/null | head -20

echo ""
echo "=== 8. 已完成主题数 ==="
[ -f completed-topics.txt ] && echo "  $(wc -l < completed-topics.txt) 个" || echo "  0 个"
```

### 第二步：多维度缺口分析

基于诊断数据，从以下 6 个维度评估：

1. **覆盖度**：哪些子目录笔记数 < 3？这些是结构性空白。
2. **深度**：哪些笔记缺少参数速查表、缺少具体数据、内容过于概括？
3. **断链**：wikilink 指向不存在的笔记 = 最明确的知识空白，优先填补。
4. **视觉**：哪些笔记没有 Mermaid 图或图片引用？尤其是涉及尺寸关系和空间布局的笔记。
5. **关联**：wikilink 密度 < 3 的笔记需要补充关联。
6. **工具缺口**：有没有主题需要写脚本才能更好地整理（如对比表、计算表）？

### 第三步：生成下一轮主题

输出文件 `next-round-topics.txt`，格式：

```
# 第 N 轮 - 自动生成于 {日期}
# 知识库现状: {笔记数}篇 | 链接密度: {数值} | 断链: {数量}
# 本轮重点: {一句话描述}

## P1 - 关键空白（断链 + 结构性缺失）
{主题}|{层级}|{原因，如"断链: [[xxx]] 被引用 3 次但不存在"}
{主题}|{层级}|{原因}

## P2 - 深化补充（参数/视觉/深度不足）
{主题}|{层级}|{原因，如"笔记《xxx》缺少参数速查表和 Mermaid 图"}
{主题}|{层级}|{原因}

## P3 - 边缘扩展（新场景/新材料/趋势）
{主题}|{层级}|{原因}
{主题}|{层级}|{原因}

## TOOL - 需要工具辅助的特殊任务
{任务描述}|{工具类型: python/curl/数据整合}|{原因}
```

每个优先级至少 3 个主题，TOOL 至少 1 个，总计 10-20 个。

### 第四步：更新状态索引

创建或更新 `00-索引/知识库状态.md`：

```markdown
---
title: "知识库状态"
date: {今天}
auto_generated: true
---

# 知识库状态

## 统计概览
| 指标 | 数值 |
|------|------|
| 笔记总数 | {N} |
| wikilink 总数 | {N} |
| 链接密度 | {N/篇} |
| 断链数 | {N} |
| 含视觉资料笔记 | {N}/{total} |
| 含参数表笔记 | {N}/{total} |

## 各层级覆盖
| 层级 | 笔记数 | 状态 |
|------|--------|------|
| {目录1} | {N} | {充足/不足/空白} |
| {目录2} | {N} | ... |

## 本轮发现的主要缺口
- {缺口1}
- {缺口2}
- {缺口3}

## 下一轮调研重点
- {方向1}
- {方向2}
```

## 质量要求

- 新主题必须足够具体，能直接作为 `/deep-research` 的输入
- 避免与 `completed-topics.txt` 中已完成主题重复
- P1 主题必须有明确的证据支撑（断链记录、空目录）
- TOOL 类任务要说明具体用什么工具做什么（如"用 Python 汇总 X、Y、Z 三家规格为对比表"）
- P2 中关于"补充视觉资料"的主题，要明确指出需要什么类型的图（Mermaid 流程图、SVG 尺寸图、参考图片记录）

## 执行

现在开始分析知识库，生成下一轮主题。
