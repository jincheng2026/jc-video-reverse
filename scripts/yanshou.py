# -*- coding: utf-8 -*-
"""逐帧验收：把重做的片段和原片对应的那一段，每一帧都比一遍。审的一方用，做的一方不许改这个文件。
要解码两条片子，是重活，用 pao.sh 可以放另一台电脑跑。

用法：python3 -B yanshou.py 原片 重做片 输出文件夹 [--yuan-qi 原片起帧，默认 0] [--huanping 帧号,帧号…]
  重做片的第 0 帧对应原片的第 --yuan-qi 帧，一直比到重做片结束。整条片子都重做了就不写 --yuan-qi。
  --huanping  这一段里的换屏点（原片帧号），用来把「换屏后 20 帧内」单独算，不写就不分。

每帧量四个数（两边都缩到宽 540，只比亮度，0 到 255）：
  动法差   把画面切成 60×60 的小块，每块算「这一帧比上一帧变了多少」，原片和重做各算一份再相减，取最大的一块。
           只看「动得一不一样」：东西是不是同一帧出现、同一帧在动、动得一样快。字体不像这类一直不变的差别不影响它。
           超过 12 算这一帧没过。看动效做得对不对，以它为准。
  整幅差   这一帧两边逐像素差的平均。看整体像不像。超过 8 算没过。
  清晰度   重做这一帧的清晰度是原片的几成。低于 0.7 或高于 1.4 算没过。
           为什么要有它：让程序找「像素差最小」的参数时，会把清楚的叠化凑成一团糊，糊的画面整幅差反而小（2026-11-23 踩过）。
  小块差   最不像的那一个小块差多少。字体不是同一款时它一直偏大，只作参考。

这四个数的门槛是 2026-11-23 在红白大字风两条片子上定的。动法差 12 很严：一个字早出一帧、过渡多糊两帧都算。
放宽到 40 才是「一眼看得出」，结果里两个数都报。

产出：验收结果.txt（总数、没过线的连续段落和起止帧）、逐帧数.csv。
"""
import argparse, os
import numpy as np
import lib

