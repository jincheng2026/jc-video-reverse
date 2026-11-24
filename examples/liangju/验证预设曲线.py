# -*- coding: utf-8 -*-
"""验证「同一种过渡、同一种进场、同一种字出法是不是同一条曲线」。两条片子都能用。
只读 逐屏数据.json 里量出来的逐帧数（轨迹、逐帧合成表、字表、标注表），不重新量。

怎么比：
  图滑入：每张滑进来的图，算它每一帧离停稳位置还差多少像素 d，按「从开始动到停稳」把时间归一成 0 到 1，
          d 除以开始那一帧的 d 也归一成 0 到 1，拿 d = (1 - t)^p 去配，每张图得到一个 p 和用了几帧。
          另外看「每帧走的像素 ÷ 前一帧走的像素」（减速比），预设的减速曲线这个比在各屏应该一样。
          开头几帧图还在画面外看不见，量不到，所以从图露出来那一帧算起。
  换屏过渡：同一种过渡的逐帧表，时间按表的长度归一成 0 到 1，看新画面占的比例、模糊、加亮在 0、0.25、0.5、0.75、1 这几个点的值。
  大字：每个字从开始显出来（透明度到 0.25）到完全出来用几帧；一个字一个字出的，相邻两个字隔几帧。按出法分开数。
  标注：沿线画出用几帧、每帧画到几成。

用法：python3 -B 验证预设曲线.py 逐屏数据.json [输出.md]   不写输出就打印。
其他程序里用：import 验证预设曲线 as V; V.report(data) 返回 markdown 文字。
"""
import json, math, sys
from collections import defaultdict
import numpy as np


def slide_curves(data):
    out = []
    for s in data['屏']:
        for it in s.get('图', []):
            mv = it.get('进场', {})
            tr = it.get('轨迹')
            if not tr or mv.get('法') != '滑入':
                continue
            vis = [r for r in tr if r[4] >= 0.15 and r[1] > -5000]
            if len(vis) < 4:
                continue
            xf, yf = tr[-1][1], tr[-1][2]
            d = [math.hypot(r[1] - xf, r[2] - yf) for r in vis]
            # 停稳：第一次离终点不到 2 像素
            k_end = next((i for i, x in enumerate(d) if x < 2), len(d) - 1)
            if k_end < 3:
                continue
            d = d[:k_end + 1]
            steps = [d[i] - d[i + 1] for i in range(len(d) - 1)]
            ratios = [steps[i + 1] / steps[i] for i in range(len(steps) - 1) if steps[i] > 3]
            # 配 d = d0 (1 - t)^p，t = k / k_end
            ks = np.arange(len(d))
            t = ks / k_end
            sel = (d > np.array(2.0)) & (t < 1)
            sel = np.array([x > 2 for x in d]) & (t < 1)
            if sel.sum() >= 3:
                y = np.log(np.array(d)[sel] / d[0])
                x = np.log(1 - t[sel])
                p = float((x * y).sum() / max((x * x).sum(), 1e-9))
            else:
                p = float('nan')
            out.append(dict(屏=s['号'], 图=it['素材'], 方向=mv.get('从'), 帧数=k_end, 起=vis[0][0], 距离=round(d[0]), p=p,
                            步=[round(x) for x in steps], 减速比=[round(x, 2) for x in ratios],
                            曲线=[round(x / d[0], 3) for x in d]))
    return out


# 图滑入的标准曲线：每帧走的像素 ÷ 最快那一帧走的，从开始动到停稳 13 帧。
# 由第一条的横着滑进来的脸（第 34、41、49 屏能看全 12 帧：113、112、110、104、95、84、72、58、45、33、22、12）
# 和竖着滑进来的四宫格（第 20 屏：100、100、97、92、85、75、64、52、40、29、19、11）定的，两组除以最快那帧以后是同一串数。
STD_SLIDE = [0.98, 1.0, 0.99, 0.97, 0.92, 0.85, 0.75, 0.64, 0.52, 0.40, 0.29, 0.19, 0.11]


