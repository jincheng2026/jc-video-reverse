# -*- coding: utf-8 -*-
"""逐帧量大字的每个字、每个标注在原片里每一帧有多「显」（0 = 没有，1 = 完全出来），写成每帧一个数的表。
两条片子都能用。要先用 逐帧工具.py 缓存 做好原片缓存。

怎么量：
  1. 用渲染程序把这个字（或这个标注）画出来，拿它的笔画位置往外扩几像素当「量的地方」。
  2. 在原片 270×480 彩色缓存的每一帧里，数这块地方的「字色」有多浓：
       白字：每个像素 min(红,绿,蓝) 超过 40 的部分；红字、红标注：红 减 max(绿,蓝) 超过 15 的部分；黄：min(红,绿) 减 蓝 超过 20 的部分。
       （都是线性的，半透明的字算一半）
     原片的数要先减掉复刻这一屏「只有图、没有字和标注」那一帧同一处的数，免得底下的图（比如滑进来的脸）被当成字。
  3. 字没出来之前的几帧当 0，字出全以后那一段的中位数当 1，中间按比例算。小于 0.08 记 0，大于 0.9 记 1（去掉抖动）。
  这个数既包括变透明，也包括字从小变大、从糊变清（都会让字色变淡），所以照它画出来的透明度能对上「哪一帧出、出几帧」，
  对不上「字变大」这类形状变化。

用法：
  python3 -B 量字和标注.py 逐屏数据.json 缓存文件夹 屏号[,屏号...] [存=结果.json] [写回]
      屏里每段大字记 「逐帧」: {"起": 第一帧, "透明": [[每个字的数], ...每帧一行]}；
      每个标注记 「逐帧透明」: {"起", "值"}，带「画出帧」的标注，出来那几帧记成「逐帧画到」（画到几成），透明记 1。
      渲染程序见到这两样就照表画，不再用 起、止、淡入、淡出、闪 这些公式。
  python3 -B 量字和标注.py 形变 逐屏数据.json 缓存文件夹 屏号[,屏号...] [存=结果.json] [写回]
      量每段大字出来那几帧整体的放大和挪动（原片这段字停稳那一帧的字色当样子，只用来量），
      记成 「逐帧形变」: {"起", "值": [[放大, 右移, 下移], ...]}（以字框中心放大，像素按 1080 宽算）。
  python3 -B 量字和标注.py 并入 逐屏数据.json 结果1.json [...]
      并入时先清理：第一次到 0.5 之前两帧以前、最后一次到 0.5 之后两帧以后的零星数记 0（底图对不齐带出来的假信号）；
      第一次过半的帧和数据里写的出现帧差 6 帧以上、而且之后过半的帧不到八成的，当成量错了，不用（留着原来的公式）。
      这一屏开始以前的帧记 0（那时画面还是上一屏）。
      不闪的大字和数据里没写「闪」的标注：出来的那几帧只许越来越显，全出以后 4 帧以内的短低谷补成 1；
      一直到不了 0.9 却稳稳挂着的，按它挂着时的数当 1。
      数据里写了「止」（消失帧）的，那一帧以后记 0（字幕压在字框里时，量的时候会把字幕当成字还在）。
      「弹一下」和带「起始比例」的大字加「逐帧带形变」，字的放大缩小照原来的出法做。
"""
import json, os, sys
import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import 逐帧工具 as T


def sig_img(rgb, color):
    a = rgb.astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    if color == '白':
        return np.clip((np.minimum(np.minimum(r, g), b) - 40) / 215, 0, 1)
    if color == '红':
        return np.clip((r - np.maximum(g, b) - 15) / 200, 0, 1)
    if color == '黄':
        return np.clip((np.minimum(r, g) - b - 20) / 200, 0, 1)
    return np.clip((a.mean(-1) - 120) / 120, 0, 1)


