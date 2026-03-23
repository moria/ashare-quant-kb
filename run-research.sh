#!/bin/bash
# run-research.sh — DeepResearch 7×24 自主调研引擎
#
# 领域无关的调研调度器，从 project.json 读取配置，
# 驱动 Claude Code 系统化构建知识库。
#
# 核心机制:
#   1. 断点续跑 — completed-topics.txt 记录进度
#   2. 自动重试 — 指数退避，最多 3 次
#   3. 版本控制 — 每层完成打 git tag 快照
#   4. 自动进化 — Claude 分析空白，生成下一轮主题
#   5. 并发执行 — 可配置的并行调研槽位
#   6. 文件锁并发安全
#
# 用法:
#   tmux new -s research 'bash run-research.sh'   # 推荐
#   nohup bash run-research.sh &                   # 或后台运行
#
# 控制:
#   touch PAUSE → 暂停 | touch STOP → 停止 | tail -f research-progress.log

# ================================================================
# 从 project.json 读取配置
# ================================================================
VAULT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$VAULT_DIR/project.json"

if [ ! -f "$PROJECT" ]; then
    echo "错误: 未找到 project.json（路径: $VAULT_DIR）"
    echo "请先运行 init.sh 或将 project.example.json 复制为 project.json"
    exit 1
fi

# 检查 jq
command -v jq &>/dev/null || { echo "错误: 需要 jq。安装方法: brew install jq"; exit 1; }

# 读取配置
PROJECT_NAME=$(jq -r '.project.name' "$PROJECT")
RAW_DIR=$(jq -r '.project.vault_dirs.raw_materials // "99-原始资料"' "$PROJECT")
RESEARCH_CMD=$(jq -r '.research_command.name // "deep-research"' "$PROJECT")

# 代理设置
HTTP_PROXY=$(jq -r '.proxy.http // ""' "$PROJECT")
HTTPS_PROXY=$(jq -r '.proxy.https // ""' "$PROJECT")
SOCKS_PROXY=$(jq -r '.proxy.socks5 // ""' "$PROJECT")
[ -n "$HTTP_PROXY" ] && export http_proxy="$HTTP_PROXY"
[ -n "$HTTPS_PROXY" ] && export https_proxy="$HTTPS_PROXY"
[ -n "$SOCKS_PROXY" ] && export all_proxy="$SOCKS_PROXY"

# 调度参数
INTERVAL_OK=$(jq -r '.orchestration.interval_ok // 30' "$PROJECT")
INTERVAL_FAIL=$(jq -r '.orchestration.interval_fail // 120' "$PROJECT")
MAX_BACKOFF=$(jq -r '.orchestration.max_backoff // 600' "$PROJECT")
MAX_RETRIES=$(jq -r '.orchestration.max_retries // 3' "$PROJECT")
TOPIC_TIMEOUT=$(jq -r '.orchestration.topic_timeout // 600' "$PROJECT")
EVOLVE_TIMEOUT=$(jq -r '.orchestration.evolve_timeout // 300' "$PROJECT")
MAX_PARALLEL=$(jq -r '.orchestration.max_parallel // 3' "$PROJECT")

# 文件路径
LOG="$VAULT_DIR/research-progress.log"
COMPLETED="$VAULT_DIR/completed-topics.txt"
FAILED="$VAULT_DIR/failed-topics.txt"
NEXT_TOPICS="$VAULT_DIR/next-round-topics.txt"
LOCKFILE="$VAULT_DIR/.research-lock"
GIT_LOCKFILE="$VAULT_DIR/.git-lock"

set +e

# ================================================================
# macOS 兼容：timeout 替代
# ================================================================
if ! command -v timeout &>/dev/null; then
    timeout() {
        local secs="$1"; shift
        "$@" &
        local pid=$!
        ( sleep "$secs"; kill "$pid" 2>/dev/null ) &
        local watcher=$!
        wait "$pid" 2>/dev/null
        local rc=$?
        kill "$watcher" 2>/dev/null
        wait "$watcher" 2>/dev/null
        return $rc
    }
fi

