#!/bin/bash
# update-context.sh — 调研完成后更新 CLAUDE.md 持久记忆
#
# 用法: bash update-context.sh "主题名称" "创建的笔记1.md,笔记2.md" "关键发现摘要"
#
# 由 run-research.sh 在每个主题完成后自动调用。

VAULT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_MD="$VAULT_DIR/CLAUDE.md"
PROJECT="$VAULT_DIR/project.json"
TOPIC="$1"
NOTES="$2"
FINDINGS="$3"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M')"

[ ! -f "$CLAUDE_MD" ] && exit 0

# 从 project.json 读取原始资料目录
RAW_DIR="99-原始资料"
if [ -f "$PROJECT" ] && command -v jq &>/dev/null; then
    RAW_DIR=$(jq -r '.project.vault_dirs.raw_materials // "99-原始资料"' "$PROJECT")
fi

# ===== 追加调研日志 =====
echo "- [$TIMESTAMP] $TOPIC — 笔记: $NOTES" >> "$CLAUDE_MD"

# ===== 重建已创建笔记清单 =====
{
    echo ""
    echo "<!-- 自动更新：所有已创建笔记列表，用于 wikilink 一致性校验 -->"
    find "$VAULT_DIR" -name "*.md" \
        -not -path "*/.claude/*" \
        -not -path "*/.git/*" \
        -not -path "*/${RAW_DIR}/*" \
        -not -name "CLAUDE.md" \
        -not -name "README.md" | sort | while read f; do
        relpath="${f#$VAULT_DIR/}"
        name="$(basename "${f%.md}")"
        dir="$(dirname "$relpath")"
        title="$(grep '^title:' "$f" 2>/dev/null | head -1 | sed 's/title: *//;s/"//g')"
        [ -z "$title" ] && title="$name"
        echo "- [[$name]] ($dir) — $title"
    done
} > /tmp/dr_notes_registry.tmp

python3 -c "
import re
with open('$CLAUDE_MD', 'r') as f:
    content = f.read()
pattern = r'(## 已创建笔记清单\n).*?((?=\n## )|$)'
with open('/tmp/dr_notes_registry.tmp', 'r') as f:
    new_registry = f.read()
replacement = r'\1' + new_registry.replace('\\\\', '\\\\\\\\') + '\n'
content = re.sub(pattern, replacement, content, flags=re.DOTALL)
with open('$CLAUDE_MD', 'w') as f:
    f.write(content)
" 2>/dev/null

# ===== 更新实体与参数缓存 =====
{
    echo ""
    echo "<!-- 自动更新：高频引用的实体和尺寸参数 -->"
    echo ""

    echo "### 高频引用实体"
    # 提取频繁出现的大写字母开头多字符词汇（通常是品牌/标准）
    grep -roh "[A-Z][a-zA-Z]\{2,\}" --include="*.md" "$VAULT_DIR" 2>/dev/null \
        | grep -v -E "^(The|This|That|These|Those|When|Where|What|Which|From|With|Into|About|Each|Every|Some|Many|Most|More|Also|Only|Just|Very|Much|Even|Still|Well|Back|Over|Down|Away|Here|There|Then|Than|Both|Such|Like|Same|Other|After|Before|Between|Under|Above)$" \
        | sort | uniq -c | sort -rn | head -20 | while read count entity; do
        echo "- $entity ($count 次)"
    done

    echo ""
    echo "### 高频引用尺寸"
    grep -roh "[0-9]\{2,4\}\s*mm" --include="*.md" "$VAULT_DIR" 2>/dev/null | sort | uniq -c | sort -rn | head -15 | while read count dim; do
        echo "- $dim ($count 次)"
    done
} > /tmp/dr_cache.tmp

python3 -c "
import re
with open('$CLAUDE_MD', 'r') as f:
    content = f.read()
pattern = r'(## 实体与参数缓存\n).*?((?=\n## )|$)'
with open('/tmp/dr_cache.tmp', 'r') as f:
    new_cache = f.read()
replacement = r'\1' + new_cache.replace('\\\\', '\\\\\\\\') + '\n'
content = re.sub(pattern, replacement, content, flags=re.DOTALL)
with open('$CLAUDE_MD', 'w') as f:
    f.write(content)
" 2>/dev/null

# 清理临时文件
rm -f /tmp/dr_notes_registry.tmp /tmp/dr_cache.tmp
