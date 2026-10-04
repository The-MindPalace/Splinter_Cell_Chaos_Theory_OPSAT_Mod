"""Windows plumbing and text helpers for OPSAT's overlay.

LayeredWindow puts an RGBA image on screen with UpdateLayeredWindow (per-pixel alpha, click-through,
topmost). InputBox is a small normal window holding a text Entry, placed over the drawn input line.
The text helpers (glyph fallback to Segoe UI Symbol, measuring, wrapping) are shared with hud.py.
"""
import ctypes
import ctypes.wintypes as wt

import os
import tkinter as tk

from PIL import Image, ImageFont

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
        self.mask_prefixes, self.styled, self.placed = mask_prefixes, None, None
        self.entry.bind('<KeyRelease>', lambda e: self.mask())
        self.win.withdraw()

    def mask(self):
        """Dots for API keys: at the key prompt, or anything typed that starts like one (/key, sk-ant)."""
        show = '\u2022' if self.force_mask or self.entry.get().lower().startswith(self.mask_prefixes) else ''
        if self.entry.cget('show') != show:
            self.entry.config(show=show)

    def style(self, bg, fg, caret, font, masked):
        """Match the drawn input plate: colours, caret, font."""
        if masked:
            self.force_mask = True
        if (bg, fg, caret, font) != self.styled:
            self.styled = (bg, fg, caret, font)
            self.win.configure(bg=bg)
            self.entry.config(bg=bg, fg=fg, insertbackground=caret, insertwidth=2, font=font, borderwidth=2)

    def show(self, x, y, w, h):
        geo = '%dx%d+%d+%d' % (max(40, w), max(18, h), x, y)
        if geo != self.placed or not self.win.winfo_viewable():
            self.placed = geo
            self.win.geometry(geo)
            self.win.deiconify()
            self.win.lift()
        self.mask()

    def hide(self):
        self.entry.delete(0, 'end')
        self.force_mask, self.placed = False, None
        self.entry.config(show='')
        self.win.withdraw()

    def hwnd(self):
        self.win.update_idletasks()
        return u32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