# ================================================================
# 并发安全：文件锁
# ================================================================
acquire_lock() {
    if [ -f "$LOCKFILE" ]; then
        local lock_pid=$(cat "$LOCKFILE" 2>/dev/null)
        if kill -0 "$lock_pid" 2>/dev/null; then
            log "[锁] 另一个实例 (PID $lock_pid) 正在运行，退出"
            log "[锁] 如果确认无其他实例，删除 .research-lock 后重试"
            exit 1
        else
            log "[锁] 发现残留锁 (PID $lock_pid 已不存在)，清理并继续"
            rm -f "$LOCKFILE"
        fi
    fi
    echo $$ > "$LOCKFILE"
    trap 'cleanup_and_exit' EXIT INT TERM
    log "[锁] 已获取 (PID $$)"
}

# 全局子进程跟踪
CHILD_PIDS=()
RUNNING_STATUS="$VAULT_DIR/.running-topics"

register_child()   { CHILD_PIDS+=("$1"); }
unregister_child() {
    local new=()
    for p in "${CHILD_PIDS[@]}"; do [ "$p" != "$1" ] && new+=("$p"); done
    CHILD_PIDS=("${new[@]}")
}

mark_running() {
    _file_lock "$VAULT_DIR/.status-lock"
    echo "$1" >> "$RUNNING_STATUS"
    _file_unlock "$VAULT_DIR/.status-lock"
}
unmark_running() {
    _file_lock "$VAULT_DIR/.status-lock"
    grep -vFx "$1" "$RUNNING_STATUS" > "$RUNNING_STATUS.tmp" 2>/dev/null
    mv "$RUNNING_STATUS.tmp" "$RUNNING_STATUS" 2>/dev/null
    _file_unlock "$VAULT_DIR/.status-lock"
}

