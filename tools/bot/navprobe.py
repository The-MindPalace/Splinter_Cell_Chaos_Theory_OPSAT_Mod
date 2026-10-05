"""Read-only probe: the level's AI navigation network (NavigationPoints + ReachSpecs) from game memory.

  python navprobe.py            counts per class, property offsets, a few sample links
"""
import collections
import ctypes
import struct
import sys

sys.path.insert(0, r'C:\Program Files (x86)\Steam\steamapps\common\Splintercell Chaos Theory\CheatOverlay')
ctypes.windll.shcore.SetProcessDpiAwareness(2)
import cheat_overlay as co

WANT = {('NavigationPoint', 'PathList'), ('NavigationPoint', 'nextNavigationPoint'), ('ReachSpec', 'Start'),
        ('ReachSpec', 'End'), ('ReachSpec', 'reachFlags'), ('ReachSpec', 'Distance'),
        ('ReachSpec', 'CollisionRadius'), ('ReachSpec', 'CollisionHeight'), ('Actor', 'Location'),
        ('LevelInfo', 'NavigationPointList'), ('NavigationPoint', 'upstreamPaths')}


def main():
    g = co.Game(co.Mem(co.find_pid()))
    props, classes = {}, {}
    objs = g.objects()
    for o in objs:
        hdr = g.m.read(o + co.O_OUTER, 0x10)
        if not hdr:
            continue
        outer, _, nm, cls = struct.unpack('<IIII', hdr)
        n, cn = g.name(nm), g.oname(cls)
        if cn and cn.endswith('Property') and (g.oname(outer), n) in WANT:
            props[(g.oname(outer), n)] = (g.m.u32(o + co.P_OFFSET), cn)
        if cn == 'Class':
            classes[o] = n
    for k in sorted(WANT):
        print('%-40s %s' % ('.'.join(k), props.get(k)))
    # every object whose class (or a superclass) is NavigationPoint
    nav_cls = next(o for o, n in classes.items() if n == 'NavigationPoint')
    count, navs = collections.Counter(), []
    for o in objs:
        c = g.m.u32(o + co.O_CLASS)
        if c in classes and g._is_a(c, nav_cls):
            count[classes[c]] += 1
            navs.append(o)
    print('navigation points:', len(navs), dict(count.most_common(15)))
    loc = props[('Actor', 'Location')][0]
    pl = props.get(('NavigationPoint', 'PathList'), (None,))[0]
    s_end = props.get(('ReachSpec', 'End'), (None,))[0]
    s_flags = props.get(('ReachSpec', 'reachFlags'), (None,))[0]
    links = 0
    for o in navs[:400]:
        if pl is None:
            break
        data, num = struct.unpack('<II', g.m.read(o + pl, 8))
        if 0 < num < 64:
            links += num
    print('links from first 400 nodes:', links)
    for o in navs[:6]:
        p = g._vec(o + loc)
        line = '%s %s at %s' % (g.oname(o), classes.get(g.m.u32(o + co.O_CLASS)), [round(v) for v in p] if p else None)
        if pl is not None:
            data, num = struct.unpack('<II', g.m.read(o + pl, 8))
            ends = []
            for i in range(min(num, 6)):
                spec = g.m.u32(data + 4 * i)
                end = g.m.u32(spec + s_end) if spec else 0
                fl = g.m.u32(spec + s_flags) if spec and s_flags is not None else None
                ep = g._vec(end + loc) if end else None
                ends.append((g.oname(end), [round(v) for v in ep] if ep else None, fl))
            line += ' -> %s' % ends
        print(' ', line)


if __name__ == '__main__':
    main()
