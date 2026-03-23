#!/bin/bash
# progress.sh — DeepResearch 调研进度面板
#
# 用法:
#   bash progress.sh           # 完整进度面板
#   bash progress.sh --watch   # 每 30 秒自动刷新
#   bash progress.sh --json    # JSON 输出（供外部工具消费）

VAULT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$VAULT_DIR/project.json"
COMPLETED="$VAULT_DIR/completed-topics.txt"
FAILED="$VAULT_DIR/failed-topics.txt"
LOG="$VAULT_DIR/research-progress.log"

# 检查 jq
command -v jq &>/dev/null || { echo "错误: 需要 jq"; exit 1; }
[ -f "$PROJECT" ] || { echo "错误: 未找到 project.json"; exit 1; }

# 读取配置
PROJECT_NAME=$(jq -r '.project.name' "$PROJECT")
RAW_DIR=$(jq -r '.project.vault_dirs.raw_materials // "99-原始资料"' "$PROJECT")

# ================================================================
# 从 project.json 加载主题
# ================================================================
declare -A LAYER_TOPICS LAYER_NAMES LAYER_COLORS
LAYER_IDS=()

while IFS= read -r lid; do
    [ -n "$lid" ] && LAYER_IDS+=("$lid")
done < <(jq -r '.research_topics | keys[]' "$PROJECT" 2>/dev/null)

for lid in "${LAYER_IDS[@]}"; do
    LAYER_TOPICS[$lid]=$(jq -r ".research_topics.\"$lid\" | length" "$PROJECT")
    LAYER_NAMES[$lid]=$(jq -r ".knowledge_layers[] | select(.id == \"$lid\") | .name // \"$lid\"" "$PROJECT")
    LAYER_COLORS[$lid]=$(jq -r ".knowledge_layers[] | select(.id == \"$lid\") | .color // \"green\"" "$PROJECT")
done

PRESET_TOTAL=0
for lid in "${LAYER_IDS[@]}"; do
    PRESET_TOTAL=$((PRESET_TOTAL + ${LAYER_TOPICS[$lid]}))
done

# ================================================================
# 颜色定义
# ================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

color_code() {
    case "$1" in
        red) echo "$RED" ;;
        green) echo "$GREEN" ;;
        yellow) echo "$YELLOW" ;;
        blue) echo "$BLUE" ;;
        purple) echo "$PURPLE" ;;
        cyan) echo "$CYAN" ;;
        *) echo "$GREEN" ;;
    esac
}

# ================================================================
# 工具函数
# ================================================================
is_done() {
    [ -f "$COMPLETED" ] && grep -qFx "$1" "$COMPLETED" 2>/dev/null
}

is_failed() {
    [ -f "$FAILED" ] && grep -qFx "$1" "$FAILED" 2>/dev/null
}

count_done_for_layer() {
    local lid="$1"
    local count=0
    while IFS= read -r topic; do
        [ -n "$topic" ] && is_done "$topic" && count=$((count + 1))
    done < <(jq -r ".research_topics.\"$lid\"[]? // empty" "$PROJECT")
    echo $count
}

count_failed_for_layer() {
    local lid="$1"
    local count=0
    while IFS= read -r topic; do
        [ -n "$topic" ] && is_failed "$topic" && count=$((count + 1))
    done < <(jq -r ".research_topics.\"$lid\"[]? // empty" "$PROJECT")
    echo $count
}

# 进度条
bar() {
    local done=$1 total=$2 width=${3:-30} color=${4:-$GREEN}
    local pct=0
    [ $total -gt 0 ] && pct=$((done * 100 / total))
    local filled=$((done * width / total))
    [ $filled -gt $width ] && filled=$width
    local empty=$((width - filled))

    printf "${color}"
    printf "█%.0s" $(seq 1 $filled 2>/dev/null) || true
    printf "${GRAY}"
    printf "░%.0s" $(seq 1 $empty 2>/dev/null) || true
    printf "${NC} ${BOLD}%3d%%${NC}" $pct
}

