"""Per-pixel-alpha overlay windows for OPSAT.

PilCanvas mimics the subset of tkinter.Canvas that OPSAT draws with (create_text/line/rectangle/
polygon/oval/arc/window, bbox, move, delete, tag_lower/tag_raise), but renders with Pillow onto an
RGBA image, so backgrounds can be real translucent gradients and text stays anti-aliased.
LayeredWindow puts that image on screen with UpdateLayeredWindow (click-through, topmost).
InputBox is a small normal window holding a text Entry, placed over the drawn input line.
"""
import ctypes
import ctypes.wintypes as wt

import os
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont

u32 = ctypes.WinDLL('user32', use_last_error=True)
g32 = ctypes.WinDLL('gdi32', use_last_error=True)
FONTS = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
PT = 96 / 72  # Tk font sizes are points; the overlay is per-monitor DPI aware at 96 dpi per 100 %

_font_cache = {}


def font(spec):
    """Tk-style font tuple -> PIL font. (family, size[, 'bold'])."""
    family, size = spec[0], spec[1]
    bold = len(spec) > 2 and 'bold' in spec[2]
    key = (family, size, bold)
    f = _font_cache.get(key)
    if f is None:
        px = max(6, int(round(abs(size) * PT)))
        if family.lower().startswith('segoe'):
            f = ImageFont.truetype(os.path.join(FONTS, 'seguisb.ttf' if bold else 'segoeui.ttf'), px)
        elif family.lower().startswith('consolas'):
            f = ImageFont.truetype(os.path.join(FONTS, 'consolab.ttf' if bold else 'consola.ttf'), px)
        else:
            f = ImageFont.truetype(os.path.join(FONTS, 'bahnschrift.ttf'), px)
            width = ('SemiCondensed' if 'SemiCondensed' in family else
                     'Condensed' if 'Condensed' in family else '')
            if 'Light' in family:
                weight = 'Light'
            else:
                weight = 'Bold' if bold else ''
            name = ' '.join(p for p in (weight, width) if p) or 'Regular'
            try:
                f.set_variation_by_name(name)
            except (OSError, ValueError):
                pass
        _font_cache[key] = f
    return f


_fallback, _glyph = {}, {}


def fallback(f):
    """Segoe UI Symbol at the same pixel size, for glyphs Bahnschrift/Consolas lack (ticks, arrows, boxes)."""
    fb = _fallback.get(f.size)
    if fb is None:
        fb = _fallback[f.size] = ImageFont.truetype(os.path.join(FONTS, 'seguisym.ttf'), f.size)
    return fb


def has_glyph(f, ch):
    key = (id(f), ch)
    v = _glyph.get(key)
    if v is None:
        v = _glyph[key] = ord(ch) < 0x2000 or bytes(f.getmask(ch)) != bytes(f.getmask(chr(0xFFFF)))
    return v


def runs(text, f):
    """Split text into (segment, font) runs, switching to the symbol font where needed."""
    out, cur, cur_f = [], '', f
    for ch in text:
        g = f if has_glyph(f, ch) else fallback(f)
        if g is not cur_f and cur:
            out.append((cur, cur_f))
            cur = ''
        cur_f = g
        cur += ch
    if cur:
        out.append((cur, cur_f))
    return out


def textlen(text, f):
    return sum(g.getlength(seg) for seg, g in runs(text, f))


def rgba(colour, alpha=255):
    if not colour:
        return None
    if isinstance(colour, tuple):
        return colour
    c = colour.lstrip('#')
    if len(c) == 8:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


def wrap(text, f, width):
    lines = []
    for para in text.split('\n'):
        if not width:
            lines.append(para)
            continue
        line = ''
        for word in para.split(' '):
            trial = (line + ' ' + word) if line else word
            if textlen(trial, f) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
            while textlen(line, f) > width and len(line) > 1:  # very long word: hard break
                cut = len(line)
                while cut > 1 and textlen(line[:cut], f) > width:
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line)
    return lines