def mask270(alpha1080, grow=6):
    """1080×1920 的不透明度 → 270×480 的量的地方（往外扩 grow 像素）。"""
    m = Image.fromarray((alpha1080 > 60).astype(np.uint8) * 255)
    if grow:
        m = m.filter(ImageFilter.MaxFilter(2 * grow + 1))
    m = m.resize((270, 480), Image.BOX)
    return np.asarray(m, np.float32) / 255 > 0.25


class BG:
    """复刻这一屏「只有图、没有字和标注」的样子（缩到 270×480），量字色时减掉，免得图滑过来被当成字。"""

    def __init__(self, R, base, data, scr):
        self.R, self.base, self.data, self.scr, self.cache = R, base, data, scr, {}
        self.orig = R.Orig(data['视频']['原片'], max(scr['起'] - 12, 0))

    def get(self, f):
        if f not in self.cache:
            f2 = min(max(f, self.scr['起']), self.scr['止'] - 1)
            lay, _, _ = self.R.photo_layer(self.base, self.scr, f2, self.orig)
            self.R.draw_pip(lay, self.data, f2, self.orig, under=True)
            self.R.draw_pip(lay, self.data, f2, self.orig)
            self.cache[f] = np.asarray(lay.convert('RGB').resize((270, 480), Image.BOX))
        return self.cache[f]


def curve(C, m, color, fa, fb, f_on, bg=None):
    """在 [fa, fb) 里逐帧量。f_on：数据里写的出现帧，用来定「之前」和「之后」。bg：减掉复刻的底图。"""
    def one(f):
        if not m.any():
            return 0.0
        so = sig_img(C[f], color)[m]
        if bg is not None:
            so = np.clip(so - sig_img(bg.get(f), color)[m], 0, 1)
        return float(so.mean())
    s = np.array([one(f) for f in range(fa, fb)])
    pre = s[:max(1, f_on - fa - 1)]
    lo = float(np.median(pre)) if len(pre) else 0.0
    post = s[min(f_on - fa + 12, len(s) - 1):]
    hi = float(np.percentile(post, 70)) if len(post) else s.max()
    if hi - lo < 0.02:
        return None, s
    a = (s - lo) / (hi - lo)
    a = np.where(a < 0.08, 0.0, np.where(a > 0.9, 1.0, a))
    return np.clip(a, 0, 1), s


def table(a, fa):
    """把一条曲线裁成「第一帧不为 0 到最后一帧不为 0」，再多留一帧 0 收尾。"""
    nz = np.where(a > 0)[0]
    if len(nz) == 0:
        return None
    i0, i1 = nz[0], nz[-1]
    vals = list(a[i0:i1 + 1])
    if i1 + 1 < len(a):
        vals.append(0.0)
    return fa + int(i0), [round(float(v), 2) for v in vals]