# ================================================================
# 主显示函数
# ================================================================
show_progress() {
    local total_done=$(wc -l < "$COMPLETED" 2>/dev/null || echo 0)
    local total_failed=$(wc -l < "$FAILED" 2>/dev/null || echo 0)
    local total_notes=$(find "$VAULT_DIR" -name "*.md" -not -path "*/.claude/*" -not -path "*/.git/*" -not -path "*/${RAW_DIR}/*" -not -name "CLAUDE.md" 2>/dev/null | wc -l)
    local total_links=$(grep -roh "\[\[[^]]*\]\]" --include="*.md" "$VAULT_DIR" 2>/dev/null | wc -l)
    local total_pdfs=$(find "$VAULT_DIR/$RAW_DIR" -name "*.pdf" 2>/dev/null | wc -l)
    local git_tags=$(git -C "$VAULT_DIR" tag -l "round-*" 2>/dev/null | wc -l)

    # 各层级统计
    local preset_done=0
    for lid in "${LAYER_IDS[@]}"; do
        local ld=$(count_done_for_layer "$lid")
        preset_done=$((preset_done + ld))
    done

    # 进化统计
    local evolve_topics=0 evolve_rounds=0
    if [ -f "$COMPLETED" ]; then
        local preset_topics_file=$(mktemp)
        for lid in "${LAYER_IDS[@]}"; do
            jq -r ".research_topics.\"$lid\"[]? // empty" "$PROJECT"
        done > "$preset_topics_file"
        evolve_topics=$(grep -cvFf "$preset_topics_file" "$COMPLETED" 2>/dev/null || echo 0)
        rm -f "$preset_topics_file"
    fi
    evolve_rounds=$(ls "$VAULT_DIR/$RAW_DIR"/evolved-round-*.txt 2>/dev/null | wc -l)

    # 当前状态
    local status="空闲"
    if [ -f "$VAULT_DIR/.research-lock" ]; then
        local lock_pid=$(cat "$VAULT_DIR/.research-lock" 2>/dev/null)
        if kill -0 "$lock_pid" 2>/dev/null; then
            status="${GREEN}运行中${NC} (PID $lock_pid)"
        else
            status="${GRAY}已停止${NC}"
        fi
    fi
    [ -f "$VAULT_DIR/PAUSE" ] && status="${YELLOW}已暂停${NC}"

    # 渲染
    clear 2>/dev/null || true

    echo ""
    echo -e "${BOLD}  ╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}  ║       $PROJECT_NAME · 调研进度面板${NC}"
    echo -e "${BOLD}  ╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    echo -e "  状态: $status"
    echo ""

    # 总进度
    echo -ne "  ${BOLD}总进度${NC}     "
    bar $preset_done $PRESET_TOTAL 40 "$BLUE"
    echo -e "  ${DIM}${preset_done}/${PRESET_TOTAL} 预设${NC}  ${DIM}+${evolve_topics} 进化${NC}"
    echo ""

    # 分层进度
    for lid in "${LAYER_IDS[@]}"; do
        local layer_name="${LAYER_NAMES[$lid]}"
        local layer_total=${LAYER_TOPICS[$lid]}
        local layer_done=$(count_done_for_layer "$lid")
        local layer_fail=$(count_failed_for_layer "$lid")
        local layer_color=$(color_code "${LAYER_COLORS[$lid]}")

        printf "  ${BOLD}%-4s${NC} %-20s " "$lid" "$layer_name"
        bar $layer_done $layer_total 25 "$layer_color"
        echo -e "  ${layer_done}/${layer_total}$([ $layer_fail -gt 0 ] && echo -e "  ${RED}${layer_fail} 失败${NC}")"
    done

    echo ""
    echo -e "  ${BOLD}进化轮次${NC}         ${evolve_rounds} 轮  ${evolve_topics} 个主题已完成"

    # 知识库统计
    echo ""
    echo -e "  ┌──────────────────────────────────────────────┐"
    echo -e "  │  ${BOLD}知识库统计${NC}                                   │"
    echo -e "  │  笔记: ${BOLD}${total_notes}${NC} 篇    链接: ${BOLD}${total_links}${NC} 个    密度: ${BOLD}$(echo "scale=1; $total_links / ($total_notes + 1)" | bc 2>/dev/null || echo '?')${NC}/篇  │"
    echo -e "  │  PDF: ${BOLD}${total_pdfs}${NC} 份     标签: ${BOLD}${git_tags}${NC} 个                         │"
    echo -e "  └──────────────────────────────────────────────┘"

    # 目录分布
    echo ""
    echo -e "  ${BOLD}目录分布${NC}"
    for lid in "${LAYER_IDS[@]}"; do
        local dir=$(jq -r ".knowledge_layers[] | select(.id == \"$lid\") | .directory // \"\"" "$PROJECT")
        [ -z "$dir" ] && continue
        local count=0
        [ -d "$VAULT_DIR/$dir" ] && count=$(find "$VAULT_DIR/$dir" -name "*.md" 2>/dev/null | wc -l)
        printf "  %-20s %s\n" "$dir" "$(printf '■%.0s' $(seq 1 $count 2>/dev/null) || true) ${count}"
    done

    # 最近完成
    echo ""
    echo -e "  ${BOLD}最近完成${NC}"
    if [ -f "$COMPLETED" ]; then
        tail -5 "$COMPLETED" | while read topic; do
            echo -e "  ${GREEN}✓${NC} ${DIM}$(echo "$topic" | cut -c1-60)${NC}"
        done
    else
        echo -e "  ${GRAY}暂无${NC}"
    fi

    # 失败主题
    if [ -f "$FAILED" ] && [ -s "$FAILED" ]; then
        echo ""
        echo -e "  ${BOLD}${RED}失败主题${NC}"
        cat "$FAILED" | while read topic; do
            echo -e "  ${RED}✗${NC} ${DIM}$(echo "$topic" | cut -c1-60)${NC}"
        done
    fi

    echo ""
    echo -e "  ${GRAY}更新时间: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
    echo -e "  ${GRAY}控制: touch PAUSE 暂停 | touch STOP 停止 | bash progress.sh --watch 自动刷新${NC}"
    echo ""
}