def slide_vs_std(data, tol=4):
    """每张滑入的图：按停稳那一帧对齐（去掉最后零点几到几像素的「找停」），只看露出来以后的那几帧，
    和标准曲线比。比法：用最小二乘定这张图「最快那帧走多少像素」，看每帧差几像素。"""
    out = []
    for s in data['屏']:
        for it in s.get('图', []):
            tr = it.get('轨迹'); mv = it.get('进场', {})
            if not tr or mv.get('法') != '滑入':
                continue
            xf, yf = tr[-1][1], tr[-1][2]
            d = [math.hypot(r[1] - xf, r[2] - yf) for r in tr]
            st = [d[i] - d[i + 1] for i in range(len(d) - 1)]
            while st and abs(st[-1]) < 6:   # 停稳前后那几帧零点几到几像素的抖动
                st.pop()
            # 往前找到第一帧露出来（前面是图还在画面外、量不准的帧）：从后往前，步长一直在变大就算
            k = len(st) - 1
            while k > 0 and st[k - 1] >= st[k] - 3 and st[k - 1] < 400:
                k -= 1
            seg = st[k:]
            v = np.array(seg[1:])  # 露出来的头一帧图只露一部分，位置量不准，不比
            n = len(v)
            best = None
            for o in (0, 1, 2):  # 停稳那一帧可能早认或晚认一帧：允许错开 0 到 2 帧
                if n < 4 or n + o > len(STD_SLIDE):
                    continue
                ref = np.array(STD_SLIDE[len(STD_SLIDE) - n - o:len(STD_SLIDE) - o])
                peak = float((ref * v).sum() / (ref * ref).sum())
                dev = float(np.abs(v - ref * peak).max())
                if best is None or dev < best[0]:
                    best = (dev, peak, o)
            if best is None:
                out.append(dict(屏=s['号'], 图=it['素材'], 从=mv.get('从'), 步=[round(x) for x in seg], 合=None))
                continue
            dev, peak, o = best
            out.append(dict(屏=s['号'], 图=it['素材'], 从=mv.get('从'), 步=[round(x) for x in seg], 最快=round(peak), 差=round(dev, 1), 错开=o, 合=dev <= tol))
    return out


def trans_curves(data):
    out = defaultdict(list)
    for s in data['屏']:
        tr = s.get('进法', {})
        if tr.get('法') != '逐帧合成':
            continue
        rows = tr['参数']['逐帧']
        pre = tr['参数'].get('前', 0)
        out[tr.get('原法', '?')].append((s['号'], pre, rows))
    return out


def at(seq, t):
    """按 0 到 1 的时间取序列里的值（线性插）。"""
    if len(seq) == 1:
        return seq[0]
    x = t * (len(seq) - 1)
    i = min(int(x), len(seq) - 2)
    return seq[i] + (seq[i + 1] - seq[i]) * (x - i)


def text_stats(data):
    ramp, gap = defaultdict(list), defaultdict(list)
    for s in data['屏']:
        for tx in s.get('大字', []):
            tb = tx.get('逐帧')
            if not tb:
                continue
            A = np.array(tb['透明'])
            kind = tx.get('出法', '淡入')
            starts = []
            for c in range(A.shape[1]):
                col = A[:, c]
                on = np.where(col >= 0.25)[0]
                full = np.where(col >= 0.99)[0]
                if len(on) and len(full):
                    ramp[kind].append(int(full[0] - on[0]))
                    starts.append(int(on[0]))
            if len(starts) > 1:
                gap[kind] += [b - a for a, b in zip(starts[:-1], starts[1:])]
    return ramp, gap


