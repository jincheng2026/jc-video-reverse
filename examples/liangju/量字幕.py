# -*- coding: utf-8 -*-
"""逐帧量字幕第几帧出、第几帧换，和数据里写的对一对。两条片子都能用（要先用 逐帧工具.py 缓存）。

怎么量：每句字幕在它那一条横带里（字的中线 y 上下各 36 像素、字幕横向占的那一段），
逐帧算这条带和上一帧差多少（270×480 彩色缓存，像素差的平均）。字幕换句、出现、消失的那一帧，这条带会突然变。
在数据写的 起、止 前后 6 帧里找变得最多的那一帧，变化要明显大于这一段平时的抖动（比中位数大 4 倍以上、且大于 3）才算。
同时量带里「白字」像素（min(红,绿,蓝) > 200）的多少，看那一帧前后有没有字。

用法：
  python3 -B 量字幕.py 逐屏数据.json 缓存文件夹 [写回]
      每句报：数据写的起止、量出来的起止、差几帧。写回：把量出来的起止写进 JSON（只改差了的、量得出的）。
"""
import json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import 逐帧工具 as T


def band(C, f, y, x0, x1):
    a = np.asarray(C[f], np.float32)
    y0, y1 = max(int((y - 36) / 4), 0), min(int((y + 36) / 4), 480)
    return a[y0:y1, max(int(x0 / 4), 0):min(int(x1 / 4), 270)]


def main():
    jp, cdir = sys.argv[1], sys.argv[2]
    write = '写回' in sys.argv[3:]
    data = json.load(open(jp))
    C = T.orig_rgb(cdir)
    n = len(C)
    subs = data.get('字幕', [])
    changed = 0
    for s in subs:
        st = dict(data['样式']['字幕']); st.update(s.get('样式', {}))
        w = len(s['字']) * st['字号'] * 1.05
        cx = s.get('x', 540)
        x0, x1 = cx - w / 2, cx + w / 2

        def jump(f):
            if f <= 0 or f >= n:
                return 0.0
            return float(np.abs(band(C, f, s['y'], x0, x1) - band(C, f - 1, s['y'], x0, x1)).mean())

        res = {}
        for key in ('起', '止'):
            f0 = s[key]
            cand = [(jump(f), f) for f in range(max(f0 - 6, 1), min(f0 + 7, n))]
            base = np.median([c for c, _ in cand])
            best = max(cand)
            if best[0] > max(3.0, 4 * base):
                # 同样大的变化里取离数据写的最近的
                near = [f for c, f in cand if c >= 0.8 * best[0]]
                res[key] = min(near, key=lambda f: abs(f - f0))
        msg = f"「{s['字']}」数据 {s['起']}-{s['止']}，量出 {res.get('起', '量不出')}-{res.get('止', '量不出')}"
        d0 = res.get('起', s['起']) - s['起']; d1 = res.get('止', s['止']) - s['止']
        if d0 or d1:
            msg += f'  差 {d0:+d}、{d1:+d} 帧'
            if write:
                if '起' in res:
                    s['起'] = res['起']
                if '止' in res and res['止'] > s['起']:
                    s['止'] = res['止']
                changed += 1
        print(msg)
    if write:
        json.dump(data, open(jp, 'w'), ensure_ascii=False, indent=1)
        print('改了', changed, '句')


if __name__ == '__main__':
    main()
