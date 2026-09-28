# shellcheck shell=bash
# 假命令公共部分：把调用记录追加到 $FAKE_LOG；状态文件目录 $FAKE_STATE
FAKE_LOG="${FAKE_LOG:-/dev/null}"
FAKE_STATE="${FAKE_STATE:-/tmp}"
flog() { printf '%s %s\n' "$(basename "$0")" "$*" >> "${FAKE_LOG}"; }