def measure_screen(R, base, data, scr, C, style):
    out = {'字': {}, '标注': {}}
    a_s, b_s = scr['起'], scr['止']
    bg = BG(R, base, data, scr)
    for ti, tx in enumerate(scr.get('大字', [])):
        st = dict(style); st.update(tx.get('样式', {})); st['颜色'] = tx.get('色', '白')
        chars = R.text_chars(st, tx['字'], tx['框'], tx.get('竖排', False))
        fa = max(a_s - 6, tx['起'] - 12, 0)
        fb = b_s + (6 if scr['号'] < data['屏'][-1]['号'] else 0)
        fb = min(fb, len(C))
        curves = []
        for lay, (ox, oy), _ in chars:
            al = np.zeros((1920, 1080), np.float32)
            A = np.asarray(lay.getchannel('A'), np.float32)
            h, w = A.shape
            x0, y0 = max(ox, 0), max(oy, 0)
            x1, y1 = min(ox + w, 1080), min(oy + h, 1920)
            al[y0:y1, x0:x1] = A[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
            m = mask270(al, 5)
            a, _ = curve(C, m, tx.get('色', '白'), fa, fb, tx['起'], bg)
            curves.append(a)
        if any(c is None for c in curves):
            continue
        A = np.stack(curves, 1)  # 帧 × 字
        any_on = A.max(1) > 0
        nz = np.where(any_on)[0]
        if len(nz) == 0:
            continue
        i0, i1 = nz[0], nz[-1]
        rows = A[i0:i1 + 1].round(2).tolist()
        if i1 + 1 < len(A):
            rows.append([0.0] * A.shape[1])
        out['字'][str(ti)] = {'起': fa + int(i0), '透明': rows}
    for ai, an in enumerate(scr.get('标注', [])):
        it = dict(an); it.pop('闪', None); it.pop('淡出', None); it.pop('止', None)
        lay = R.ann_layer(it, it['起'] + 200)
        al = np.asarray(lay.getchannel('A'), np.float32)
        m = mask270(al, 4)
        col = an.get('色', '黄' if an['种类'] in ('黄箭头', '箭头线') else '红')
        fa = max(a_s - 2, an['起'] - 12, 0)
        fb = min(b_s + 4, len(C))
        a, _ = curve(C, m, col, fa, fb, an['起'], bg)
        if a is None:
            continue
        t = table(a, fa)
        if t is None:
            continue
        f0, vals = t
        res = {'起': f0, '值': vals}
        if an.get('画出帧'):
            # 第一次到 1 之前算「正在画」：画到几成 = 这个数，透明 1
            k = next((i for i, v in enumerate(vals) if v >= 0.9), len(vals))
            res['画到'] = [round(v, 2) for v in vals[:k]]
            res['值'] = [1.0] * k + vals[k:]
        out['标注'][str(ai)] = res
    return out


def clean_col(v):
    """去掉零星的假信号：第一次到 0.5 之前两帧以前、最后一次到 0.5 之后两帧以后的数都记 0。"""
    v = list(v)
    hi = [i for i, x in enumerate(v) if x >= 0.5]
    if not hi:
        return [0.0] * len(v)
    for i in range(0, max(hi[0] - 2, 0)):
        v[i] = 0.0
    for i in range(hi[-1] + 3, len(v)):
        v[i] = 0.0
    return v


def steady(v):
    """不闪的字：从开始显到第一次全出只许往上走，第一次全出到最后一次 ≥0.9 之间的低谷补成 1（字没动，是量的时候被底下的图或字变大带偏了）。"""
    v = list(v)
    full = [i for i, x in enumerate(v) if x >= 0.9]
    if not full:
        # 一直没到 0.9、却稳稳挂着的（字比复刻的字细，量出来偏淡），按它挂着时的数当 1
        hi = [i for i, x in enumerate(v) if x >= 0.4]
        if not hi:
            return v
        p = float(np.percentile(v[hi[0]:hi[-1] + 1], 80))
        if p < 0.4:
            return v
        v = [min(1.0, x / p) for x in v]
        full = [i for i, x in enumerate(v) if x >= 0.9]
    m = 0.0
    for i in range(full[0] + 1):
        m = max(m, v[i]); v[i] = m
    # 全出以后的低谷：只补 4 帧以内的短低谷（长的低谷是真没了，比如前一屏的东西被当成这段字）
    i = full[0]
    while i <= full[-1]:
        if v[i] < 0.9:
            j = i
            while j <= full[-1] and v[j] < 0.9:
                j += 1
            if j - i <= 4:
                for q in range(i, j):
                    v[q] = 1.0
            i = j
        else:
            i += 1
    return v


def stays(vals):
    """第一次过半以后，过半的帧占几成（真出来的东西会一直挂着，量错的是零星几帧）。"""
    hi = [i for i, x in enumerate(vals) if x >= 0.5]
    if not hi:
        return 0.0
    rest = vals[hi[0]:]
    return sum(1 for x in rest if x >= 0.5) / len(rest)


def trim_table(f0, rows):
    """把表前后全是 0 的行去掉（留一行 0 收尾）。rows：每帧一个数，或每帧一行。"""
    nz = lambda r: (max(r) if isinstance(r, list) else r) > 0
    idx = [i for i, r in enumerate(rows) if nz(r)]
    if not idx:
        return None
    i0, i1 = idx[0], idx[-1]
    out = rows[i0:i1 + 1]
    if i1 + 1 < len(rows):
        out.append(rows[i1 + 1])
    return f0 + i0, out


def measure_shape(data, scr, tx, C):
    """量一段大字出来那几帧整体怎么变：每帧放大几倍（以字框中心为准）、往右往下挪多少像素。
    拿原片里这段字停稳那一帧的「字色」当样子（只用来量，不画），在前面每一帧里试放大 0.5 到 1.6 倍、挪 ±48 像素，找最像的。"""
    tb = tx.get('逐帧')
    if tb:
        rows = tb['透明']
        full = [i for i, r in enumerate(rows) if min(r) >= 0.99]
        if not full:
            return None
        f_on = tb['起'] + next(i for i, r in enumerate(rows) if max(r) >= 0.3)
        fs = tb['起'] + full[min(len(full) - 1, 12)]  # 全出以后再等几帧（等它停稳）
    else:
        f_on = tx['起']
        fs = min(tx.get('止', scr['止']) - 2, tx['起'] + 20)
    fs = min(fs, scr['止'] - 1, len(C) - 1)
    col = tx.get('色', '白')
    x0, y0, x1, y1 = [v / 4 for v in tx['框']]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    pad = 14
    X0, Y0 = int(max(x0 - pad - (x1 - x0) * 0.3, 0)), int(max(y0 - pad - (y1 - y0) * 0.3, 0))
    X1, Y1 = int(min(x1 + pad + (x1 - x0) * 0.3, 270)), int(min(y1 + pad + (y1 - y0) * 0.3, 480))
    tpl_full = sig_img(C[fs], col)
    tx0, ty0, tx1, ty1 = int(max(x0 - 3, 0)), int(max(y0 - 3, 0)), int(min(x1 + 3, 270)), int(min(y1 + 3, 480))
    tpl = tpl_full[ty0:ty1, tx0:tx1]
    if tpl.sum() < 5:
        return None
    from PIL import Image as _I
    out = []
    rng = int(max(3, min(12, (y1 - y0) * 0.3)))  # 上下最多挪字高的三成，免得跳到旁边另一行字上
    prev = (1.0, 0, 0)
    for f in range(fs, max(f_on - 1, scr['起']) - 1, -1):  # 从停稳那一帧往前一帧一帧量，每帧只在后一帧附近找
        S = sig_img(C[f], col)[Y0:Y1, X0:X1]
        if S.sum() < 2:
            out.append(None); continue
        best = None
        for sc in np.arange(max(0.5, prev[0] - 0.15), min(1.6, prev[0] + 0.15) + 1e-6, 0.05):
            h, w = max(2, int(round(tpl.shape[0] * sc))), max(2, int(round(tpl.shape[1] * sc)))
            T_ = np.asarray(_I.fromarray((tpl * 255).astype(np.uint8)).resize((w, h), _I.BILINEAR), np.float32) / 255
            # 模板中心放在 (cx+dx, cy+dy)
            for dy in range(max(-rng, prev[2] // 4 - 4), min(rng, prev[2] // 4 + 4) + 1):
                for dx in range(max(-12, prev[1] // 4 - 4), min(12, prev[1] // 4 + 4) + 1):
                    ox = int(round(cx + dx - w / 2)) - X0
                    oy = int(round(cy + dy - h / 2)) - Y0
                    if ox < 0 or oy < 0 or ox + w > S.shape[1] or oy + h > S.shape[0]:
                        continue
                    win = S[oy:oy + h, ox:ox + w]
                    num = float((win * T_).sum())
                    den = float((T_ * T_).sum()) + 1e-6
                    a = num / den
                    e = float(((win - a * T_) ** 2).sum()) + float((S ** 2).sum() - (win ** 2).sum())
                    if best is None or e < best[0]:
                        best = (e, round(float(sc), 2), dx * 4, dy * 4, a)
        out.append(best[1:4] if best and best[4] > 0.2 else None)
        if best and best[4] > 0.2:
            prev = best[1:4]
    out.reverse()
    # 去掉末尾已经是 1 倍、不挪的帧
    while out and (out[-1] is None or (abs(out[-1][0] - 1) < 0.03 and abs(out[-1][1]) <= 4 and abs(out[-1][2]) <= 4)):
        out.pop()
    if not out:
        return None
    f0 = max(f_on - 1, scr['起'])
    vals = []
    last = [1.0, 0, 0]
    for r in out:
        last = list(r) if r is not None else last
        vals.append(last)
    return {'起': f0, '值': vals}


def merge(jp, files):
    data = json.load(open(jp))
    for fn in files:
        res = json.load(open(fn))
        for scr in data['屏']:
            r = res.get(str(scr['号']))
            if not r:
                continue
            for k, v in r['字'].items():
                A = np.array(v['透明'], float)
                tx = scr['大字'][int(k)]
                k_in = scr['起'] - v['起']  # 这一屏开始以前（还是上一屏的画面）量到的都不算
                if k_in > 0:
                    A[:min(k_in, len(A))] = 0
                cols = [clean_col(A[:, c]) for c in range(A.shape[1])]
                if not tx.get('闪'):
                    cols = [steady(c) for c in cols]
                A = np.stack(cols, 1)
                if tx.get('止') is not None:  # 数据里量过消失帧的，那一帧以后记 0（字幕压在字框里时会被当成字还在）
                    k0 = tx['止'] - v['起']
                    if 0 <= k0 < len(A):
                        A[k0:] = 0
                t = trim_table(v['起'], A.round(2).tolist())
                if t is None:
                    continue
                first_half = next((i for i, r in enumerate(t[1]) if max(r) >= 0.5), None)
                if first_half is None or (abs(t[0] + first_half - tx['起']) > 6 and stays([max(r) for r in t[1]]) < 0.8):
                    print(f"  屏{scr['号']}「{tx['字']}」量出来第 {t[0] + (first_half or 0)} 帧过半，和数据的 {tx['起']} 差太多，不用")
                    continue
                tx['逐帧'] = {'起': t[0], '透明': t[1]}
                if tx.get('出法') == '弹一下' or tx.get('参数', {}).get('起始比例'):
                    tx['逐帧带形变'] = True  # 字放大缩小照原来的出法做，透明度照表
            for k, v in r['标注'].items():
                an = scr['标注'][int(k)]
                vals = v['值']
                if '画到' in v:  # 画到几成的那几帧本来记 1，先还原成量出来的数再清理
                    vals = v['画到'] + vals[len(v['画到']):]
                k_in = scr['起'] - v['起']
                if k_in > 0:
                    vals = [0.0] * min(k_in, len(vals)) + list(vals[k_in:])
                if an.get('止') is not None:
                    k0 = an['止'] - v['起']
                    if 0 <= k0 < len(vals):
                        vals = list(vals[:k0]) + [0.0] * (len(vals) - k0)
                vals = clean_col(vals)
                if not an.get('闪'):  # 数据里没写闪的标注：中间的起伏是箭头来回顶、底图带偏的，抹平
                    vals = steady(vals)
                t = trim_table(v['起'], [round(x, 2) for x in vals])
                if t is None:
                    continue
                f0, vals = t
                first_half = next((i for i, x in enumerate(vals) if x >= 0.5), None)
                if first_half is None or (abs(f0 + first_half - an['起']) > 6 and stays(vals) < 0.8):
                    print(f"  屏{scr['号']} 标注{k}（{an['种类']}）量出来第 {f0 + (first_half or 0)} 帧过半，和数据的 {an['起']} 差太多，不用")
                    continue
                res_v = vals
                if '画到' in v:
                    k2 = next((i for i, x in enumerate(vals) if x >= 0.9), len(vals))
                    an['逐帧画到'] = {'起': f0, '值': vals[:k2]}
                    res_v = [1.0] * k2 + vals[k2:]
                an['逐帧透明'] = {'起': f0, '值': res_v}
    json.dump(data, open(jp, 'w'), ensure_ascii=False, indent=1)
    print('并入了', len(files), '个结果')


def main():
    if sys.argv[1] == '并入':
        return merge(sys.argv[2], sys.argv[3:])
    if sys.argv[1] == '形变':  # 形变 逐屏数据.json 缓存 屏号 存=结果.json
        jp, cdir = sys.argv[2], sys.argv[3]
        which = [int(v) for v in sys.argv[4].split(',')]
        opts = dict(r.split('=', 1) for r in sys.argv[5:] if '=' in r)
        data = json.load(open(jp)); C = T.orig_rgb(cdir)
        saved = {}
        for scr in data['屏']:
            if scr['号'] not in which:
                continue
            for ti, tx in enumerate(scr.get('大字', [])):
                r = measure_shape(data, scr, tx, C)
                if r:
                    saved.setdefault(str(scr['号']), {})[str(ti)] = r
                    print(f"屏{scr['号']}「{tx['字']}」第 {r['起']} 帧起 {len(r['值'])} 帧：{r['值'][:8]}")
        if '存' in opts:
            json.dump(saved, open(opts['存'], 'w'), ensure_ascii=False)
        if '写回' in sys.argv[5:]:
            for scr in data['屏']:
                for k, v in saved.get(str(scr['号']), {}).items():
                    scr['大字'][int(k)]['逐帧形变'] = v
            json.dump(data, open(jp, 'w'), ensure_ascii=False, indent=1)
        return
    jp, cdir = sys.argv[1], sys.argv[2]
    which = [int(v) for v in sys.argv[3].split(',')]
    rest = sys.argv[4:]
    opts = dict(r.split('=', 1) for r in rest if '=' in r)
    data = json.load(open(jp))
    base = os.path.dirname(os.path.abspath(jp))
    R = T.load_renderer()
    R.OS = data['视频'].get('原片倍数', 2)
    C = T.orig_rgb(cdir)
    saved = {}
    for scr in data['屏']:
        if scr['号'] not in which:
            continue
        r = measure_screen(R, base, data, scr, C, data['样式']['大字'])
        saved[str(scr['号'])] = r
        for k, v in r['字'].items():
            tx = scr['大字'][int(k)]
            print(f"屏{scr['号']} 大字「{tx['字']}」数据起 {tx['起']}，量出第 {v['起']} 帧起，{len(v['透明'])} 帧")
            if '存' not in opts:
                for i, row in enumerate(v['透明'][:20]):
                    print('    ', v['起'] + i, row)
        for k, v in r['标注'].items():
            an = scr['标注'][int(k)]
            print(f"屏{scr['号']} 标注{k}（{an['种类']}）数据起 {an['起']}，量出第 {v['起']} 帧起，{len(v['值'])} 帧；画到 {v.get('画到')}")
            if '存' not in opts:
                print('    ', v['值'][:40])
    if '存' in opts:
        json.dump(saved, open(opts['存'], 'w'), ensure_ascii=False)
    if '写回' in rest:
        json.dump(saved, open(os.path.join(os.path.dirname(cdir), '_临时.json'), 'w'))
        merge(jp, [os.path.join(os.path.dirname(cdir), '_临时.json')])


if __name__ == '__main__':
    main()
