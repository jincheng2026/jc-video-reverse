# -*- coding: utf-8 -*-
"""全片过一遍：每一帧都看，量出哪里换屏、哪些时间段画面在动、哪些时间段画面一直在轻微变，并出两种给眼睛看的图。
要解码整条片子两遍，是重活，用 pao.sh 可以放另一台电脑跑。

用法：python3 -B quanpian.py 视频 输出文件夹 [--bukan x0,y0,x1,y1]… [--meige 帧数]
  --bukan  量的时候不看画面的这一块，四个数是 0 到 1 的比例（左、上、右、下），可以写多次。
           画面角上一直有个会动的真人小窗时用，不然整条片子每一帧都算「在动」。
  --meige  总览图每隔几帧取一格，默认是 0.2 秒对应的帧数（30 帧的片子是 6）。

产出（都在输出文件夹里）：
  全片概况.json   片子的宽高帧率帧数；换屏候选；动作区间；一直在轻微变的区间；用的门槛
  全片概况.txt    上面这些给人看的版本
  帧差.npz        每帧的原始数（整幅变化、最大小块变化、隔半秒的变化）
  总览/总览_NN.jpg   全片每隔 --meige 帧一格，换屏候选那一格顶上有红条。用来定「每一屏有什么」
  动作条/动作条_NN.jpg  每个动作区间逐帧排开（长的隔帧取）。用来定「每样东西怎么出来、怎么走」

三个数是什么（亮度 0 到 255）：
  整幅变化      这一帧和上一帧逐像素差的平均。硬切、切到黑时很大。
  最大小块变化  把画面切成 60 分之一宽的小块，哪一块和上一帧差得最多。一个小箭头冒出来，整幅变化看不出，它看得出。
  隔半秒的变化  这一帧和半秒前那一帧的差。画面在慢慢推近或平移时，一帧一帧看几乎不动，隔半秒看就明显。
"""
import argparse, json, os, shutil, subprocess
import numpy as np
from PIL import Image, ImageDraw
import lib