def paste(img, layer, x, y):
    """alpha_composite that clips to the destination."""
    sx, sy = max(0, -x), max(0, -y)
    w = min(layer.width - sx, img.width - max(0, x))
    h = min(layer.height - sy, img.height - max(0, y))
    if w > 0 and h > 0:
        img.alpha_composite(layer.crop((sx, sy, sx + w, sy + h)), (max(0, x), max(0, y)))


class PilCanvas:
    def __init__(self):
        self.items = []  # dicts in z-order
        self.next_id = 1
        self.windows = {}

    # ---- tkinter.Canvas subset ----------------------------------------------------------------
    def delete(self, what='all'):
        if what == 'all':
            self.items, self.windows = [], {}
        else:
            self.items = [i for i in self.items if i['id'] != what and what not in i['tags']]

    def config(self, **kw):
        pass

    configure = config

    def _add(self, kind, coords, kw, bbox):
        tags = kw.get('tags', ())
        tags = (tags,) if isinstance(tags, str) else tuple(tags)
        item = dict(id=self.next_id, kind=kind, coords=list(coords), kw=kw, bbox=list(bbox), tags=tags)
        self.next_id += 1
        self.items.append(item)
        return item['id']

    def create_text(self, x, y, text='', anchor='center', fill='#ffffff', font=('Bahnschrift', 10), width=0, **kw):
        f = globals()['font'](font)
        lines = wrap(str(text), f, width)
        asc, desc = f.getmetrics()
        lh = asc + desc
        w = max((textlen(l, f) for l in lines), default=0)
        h = lh * len(lines)
        ax = {'w': 0, 'nw': 0, 'sw': 0, 'n': 0.5, 'center': 0.5, 's': 0.5, 'e': 1, 'ne': 1, 'se': 1}[anchor]
        ay = {'n': 0, 'nw': 0, 'ne': 0, 'w': 0.5, 'center': 0.5, 'e': 0.5, 's': 1, 'sw': 1, 'se': 1}[anchor]
        x0, y0 = x - w * ax, y - h * ay
        kw.update(text=text, fill=fill, font=f, lines=lines, lh=lh, just=ax)
        return self._add('text', (x0, y0), kw, (int(x0), int(y0), int(x0 + w + 0.999), int(y0 + h)))

    def create_line(self, *c, **kw):
        xs, ys = c[0::2], c[1::2]
        return self._add('line', c, kw, (min(xs), min(ys), max(xs), max(ys)))

    def create_rectangle(self, x0, y0, x1, y1, **kw):
        return self._add('rect', (x0, y0, x1, y1), kw, (x0, y0, x1, y1))

    def create_polygon(self, *c, **kw):
        xs, ys = c[0::2], c[1::2]
        return self._add('poly', c, kw, (min(xs), min(ys), max(xs), max(ys)))

    def create_oval(self, x0, y0, x1, y1, **kw):
        return self._add('oval', (x0, y0, x1, y1), kw, (x0, y0, x1, y1))

    def create_arc(self, x0, y0, x1, y1, **kw):
        return self._add('arc', (x0, y0, x1, y1), kw, (x0, y0, x1, y1))

    def create_window(self, x, y, anchor='nw', window=None, width=0, height=0, **kw):
        iid = self._add('window', (x, y), kw, (x, y, x + width, y + height))
        self.windows[iid] = (int(x), int(y), int(width), int(height))
        return iid

    def bbox(self, what):
        boxes = [i['bbox'] for i in self.items if i['id'] == what or what in i['tags']]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    def move(self, what, dx, dy):
        for i in self.items:
            if i['id'] == what or what in i['tags']:
                i['coords'] = [v + (dx if k % 2 == 0 else dy) for k, v in enumerate(i['coords'])]
                b = i['bbox']
                i['bbox'] = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]
                if i['id'] in self.windows:
                    x, y, w, h = self.windows[i['id']]
                    self.windows[i['id']] = (x + dx, y + dy, w, h)

    def tag_lower(self, what):
        sel = [i for i in self.items if i['id'] == what or what in i['tags']]
        self.items = sel + [i for i in self.items if i not in sel]

    def tag_raise(self, what):
        sel = [i for i in self.items if i['id'] == what or what in i['tags']]
        self.items = [i for i in self.items if i not in sel] + sel

    def window_rect(self):
        return next(iter(self.windows.values()), None)

    # ---- rendering ----------------------------------------------------------------------------
    def render(self, W, H, background=None):
        img = background.copy() if background is not None else Image.new('RGBA', (W, H), (0, 0, 0, 0))
        for it in self.items:
            kind, c, kw = it['kind'], it['coords'], it['kw']
            if kind == 'text':
                f, fill = kw['font'], rgba(kw['fill'])
                if not fill:
                    continue
                b = it['bbox']
                lw, lh = max(1, b[2] - b[0] + 2), max(1, b[3] - b[1] + 2)
                layer = Image.new('RGBA', (lw, lh), (0, 0, 0, 0))
                d = ImageDraw.Draw(layer)
                asc = f.getmetrics()[0]
                for n, line in enumerate(kw['lines']):
                    lx = (lw - 2 - textlen(line, f)) * kw['just']
                    for seg, g in runs(line, f):
                        ty = n * kw['lh'] + asc - g.getmetrics()[0]
                        if g.size >= 14:  # shadows help body text on bright frames; tiny labels stay crisp
                            d.text((lx + 1, ty + 1), seg, font=g, fill=(0, 0, 0, min(200, fill[3])))
                        d.text((lx, ty), seg, font=g, fill=fill)
                        lx += g.getlength(seg)
                paste(img, layer, int(c[0]), int(c[1]))
                continue
            if kind == 'window':
                continue
            fill, outline = rgba(kw.get('fill')), rgba(kw.get('outline'))
            width = int(kw.get('width', 1) or 1)
            translucent = (fill and fill[3] < 255) or (outline and outline[3] < 255)
            layer = Image.new('RGBA', img.size, (0, 0, 0, 0)) if translucent else None
            d = ImageDraw.Draw(layer if translucent else img)
            if kind == 'line':
                col = rgba(kw.get('fill', '#ffffff'))
                if col:
                    d.line([(c[k], c[k + 1]) for k in range(0, len(c), 2)], fill=col, width=width)
            elif kind == 'rect':
                x0, y0, x1, y1 = c
                box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
                if kw.get('radius'):
                    d.rounded_rectangle(box, radius=kw['radius'], fill=fill, outline=outline, width=width if outline else 0)
                else:
                    d.rectangle(box, fill=fill, outline=outline, width=width if outline else 0)
            elif kind == 'poly':
                d.polygon([(c[k], c[k + 1]) for k in range(0, len(c), 2)], fill=fill, outline=outline)
                if outline and width > 1:
                    pts = [(c[k], c[k + 1]) for k in range(0, len(c), 2)]
                    d.line(pts + pts[:1], fill=outline, width=width)
            elif kind == 'oval':
                d.ellipse(c, fill=fill, outline=outline, width=width if outline else 0)
            elif kind == 'arc':
                start, extent = kw.get('start', 0), kw.get('extent', 90)
                d.pieslice(c, -(start + extent), -start, fill=fill, outline=outline, width=width)
            if translucent:
                img.alpha_composite(layer)
        return img