cleanup_and_exit() {
    log "[关闭] 正在停止所有子进程..."
    for pid in "${CHILD_PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    local waited=0
    while [ ${#CHILD_PIDS[@]} -gt 0 ] && [ $waited -lt 10 ]; do
        for pid in "${CHILD_PIDS[@]}"; do
            kill -0 "$pid" 2>/dev/null || unregister_child "$pid"
        done
        sleep 1; waited=$((waited + 1))
    done
    for pid in "${CHILD_PIDS[@]}"; do
        kill -9 "$pid" 2>/dev/null
    done
    rm -f "$LOCKFILE" "$RUNNING_STATUS" "$VAULT_DIR/STOP"
    log "[关闭] 已全部停止"
    exit 0
}

# ================================================================
# 工具函数
# ================================================================
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG"; }

touch "$COMPLETED" "$FAILED" 2>/dev/null

is_done()   { grep -qFx "$1" "$COMPLETED" 2>/dev/null; }

_file_lock() {
    local lockdir="$1"
    while ! mkdir "$lockdir" 2>/dev/null; do sleep 0.1; done
}
_file_unlock() { rmdir "$1" 2>/dev/null; }

mark_done() {
    _file_lock "$VAULT_DIR/.completed-lock"
    echo "$1" >> "$COMPLETED"
    _file_unlock "$VAULT_DIR/.completed-lock"
}
mark_fail() {
    _file_lock "$VAULT_DIR/.failed-lock"
    echo "$1" >> "$FAILED"
    _file_unlock "$VAULT_DIR/.failed-lock"
}

check_pause() {
    while [ -f "$VAULT_DIR/PAUSE" ]; do
        log "[暂停] 等待中... (rm PAUSE 恢复)"
        sleep 30
    done
}

is_stopping() { [ -f "$VAULT_DIR/STOP" ]; }

check_stop() {
    if is_stopping; then
        log "[停止] 优雅退出"
        exit 0
    fi
}

smart_sleep() {
    local s=$1
    while [ $s -gt 0 ]; do
        check_stop; check_pause
        local c=5; [ $s -lt $c ] && c=$s
        sleep $c; s=$((s - c))
    done
}

# ================================================================
# 网络连通性检测
# ================================================================
NET_CHECK_URLS=("https://api.anthropic.com" "https://www.google.com" "https://www.baidu.com")
NET_FAIL_COUNT=0
NET_FAIL_THRESHOLD=3

check_network() {
    for url in "${NET_CHECK_URLS[@]}"; do
        if curl -s --max-time 10 --head "$url" >/dev/null 2>&1; then
            NET_FAIL_COUNT=0
            return 0
        fi
    done
    NET_FAIL_COUNT=$((NET_FAIL_COUNT + 1))
    return 1
}

wait_for_network() {
    if [ $NET_FAIL_COUNT -lt $NET_FAIL_THRESHOLD ]; then
        return 0
    fi
    log "[网络] 检测到网络中断（连续 ${NET_FAIL_COUNT} 次检测失败），进入等待..."
    local wait_interval=30
    local total_waited=0
    while true; do
        check_stop; check_pause
        sleep $wait_interval
        total_waited=$((total_waited + wait_interval))
        if [ $wait_interval -lt 300 ]; then
            wait_interval=$((wait_interval * 2))
            [ $wait_interval -gt 300 ] && wait_interval=300
        fi
        if check_network; then
            log "[网络] 网络恢复（等待了 ${total_waited} 秒），继续调研"
            return 0
        fi
        log "[网络] 仍无网络，已等待 ${total_waited} 秒... (touch STOP 可退出)"
    done
}

# ================================================================
# 版本控制
# ================================================================
git_commit() {
    _file_lock "$GIT_LOCKFILE"
    cd "$VAULT_DIR"
    git add -A 2>/dev/null
    git commit -m "$1" --quiet 2>/dev/null || true
    _file_unlock "$GIT_LOCKFILE"
}

git_tag_round() {
    local name="$1"
    local tag="round-${name}-$(date '+%Y%m%d-%H%M%S')"
    cd "$VAULT_DIR"
    git add -A 2>/dev/null
    git commit -m "里程碑: $name" --quiet 2>/dev/null || true
    git tag "$tag" 2>/dev/null || true
    log "[GIT] 标签: $tag"
}

# ================================================================
# 调研执行
# ================================================================
run_topic() {
    local topic="$1" layer="$2" idx="$3" total="$4"

    is_done "$topic" && { log "[$layer] ($idx/$total) 跳过: $topic"; return 0; }
    is_stopping && return 1

    mark_running "[$layer] ($idx/$total) $topic"

    check_network || wait_for_network

    local retry=0 backoff=$INTERVAL_FAIL

    while [ $retry -lt $MAX_RETRIES ]; do
        is_stopping && { unmark_running "[$layer] ($idx/$total) $topic"; return 1; }

        local rl=""; [ $retry -gt 0 ] && rl=" (重试 $retry)"
        log "[$layer] ($idx/$total)${rl} 开始: $topic"

        local safe_name=$(echo "$topic" | tr ' /' '_-' | cut -c 1-60)
        local topic_log="$VAULT_DIR/logs/${safe_name}.log"
        local rc_file="$VAULT_DIR/logs/.rc-${safe_name}"
        local win_name="${layer}-${idx}"
        mkdir -p "$VAULT_DIR/logs"
        rm -f "$rc_file"

        # 在 tmux 新窗口中运行 claude
        tmux new-window -d -n "$win_name" \
            "export http_proxy='${http_proxy:-}' https_proxy='${https_proxy:-}' all_proxy='${all_proxy:-}' && cd '$VAULT_DIR' && claude -p --dangerously-skip-permissions '/$RESEARCH_CMD $topic'; echo \$? > '$rc_file'"

        tmux pipe-pane -t "$win_name" -o "cat >> '$topic_log'"

        # 等待完成或超时
        local waited=0
        while [ ! -f "$rc_file" ]; do
            sleep 5
            waited=$((waited + 5))
            if is_stopping; then
                tmux kill-window -t "$win_name" 2>/dev/null
                break
            fi
            if [ $waited -ge $TOPIC_TIMEOUT ]; then
                log "[$layer] ($idx/$total) 超时: $topic"
                tmux kill-window -t "$win_name" 2>/dev/null
                break
            fi
        done

        local rc=$(cat "$rc_file" 2>/dev/null || echo 1)
        rm -f "$rc_file"
        cat "$topic_log" >> "$LOG" 2>/dev/null

        if [ $rc -eq 0 ]; then
            log "[$layer] ($idx/$total) 完成: $topic"
            mark_done "$topic"
            unmark_running "[$layer] ($idx/$total) $topic"
            bash "$VAULT_DIR/update-context.sh" "$topic" "" "" 2>/dev/null
            git_commit "调研: $topic"
            smart_sleep $INTERVAL_OK
            return 0
        fi

        if [ $rc -eq 143 ] || is_stopping; then
            log "[$layer] ($idx/$total) 已停止: $topic"
            unmark_running "[$layer] ($idx/$total) $topic"
            return 1
        fi

        retry=$((retry + 1))
        if [ $retry -lt $MAX_RETRIES ]; then
            if ! check_network; then
                log "[$layer] ($idx/$total) 失败(rc=$rc) 疑似断网，等待网络恢复..."
                wait_for_network
            else
                log "[$layer] ($idx/$total) 失败(rc=$rc) 等待 ${backoff}s，重试 $retry: $topic"
                smart_sleep $backoff
            fi
            backoff=$((backoff * 2))
            [ $backoff -gt $MAX_BACKOFF ] && backoff=$MAX_BACKOFF
        fi
    done

    log "[$layer] ($idx/$total) 放弃: $topic"
    mark_fail "$topic"
    unmark_running "[$layer] ($idx/$total) $topic"
    smart_sleep $INTERVAL_FAIL
    return 1
}

run_batch() {
    local layer="$1"; shift; local topics=("$@"); local total=${#topics[@]}
    [ $total -eq 0 ] && return 0
    log ""; log "--- [$layer] $total 个主题 (并发=$MAX_PARALLEL) ---"

    local pids=()
    for i in "${!topics[@]}"; do
        is_stopping && break

        while true; do
            is_stopping && break
            local alive=0
            local new_pids=()
            for pid in "${pids[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    alive=$((alive + 1))
                    new_pids+=("$pid")
                else
                    wait "$pid" 2>/dev/null
                    unregister_child "$pid"
                fi
            done
            pids=("${new_pids[@]}")
            [ $alive -lt $MAX_PARALLEL ] && break
            sleep 2
        done

        is_stopping && break
        run_topic "${topics[$i]}" "$layer" "$((i+1))" "$total" &
        local cpid=$!
        pids+=($cpid)
        register_child "$cpid"
    done

    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null
        unregister_child "$pid"
    done
}

retry_failed() {
    [ ! -s "$FAILED" ] && return 0
    log ""; log "--- 重试失败主题 ---"
    local topics=()
    while IFS= read -r line; do [ -n "$line" ] && topics+=("$line"); done < "$FAILED"
    > "$FAILED"
    run_batch "重试" "${topics[@]}"
}

# ================================================================
# 自动进化
# ================================================================
evolve() {
    local round_num="$1"
    log ""; log "========== 进化 #$round_num: 分析知识库 =========="

    timeout $EVOLVE_TIMEOUT claude -p --dangerously-skip-permissions "/evolve-topics" 2>&1 | tee -a "$LOG"
    local rc=${PIPESTATUS[0]}

    if [ $rc -ne 0 ]; then
        log "[进化] 分析失败 (rc=$rc)"
        return 1
    fi

    if [ ! -f "$NEXT_TOPICS" ]; then
        log "[进化] 未生成 next-round-topics.txt"
        return 1
    fi

    local new_topics=() new_layers=()
    while IFS='|' read -r topic layer reason; do
        [[ "$topic" =~ ^[[:space:]]*#  ]] && continue
        [[ "$topic" =~ ^[[:space:]]*$  ]] && continue
        topic=$(echo "$topic" | xargs)
        layer=$(echo "$layer" | xargs)
        [ -z "$layer" ] && layer="进化"
        new_topics+=("$topic")
        new_layers+=("$layer")
    done < "$NEXT_TOPICS"

    local count=${#new_topics[@]}
    if [ $count -eq 0 ]; then
        log "[进化] 未解析到主题"
        return 1
    fi

    log "[进化] 为第 $round_num 轮生成了 $count 个主题"

    mkdir -p "$VAULT_DIR/$RAW_DIR"
    mv "$NEXT_TOPICS" "$VAULT_DIR/$RAW_DIR/evolved-round-${round_num}-$(date '+%Y%m%d').txt" 2>/dev/null
    git_commit "进化: 第 $round_num 轮主题列表"

    for i in "${!new_topics[@]}"; do
        run_topic "${new_topics[$i]}" "R${round_num}-${new_layers[$i]}" "$((i+1))" "$count"
    done

    return 0
}

# ================================================================
# 环境预检
# ================================================================
preflight() {
    log "[预检] 检查环境..."

    acquire_lock

    if ! command -v python3 &>/dev/null; then
        log "[预检] 警告: 未找到 python3，PDF 工具将无法使用"
    fi

    if [ -f "$VAULT_DIR/update-context.sh" ]; then
        chmod +x "$VAULT_DIR/update-context.sh"
    else
        log "[预检] 警告: 未找到 update-context.sh，上下文更新已禁用"
    fi

    if [ ! -f "$VAULT_DIR/CLAUDE.md" ]; then
        log "[预检] 警告: 未找到 CLAUDE.md，请先运行 init.sh"
    fi

    mkdir -p "$VAULT_DIR/$RAW_DIR" "$VAULT_DIR/$RAW_DIR/images"

    cd "$VAULT_DIR"
    if [ ! -d ".git" ]; then
        git init
        git commit --allow-empty -m "init"
        log "[预检] git 已初始化"
    fi

    log "[预检] 通过"
}

# ================================================================
# 主流程
# ================================================================
preflight

# 统计主题数
TOTAL_PRESET=0
LAYER_IDS=()
while IFS= read -r lid; do
    [ -n "$lid" ] && LAYER_IDS+=("$lid")
done < <(jq -r '.research_topics | keys[]' "$PROJECT" 2>/dev/null)

for lid in "${LAYER_IDS[@]}"; do
    count=$(jq -r ".research_topics.\"$lid\" | length" "$PROJECT")
    TOTAL_PRESET=$((TOTAL_PRESET + count))
done

DONE_COUNT=$(wc -l < "$COMPLETED" 2>/dev/null || echo 0)

log "============================================"
log "   $PROJECT_NAME — DeepResearch 调研引擎"
log "============================================"
log "预设: $TOTAL_PRESET | 已完成: $DONE_COUNT | 剩余: $((TOTAL_PRESET - DONE_COUNT))"
log "控制: touch PAUSE / touch STOP"
log "============================================"

# 按层级执行
for lid in "${LAYER_IDS[@]}"; do
    topics=()
    while IFS= read -r topic; do
        [ -n "$topic" ] && topics+=("$topic")
    done < <(jq -r ".research_topics.\"$lid\"[]? // empty" "$PROJECT")

    layer_name=$(jq -r ".knowledge_layers[] | select(.id == \"$lid\") | .name // \"$lid\"" "$PROJECT")
    run_batch "$lid-$layer_name" "${topics[@]}"
    git_tag_round "$lid-$(echo "$layer_name" | tr ' ' '-' | tr '[:upper:]' '[:lower:]')"
done

# 重试失败主题
retry_failed
git_tag_round "预设完成"

log ""
log "========== 所有预设主题已完成 =========="
log "成功: $(wc -l < "$COMPLETED") | 失败: $(wc -l < "$FAILED")"

# 自动进化循环
EVOLVE_ROUND=1

log ""
log "========== 进入自动进化模式 =========="

while true; do
    check_stop; check_pause

    note_count=$(find . -name "*.md" -not -path "./.claude/*" -not -path "./.git/*" | wc -l)

    log ""
    log "======================================================"
    log "  进化轮次 #$EVOLVE_ROUND"
    log "  笔记: $note_count | 已完成: $(wc -l < "$COMPLETED")"
    log "======================================================"

    evolve $EVOLVE_ROUND

    if [ $? -eq 0 ]; then
        retry_failed
        git_tag_round "进化-${EVOLVE_ROUND}"
        log "[版本] 进化第 #$EVOLVE_ROUND 轮已标记"
    else
        log "[进化] 第 #$EVOLVE_ROUND 轮未产出"
    fi

    EVOLVE_ROUND=$((EVOLVE_ROUND + 1))

    log "下一轮进化将在 10 分钟后开始... (touch STOP 退出)"
    smart_sleep 600
done