def std_table(data):
    """标准曲线表：每种动作定下来的那一条，用在哪几屏，哪几屏对不上（附数字）。"""
    sv = slide_vs_std(data)
    ok = sorted({x['屏'] for x in sv if x['合']})
    bad = [x for x in sv if not x['合']]
    wipe, smear = [], []
    for sc in data['屏']:
        for tx in sc.get('大字', []):
            src = tx.get('逐帧入形', {}).get('来源', '')
            if '撑开' in src or '片头' in src or '撑开' in src:
                wipe.append(f"{sc['号']}「{tx['字']}」")
            elif '虚影淡出' in src:
                smear.append(f"{sc['号']}「{tx['字']}」")
    tc = trans_curves(data)
    fl = {n: rows for n, _, rows in tc.get('闪白', [])}
    L = ['| 动作 | 标准曲线（每帧的数） | 用在哪里 | 对不上的（附数字） |', '| --- | --- | --- | --- |']
    L.append('| 图滑入（横着、竖着都是这一条，只是滑的距离不同） | 13 帧，每帧走的像素是最快那帧的 ' + '、'.join(f'{v:.2f}' for v in STD_SLIDE) +
             f' | {len(sv) - len(bad)} 张图，在第 ' + '、'.join(map(str, ok)) + ' 屏 | ' +
             '；'.join(f'第 {x["屏"]} 屏 {x["图"].split("_", 1)[-1]}：{"、".join(map(str, x["步"]))}' for x in bad) +
             '。第 48 屏四张是先慢后快再慢（切黑滑入），是另一条；第 30、31 屏轨迹只量到最后一两帧，比不了 |')
    L.append('| 字「一个字一个字从左边撑开」 | 一个字横向是满宽的 0.15、0.5、0.82、0.95、1（以字的左边为准）；字和字隔 1 到 2 帧起 | ' + '、'.join(wipe) +
             ' | 第 2、4 屏量出来一模一样（「无」字宽 8、26、45、52 列与 8、28、49、53 列，满宽约 52 到 57 列），这两屏直接用量出来的宽度；'
             '第 45 屏「原生鼻子过长」字和字隔 1 到 2 帧（第 3137、3139、3140、3142、3143、3145 帧起），第 2、4 屏隔 2 帧；第 1 屏是片头第一帧，从曲线中间开始 |')
    L.append('| 字「整段带虚影淡出」 | 3 帧：透明度 0.3、0.8、1；横向拖影 40、8、0 像素 | ' + '、'.join(smear) +
             ' | 起点是逐段看对照条定的。第 37 屏四段字出来时还带「先小后大再回弹」的大小变化，用量出来的「逐帧形变」叠在上面 |')
    L.append('| 换屏「叠化」 | 新画面每帧多占同样多（直线），旧画面同样少，不加亮、不放大 | 第 46 屏（12 帧） | '
             '换成标准以前量出来的：第 2 屏在新旧差得远的地方直接量，新画面占 0.11、0.18、0.26、0.34、0.49、0.58、0.67、0.77、0.86、0.96；'
             '第 46 屏拟合是 0.07、0.15、0.24、0.33、0.41、0.50、0.58、0.66、0.74、0.83、0.91。都是直线。'
             '第 2 屏其实不是普通叠化：原片两张脸的眼睛、嘴是一边叠一边挪到一起的（变脸），叠化画不出来；'
             '这一屏试过标准直线，开头那帧动法差从 19 涨到 51，退回按清晰度重拟的表 |')
    if len(fl) >= 2:
        L.append('| 换屏「闪白」 | 定不出一条 | 第 10、13、36 屏 | ' + '；'.join(
            f'第 {n} 屏加亮 ' + '、'.join(f"{r['加']:.0f}" for r in rows) + '，新画面占 ' + '、'.join(f"{r['新'][2]:.2f}" for r in rows) for n, rows in fl.items()) +
            '。第 10 屏是换屏前 3 帧开始变白，第 36 屏是换屏那一帧最白（加亮 242）再往下退，形状不同 |')
    L.append('| 标注「沿线画出」 | 4 帧左右画完，每帧多画四分之一左右（直线） | 第 14、24、30、37 屏的虚线 | 第 5 屏虚线慢：0.30、0.45、0.57、0.67、0.78、0.85；第 15 屏 0.36、0.48、0.61、0.71、0.82 |')
    return '\n'.join(L)