class LayeredWindow:
    """A click-through, topmost window that shows an RGBA image with per-pixel alpha."""

    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.withdraw()
        self.win.update_idletasks()
        self.hwnd = u32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        ex = u32.GetWindowLongW(self.hwnd, -20)
        u32.SetWindowLongW(self.hwnd, -20, ex | 0x80000 | 0x20 | 0x80 | 0x08000000 | 0x8)
        self.shown = False

    def show(self, img, x, y):
        if not self.shown:
            self.win.deiconify()
            self.shown = True
        u32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_TOPMOST, no move/size/activate
        update_layered(self.hwnd, img, x, y)

    def hide(self):
        if self.shown:
            self.win.withdraw()
            self.shown = False


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [('BlendOp', ctypes.c_ubyte), ('BlendFlags', ctypes.c_ubyte), ('SourceConstantAlpha', ctypes.c_ubyte),
                ('AlphaFormat', ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG), ('biPlanes', wt.WORD),
                ('biBitCount', wt.WORD), ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
                ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


g32.CreateDIBSection.restype = wt.HBITMAP
g32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
g32.CreateCompatibleDC.restype = wt.HDC
g32.CreateCompatibleDC.argtypes = [wt.HDC]
u32.GetDC.argtypes = [wt.HWND]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
u32.GetParent.restype = wt.HWND
u32.GetParent.argtypes = [wt.HWND]
u32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
g32.SelectObject.restype = wt.HGDIOBJ
g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g32.DeleteObject.argtypes = [wt.HGDIOBJ]
g32.DeleteDC.argtypes = [wt.HDC]
u32.GetDC.restype = wt.HDC
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                    ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]


