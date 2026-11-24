# -*- coding: utf-8 -*-
"""声音对画面：把一份「音效时间表」里的音效和配乐，对到 quanpian.py 量出的换屏点和动作区间上，出一张表。
回答三件事：音效一共几个、各是哪一类；每个音效卡在画面的什么动作上、差几帧；配乐从哪到哪。

音效时间表作者是用自己的一个还没公开的工具 rea-video 出的（30 秒的片子约一分多钟）：
  zsh pao.sh 工作文件夹 -- rea-video decompose --no-vlm --no-asr --no-speakers -o 声音/rea_第一条 原片/第一条.mp4
再跑本脚本（轻）：
  zsh pao.sh --benji 工作文件夹 shengyin.py 声音/rea_第一条/breakdown.json 第一条/全片概况.json 声音/第一条_声音对画面.md

你没有 rea-video 时，用别的办法找出音效起点，整理成同样的 JSON 就能喂给本脚本：
  {"audio": {"sfx": {"events": [{"t": 开始秒, "dur": 持续秒, "type": "ding 这类名字", "level_db": 响度}]},
             "music": {"present": true, "segments": [[开始秒, 结束秒]], "tempo_bpm": 每分钟拍数}}}

只取工具量出来的东西（音效的时间、长短、响度，配乐的起止和拍子）。
它「判断」出来的东西不取：2026-11-23 试跑红白大字风第一条前 30 秒，它把真人口播判成「TTS 配音」；
此前它还把明星真照片判成 86% 到 96% 的「AI 生成视频」。
这一步 2026-11-23 才第一次试，只在一段 30 秒的片子上跑过（这台电脑和另一台电脑各一次，找出的 9 个音效时间一样），\n音效找得全不全没有人对着耳朵核过。交付时照实写。
"""
import json, sys
import lib

import argparse
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('yinxiao', help='音效时间表 JSON（格式见上）'); ap.add_argument('gaikuang', help='quanpian.py 出的 全片概况.json'); ap.add_argument('out', help='输出的 .md')
a = ap.parse_args()
bd, gk, out = json.load(open(a.yinxiao)), json.load(open(a.gaikuang)), a.out
fps = gk['视频']['帧率']; cuts = gk['换屏候选']; acts = gk['动作区间']
au = bd.get('audio', {}); ev = (au.get('sfx') or {}).get('events', []); mu = au.get('music') or {}
ZH = {'ding': '叮', 'pop': '啵', 'click': '咔哒', 'whoosh': '呼', 'swoosh': '呼', 'boom': '咚', 'impact': '撞击', 'riser': '上升音'}
rows = []; on_cut = on_act = 0
for e in ev:
    f = round(e['t'] * fps)
    c = min(cuts, key=lambda x: abs(x - f)) if cuts else None
    a = min(acts, key=lambda x: abs(x['起'] - f)) if acts else None
    what = '没对上画面动作'
    if c is not None and abs(c - f) <= 3:
        what = f'换屏（第 {c} 帧），音效{"早" if f < c else "晚"} {abs(c - f)} 帧' if c != f else f'换屏（第 {c} 帧），同一帧'; on_cut += 1
    elif a is not None and abs(a['起'] - f) <= 4:
        d = f - a['起']; what = f'动作 {a["编号"]} 开始（第 {a["起"]} 帧，看 {a["在哪张图"]}），音效{"早" if d < 0 else "晚"} {abs(d)} 帧' if d else f'动作 {a["编号"]} 开始（第 {a["起"]} 帧，看 {a["在哪张图"]}），同一帧'; on_act += 1
    rows.append(f'| {e["t"]:.2f} | {f} | {ZH.get(e.get("type"), e.get("type", ""))} | {e.get("dur", 0):.2f} | {e.get("level_db", 0):.0f} | {what} |')
n = len(ev); dur = gk['视频']['秒']
L = [f'# 声音对画面：{gk["视频"]["文件"]}', '',
     f'- 音效 {n} 个，平均 {dur / n:.1f} 秒一个。' if n else '- 音效时间表里一个音效都没有。',
     f'- 卡在换屏上的 {on_cut} 个，卡在屏内某个动作开始上的 {on_act} 个，没对上画面动作的 {n - on_cut - on_act} 个（可能卡在口播的某个词上，也可能是找错了，要人听）。',
     f'- 配乐：' + ('、'.join(f'{a:.1f} 到 {b:.1f} 秒' for a, b in mu.get('segments', [])) + f'，大约每分钟 {mu.get("tempo_bpm", 0):.0f} 拍（这个数工具自己给了别的可能：{mu.get("tempo_alternatives")}）' if mu.get('present') else '没检出配乐'),
     f'- 换屏一共 {len(cuts)} 处，其中有音效的 {on_cut} 处。', '',
     '这张表是程序对出来的，没有人听过。音效「是哪一类」是工具按声音形状分的，只能当线索。', '',
     '| 秒 | 帧 | 像什么声音 | 多长（秒） | 响度（分贝） | 卡在画面的什么上 |', '| --- | --- | --- | --- | --- | --- |'] + rows
open(out, 'w').write('\n'.join(L) + '\n')
print('\n'.join(L[:8])); print('…… 全表见', out)
