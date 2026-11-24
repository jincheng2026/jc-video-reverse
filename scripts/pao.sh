#!/bin/zsh
# 跑本文件夹里的脚本。默认就在这台电脑上跑；配了另一台电脑时，把重活送过去跑，跑完把新出的文件取回同一个位置。
# 为什么要有「送到另一台电脑」这一档：抽全片的帧、渲染、逐帧比都很吃 CPU。作者的电脑同时开着很多 AI 对话，
# 全压在一台上负载到过 40（10 核），所以把重活放到另一台 Mac 上跑。你只有一台电脑就不用管，什么都不用配。
#
# 用法：
#   zsh pao.sh 工作文件夹 脚本名.py 参数…     跑本文件夹里的脚本（quanpian.py、yanshou.py、sanlian.py、sekuai.py 这些）
#   zsh pao.sh 工作文件夹 -- 命令 参数…        跑别的命令，比如你为这种风格写的重做程序
#   zsh pao.sh --benji 工作文件夹 …            不管配没配另一台电脑，都在这台电脑跑。给二十秒以内的轻活用（画面条、对照条）
#
# 怎么配另一台电脑（可选）：
#   export VIDEO_REVERSE_REMOTE=另一台电脑的 ssh 名字      要能免密码 ssh 上去；那台电脑上要有 python3、ffmpeg、rsync
#   export VIDEO_REVERSE_REMOTE_DIR=那台电脑上放活的文件夹   相对那台电脑的家目录，默认 video-reverse-work
#   export VIDEO_REVERSE_REMOTE_PATH=那台电脑上要先加进 PATH 的文件夹   比如专门给这件事建的 Python 环境的 bin
# 没设 VIDEO_REVERSE_REMOTE 时，下面说的「送、跑、取」都不发生，直接在这台电脑的工作文件夹里跑。
#
# 配了以后是三步：送（工作文件夹和本文件夹里的脚本，只送变了的）、跑（进到那台电脑上同名的工作文件夹里跑）、取（只取这次开跑以后新写或改过的文件）。
#
# 要知道的几件事：
#   1. 命令里的路径一律写成相对工作文件夹的相对路径。原片先复制进工作文件夹的「原片」子文件夹，不要写 ~/Downloads 这类别处的路径，另一台电脑上没有。
#   2. 两头都只让新的盖旧的。在这台电脑上把一个文件换回旧版本以后要 touch 它一下，不然送不过去。
#   3. 两台电脑的 Pillow、numpy 要同一个版本。作者踩过：一台 Pillow 12.2.0、一台 13.0.0，后者渲出来字幕的白字不见了。
#   4. 两台电脑的 ffmpeg 版本可以不同（作者是 6.0 和 9.0.2）。两台读同一条片子的颜色会差 2.3 级，所以量东西的脚本一律只取视频里存的亮度（lib.py 的 luma_frames），不要自己另写读法。
#   5. 退出码就是那条命令的退出码。配了另一台电脑却连不上时退出码 97：这时不要自己悄悄改回这台电脑跑重活，先告诉用户。

set -u
SELF=${0:A:h}
R=${VIDEO_REVERSE_REMOTE:-}
RBASE=${VIDEO_REVERSE_REMOTE_DIR:-video-reverse-work}
RPATH=${VIDEO_REVERSE_REMOTE_PATH:-}
SSH=(ssh -o ConnectTimeout=8 -o BatchMode=yes -o ServerAliveInterval=30)

benji=0
[[ "${1:-}" == "--benji" ]] && { benji=1; shift; }
if (( $# < 2 )); then
  print -u2 "用法：zsh pao.sh [--benji] 工作文件夹 脚本名.py 参数…   或   zsh pao.sh [--benji] 工作文件夹 -- 命令 参数…"; exit 2
fi
WORK=${1:A}; shift
[[ -d "$WORK" ]] || { print -u2 "没有这个工作文件夹：$WORK"; exit 2; }
NAME=${WORK:t}
[[ "$NAME" == *" "* ]] && { print -u2 "工作文件夹的名字里不要有空格：$NAME"; exit 2; }

if [[ "$1" == "--" ]]; then
  shift; lcmd=("$@"); rcmd=${(j: :)${(q)@}}
else
  s=$1; shift
  [[ -f "$SELF/$s" ]] || { print -u2 "scripts 里没有这个脚本：$s"; exit 2; }
  lcmd=(python3 -B "$SELF/$s" "$@")
  rcmd="python3 -B ~/'$RBASE/_scripts'/${(q)s} ${(j: :)${(q)@}}"
fi

if (( benji )) || [[ -z "$R" ]]; then
  cd "$WORK" && "${lcmd[@]}"; exit $?
fi

$SSH $R true 2>/dev/null || { print -u2 "另一台电脑连不上（$R）。不要悄悄改回这台电脑跑重活，先告诉用户。"; exit 97; }
RS=(rsync -a --update -e "ssh -o ConnectTimeout=8 -o BatchMode=yes" --exclude '__pycache__' --exclude '.DS_Store' --exclude '_帧临时')
t0=$(date +%s)
$SSH $R "mkdir -p ~/'$RBASE/_scripts' ~/'$RBASE'/${(q)NAME}" || exit 97
$RS "$SELF/" "$R:$RBASE/_scripts/" || { print -u2 "送脚本失败"; exit 97; }
$RS "$WORK/" "$R:$RBASE/$NAME/" || { print -u2 "送工作文件夹失败"; exit 97; }
t1=$(date +%s)

mark="/tmp/video-reverse-$$-$t0.mark"
pre=""; [[ -n "$RPATH" ]] && pre="export PATH=${(q)RPATH}:\$PATH && "
keep=""; $SSH $R "command -v caffeinate" >/dev/null 2>&1 && keep="caffeinate -is "
$SSH $R "touch $mark; cd ~/'$RBASE'/${(q)NAME} && $pre$keep$rcmd"
rc=$?
t2=$(date +%s)

list=$($SSH $R "cd ~/'$RBASE'/${(q)NAME} && find . -type f -newer $mark ! -name '.DS_Store' ! -path '*/__pycache__/*' ! -path '*/_帧临时/*'")
if [[ -n "$list" ]]; then
  print -r -- "$list" | $RS --files-from=- "$R:$RBASE/$NAME/" "$WORK/" || print -u2 "取回失败"
fi
$SSH $R "rm -f $mark"
t3=$(date +%s)
print -u2 "［另一台电脑 $R］送 $((t1 - t0)) 秒，跑 $((t2 - t1)) 秒，取 $((t3 - t2)) 秒，退出码 $rc"
exit $rc