def premultiply(img):
    r, g, b, a = img.split()
    from PIL import ImageChops
    return Image.merge('RGBA', [ImageChops.multiply(ch, a) for ch in (r, g, b)] + [a])


def update_layered(hwnd, img, x, y):
    w, h = img.size
    data = premultiply(img).tobytes('raw', 'BGRA')
    screen = u32.GetDC(None)
    mem = g32.CreateCompatibleDC(screen)
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hbmp = g32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, data, len(data))
    old = g32.SelectObject(mem, hbmp)
    blend = BLENDFUNCTION(0, 0, 255, 1)
    u32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wt.POINT(int(x), int(y))), ctypes.byref(wt.SIZE(w, h)), mem,
                            ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), 2)
    g32.SelectObject(mem, old)
    g32.DeleteObject(hbmp)
    g32.DeleteDC(mem)
    u32.ReleaseDC(None, screen)


_gradients = {}


def gradient(W, H, left=0.80, right=0.0, colour=(4, 10, 8), fade_from=0.45):
    """Translucent backdrop: solid-ish on the left, fading to clear on the right, softly faded at the top.
    Cached per size - it is the most expensive thing to build and never changes."""
    key = (W, H, left, right, colour, fade_from)
    if key not in _gradients:
        _gradients.clear()
        _gradients[key] = _gradient(W, H, left, right, colour, fade_from)
    return _gradients[key]


def _gradient(W, H, left, right, colour, fade_from):
    row = Image.new('L', (W, 1))
    px = row.load()
    for x in range(W):
        t = x / max(1, W - 1)
        a = left if t < fade_from else left + (right - left) * ((t - fade_from) / (1 - fade_from)) ** 1.4
        px[x, 0] = int(255 * max(0.0, min(1.0, a)))
    alpha = row.resize((W, H))
    if H > 0:
        col = Image.new('L', (1, H))
        cp = col.load()
        edge = max(1, int(H * 0.06))
        for y in range(H):
            cp[0, y] = int(255 * min(1.0, (y + 1) / edge))
        from PIL import ImageChops
        alpha = ImageChops.multiply(alpha, col.resize((W, H)))
    img = Image.new('RGBA', (W, H), colour + (0,))
    img.putalpha(alpha)
    return img


class InputBox:
    """Normal (focusable) window with an Entry, shown over the drawn input line while typing."""

    def __init__(self, root, on_submit, on_cancel, mask_prefixes=('/key', 'sk-ant')):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg='#13201a')
        self.entry = tk.Entry(self.win, bg='#13201a', fg='#ffffff', insertbackground='#8ff0a4', relief='flat',
                              font=('Segoe UI', 12), highlightthickness=0, borderwidth=4)
        self.entry.pack(fill='both', expand=True)
        self.entry.bind('<Return>', lambda e: on_submit(self.entry.get()))
        self.entry.bind('<Escape>', lambda e: on_cancel())
        self.force_mask = False
        self.entry.bind('<KeyRelease>', lambda e: self.entry.config(
            show='*' if self.force_mask or self.entry.get().lower().startswith(mask_prefixes) else ''))
        self.win.withdraw()

    def show(self, x, y, w, h):
        self.win.geometry('%dx%d+%d+%d' % (max(40, w), max(18, h), x, y))
        self.win.deiconify()
        self.win.lift()
        self.entry.config(show='*' if self.force_mask else '')

    def hide(self):
        self.entry.delete(0, 'end')
        self.force_mask = False
        self.win.withdraw()

    def hwnd(self):
        self.win.update_idletasks()
        return u32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