# ================================================================
# JSON 输出
# ================================================================
show_json() {
    local total_done=$(wc -l < "$COMPLETED" 2>/dev/null || echo 0)
    local total_failed=$(wc -l < "$FAILED" 2>/dev/null || echo 0)
    local total_notes=$(find "$VAULT_DIR" -name "*.md" -not -path "*/.claude/*" -not -path "*/.git/*" -not -path "*/${RAW_DIR}/*" -not -name "CLAUDE.md" 2>/dev/null | wc -l)

    local preset_done=0
    local layers_json="{"
    local first=true
    for lid in "${LAYER_IDS[@]}"; do
        local ld=$(count_done_for_layer "$lid")
        local lt=${LAYER_TOPICS[$lid]}
        preset_done=$((preset_done + ld))
        $first || layers_json+=","
        layers_json+="\"$lid\":{\"done\":$ld,\"total\":$lt}"
        first=false
    done
    layers_json+="}"

    cat << ENDJSON
{
  "project": "$PROJECT_NAME",
  "preset_total": $PRESET_TOTAL,
  "preset_done": $preset_done,
  "total_completed": $total_done,
  "total_failed": $total_failed,
  "total_notes": $total_notes,
  "layers": $layers_json,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
ENDJSON
}

# ================================================================
# 入口
# ================================================================
case "${1:-}" in
    --watch)
        interval=${2:-30}
        echo "自动刷新模式（每 ${interval} 秒），按 Ctrl+C 退出"
        while true; do
            show_progress
            sleep "$interval"
        done
        ;;
    --json)
        show_json
        ;;
    *)
        show_progress
        ;;
esac