def report(data):
    L = []
    L.append('### 0. 同一种动作是不是同一条曲线（验证预设）')
    L.append('')
    L.append('**标准曲线表**（对得上的用同一条，只是时长或距离不同；对不上的把数字列出来）：')
    L.append('')
    L.append(std_table(data))
    L.append('')
    L.append('下面是每类的明细。')
    L.append('')
    L.append('这一节由 [验证预设曲线.py](../程序/验证预设曲线.py) 从数据里量出来的逐帧数算。剪映的转场和动画是预设，同一种预设应该是同一条曲线、只是时长不同；下面逐类看是不是这样。')
    L.append('')
    sl = slide_curves(data)
    L.append(f'**图滑入**（{len(sl)} 张有逐帧轨迹）：')
    L.append('')
    if sl:
        ps = np.array([x['p'] for x in sl if not math.isnan(x['p'])])
        ns = np.array([x['帧数'] for x in sl])
        allr = [r for x in sl for r in x['减速比'][2:]]  # 去掉头两帧（图刚露出来，量的位置不准）
        L.append(f'- 从图露出来到停稳：帧数中位 {np.median(ns):.0f}（最少 {ns.min()}、最多 {ns.max()}）。')
        L.append(f'- 按「离停稳还差多少 = 开始时 ×(1 − 归一时间)^p」配，p 的中位 {np.median(ps):.2f}，一半的图在 {np.percentile(ps, 25):.2f} 到 {np.percentile(ps, 75):.2f} 之间。'
                 f'p 越大越「先快后慢」。p = 2 是常见的二次减速。')
        if allr:
            ar = np.array(allr)
            L.append(f'- 减速比（这一帧走的 ÷ 上一帧走的，去掉刚露出来的头两帧）：中位 {np.median(ar):.2f}，一半在 {np.percentile(ar, 25):.2f} 到 {np.percentile(ar, 75):.2f} 之间。')
        # 典型的几张：每帧走的像素
        by = defaultdict(list)
        for x in sl:
            by[x['帧数']].append(x)
        L.append('- 每帧走的像素（同样帧数的图放在一起比，看是不是同一条）：')
        for n in sorted(by):
            g = by[n]
            if len(g) < 2:
                continue
            L.append(f'  - 用 {n} 帧的 {len(g)} 张：' + '；'.join(f'第 {x["屏"]} 屏 {x["图"].split("_")[-1]}（从{x["方向"]}）{"、".join(map(str, x["步"]))}' for x in g[:6]) + ('……' if len(g) > 6 else ''))
        # 归一曲线比一比：t = 0.25、0.5、0.75 时剩下几成
        cs = np.array([[at(x['曲线'], t) for t in (0.25, 0.5, 0.75)] for x in sl])
        L.append(f'- 归一以后 t = 0.25、0.5、0.75 时离停稳还剩几成：中位 {cs[:, 0].mean():.2f}、{np.median(cs[:, 1]):.2f}、{np.median(cs[:, 2]):.2f}；'
                 f'各图之间的标准差 {cs[:, 0].std():.2f}、{cs[:, 1].std():.2f}、{cs[:, 2].std():.2f}。')
        tight = (np.abs(cs - np.median(cs, 0)) < 0.08).all(1)
        L.append(f'- 三个点都在中位上下 0.08 以内的：{int(tight.sum())} / {len(sl)} 张。'
                 + ('**结论：滑入是同一条曲线**（只是时长不同），可以定一条标准曲线。' if tight.mean() >= 0.7 else
                    '**结论：不全是同一条**，偏出去的见下表。'))
        off = [x for x, ok in zip(sl, tight) if not ok]
        if off:
            L.append('- 偏出去的：' + '；'.join(f'第 {x["屏"]} 屏 {x["图"]}（{x["帧数"]} 帧，归一曲线 {"、".join(str(round(at(x["曲线"], t), 2)) for t in (0.25, 0.5, 0.75))}）' for x in off[:12]))
    L.append('')
    sv = slide_vs_std(data)
    ok = [x for x in sv if x['合']]
    bad = [x for x in sv if not x['合']]
    L.append('**图滑入的标准曲线**（按停稳那一帧对齐来比；上面按「从开始动」归一的比法，会被图开头几帧还在画面外、量不到拖偏）：')
    L.append('')
    L.append('- 标准曲线：从开始动到停稳 13 帧，每帧走的像素是最快那一帧的 ' + '、'.join(f'{v:.2f}' for v in STD_SLIDE) + '。'
             '横着滑进来的脸最快那帧走 113 像素左右（只露出后 8 帧：96、84、72、58、45、33、22、12），竖着滑进来的四宫格最快那帧 100 像素左右。只有滑的距离不同，形状一样。')
    L.append(f'- {len(sv)} 张滑入的图里，{len(ok)} 张每帧和标准曲线差不到 4 像素（就是同一条；比的时候去掉了图刚露出来那一帧，停稳那帧允许错开 0 到 2 帧）。')
    if bad:
        L.append(f'- 对不上的 {len(bad)} 张（每帧走的像素）：' + '；'.join(
            f'第 {x["屏"]} 屏 {x["图"].split("_", 1)[-1]}（从{x["从"]}）{"、".join(map(str, x["步"]))}' + (f'，最多差 {x["差"]} 像素' if x.get('差') is not None else '') for x in bad) + '。')
    L.append('')
    tc = trans_curves(data)
    L.append('**换屏过渡**（有逐帧合成表的屏，时间按表长归一）：')
    L.append('')
    L.append('| 过渡 | 屏 | 帧数 | 新画面占（t=0、0.25、0.5、0.75、1） | 旧画面占 | 模糊 | 加亮 |')
    L.append('| --- | --- | --- | --- | --- | --- | --- |')
    for kind, lst in tc.items():
        for n, pre, rows in lst:
            an = [r['新'][2] for r in rows]; ao = [r['旧'][2] for r in rows]
            bl = [r['新'][1] for r in rows]; ad = [r['加'] for r in rows]
            f5 = lambda q: '、'.join(f'{at(q, t):.2f}'.rstrip('0').rstrip('.') for t in (0, .25, .5, .75, 1))
            L.append(f'| {kind} | {n} | {len(rows)}（换屏前 {pre}） | {f5(an)} | {f5(ao)} | {f5(bl)} | {f5(ad)} |')
    L.append('')
    for kind, lst in tc.items():
        if len(lst) < 2:
            L.append(f'- {kind}：只有 {len(lst)} 屏有逐帧表，没法比是不是同一条。')
            continue
        M = np.array([[at([r['新'][2] for r in rows], t) for t in (0, .25, .5, .75, 1)] for _, _, rows in lst])
        spread = float(M.std(0).max())
        L.append(f'- {kind}（{len(lst)} 屏）：新画面占的比例在 5 个时间点上，各屏之间标准差最大 {spread:.2f}。'
                 + ('差得不多，可以当同一条曲线。' if spread < 0.12 else '差得开，不能直接当同一条，见上表。'))
    L.append('')
    ramp, gap = text_stats(data)
    L.append('**大字出法**（按数据里写的出法分开）：')
    L.append('')
    for kind in ramp:
        r = np.array(ramp[kind]); g = np.array(gap.get(kind, []))
        t = f'- {kind}：{len(r)} 个字，从开始显到全出用几帧：中位 {np.median(r):.0f}，'
        t += '、'.join(f'{v} 帧 {int((r == v).sum())} 个' for v in sorted(set(r.tolist()))[:6])
        if len(g):
            t += f'；相邻两个字隔几帧：中位 {np.median(g):.0f}，' + '、'.join(f'{v} 帧 {int((g == v).sum())} 次' for v in sorted(set(g.tolist()))[:6])
        L.append(t + '。')
    L.append('')
    dr = []
    for s in data['屏']:
        for an in s.get('标注', []):
            v = an.get('逐帧画到', {}).get('值')
            if v:
                dr.append((s['号'], an['种类'], v))
    if dr:
        L.append(f'**标注沿线画出**（{len(dr)} 个）：用几帧 ' + '、'.join(str(len(v)) for _, _, v in dr) + '。每帧画到几成：' +
                 '；'.join(f'第 {n} 屏{k} ' + '、'.join(f'{x:.2f}' for x in v) for n, k, v in dr[:8]) + '。')
        L.append('')
    return '\n'.join(L)


if __name__ == '__main__':
    d = json.load(open(sys.argv[1]))
    txt = report(d)
    if len(sys.argv) > 2:
        open(sys.argv[2], 'w').write(txt + '\n')
    print(txt)