T_CUT = 14.0     # 整幅变化超过它，算「换屏候选」
T_MOVE = 4.0     # 最大小块变化超过它，算「这一帧有东西在动」
T_SLOW = 2.5     # 隔半秒的整幅变化超过它……
T_GENTLE = (0.15, 3.0)  # ……同时每一帧的整幅变化落在这个范围里，连着半秒以上，算「一直在轻微变」
CELL = 160


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video'); ap.add_argument('out')
    ap.add_argument('--bukan', action='append', default=[]); ap.add_argument('--meige', type=int)
    a = ap.parse_args()
    v = lib.info(a.video)
    fps = v['fps']; meige = a.meige or max(1, round(fps / 5))
    os.makedirs(a.out, exist_ok=True)
    sw = 180; sh = int(round(sw * v['h'] / v['w'] / 20)) * 20; B = 20
    keep = np.ones((sh, sw), bool)
    for s in a.bukan:
        x0, y0, x1, y1 = (float(x) for x in s.split(','))
        keep[int(y0 * sh):int(y1 * sh), int(x0 * sw):int(x1 * sw)] = False
    kb = keep.reshape(sh // B, B, sw // B, B).mean(axis=(1, 3)) > 0.5   # 这个小块算不算

    # 第一遍：量
    K = max(2, round(fps / 2))
    whole, bmax, barg, slow = [], [], [], []
    buf = []
    for y in lib.luma_frames(a.video, sw, sh):
        if buf:
            d = np.abs(y - buf[-1])
            whole.append(float(d[keep].mean()))
            bd = d.reshape(sh // B, B, sw // B, B).mean(axis=(1, 3)); bd[~kb] = 0
            i = int(bd.argmax()); bmax.append(float(bd.flat[i])); barg.append(i)
        else:
            whole.append(0.0); bmax.append(0.0); barg.append(0)
        slow.append(float(np.abs(y - buf[0])[keep].mean()) if len(buf) >= K else 0.0)
        buf.append(y)
        if len(buf) > K:
            buf.pop(0)
    n = len(whole)
    whole, bmax, slow = np.array(whole), np.array(bmax), np.array(slow)
    np.savez_compressed(os.path.join(a.out, '帧差.npz'), 整幅变化=whole, 最大小块变化=bmax, 隔半秒的变化=slow)

    # 换屏候选：整幅变化的尖峰。连着几帧都高的（闪白、甩动）只记最高的那一帧。
    cuts = []
    for s, e in lib.segs(whole > T_CUT, gap=1):
        cuts.append(s + int(whole[s:e].argmax()))
    # 动作区间
    moving = bmax > T_MOVE
    acts = []
    gx = sw // B
    for s, e in lib.segs(moving, gap=2):
        j = s + int(bmax[s:e].argmax()); by, bx = divmod(barg[j], gx)
        acts.append(dict(起=s, 止=e, 帧数=e - s, 起秒=round(s / fps, 2), 最大=round(float(bmax[j]), 1), 最大在帧=j,
                         动得最多的位置=[round((bx + 0.5) * B / sw, 2), round((by + 0.5) * B / sh, 2)],
                         含换屏候选=[c for c in cuts if s <= c < e]))
    # 一直在轻微变：每一帧都只变一点点（不是静止、也不是大动作），隔半秒看却差得明显，这样连着半秒以上。
    # 真人画面、会动的视频片段、画面在慢慢推近或平移，都长这样；静止的照片和来回小幅晃的箭头不会。
    gentle = (whole > T_GENTLE[0]) & (whole < T_GENTLE[1]) & (slow > T_SLOW)
    slows = [dict(起=s, 止=e, 帧数=e - s, 起秒=round(s / fps, 2), 止秒=round(e / fps, 2),
                  每帧变化中位=round(float(np.median(whole[s:e])), 2), 隔半秒变化中位=round(float(np.median(slow[s:e])), 1))
             for s, e in lib.segs(gentle, gap=3) if e - s >= K]

    # 第二遍：出小图
    tmp = os.path.join(a.out, '_帧临时'); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    ch = int(round(CELL * v['h'] / v['w'] / 2)) * 2
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', a.video, '-map', '0:v:0', '-fps_mode', 'passthrough',
                    '-vf', f'scale={CELL}:{ch}:flags=area', '-q:v', '4', os.path.join(tmp, 'f_%06d.jpg')], check=True)
    f = lib.font(15)

    def cell(i):
        return Image.open(os.path.join(tmp, f'f_{i + 1:06d}.jpg'))

    def sheet(rows, path, redset=()):
        """rows：[(行首说明, [帧号…])]"""
        cols = max(len(r[1]) for r in rows)
        H = sum((ch + 22) + (20 if r[0] else 0) for r in rows)
        im = Image.new('RGB', (cols * (CELL + 3), H), (40, 40, 40)); d = ImageDraw.Draw(im); y = 0
        for title, fr in rows:
            if title:
                d.text((4, y + 1), title, fill=(120, 220, 255), font=f); y += 20
            for c, i in enumerate(fr):
                if i >= n:
                    continue
                x = c * (CELL + 3)
                im.paste(cell(i), (x, y + 20))
                d.text((x + 3, y + 1), f'{i} {i / fps:.2f}', fill=(255, 255, 0), font=f)
                if i in redset:
                    d.rectangle([x, y + 20, x + CELL - 1, y + 25], fill=(255, 40, 40))
            y += ch + 22
        im.save(path, quality=85)

    od = os.path.join(a.out, '总览'); shutil.rmtree(od, ignore_errors=True); os.makedirs(od)
    idx = list(range(0, n, meige))
    red = {i for i in idx if any(i - meige < c <= i for c in cuts)}
    per = 10 * 8
    for k in range(0, len(idx), per):
        part = idx[k:k + per]
        sheet([('', part[r:r + 10]) for r in range(0, len(part), 10)], os.path.join(od, f'总览_{k // per + 1:02d}.jpg'), red)

    ad = os.path.join(a.out, '动作条'); shutil.rmtree(ad, ignore_errors=True); os.makedirs(ad)
    rows, sheets = [], 0

    def flush():
        nonlocal rows, sheets
        if rows:
            sheets += 1
            sheet(rows, os.path.join(ad, f'动作条_{sheets:02d}.jpg'), set(cuts)); rows = []
    for k, t in enumerate(acts):
        s, e = max(0, t['起'] - 2), min(n, t['止'] + 2)
        step = 1 if e - s <= 40 else int(np.ceil((e - s) / 40))
        fr = list(range(s, e, step))
        t['编号'] = k + 1; t['隔几帧取'] = step
        need = (len(fr) + 9) // 10
        if len(rows) + need > 8:
            flush()
        t['在哪张图'] = f'动作条_{sheets + 1:02d}.jpg'
        for r in range(0, len(fr), 10):
            rows.append((f'动作 {k + 1}：第 {t["起"]} 到 {t["止"]} 帧（{t["起秒"]} 秒起，{t["帧数"]} 帧）' + ('，隔 %d 帧取一格' % step if step > 1 else '') if r == 0 else '', fr[r:r + 10]))
    flush()
    shutil.rmtree(tmp, ignore_errors=True)

    long_acts = [t for t in acts if t['帧数'] > fps * 2]
    out = dict(视频=dict(文件=a.video, 宽=v['w'], 高=v['h'], 帧率=round(fps, 3), 帧数=n, 秒=round(n / fps, 2), 有声音=v['audio'],
                       帧率不固定=bool(v['avg'] != v['r'])),
               门槛=dict(换屏候选_整幅变化超过=T_CUT, 在动_最大小块变化超过=T_MOVE, 轻微变_隔半秒变化超过=T_SLOW, 轻微变_每帧变化范围=list(T_GENTLE), 不看的区域=a.bukan),
               换屏候选=cuts, 动作区间=acts, 一直在轻微变的区间=slows)
    json.dump(out, open(os.path.join(a.out, '全片概况.json'), 'w'), ensure_ascii=False, indent=1)
    L = [f'{a.video}：{v["w"]}×{v["h"]}，每秒 {fps:g} 帧，共 {n} 帧、{n / fps:.1f} 秒' + ('' if v['audio'] else '，没有声音'),
         f'换屏候选 {len(cuts)} 处（整幅变化超过 {T_CUT:g} 的尖峰）。叠化、慢慢变形这类换屏不会在这里面，要到动作条里找。',
         '  帧号：' + '、'.join(str(c) for c in cuts),
         f'动作区间 {len(acts)} 段，合计 {int(moving.sum())} 帧，占全片 {100 * moving.mean():.0f}%。其中超过 2 秒的有 {len(long_acts)} 段'
         + ('（多半是真人画面或会动的视频片段，不是动效；如果是角上的真人小窗，用 --bukan 把它那一块去掉重跑）' if long_acts else '') + '。',
         f'一直在轻微变的区间 {len(slows)} 段，合计 {sum(t["帧数"] for t in slows)} 帧。真人画面、会动的视频片段、画面在慢慢推近或平移都长这样，'
         '逐段用 huamiantiao.py 隔 5 帧取一格看是哪种；看着像静止照片的那些，就是在慢慢推近或平移：'
         + '、'.join(f'{t["起"]}-{t["止"]}' for t in slows[:80]),
         f'总览图 {len(os.listdir(od))} 张（每 {meige} 帧一格），动作条 {sheets} 张。',
         '这些数只说明「哪里有变化」。是什么在变、怎么变，要看图。']
    if v['avg'] != v['r']:
        L.insert(1, '【注意】这条片子的帧率不固定，帧号乘出来的秒数会有偏差，对口播时间时以实际时间为准。')
    open(os.path.join(a.out, '全片概况.txt'), 'w').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
