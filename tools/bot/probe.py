"""Find an open direction: try short moves at several headings, report how far each got (returns to start)."""
import sys, time, math
ARGS = sys.argv[1:]; sys.argv = ["sam.py", "look"]
exec(open('sam.py', encoding='utf-8').read().split("if __name__ == '__main__':")[0])
s = Sam(); s.front()
x0, y0, z0, yaw0 = s.pose()
step = float(ARGS[0]) if ARGS else 2.0
res = []
for d in (0, 25, -25, 50, -50, 75, -75, 105, -105):
    h = math.radians(yaw0 + d)
    s.goto(x0 + math.cos(h) * step * 100, y0 + math.sin(h) * step * 100, timeout=4, tol=0.3)
    x, y, z, _ = s.pose()
    got = math.hypot(x - x0, y - y0) / 100
    res.append((got, d, z - z0))
    s.goto(x0, y0, timeout=5, tol=0.3)
for got, d, dz in sorted(res, reverse=True):
    print('heading %+4d deg: moved %.1fm dz %+.0f' % (d, got, dz))