B = 60
T_WHOLE, T_BLOCK, T_MOVE, T_MOVE_EYE, T_SHARP, NEAR = 8.0, 40.0, 12.0, 40.0, (0.7, 1.4), 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('yuan'); ap.add_argument('chong'); ap.add_argument('out')
    ap.add_argument('--yuan-qi', type=int, default=0); ap.add_argument('--huanping', default='')
    a = ap.parse_args()
    vo, vr = lib.info(a.yuan), lib.info(a.chong)
    if abs(vo['fps'] - vr['fps']) > 0.01:
        lib.die(f'两条片子帧率不一样（原片 {vo["fps"]:g}，重做 {vr["fps"]:g}），没法一帧对一帧比。重做片要按原片的帧率出。')
    if abs(vo['w'] / vo['h'] - vr['w'] / vr['h']) > 0.01:
        lib.die('两条片子的宽高比不一样，没法比。')
    fps = vo['fps']
    SW = 540; SH = int(round(SW * vo['h'] / vo['w'] / B)) * B
    GX = SW // B
    os.makedirs(a.out, exist_ok=True)

    def blocks(x):
        return x.reshape(SH // B, B, GX, B).mean(axis=(1, 3))

    def sharp(x):
        return float(np.abs(np.diff(x, axis=1)).mean() + np.abs(np.diff(x, axis=0)).mean())

    whole, blk, blk_at, mov, mov_at, so, sr = [], [], [], [], [], [], []
    po = pr = None
    for o, r in zip(lib.luma_frames(a.yuan, SW, SH, skip=a.yuan_qi), lib.luma_frames(a.chong, SW, SH)):
        d = np.abs(o - r); whole.append(float(d.mean())); so.append(sharp(o)); sr.append(sharp(r))
        bd = blocks(d); i = int(bd.argmax()); blk.append(float(bd.flat[i])); blk_at.append(i)
        if po is None:
            mov.append(0.0); mov_at.append(0)
        else:
            md = np.abs(blocks(np.abs(o - po)) - blocks(np.abs(r - pr)))
            i = int(md.argmax()); mov.append(float(md.flat[i])); mov_at.append(i)
        po, pr = o, r
    n = len(whole)
    if n == 0:
        lib.die('一帧也没比上，看看 --yuan-qi 是不是超过了原片的帧数。')
    whole, blk, mov = np.array(whole), np.array(blk), np.array(mov)
    ratio = (np.array(sr) + 0.5) / (np.array(so) + 0.5)
    bad_sharp = (ratio < T_SHARP[0]) | (ratio > T_SHARP[1])
    cuts = sorted(int(x) - a.yuan_qi for x in a.huanping.split(',') if x.strip())
    since = np.full(n, 9999, int)
    for c in cuts:
        if 0 <= c < n:
            e = min(n, c + NEAR); since[c:e] = np.minimum(since[c:e], np.arange(e - c))
    near = since < NEAR
    sx, sy = vo['w'] / SW, vo['h'] / SH

    def pos(i):
        gy, gx = divmod(i, GX); return f'{int(gx * B * sx)},{int(gy * B * sy)}'
    F = lambda i: i + a.yuan_qi   # 报的都是原片帧号

    with open(os.path.join(a.out, '逐帧数.csv'), 'w') as f:
        f.write('原片帧,秒,离换屏帧数,整幅差,小块差,最差小块左上角,动法差,动法差最大小块左上角,重做清晰度是原片几成\n')
        for i in range(n):
            f.write(f'{F(i)},{F(i) / fps:.3f},{since[i]},{whole[i]:.2f},{blk[i]:.1f},"{pos(blk_at[i])}",{mov[i]:.1f},"{pos(mov_at[i])}",{ratio[i]:.2f}\n')  # 位置里有逗号，要加引号，不然表格会错列

    def line(name, m):
        k = int(m.sum())
        if k == 0:
            return f'{name}：0 帧'
        return (f'{name}：{k} 帧。动法差超过 {T_MOVE:g} 的占 {100 * (mov[m] > T_MOVE).mean():.1f}%（{int((mov[m] > T_MOVE).sum())} 帧），'
                f'超过 {T_MOVE_EYE:g}（一眼看得出）的占 {100 * (mov[m] > T_MOVE_EYE).mean():.1f}%；'
                f'整幅差 中位 {np.median(whole[m]):.1f}、最大 {whole[m].max():.1f}，超过 {T_WHOLE:g} 的占 {100 * (whole[m] > T_WHOLE).mean():.1f}%，超过 12 的占 {100 * (whole[m] > 12).mean():.1f}%；'
                f'小块差超过 {T_BLOCK:g} 的占 {100 * (blk[m] > T_BLOCK).mean():.1f}%')
    L = [f'原片 {a.yuan} 从第 {a.yuan_qi} 帧起，重做片 {a.chong}，一共比了 {n} 帧（重做片 {vr["n"]} 帧）。两边都缩到 {SW}×{SH}、只比亮度。',
         f'过线标准：动法差不超过 {T_MOVE:g}（主要看这个）、整幅差不超过 {T_WHOLE:g}、清晰度在原片的 {T_SHARP[0]} 到 {T_SHARP[1]} 倍之间；小块差只作参考。', '',
         line('全部', np.ones(n, bool))]
    if cuts:
        L += [line(f'换屏后 {NEAR} 帧内', near), line('其余', ~near)]
    L.append(f'清晰度：重做比原片糊三成以上的 {int((ratio < T_SHARP[0]).sum())} 帧，清楚三成以上的 {int((ratio > T_SHARP[1]).sum())} 帧')
    for name, m, arr, at in (('动法差', mov > T_MOVE, mov, mov_at), ('整幅差', whole > T_WHOLE, whole, None), ('清晰度', bad_sharp, None, None)):
        ss = lib.segs(m)
        L += ['', f'{name}没过线的段落：{len(ss)} 段，合计 {int(m.sum())} 帧（原片起帧-止帧、秒、最差的数在哪一帧、画面上的位置）']
        for s, e in ss:
            if arr is None:
                L.append(f'  {F(s):>5}-{F(e):<5} {F(s) / fps:7.2f}秒  重做是原片的 {ratio[s:e].min():.2f} 到 {ratio[s:e].max():.2f} 倍')
            else:
                j = s + int(arr[s:e].argmax())
                L.append(f'  {F(s):>5}-{F(e):<5} {F(s) / fps:7.2f}秒  最大 {arr[j]:.1f} 在第 {F(j)} 帧' + (f'，位置 {pos(at[j])}' if at else ''))
    L += ['', '没过线的段落，逐段用 duizhaotiao.py 拼原片和重做的逐帧对照条看是什么东西、差在哪，再改。只看数不看图会改错方向。']
    open(os.path.join(a.out, '验收结果.txt'), 'w').write('\n'.join(L) + '\n')
    print('\n'.join(L[:8])); print('…… 全文见', os.path.join(a.out, '验收结果.txt'))


if __name__ == '__main__':
    main()
