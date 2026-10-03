"""Splinter Cell: Chaos Theory - real cheat-state overlay.

Reads the game's own memory (read-only) and shows a badge only while the
cheat is actually active on Sam:

  GOD MODE   EPlayerController.bInvincible   (toggled by the Invincible command, F2)
  INVISIBLE  Pawn.bInvisible                 (toggled by the Invisible command, F3)

Nothing is written to the game. Badges are drawn in a click-through,
always-on-top window over the (borderless) game window, and only while the
game is the foreground window.

Runs in the background (started from the Windows Startup folder), waits for
the game however it is launched, and goes back to waiting when it closes.
Only one copy runs at a time.

Usage:  pythonw cheat_overlay.py           (normal)
        python  cheat_overlay.py --log     (also prints state changes)
"""
import ctypes, ctypes.wintypes as wt, math, struct, sys, time, tkinter as tk
from layered import PilCanvas, LayeredWindow, InputBox, gradient

EXE = 'splintercell3.exe'

# Offsets inside splintercell3.exe (Steam build) of the engine's global tables.
GNAMES_RVA = 0xA0DFC0    # TArray<FNameEntry*> FName::Names
GOBJECTS_RVA = 0xA12084  # TArray<UObject*>   UObject::GObjObjects
# UObject / UProperty field offsets in this engine build.
O_OUTER, O_NAME, O_CLASS, O_SUPER = 0x18, 0x20, 0x24, 0x28
P_OFFSET, P_BITMASK = 0x44, 0x54

# What OPSAT reads for RADAR (all read-only).
INTEL_PROPS = [('Actor', 'Location'), ('Pawn', 'Health'), ('EPawn', 'm_VisibilityConeAngle'),
               ('EPawn', 'm_VisibilityMaxDistance'), ('EAIController', 'LastGoalType'), ('EAIController', 'Pattern'),
               ('EPattern', 'CurrentEventType'), ('EPattern', 'Stress_ReactionStep'), ('ESensor', 'CurrentRotation'),
               ('ESensor', 'VisibilityConeAngle'), ('ESensor', 'VisibilityMaxDistance'),
               ('EchelonLevelInfo', 'AlarmStage')]
INTEL_ENUMS = ('GoalType', 'AIEventType', 'StressSteps')
OBJ_TYPES = {0: 'PRIMARY', 1: 'SECONDARY', 2: 'OPPORTUNITY', 3: 'FALLBACK', 4: 'BONUS'}
MARKER_PROPS = [('E3DMapSystem', 'MapObjectives'), ('E3DBeacon', 'InitialLocation')]
UU_PER_M = 100.0  # 1 unit = 1 cm in this engine (Sam crouched = 120 units, runs 400 units/s)


POLL_MS = 200
IDLE_POLL_MS = 2000  # while the game is not running
RESCAN_S = 6.0  # full object-table rescans are the costliest read; the AI roster rarely changes

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
u32 = ctypes.WinDLL('user32', use_last_error=True)
psapi = ctypes.WinDLL('psapi')
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
u32.GetForegroundWindow.restype = wt.HWND
u32.GetWindowLongW.restype = ctypes.c_long
u32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                ('szExeFile', ctypes.c_wchar * 260)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE


def find_pid():
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wt.HANDLE(-1).value):
        return None
    try:
        e = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == EXE:
                return e.th32ProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return None


class Mem:
    def __init__(self, pid):
        self.pid = pid
        self.h = k32.OpenProcess(0x0410, False, pid)  # VM_READ | QUERY_INFORMATION
        if not self.h:
            raise OSError('OpenProcess failed (%d)' % ctypes.get_last_error())

    def close(self):
        k32.CloseHandle(self.h)

    def alive(self):
        code = wt.DWORD()
        return k32.GetExitCodeProcess(self.h, ctypes.byref(code)) and code.value == 259  # STILL_ACTIVE

    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)) or n.value != size:
            return None
        return buf.raw

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack('<I', b)[0] if b else None

    def exe_base(self):
        mods = (wt.HMODULE * 1024)()
        need = wt.DWORD()
        psapi.EnumProcessModulesEx(self.h, mods, ctypes.sizeof(mods), ctypes.byref(need), 0x01)
        name = ctypes.create_unicode_buffer(260)
        for m in mods[:need.value // ctypes.sizeof(wt.HMODULE)]:
            psapi.GetModuleBaseNameW(self.h, wt.HMODULE(m), name, 260)
            if name.value.lower() == EXE:
                return m
        return None


class Game:
    """Resolves the cheat flags by name from the engine's object table."""

    def __init__(self, mem):
        self.m = mem
        base = mem.exe_base()
        if not base:
            raise RuntimeError('game module not loaded yet')
        hdr = mem.read(base + GNAMES_RVA, 8)
        self.names_ptr, self.names_num = struct.unpack('<II', hdr)
        self.objs_hdr = base + GOBJECTS_RVA
        self.name_cache = {}
        if self.name(0) != 'None':
            raise RuntimeError('engine tables not ready / unsupported game version')
        self._resolve()
        self.players = []
        self.last_scan = 0.0

    def name(self, i):
        s = self.name_cache.get(i)
        if s is None and 0 <= i < self.names_num:
            e = self.m.u32(self.names_ptr + i * 4)
            b = self.m.read(e + 12, 64) if e else None
            if b:
                s = b.split(b'\0')[0].decode('latin1')
                self.name_cache[i] = s
        return s

    def objects(self):
        data, num = struct.unpack('<II', self.m.read(self.objs_hdr, 8))
        raw = self.m.read(data, num * 4)
        return [o for o in struct.unpack('<%dI' % num, raw) if o] if raw else []

    def oname(self, o):
        n = self.m.u32(o + O_NAME) if o else None
        return self.name(n) if n is not None else None

    def _resolve(self):
        want = {('EPlayerController', 'bInvincible'), ('Pawn', 'bInvisible'), ('Controller', 'Pawn'),
                ('EMissionManager', 'Objectives')}
        want.add(('E3DMapSystem', 'MapBBs'))
        want |= set(MARKER_PROPS)
        want |= set(INTEL_PROPS)
        self.props, self.pc_class = {}, None
        self.classes_by_name, self.enums = {}, {}
        fields = {'sPSObjective': {}, 'st3DMapBlackBox': {}, 'st3DMapObjective': {}, 'st3DMapBeacon': {}}  # struct -> member -> (offset, size, bitmask)
        for o in self.objects():
            hdr = self.m.read(o + O_OUTER, 0x10)
            if not hdr:
                continue
            outer, _, nm, cls = struct.unpack('<IIII', hdr)
            n, cn = self.name(nm), self.oname(cls)
            if cn == 'Class' and n == 'EPlayerController':
                self.pc_class = o
            if cn == 'Class' and n in ('EAIController', 'ESensor', 'EchelonLevelInfo'):
                self.classes_by_name[n] = o
            elif cn == 'Enum' and n in INTEL_ENUMS:
                self.enums[n] = self._enum_names(o)
            elif cn and cn.endswith('Property'):
                on = self.oname(outer)
                mask = self.m.u32(o + P_BITMASK) if cn == 'BoolProperty' else 0
                if (on, n) in want:
                    self.props[(on, n)] = (self.m.u32(o + P_OFFSET), mask)
                elif on in fields:
                    fields[on][n] = (self.m.u32(o + P_OFFSET), struct.unpack('<H', self.m.read(o + 0x36, 2))[0], mask)
        core = want - set(INTEL_PROPS)
        if core - set(self.props) or not self.pc_class:
            raise RuntimeError('could not resolve cheat flags (%s)' % (core - set(self.props)))
        # Intel is optional: if any piece is missing the RADAR tab just says so.
        self.intel_ok = set(INTEL_PROPS) <= set(self.props) and len(self.classes_by_name) == 3
        self.pawn_off = self.props[('Controller', 'Pawn')][0]
        obj, bb = fields['sPSObjective'], fields['st3DMapBlackBox']
        self.obj_key_off, self.obj_id_off = obj['Key'][0], obj['ID'][0]
        self.obj_pkg_off, self.obj_status_off = obj['Package'][0], obj['ObjectiveStatus'][0]
        self.obj_size = max(off + size for off, size, _ in obj.values())
        self.obj_detail_off = obj['detailkey'][0]
        self.obj_type_off = obj['ObjectiveType'][0]
        self.bb_adj_off = bb['AdjacentRoomsIdx'][0] if 'AdjacentRoomsIdx' in bb else None
        self.room_graph = {}
        self.bb_name_off = bb['BBName'][0]
        self.bb_cur_off, self.bb_cur_mask = bb['bCurrent'][0], bb['bCurrent'][2]
        self.bb_vis_off, self.bb_vis_mask = bb['bVisited'][0], bb['bVisited'][2]
        self.visited_rooms = []
        self.bb_size = max(off + size for off, size, _ in bb.values())
        # Objective beacons from the game's own 3D map (optional: RADAR just omits them if missing).
        mo, mb = fields['st3DMapObjective'], fields['st3DMapBeacon']
        try:
            self.mo = dict(size=max(o + z for o, z, _ in mo.values()), id=mo['ObjID'][0], beacons=mo['Beacons'][0],
                           done=mo['bCompleted'][0], done_mask=mo['bCompleted'][2],
                           b_size=max(o + z for o, z, _ in mb.values()), b_id=mb['BeaconID'][0],
                           b_actors=mb['Beacons'][0], tp=bb['targetPos'][0], zones=bb['BBZoneInfos'][0])
            self.markers_ok = set(MARKER_PROPS) <= set(self.props)
        except (KeyError, ValueError):
            self.markers_ok = False
        self.map_xf, self.map_xf_src = None, None
        self.mission_mgrs, self.map_systems, self.level_name = [], [], None
        self.room = None
        self.room_names, self.room_names_src = [], None  # every room on the current map
        self.ai_ctrls, self.sensors, self.level_info = [], [], None

    def _enum_names(self, enum):
        raw = self.m.read(enum, 0x60) or b''
        for off in range(0x28, 0x58, 4):
            data, num = struct.unpack_from('<II', raw, off)
            if data and 0 < num < 256:
                ids = struct.unpack('<%dI' % num, self.m.read(data, num * 4))
                names = [self.name(i) for i in ids]
                if all(names) and len(set(names)) > 1:
                    return names
        return []

    def _is_a(self, cls, target):
        for _ in range(16):
            if not cls:
                return False
            if cls == target:
                return True
            cls = self.m.u32(cls + O_SUPER)
        return False

    def _state_name(self, obj):
        sf = self.m.u32(obj + 0xC)  # UObject::StateFrame -> FStateFrame { Node, StateNode, ... }
        return self.oname(self.m.u32(sf + 4)) if sf else None

    def _actor_pose(self, a):
        raw = self.m.read(a + self.props[('Actor', 'Location')][0], 24)
        if not raw:
            return None
        x, y, z, pitch, yaw, roll = struct.unpack('<3f3i', raw)
        return (x, y, z), yaw & 0xFFFF

    def _f32(self, addr):
        b = self.m.read(addr, 4)
        return struct.unpack('<f', b)[0] if b else 0.0

    def _byte(self, addr):
        b = self.m.read(addr, 1)
        return b[0] if b else 0

    def intel(self):
        """Snapshot of everything OPSAT can know: Sam, guards, sensors, alarm stage."""
        if not self.intel_ok or not self.players:
            return None
        P = lambda c, n: self.props[(c, n)][0]
        sam = self.m.u32(self.players[0] + self.pawn_off)
        sam_pose = self._actor_pose(sam) if sam else None
        if not sam_pose:
            return None
        # Orient everything to the camera (the player controller's rotation), not Sam's body,
        # so "12 o'clock" is straight ahead on screen.
        cam = self._actor_pose(self.players[0])
        if cam:
            sam_pose = (sam_pose[0], cam[1])

        def enum(e, i):
            names = self.enums.get(e) or []
            return names[i] if i < len(names) else str(i)

        guards = []
        for c in self.ai_ctrls:
            pawn = self.m.u32(c + self.pawn_off)
            if not pawn or pawn == sam:
                continue
            pose = self._actor_pose(pawn)
            if not pose or not any(pose[0]):
                continue
            pat = self.m.u32(c + P('EAIController', 'Pattern'))
            guards.append({
                'name': self.oname(self.m.u32(pawn + O_CLASS)) or '?',
                'loc': pose[0], 'yaw': pose[1],
                'health': self.m.u32(pawn + P('Pawn', 'Health')) or 0,
                'state': self._state_name(c) or '',
                'goal': enum('GoalType', self._byte(c + P('EAIController', 'LastGoalType'))),
                'event': enum('AIEventType', self._byte(pat + P('EPattern', 'CurrentEventType'))) if pat else 'AI_NONE',
                'stress': enum('StressSteps', self._byte(pat + P('EPattern', 'Stress_ReactionStep'))) if pat else 'S_Casual',
                'cone': self._f32(pawn + P('EPawn', 'm_VisibilityConeAngle')),
                'range': self._f32(pawn + P('EPawn', 'm_VisibilityMaxDistance')),
            })
        sensors = []
        for snr in self.sensors:
            pose = self._actor_pose(snr)
            if not pose or not any(pose[0]):
                continue
            rot = self.m.read(snr + P('ESensor', 'CurrentRotation'), 12)
            yaw = struct.unpack('<3i', rot)[1] if rot else pose[1]
            sensors.append({
                'name': self.oname(self.m.u32(snr + O_CLASS)) or '?',
                'loc': pose[0], 'yaw': yaw & 0xFFFF,
                'state': self._state_name(snr) or '',
                'cone': self._f32(snr + P('ESensor', 'VisibilityConeAngle')),
                'range': self._f32(snr + P('ESensor', 'VisibilityMaxDistance')),
            })
        alarm = self.m.u32(self.level_info + P('EchelonLevelInfo', 'AlarmStage')) if self.level_info else None
        return {'sam': sam_pose, 'guards': guards, 'sensors': sensors, 'alarm': alarm,
                'objectives': self.objective_markers()}

    def _is_pc(self, cls):
        for _ in range(16):
            if not cls:
                return False
            if cls == self.pc_class:
                return True
            cls = self.m.u32(cls + O_SUPER)
        return False

    def _scan_players(self):
        """Refreshes player controllers, the mission manager and the current level name."""
        found, mgrs, maps, level = [], [], [], None
        ais, sens, linfo = [], [], None
        classes = {}  # class object -> (name, is player controller), per scan
        for o in self.objects():
            cls = self.m.u32(o + O_CLASS)
            if not cls:
                continue
            info = classes.get(cls)
            if info is None:
                cn = self.oname(cls)
                kind = None
                if cn != 'Class' and self.intel_ok:
                    for k in ('EAIController', 'ESensor', 'EchelonLevelInfo'):
                        if self._is_a(cls, self.classes_by_name[k]):
                            kind = k
                            break
                info = classes[cls] = (cn, cn != 'Class' and self._is_pc(cls), kind)
            cn, is_pc, kind = info
            if kind == 'EAIController':
                ais.append(o)
            elif kind == 'ESensor':
                sens.append(o)
            elif kind == 'EchelonLevelInfo':
                linfo = o
            if cn == 'EMissionManager':
                mgrs.append(o)
            elif cn == 'E3DMapSystem':
                maps.append(o)
            elif cn == 'Level' and self.oname(o) == 'MyLevel':
                level = self.oname(self.m.u32(o + O_OUTER))
            elif is_pc:
                found.append(o)
        if level != self.level_name:
            self.room = None
        self.players, self.mission_mgrs, self.map_systems, self.level_name = found, mgrs, maps, level
        self.ai_ctrls, self.sensors, self.level_info = ais, sens, linfo
        self.last_scan = time.monotonic()

    def _vec(self, addr):
        b = self.m.read(addr, 12)
        return struct.unpack('<3f', b) if b else None

    def _map_transform(self, ms):
        """Map-model space -> world space, fitted from the 3D map's rooms.

        Every room has a map-space anchor (targetPos) and world-space zone actors (BBZoneInfos);
        a 2D similarity (scale + rotation + offset) plus a linear height fit maps one to the other."""
        hdr = self.m.read(ms + self.props[('E3DMapSystem', 'MapBBs')][0], 8)
        if not hdr:
            return None
        data, num = struct.unpack('<II', hdr)
        if (ms, data, num) == self.map_xf_src:
            return self.map_xf
        loc_off = self.props[('Actor', 'Location')][0]
        pairs, self.room_world = [], {}
        for i in range(min(num, 128)):
            b = data + i * self.bb_size
            tp = self._vec(b + self.mo['tp'])
            zh = self.m.read(b + self.mo['zones'], 8)
            if not tp or not zh:
                continue
            zd, zn = struct.unpack('<II', zh)
            pts = [self._vec(z + loc_off) for z in struct.unpack('<%dI' % min(zn, 16), self.m.read(zd, 4 * min(zn, 16)) or b'')
                   if z] if 0 < zn else []
            pts = [p for p in pts if p]
            if pts:
                centre = tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))
                pairs.append((tp, centre))
                self.room_world[self._fstring(b + self.bb_name_off)] = centre
        # Room graph (AdjacentRoomsIdx) and each room's map-space anchor, for routes to objectives.
        graph, anchors = {}, {}
        for i in range(min(num, 128)):
            b = data + i * self.bb_size
            name = self._fstring(b + self.bb_name_off)
            tp = self._vec(b + self.mo['tp'])
            if tp:
                anchors[name] = tp
            adj = []
            if self.bb_adj_off is not None:
                ah = self.m.read(b + self.bb_adj_off, 8)
                if ah:
                    ad, an = struct.unpack('<II', ah)
                    if 0 < an < 32:
                        adj = list(struct.unpack('<%di' % an, self.m.read(ad, 4 * an) or b'\0' * 4 * an))
            graph[name] = adj
        names = list(graph)
        self.room_graph = {n: [names[j] for j in adj if 0 <= j < len(names)] for n, adj in graph.items()}
        self.room_anchor = anchors
        xf = None
        if len(pairs) >= 3:
            n = len(pairs)
            ma = [sum(a[k] for a, _ in pairs) / n for k in range(3)]
            mw = [sum(w[k] for _, w in pairs) / n for k in range(3)]
            saa = sum((a[0] - ma[0]) ** 2 + (a[1] - ma[1]) ** 2 for a, _ in pairs)
            sc = sum((a[0] - ma[0]) * (w[0] - mw[0]) + (a[1] - ma[1]) * (w[1] - mw[1]) for a, w in pairs)
            ss = sum((a[0] - ma[0]) * (w[1] - mw[1]) - (a[1] - ma[1]) * (w[0] - mw[0]) for a, w in pairs)
            szz = sum((a[2] - ma[2]) ** 2 for a, _ in pairs)
            kz = sum((a[2] - ma[2]) * (w[2] - mw[2]) for a, w in pairs) / szz if szz else 0.0
            if saa:
                c, s_ = sc / saa, ss / saa

                def xf(p):
                    dx, dy = p[0] - ma[0], p[1] - ma[1]
                    return (mw[0] + c * dx - s_ * dy, mw[1] + s_ * dx + c * dy, mw[2] + kz * (p[2] - ma[2]))
        self.map_xf, self.map_xf_src = xf, (ms, data, num)
        return xf

    def room_at(self, map_pos):
        """3D-map room whose anchor is closest to a map-space point."""
        best, bd = None, None
        for name, a in getattr(self, 'room_anchor', {}).items():
            d = (a[0] - map_pos[0]) ** 2 + (a[1] - map_pos[1]) ** 2 + (2 * (a[2] - map_pos[2])) ** 2
            if bd is None or d < bd:
                best, bd = name, d
        return best

    def route(self, start, goal):
        """Shortest room-to-room path on the 3D map's adjacency graph, or None."""
        g = self.room_graph
        if not start or not goal or start not in g:
            return None
        prev, todo = {start: None}, [start]
        while todo:
            cur = todo.pop(0)
            if cur == goal:
                path = []
                while cur:
                    path.append(cur)
                    cur = prev[cur]
                return path[::-1]
            for n in g.get(cur, []):
                if n not in prev:
                    prev[n] = cur
                    todo.append(n)
        return None

    def objective_markers(self):
        """[{name, done, loc}] - the objective beacons the game's 3D map shows, in world space."""
        if not self.markers_ok:
            return []
        mo, out = self.mo, []
        off = self.props[('E3DMapSystem', 'MapObjectives')][0]
        init_off = self.props[('E3DBeacon', 'InitialLocation')][0]
        for ms in self.map_systems:
            hdr = self.m.read(ms + off, 8)
            xf = self._map_transform(ms) if hdr else None
            if not xf:
                continue
            data, num = struct.unpack('<II', hdr)
            for i in range(min(num, 64)):
                b = data + i * mo['size']
                oid = self._fstring(b + mo['id'])
                done = bool((self.m.u32(b + mo['done']) or 0) & mo['done_mask'])
                bh = self.m.read(b + mo['beacons'], 8)
                bd, bn = struct.unpack('<II', bh) if bh else (0, 0)
                for j in range(min(bn, 16)):
                    bb = bd + j * mo['b_size']
                    bid = self._fstring(bb + mo['b_id'])
                    ah = self.m.read(bb + mo['b_actors'], 8)
                    ad, an = struct.unpack('<II', ah) if ah else (0, 0)
                    for k in range(min(an, 4)):
                        a = self.m.u32(ad + 4 * k)
                        p = self._vec(a + init_off) if a else None
                        if p and any(p):
                            room, loc = self.room_at(p), xf(p)
                            centre = self.room_world.get(room)
                            # Some markers sit oddly in the map model (the Bank vault): if one lands far from
                            # its own room, steer to the room instead.
                            if centre and math.hypot(loc[0] - centre[0], loc[1] - centre[1]) > 15 * UU_PER_M:
                                loc = centre
                            out.append({'name': bid or oid, 'objective': oid, 'done': done, 'loc': loc,
                                        'room': room})
        return out

    def _fstring(self, addr):
        hdr = self.m.read(addr, 8)
        if not hdr:
            return ''
        data, num = struct.unpack('<II', hdr)
        if not data or not 0 < num < 256:
            return ''
        raw = self.m.read(data, num * 2)
        return raw.decode('utf-16-le', 'replace').rstrip('\0') if raw else ''

    def _read_objectives(self, mgr):
        hdr = self.m.read(mgr + self.props[('EMissionManager', 'Objectives')][0], 8)
        if not hdr:
            return [], None
        data, num = struct.unpack('<II', hdr)
        out, pkg = [], None
        for i in range(min(num, 64)):
            base = data + i * self.obj_size
            st = self.m.read(base + self.obj_status_off, 1)
            ty = self.m.read(base + self.obj_type_off, 1)
            out.append((self._fstring(base + self.obj_key_off), st[0] if st else 0, self._fstring(base + self.obj_id_off),
                        self._fstring(base + self.obj_detail_off), ty[0] if ty else 0))
            pkg = pkg or self._fstring(base + self.obj_pkg_off)
        return out, pkg

    def _current_room(self):
        """Name of the 3D-map room Sam is in (keeps the last one while between rooms)."""
        off = self.props[('E3DMapSystem', 'MapBBs')][0]
        for ms in self.map_systems:
            hdr = self.m.read(ms + off, 8)
            if not hdr:
                continue
            data, num = struct.unpack('<II', hdr)
            raw = self.m.read(data, num * self.bb_size) if 0 < num < 128 else None
            if not raw:
                continue
            if self.room_names_src != (ms, data, num):
                self.room_names_src = (ms, data, num)
                self.room_names = [self._fstring(data + i * self.bb_size + self.bb_name_off) for i in range(num)]
            self.visited_rooms = [self.room_names[i] for i in range(min(num, len(self.room_names)))
                                  if struct.unpack_from('<I', raw, i * self.bb_size + self.bb_vis_off)[0] & self.bb_vis_mask]
            for i in range(num):
                flags = struct.unpack_from('<I', raw, i * self.bb_size + self.bb_cur_off)[0]
                if flags & self.bb_cur_mask:
                    self.room = self._fstring(data + i * self.bb_size + self.bb_name_off)
                    return self.room
        return self.room

    def cheats(self):
        """(god mode, invisible) read from the player controller and pawn."""
        g_off, g_mask = self.props[('EPlayerController', 'bInvincible')]
        i_off, i_mask = self.props[('Pawn', 'bInvisible')]
        god = invis = False
        for pc in self.players:
            v = self.m.u32(pc + g_off)
            god |= bool(v and v & g_mask)
            pawn = self.m.u32(pc + self.pawn_off)
            v = self.m.u32(pawn + i_off) if pawn else None
            invis |= bool(v and v & i_mask)
        return god, invis

    def mission_state(self):
        """(mission id, current room, objectives [(key, status, id)]).

        The mission id comes from the objectives' localization package (e.g.
        'Localization\\P_01_Lighthouse' -> '01_Lighthouse'), which survives loading a
        save; the level package name is only a fallback."""
        if time.monotonic() - self.last_scan > RESCAN_S:
            self._scan_players()
        best, pkg = [], None
        for mgr in self.mission_mgrs:
            objs, p = self._read_objectives(mgr)
            if len(objs) > len(best):
                best, pkg = objs, p
        mission = None
        if pkg and '\\P_' in pkg:
            mission = pkg.split('\\P_', 1)[1]
        return mission or self.level_name, self._current_room(), best


def game_window(pid):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True
    u32.EnumWindows(cb, 0)
    return found[0] if found else None


def make_click_through(win):
    win.update_idletasks()
    hwnd = u32.GetParent(win.winfo_id()) or win.winfo_id()
    ex = u32.GetWindowLongW(hwnd, -20)
    u32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x80 | 0x08000000)  # layered, transparent, toolwindow, noactivate


VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_INSERT = 0x25, 0x26, 0x27, 0x28, 0x2D


def clean_reply(text):
    """Strip markdown the model sometimes adds and collapse blank lines - chat lines stay compact."""
    import re
    text = re.sub(r'\*\*|__|`', '', text)
    text = re.sub(r'</?telemetry>', '', text)
    text = re.sub(r'(?m)^\s*#+\s*', '', text)
    return re.sub(r'\n\s*\n+', '\n', text).strip()


def dock(hwnd):
    """Bottom-left dock, MMO style: (x, chat_top, chat_bottom, width, panel_max_h, scale) in screen pixels."""
    r = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    W, H = r.right - r.left, r.bottom - r.top
    sc = max(0.8, H / 1080)
    w = max(int(420 * sc), int(W * 0.30))
    x = r.left + int(H * 0.012)
    bottom = r.bottom - int(H * 0.014)
    chat_h = int(H * 0.28)
    top = bottom - chat_h
    return x, top, bottom, w, top - r.top - int(H * 0.06), sc


def force_foreground(hwnd):
    """SetForegroundWindow from a background process (the usual Alt-key unlock)."""
    u32.keybd_event(0x12, 0, 0, 0)
    u32.SetForegroundWindow(hwnd)
    u32.keybd_event(0x12, 0, 2, 0)
u32.GetAsyncKeyState.restype = ctypes.c_short


RANGE_M = 20.0  # radar radius
CONE_M = 5.0  # drawn length of view cones
AMBER, RED, GREY = '#ffb347', '#ff5a4f', '#556052'
ACTIVITY = {'GOAL_None': 'idle', 'GOAL_Wait': 'waiting', 'GOAL_Patrol': 'patrolling', 'GOAL_Follow': 'following',
            'GOAL_Anim': 'busy', 'GOAL_MoveTo': 'moving', 'GOAL_Shoot': 'SHOOTING', 'GOAL_MoveAndShoot': 'ADVANCING',
            'GOAL_ThrowGrenade': 'GRENADE', 'GOAL_UseMachinery': 'at a machine', 'GOAL_PlaceWallMine': 'placing mine'}
EVENTS = {'AI_AUDIO': 'heard something', 'AI_VISUAL': 'saw something', 'AI_SEE_ENEMY_INVISIBLE': 'lost sight of you',
          'AI_HEAR_QUIET_FOOTSTEP': 'heard footsteps', 'AI_SEE_ENEMY_SHADOW': 'saw a shadow',
          'AI_HEAR_LITTLE_VOICE': 'heard a voice', 'AI_HEAR_DISFUNCTION': 'heard a malfunction',
          'AI_CHANGED_DOOR_OPENED': 'noticed an open door', 'AI_HEAR_ALARM': 'heard the alarm',
          'AI_CHANGED_DEVICE_OFF': 'noticed a light off', 'AI_CHANGED_DEVICE_ON': 'noticed a light on',
          'AI_CHANGED_STICKY_CAMERA': 'spotted a sticky cam', 'AI_JAMMED_CAMERA': 'noticed a jammed camera',
          'AI_SEE_ENEMY_BARELY': 'glimpsed you', 'AI_CHANGED_BROKEN_GLASS': 'noticed broken glass',
          'AI_HEAR_DOOR': 'heard a door', 'AI_HEAR_OBJECT': 'heard an object', 'AI_CHANGED_OBJECT': 'noticed a moved object',
          'AI_BARK_POKE_DEAD_RECENT': 'found a body', 'AI_BARK_POKE_DEAD_UNCONSCIOUS': 'found a body',
          'AI_CHANGED_DOOR_BROKEN': 'noticed a broken door', 'AI_SEE_ENEMY_MIRROR': 'saw you in a mirror'}


def guard_mood(g):
    """(label, colour) from the guard's own AI state machine."""
    st = g['state']
    if st == 's_Dead' or g['health'] <= 0:
        return 'DEAD', GREY
    if st in ('s_Unconscious', 's_Stunned', 's_Groggy', 's_Grabbed', 's_Carried'):
        return 'OUT', GREY
    if g['goal'] in ('GOAL_Shoot', 'GOAL_MoveAndShoot', 'GOAL_ThrowGrenade') or g['stress'].startswith('S_HighStress'):
        return 'ALERT', RED
    if g['stress'] in ('S_NormalA', 'S_NormalB', 'S_Repetition'):
        return 'SUSPICIOUS', AMBER
    return 'CALM', '#9fe08a'


def pretty(name):
    """'AuthorizeA' -> 'AUTHORIZE A', 'RoofBeacon' -> 'ROOF'."""
    import re
    name = re.sub(r'Beacon$', '', name or '?') or name
    return re.sub(r'(?<=[a-z])(?=[A-Z0-9])', ' ', name).upper()


def merge_markers(obs):
    """Pending beacons, with ones at the same spot (within 3 m) merged: 'INFO / MONEY'."""
    out = []
    for ob in obs:
        if ob['done']:
            continue
        for o in out:
            if math.dist(o['loc'], ob['loc']) < 3 * UU_PER_M:
                o['label'] += ' / ' + pretty(ob['name'])
                break
        else:
            out.append(dict(ob, label=pretty(ob['name'])))
    return out


def relative(sam, loc):
    """(distance m, bearing deg clockwise from Sam's facing, height diff m)."""
    (sx, sy, sz), syaw = sam
    dx, dy, dz = loc[0] - sx, loc[1] - sy, loc[2] - sz
    a = math.atan2(dy, dx) - syaw / 65536 * 2 * math.pi
    return math.hypot(dx, dy) / UU_PER_M, math.degrees(a) % 360, dz / UU_PER_M


def facing(src_loc, src_yaw, cone, rng, dst_loc):
    """Is dst inside src's view cone (geometry only - walls and light are not checked)?"""
    dx, dy = dst_loc[0] - src_loc[0], dst_loc[1] - src_loc[1]
    dist = math.hypot(dx, dy)
    if dist > rng or dist < 1:
        return False
    diff = (math.degrees(math.atan2(dy, dx)) - src_yaw / 65536 * 360 + 180) % 360 - 180
    return abs(diff) <= cone / 2


class Opsat:
    """OPSAT V1.2: one panel, bottom-left, two tabs.

    TERMINAL  live next moves + DVORAK.     RADAR  objectives on top, the radar below.
    Up arrow slides it up (like a phone), Down slides it away, Left/Right switch tabs, Insert asks DVORAK."""
    # Palette
    INK = '#eef4ef'        # primary text
    SOFT = '#b9c7bd'       # body text
    MUTE = '#76877c'       # labels, hints
    FAINT = '#ffffff14'    # hairlines
    CARD = '#ffffff0c'     # card fill
    GREEN = '#8ff0a4'      # accent
    BLUE = '#86cdfa'       # objectives / navigation
    UI = 'Bahnschrift SemiCondensed'   # headings, labels, numbers
    BODY = 'Segoe UI'                  # anything you read: replies, ways, objectives
    MONO = 'Consolas'
    TABS = ('TERMINAL', 'RADAR')
    SLIDE_S = 0.24
    HERE = __import__('os').path.dirname(__import__('os').path.abspath(__file__))

    def __init__(self, root):
        import json, os
        load = lambda name: json.load(open(os.path.join(self.HERE, name), encoding='utf-8'))
        self.hints = {k.lower(): v for k, v in load('hints.json')['missions'].items()}
        self.titles = {k.lower(): v for k, v in load('objectives.json').items()}
        self.rules = {k.lower(): v for k, v in load('rules.json')['missions'].items()}
        self.walk = {k.lower(): v for k, v in load('walkthrough.json')['missions'].items()}
        self.events = __import__('collections').deque(maxlen=30)  # (time, text) things Sam triggered
        self.lw = LayeredWindow(root)
        self.canvas = PilCanvas()
        self.input = InputBox(root, lambda text: self.submit(), lambda: self.stop_typing())
        self.entry = self.input.entry
        self.open = self.shown = False
        self.tab = 0
        self.mission_id = self.room = None
        self.objs, self.room_names = [], []
        self.intel = None
        self.cheats = (False, False)
        self.chat = []          # [who, text]: who in fisher / dvorak / sys / event
        self.typing = self.key_mode = False
        self.dvorak_online = self.dvorak_busy = False
        self.bond_info = None
        self.on_submit = None
        self.game = self.game_hwnd = None
        self.anchor, self.size, self.scale = None, (560, 640), 1.0
        self.img = None
        self.slide = (0.0, 0.0, 0.0)  # (start time, from, to) of the open/close animation
        self.closing = False

    # --- state ---------------------------------------------------------------
    def mission(self):
        m = self.hints.get((self.mission_id or '').lower())
        if m or not self.room_names:
            return m
        best = max(self.hints.values(), key=lambda v: len(set(v['rooms']) & set(self.room_names)))
        return best if len(set(best['rooms']) & set(self.room_names)) >= 3 else None

    def update_state(self, mission_id, room, objs):
        """Returns True if anything visible changed."""
        if (mission_id, room, objs) == (self.mission_id, self.room, self.objs):
            return False
        self.mission_id, self.room, self.objs = mission_id, room, objs
        return True

    def section(self):
        return next((v for k, v in self.titles.items() if k.startswith('p_' + (self.mission_id or '').lower())), {})

    def objective_rows(self):
        """[(title, status, type)]: status 0 pending / 1 done / 2 cancelled; type 0 primary .. 4 bonus."""
        sect = self.section()
        return [(sect[o[0]], o[1], o[4]) for o in self.objs if sect.get(o[0])]

    def visible_markers(self):
        """Pending objective beacons (beacon ObjID == objective ID); extraction only once primaries are done."""
        rows = self.objective_rows()
        primaries_left = not rows or any(st == 0 and ty in (0, 3) for _, st, ty in rows)
        ids = {o[2]: (o[1], o[4]) for o in self.objs}
        out = []
        for ob in merge_markers((self.intel or {}).get('objectives') or []):
            status, otype = ids.get(ob['objective'], (0, 0))
            if status == 0 and not (ob['label'].startswith('EXTRACT') and primaries_left):
                ob['primary'] = otype in (0, 3)
                out.append(ob)
        return out

    def next_objective(self):
        """Story order (the game lists objectives as they unlock), nearest beacon breaks ties."""
        marks = self.visible_markers() if self.intel and self.objs else []  # nothing to steer to in the briefing
        order = {o[2]: i for i, o in enumerate(self.objs)}
        sam = (self.intel or {}).get('sam')
        return min(marks, key=lambda ob: (not ob['primary'], order.get(ob['objective'], 99),
                                          relative(sam, ob['loc'])[0])) if marks else None

    def route_to(self, ob):
        return self.game.route(self.room, ob.get('room')) if self.game and ob else None

    def room_ways(self, room):
        note = ((self.mission() or {}).get('rooms') or {}).get(room or '')
        return note['ways'] if note else []

    def walkthrough(self):
        return self.walk.get((self.mission_id or '').lower())

    def note_for(self, nxt=None):
        """Walkthrough entry {where, ways, watch} for the objective behind a map marker, or for the first pending
        primary when there is no marker (offline data, walkthrough.json)."""
        notes = (self.walkthrough() or {}).get('objectives', {})
        if nxt:
            keys = [o[0] for o in self.objs if o[2] == nxt['objective']]
        else:
            keys = [o[0] for o in self.objs if o[1] == 0 and o[4] in (0, 3)]
        return next((notes[k] for k in keys if k in notes), None)

    def ways_for(self, nxt=None):
        return (self.note_for(nxt) or {}).get('ways', [])

    def numbered(self, c, items, x, y, width, px, font, colour, gap=4):
        """Numbered list with wrapped lines indented under the text, not the number."""
        for i, text in enumerate(items, 1):
            c.create_text(x, y, anchor='nw', text=str(i), fill=self.MUTE, font=font)
            t = c.create_text(x + px(20), y, anchor='nw', text=text, fill=colour, width=width - px(20), font=font)
            y = c.bbox(t)[3] + px(gap)
        return y

    def next_moves(self):
        """(where/progress line, next line, moves, fail rule) - the live 'what now', no AI involved."""
        must = [r for r in self.objective_rows() if r[2] in (0, 3)]
        done = sum(1 for r in must if r[1] == 1)
        now = '%s' % (self.room or 'unknown').replace('_', ' ')
        nxt = self.next_objective()
        if nxt:
            path = self.route_to(nxt) or []
            via = ('  via ' + ' › '.join(r.replace('_', ' ') for r in path[1:3])) if len(path) > 1 else ''
            nxt_line = '%s   %.0fm%s' % (nxt['label'], relative(self.intel['sam'], nxt['loc'])[0], via)
        else:  # no map marker: name the first pending primary instead
            pending = [r[0] for r in must if r[1] == 0]
            nxt_line = 'Get to extraction' if must and done == len(must) else (pending[0] if pending else '')
        moves = self.ways_for(nxt) or (self.room_ways(nxt.get('room')) if nxt else [])[:3] or self.room_ways(self.room)[:3]
        fail =self.rules.get((self.mission_id or '').lower(), {}).get('fail', [])
        return now, (done, len(must)), nxt_line, moves, (fail[0] if fail else None)

    # --- keys ----------------------------------------------------------------
    def show_panel(self):
        if not self.open or self.closing:
            self.open, self.closing = True, False
            self.slide = (time.monotonic(), 0.0, 1.0)
            if self.game_hwnd:  # place now, not on the next tick, so the slide starts on the key press
                self.place(self.game_hwnd, True)

    def hide_panel(self):
        if self.open and not self.closing:
            self.stop_typing()
            self.closing = True
            self.slide = (time.monotonic(), 1.0, 0.0)

    def step_tab(self, d):
        if self.open and not self.closing:
            self.tab = (self.tab + d) % len(self.TABS)
            self.render()

    def start_typing(self):
        if not self.open or self.TABS[self.tab] != 'TERMINAL' or self.typing:
            return
        self.typing = True
        self.input.force_mask = self.key_mode
        self.render()

    def stop_typing(self):
        if not self.typing:
            return
        self.typing = self.key_mode = False
        self.input.hide()
        if self.game_hwnd:
            force_foreground(self.game_hwnd)
        self.render()

    def submit(self):
        q = self.entry.get().strip()
        if self.key_mode and q:
            q = '/key ' + q  # pasted at the API KEY prompt: always a key, never a question
        self.stop_typing()
        if q and self.on_submit:
            self.on_submit(q)

    # --- placement + animation -----------------------------------------------
    def place(self, hwnd, fg):
        if not (self.open and hwnd and fg):
            if self.shown:
                self.lw.hide()
                self.input.hide()
                self.shown = False
            return
        x, chat_top, bottom, w, _, sc = dock(hwnd)
        size = (w, int((bottom - chat_top) / 0.28 * 0.66))
        if (x, bottom) != self.anchor or size != self.size or not self.shown:
            if not self.shown and not self.closing:
                self.slide = (time.monotonic(), 0.0, 1.0)  # first frame on screen: start the slide here
            self.anchor, self.size, self.scale, self.shown = (x, bottom), size, sc, True
            self.render()

    def progress(self):
        t0, a, b = self.slide
        t = min(1.0, (time.monotonic() - t0) / self.SLIDE_S)
        e = 1 - (1 - t) ** 3  # ease-out
        return a + (b - a) * e, t >= 1.0

    def animate(self):
        """Called every frame while sliding; returns True while the animation is still running."""
        p, finished = self.progress()
        if self.img is not None and self.shown:
            x, bottom = self.anchor
            self.lw.show(self.img, x, bottom - int(self.size[1] * p))
        if finished and self.closing:
            self.open = self.closing = False
            self.lw.hide()
            self.input.hide()
            self.shown = False
        return not finished

    # --- drawing -------------------------------------------------------------
    def render(self):
        if not (self.open and self.shown and self.anchor):
            return
        c = self.canvas
        c.delete('all')
        W, H = self.size
        px = lambda v: int(v * self.scale)
        pad = px(20)
        font = lambda size, bold=False: (self.UI, px(size), 'bold') if bold else (self.UI, px(size))
        self.body = lambda size, bold=False: (self.BODY, px(size), 'bold') if bold else (self.BODY, px(size))

        # Header: wordmark, then the tab switcher on the right.
        c.create_line(1, px(10), 1, H - px(10), fill=self.GREEN, width=2)
        t = c.create_text(pad, px(16), text='OPSAT', anchor='nw', fill=self.INK, font=font(15, True))
        v = c.create_text(c.bbox(t)[2] + px(6), px(20), text='V1.2', anchor='nw', fill=self.MUTE, font=font(8, True))
        x = c.bbox(v)[2] + px(16)
        for label, on, colour in (('GOD', self.cheats[0], self.GREEN), ('INVIS', self.cheats[1], self.BLUE)):
            t2 = c.create_text(x + px(8), px(19), text=label, anchor='nw', fill='#06100c' if on else self.MUTE,
                               font=font(8, True))
            x0, y0, x1, y1 = c.bbox(t2)
            c.create_rectangle(x0 - px(7), y0 - px(4), x1 + px(7), y1 + px(4), radius=px(8),
                               fill=colour if on else '', outline='' if on else '#ffffff26')
            c.tag_raise(t2)
            x = x1 + px(14)
        x = W - pad
        for i in reversed(range(len(self.TABS))):
            active = i == self.tab
            t = c.create_text(x - px(10), px(19), text=self.TABS[i], anchor='ne', fill=self.INK if active else self.MUTE,
                              font=font(9, True))
            x0, y0, x1, y1 = c.bbox(t)
            if active:
                c.create_rectangle(x0 - px(10), y0 - px(5), x1 + px(10), y1 + px(5), fill='#8ff0a426', outline='',
                                   radius=px(9))
                c.tag_raise(t)
            x = x0 - px(14)
        c.create_line(pad, px(48), W - pad, px(48), fill=self.FAINT)
        top, width = px(60), W - 2 * pad
        if self.TABS[self.tab] == 'TERMINAL':
            self.draw_terminal(c, top, pad, width, H, px, font)
        else:
            self.draw_radar(c, top, pad, width, H, px, font)
        hint = ('ENTER send     ESC cancel' if self.typing else
                '▲ ▼  open / close      ◀ ▶  tab      INS  ask DVORAK')
        c.create_text(pad, H - px(24), anchor='nw', fill=self.MUTE, font=font(8), text=hint)
        self.compose()

    def compose(self):
        W, H = self.size
        x, bottom = self.anchor
        self.img = self.canvas.render(W, H, gradient(W, H, left=0.90, right=0.62, colour=(4, 10, 8), fade_from=0.55))
        p, _ = self.progress()
        self.lw.show(self.img, x, bottom - int(H * p))
        rect = self.canvas.window_rect()
        if self.typing and rect:
            first = not self.input.win.winfo_viewable()
            self.input.show(x + rect[0], bottom - H + rect[1], rect[2], rect[3])
            if first:  # the input line just appeared: give it the keyboard
                force_foreground(self.input.hwnd())
                self.entry.focus_force()
        elif not self.typing:
            self.input.hide()

    def card(self, c, x, y, w, h, px, colour=None):
        c.create_rectangle(x, y, x + w, y + h, fill=colour or self.CARD, outline='', radius=px(8))

    def draw_terminal(self, c, top, pad, width, H, px, font):
        # NEXT MOVES card: where you are, progress, the next objective and what you can do there.
        if self.mission_id:
            now, (done, total), nxt_line, moves, warn = self.next_moves()
            ids = []
            y = top + px(12)
            t = c.create_text(pad + px(14), y, anchor='nw', text=now.upper(), fill=self.INK, font=font(13, True))
            ids.append(t)
            c.create_text(pad + width - px(14), y + px(2), anchor='ne', text='%d / %d PRIMARIES' % (done, total),
                          fill=self.GREEN if total and done == total else self.MUTE, font=font(9, True))
            y = c.bbox(t)[3] + px(4)
            if nxt_line:
                t = c.create_text(pad + px(14), y, anchor='nw', text='NEXT  ' + nxt_line, fill=self.BLUE,
                                  width=width - px(28), font=font(11, True))
                y = c.bbox(t)[3] + px(3)
            note = self.note_for(self.next_objective()) if nxt_line else None
            if note:
                t = c.create_text(pad + px(14), y, anchor='nw', text=note['where'], fill=self.MUTE,
                                  width=width - px(28), font=self.body(10))
                y = c.bbox(t)[3] + px(3)
            y += px(3)
            if moves:
                t = c.create_text(pad + px(14), y, anchor='nw', fill=self.MUTE, font=font(8, True), text='WAYS THROUGH')
                y = c.bbox(t)[3] + px(4)
            y = self.numbered(c, moves, pad + px(14), y, width - px(28), px, self.body(11), self.SOFT)
            if warn:
                t = c.create_text(pad + px(14), y + px(3), anchor='nw', text=warn, fill=RED, width=width - px(28),
                                  font=font(9, True))
                y = c.bbox(t)[3] + px(2)
            bottom_card = y + px(12)
            self.card(c, pad, top, width, bottom_card - top, px)
            c.tag_lower(c.items[-1]['id'])
            top = bottom_card + px(14)

        # DVORAK header.
        on = self.dvorak_online
        c.create_oval(pad, top + px(5), pad + px(7), top + px(12), fill=self.GREEN if on else RED, outline='')
        c.create_text(pad + px(14), top, anchor='nw', text='DVORAK', fill=self.INK, font=font(10, True))
        c.create_text(pad + px(66), top + px(1), anchor='nw', text='online' if on else 'offline - INS to add key',
                      fill=self.MUTE, font=font(9))
        if self.bond_info:
            name, lvl, total = self.bond_info
            c.create_text(pad + width, top + px(1), anchor='ne', text=name.title(), fill=self.MUTE, font=font(9))
        top += px(24)

        # Messages, newest at the bottom; older DVORAK replies collapse to one line.
        input_h = px(34)
        bottom = H - px(36) - input_h - px(8)
        last_dvorak = max((i for i, (w, _) in enumerate(self.chat) if w == 'dvorak'), default=-1)
        rows = []
        for i, (who, msg) in enumerate(self.chat):
            if who == 'dvorak':
                body = clean_reply(msg)
                if i != last_dvorak:
                    first = body.split('\n')[0]
                    rows.append(('DVORAK', self.MUTE, (first[:90] + ' …') if len(first) > 90 or '\n' in body else first,
                                 self.MUTE))
                    continue
                if self.dvorak_busy and i == len(self.chat) - 1:
                    body = (body + ' ▌') if body else 'thinking' + '.' * (int(time.monotonic() * 3) % 4)
                rows.append(('DVORAK', self.GREEN, body, self.INK))
            elif who == 'fisher':
                rows.append(('FISHER', self.BLUE, msg, self.SOFT))
            else:
                rows.append(('', None, msg, self.MUTE))
        y, placed = bottom, []
        for name, ncol, body, bcol in reversed(rows):
            items = []
            if name:
                items.append(c.create_text(pad, 0, anchor='nw', text=name, fill=ncol, font=font(8, True)))
            b = c.create_text(pad, px(15) if name else 0, anchor='nw', text=body, fill=bcol, width=width,
                              font=self.body(12) if name == 'DVORAK' and bcol == self.INK else self.body(10))
            items.append(b)
            h = c.bbox(b)[3]
            if y - h < top:
                for it in items:
                    c.delete(it)
                break
            y -= h + px(12)
            placed.append((items, y))
        for items, yy in placed:
            for it in items:
                c.move(it, 0, yy)

        # Input field.
        iy = H - px(36) - input_h
        self.card(c, pad, iy, width, input_h, px, '#ffffff10')
        if self.typing:
            c.create_text(pad + px(12), iy + px(9), anchor='nw', text='KEY' if self.key_mode else 'ASK',
                          fill=AMBER if self.key_mode else self.GREEN, font=font(9, True))
            c.create_window(pad + px(48), iy + px(4), anchor='nw', window=self.entry, width=width - px(56),
                            height=input_h - px(8))
        else:
            c.create_text(pad + px(12), iy + px(9), anchor='nw', fill=self.MUTE, font=font(10),
                          text='Ask DVORAK  ·  press INS' if on else 'Press INS and paste your Anthropic API key')

    def draw_radar(self, c, top, pad, width, H, px, font):
        intel = self.intel
        rows = self.objective_rows()
        must = [r for r in rows if r[2] in (0, 3)]
        extra = [r for r in rows if r[2] not in (0, 3) and r[1] == 0]
        # Objectives: primaries with live ticks.
        c.create_text(pad, top, anchor='nw', text='OBJECTIVES', fill=self.MUTE, font=font(8, True))
        c.create_text(pad + width, top, anchor='ne', fill=self.MUTE, font=font(8, True),
                      text='%d / %d' % (sum(1 for r in must if r[1] == 1), len(must)))
        y = top + px(18)
        if not rows:
            t = c.create_text(pad, y, anchor='nw', text='They appear after the briefing.', fill=self.MUTE, font=font(10))
            y = c.bbox(t)[3] + px(6)
        for title, st, ty in must:
            cy = y + px(8)
            if st == 1:
                c.create_oval(pad, cy - px(5), pad + px(10), cy + px(5), fill=self.GREEN, outline='')
            elif st == 2:
                c.create_oval(pad, cy - px(5), pad + px(10), cy + px(5), fill='', outline=GREY, width=px(2))
            else:
                c.create_oval(pad, cy - px(5), pad + px(10), cy + px(5), fill='', outline=self.SOFT, width=px(2))
            t = c.create_text(pad + px(20), y, anchor='nw', text=title, width=width - px(20),
                              fill=self.MUTE if st else self.INK, font=self.body(10))
            y = c.bbox(t)[3] + px(4)
        if extra:
            t = c.create_text(pad + px(20), y, anchor='nw', fill=self.MUTE, font=font(9),
                              text='+ %d optional: %s' % (len(extra), '; '.join(r[0].rstrip('.') for r in extra[:2])),
                              width=width - px(20))
            y = c.bbox(t)[3] + px(4)
        fail = self.rules.get((self.mission_id or '').lower(), {}).get('fail', [])
        if fail:
            t = c.create_text(pad + px(20), y + px(2), anchor='nw', text='FAILS IF  ' + fail[0], fill=RED,
                              width=width - px(20), font=font(9, True))
            y = c.bbox(t)[3] + px(4)
        y += px(10)
        if not intel:
            c.create_text(pad, y, anchor='nw', text='Radar comes online in the mission.', fill=self.MUTE, font=font(10))
            return
        sam = intel['sam']
        # NEXT card.
        nxt = self.next_objective()
        if nxt:
            dist, bearing, dz = relative(sam, nxt['loc'])
            clock = int(round(bearing / 30)) % 12 or 12
            level = '' if abs(dz) < 2.5 else '   %.0fm %s' % (abs(dz), 'up' if dz > 0 else 'down')
            path = self.route_to(nxt) or []
            self.card(c, pad, y, width, px(58), px, '#86cdfa1c')
            c.create_text(pad + px(14), y + px(9), anchor='nw', text='NEXT', fill=self.BLUE, font=font(8, True))
            c.create_text(pad + px(14), y + px(24), anchor='nw', text=nxt['label'], fill=self.INK, font=font(14, True))
            c.create_text(pad + width - px(14), y + px(8), anchor='ne', text='%.0fm' % dist, fill=self.BLUE,
                          font=font(17, True))
            c.create_text(pad + width - px(14), y + px(34), anchor='ne', text="%d o'clock%s" % (clock, level),
                          fill=self.MUTE, font=font(9))
            y += px(64)
            if len(path) > 1:
                t = c.create_text(pad, y, anchor='nw', fill=self.BLUE, font=font(9), width=width,
                                  text=' \u203a '.join(r.replace('_', ' ') for r in path))
                y = c.bbox(t)[3] + px(6)
            note = self.note_for(nxt)
            if note:
                t = c.create_text(pad, y, anchor='nw', text=note['where'], fill=self.MUTE, width=width,
                                  font=self.body(10))
                y = self.numbered(c, note['ways'], pad, c.bbox(t)[3] + px(5), width, px, self.body(10), self.SOFT, 3)
                y += px(4)
        # The radar fills what is left, with the threat counts underneath.
        strip_h = px(40)
        R = max(px(60), min(width / 2, (H - px(40) - strip_h - y - px(8)) / 2))
        cx, cy = pad + width / 2, y + R
        k = R / RANGE_M
        c.create_oval(cx - R, cy - R, cx + R, cy + R, fill='#ffffff08', outline='#ffffff1c')
        for frac in (0.6, 0.25):
            c.create_oval(cx - R * frac, cy - R * frac, cx + R * frac, cy + R * frac, outline='#ffffff12')
        c.create_line(cx - R, cy, cx + R, cy, fill='#ffffff0a')
        c.create_line(cx, cy - R, cx, cy + R, fill='#ffffff0a')
        c.create_text(cx + px(4), cy - R + px(3), text='%dm' % RANGE_M, anchor='nw', fill='#ffffff40', font=font(7))

        def to_screen(loc):
            dist, bearing, dz = relative(sam, loc)
            b = math.radians(bearing)
            return cx + math.sin(b) * dist * k, cy - math.cos(b) * dist * k, dist, bearing, dz

        def cone(xs, ys, yaw, cone_deg, rng_uu, colour):
            heading = (yaw - sam[1]) / 65536 * 360
            r = min(rng_uu / UU_PER_M, CONE_M) * k
            c.create_arc(xs - r, ys - r, xs + r, ys + r, start=90 - heading - cone_deg / 2, extent=cone_deg,
                         fill=colour + '30', outline=colour + '90')

        off_states = ('s_Deactivated', 's_Malfunctioning', 's_Destructed', 's_Off', 's_Idle')
        cams = 0
        for snr in intel['sensors']:
            xs, ys, dist, _, dz = to_screen(snr['loc'])
            if dist > RANGE_M:
                continue
            off = snr['state'] in off_states
            col = GREY if off else (RED if snr['state'] == 's_Alert' else AMBER)
            if not off:
                cams += 1
                cone(xs, ys, snr['yaw'], snr['cone'], snr['range'], col)
            c.create_rectangle(xs - px(4), ys - px(4), xs + px(4), ys + px(4), fill=col, outline='', radius=px(2))
        moods, facing_me = [], 0
        for g in intel['guards']:
            xs, ys, dist, bearing, dz = to_screen(g['loc'])
            mood, col = guard_mood(g)
            if mood in ('DEAD', 'OUT'):
                if dist <= RANGE_M:
                    c.create_oval(xs - px(3), ys - px(3), xs + px(3), ys + px(3), fill='#ffffff30', outline='')
                continue
            moods.append(mood)
            sees = abs(dz) < 2.5 and facing(g['loc'], g['yaw'], g['cone'], g['range'], sam[0])
            facing_me += sees and dist < 25
            if dist > RANGE_M:
                continue
            cone(xs, ys, g['yaw'], g['cone'], g['range'], col if mood != 'CALM' else '#8ff0a4')
            r = px(5)
            c.create_oval(xs - r, ys - r, xs + r, ys + r, fill=col if abs(dz) < 2.5 else '', outline=col, width=px(2))
        for ob in self.visible_markers():
            xs, ys, dist, bearing, dz = to_screen(ob['loc'])
            is_next = nxt is not None and ob['label'] == nxt['label']
            if dist <= RANGE_M:
                q = px(7) if is_next else px(5)
                c.create_polygon(xs, ys - q, xs + q, ys, xs, ys + q, xs - q, ys, fill=self.BLUE if is_next else '',
                                 outline=self.BLUE, width=px(2))
            else:
                b = math.radians(bearing)
                ex, ey = cx + math.sin(b) * (R - px(6)), cy - math.cos(b) * (R - px(6))
                if is_next:
                    tip = (ex + math.sin(b) * px(8), ey - math.cos(b) * px(8))
                    l = (ex + math.sin(b + 2.3) * px(7), ey - math.cos(b + 2.3) * px(7))
                    rr = (ex + math.sin(b - 2.3) * px(7), ey - math.cos(b - 2.3) * px(7))
                    c.create_polygon(*tip, *l, *rr, fill=self.BLUE, outline='')
                else:
                    c.create_oval(ex - px(3), ey - px(3), ex + px(3), ey + px(3), fill='#86cdfa80', outline='')
        c.create_polygon(cx, cy - px(9), cx - px(6), cy + px(6), cx, cy + px(3), cx + px(6), cy + px(6),
                         fill=self.INK, outline='')
        # Threat counts.
        y = cy + R + px(10)
        alarm = intel.get('alarm') or 0
        cells = [('ALARM', alarm, RED), ('ALERT', moods.count('ALERT'), RED), ('SUSPICIOUS', moods.count('SUSPICIOUS'), AMBER),
                 ('FACING YOU', facing_me, AMBER), ('CAMERAS', cams, AMBER)]
        cw = width / len(cells)
        for i, (label, value, col) in enumerate(cells):
            x0 = pad + i * cw
            c.create_text(x0, y, anchor='nw', text=str(value), fill=col if value else self.SOFT, font=font(14, True))
            c.create_text(x0, y + px(20), anchor='nw', text=label, fill=self.MUTE, font=font(7, True))


class Overlay:
    KEY = '#010203'  # transparent colour

    def __init__(self, log):
        self.log = log
        # Small diagnostics log next to the script (restarted each run).
        try:
            self.logfile = open(__import__('os').path.join(Opsat.HERE, 'overlay.log'), 'w', encoding='utf-8')
        except OSError:
            self.logfile = None
        self.last_said = None
        self.mem = self.game = None
        self.last = None
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True, '-transparentcolor', self.KEY, '-alpha', 0.92)
        self.root.configure(bg=self.KEY)
        self.canvas = tk.Canvas(self.root, bg=self.KEY, highlightthickness=0, width=320, height=120)
        self.canvas.pack()
        make_click_through(self.root)
        self.root.withdraw()
        self.shown = False
        self.hud_h = 1
        self.panel = Opsat(self.root)
        from dvorak import Dvorak, mission_briefing, save_key
        self.dvorak, self.mission_briefing, self.dvorak_save_key = Dvorak(), mission_briefing, save_key
        self.panel.on_submit = self.ask_dvorak
        self.panel.dvorak_online = self.dvorak.online
        self.queued = []        # questions typed while DVORAK was still answering
        self.key_asked = False  # asked for the API key this run
        self.keys_down = set()
        self.game_fg = False
        self.announced, self.toast_until, self.hud = False, 0.0, None
        self.cur_mission = None
        self.root.after(0, self.tick)
        self.root.after(0, self.poll_keys)
        self.root.after(0, self.animate)

    def animate(self):
        """Slide frames for the open/close animation (~60 fps only while sliding)."""
        busy = self.panel.animate() if (self.panel.open and self.panel.shown) else False
        self.root.after(15 if busy else 60, self.animate)

    def poll_keys(self):
        """Hotkeys for the hint panel, only while the game window is in front."""
        if self.game_fg and self.game and not self.panel.typing:
            p = self.panel
            for vk, action in ((VK_UP, p.show_panel), (VK_DOWN, p.hide_panel), (VK_LEFT, lambda: p.step_tab(-1)),
                               (VK_RIGHT, lambda: p.step_tab(1)), (VK_INSERT, self.talk)):
                state = u32.GetAsyncKeyState(vk)
                down = bool(state & 0x8000)
                tapped = bool(state & 1)  # pressed since the last poll (catches quick taps)
                if (down and vk not in self.keys_down) or (tapped and not down):
                    try:
                        action()
                    except Exception as e:
                        self.say('panel error:', e)
                (self.keys_down.add if down else self.keys_down.discard)(vk)
        self.root.after(40, self.poll_keys)

    def talk(self):
        panel = self.panel
        panel.tab = panel.TABS.index('TERMINAL')
        panel.show_panel()
        panel.render()
        panel.start_typing()

    def say(self, *parts):
        line = ' '.join(str(p) for p in parts)
        if line == self.last_said:  # don't repeat 'waiting for game' every tick
            return
        self.last_said = line
        line = time.strftime('%H:%M:%S ') + line
        if self.log:
            print(line, flush=True)
        if self.logfile:
            self.logfile.write(line + '\n')
            self.logfile.flush()

    # ------------------------------------------------------------ DVORAK
    def telemetry(self):
        """Plain-text snapshot of the live game state for DVORAK."""
        g, panel = self.game, self.panel
        m = panel.mission()
        out = ['Mission: %s (%s)' % (m['title'] if m else 'unknown', panel.mission_id),
               'Current room: %s' % (panel.room or 'unknown')]
        if panel.room and g.room_graph.get(panel.room):
            out.append('Connected rooms from here: ' + ', '.join(g.room_graph[panel.room]))
        nxt = panel.next_objective() if panel.intel else None
        if nxt:
            path = panel.route_to(nxt)
            out.append('Nearest pending objective beacon: %s in %s%s' % (
                nxt['label'], nxt.get('room'), (' - route: ' + ' > '.join(path)) if path and len(path) > 1 else ''))
        if panel.events:
            out.append('Recent triggers (what Fisher just set off, newest last):')
            out += ['  %s %s' % (time.strftime('%H:%M', time.localtime(t)), e) for t, e in list(panel.events)[-8:]]
        rules = panel.rules.get((panel.mission_id or '').lower(), {})
        if rules.get('fail') or rules.get('penalty'):
            out.append('Mission rules: FAIL IF ' + ('; '.join(rules.get('fail', [])) or 'nothing special')
                       + ' | PENALTIES ' + ('; '.join(rules.get('penalty', [])) or 'none'))
        if g.room_names:
            seen = set(g.visited_rooms)
            out.append('Rooms visited: ' + (', '.join(r for r in g.room_names if r in seen) or 'none'))
            out.append('Rooms not visited yet: ' + (', '.join(r for r in g.room_names if r not in seen) or 'none'))
        sect = next((v for k, v in panel.titles.items() if k.startswith('p_' + (panel.mission_id or '').lower())), {})
        out.append('Objectives (status from game memory):')
        for key, status, oid, detail, otype in panel.objs:
            label = {0: 'PENDING', 1: 'DONE', 2: 'CANCELLED'}.get(status, 'STATUS %d' % status)
            label += ', ' + OBJ_TYPES.get(otype, 'type %d' % otype)
            title = sect.get(key, key)
            desc = sect.get(detail, '')
            out.append('  [%s] %s%s' % (label, title, (' - ' + desc) if desc and desc != title else ''))
        if not panel.objs:
            out.append('  (none loaded yet - probably the briefing)')
        intel = panel.intel
        if intel:
            out.append('Alarm stage: %s' % intel.get('alarm'))
            near = []
            for gd in intel['guards']:
                dist, bearing, dz = relative(intel['sam'], gd['loc'])
                mood = guard_mood(gd)[0]
                if mood in ('DEAD', 'OUT') or dist > 40:
                    continue
                clock = int(round(bearing / 30)) % 12 or 12
                floor = '' if abs(dz) < 2.5 else (' (above)' if dz > 0 else ' (below)')
                near.append((dist, '  %.0fm at %d o\'clock%s: %s, %s, last reacted to: %s' % (
                    dist, clock, floor, mood, ACTIVITY.get(gd['goal'], gd['goal']),
                    EVENTS.get(gd['event'], 'nothing'))))
            beacons = []
            for ob in intel.get('objectives') or []:
                if not ob['done']:
                    dist, bearing, dz = relative(intel['sam'], ob['loc'])
                    clock = int(round(bearing / 30)) % 12 or 12
                    beacons.append((dist, '  %s: ~%.0fm at %d o\'clock%s' % (
                        pretty(ob['name']), dist, clock, '' if abs(dz) < 2.5 else ' (%.0fm %s)' % (abs(dz), 'up' if dz > 0 else 'down'))))
            if beacons:
                out.append('Objective beacons from the game\'s 3D map (approximate):')
                out += [t for _, t in sorted(beacons)]
            out.append('Guards within 40m: %d' % len(near))
            out += [t for _, t in sorted(near)[:6]]
        return '\n'.join(out)

    def ask_dvorak(self, question, quiet=False):
        panel = self.panel
        if panel.key_mode or question.lower().startswith(('/key', 'sk-ant')):  # API key; never logged
            key = question[4:].strip() if question.lower().startswith('/key') else question.strip()
            if not key.startswith('sk-ant-'):
                panel.chat.append(['sys', 'That does not look like an Anthropic key (sk-ant-...). Not saved.'])
            else:
                self.dvorak_save_key(key)
                panel.chat.append(['sys', 'API key saved (...%s). DVORAK online.' % key[-4:]])
                self.say('api key updated')
            self.dvorak.reload_config()
            panel.dvorak_online = self.dvorak.online
            panel.render()
            return
        self.dvorak.reload_config()
        panel.dvorak_online = self.dvorak.online
        if not self.dvorak.online:
            panel.chat.append(['sys', 'DVORAK offline. Press INS, paste your Anthropic API key (Ctrl+V), ENTER.'])
            panel.render()
            return
        m = panel.mission()
        if not m or not self.game:
            panel.chat.append(['sys', 'No mission telemetry yet. Load a mission or a save first.'])
            panel.render()
            return
        self.dvorak.set_mission(panel.mission_id, self.mission_briefing(m['title'], m['rooms'], panel.section(),
                                                                        panel.walkthrough()))
        if panel.mission() and self.game and self.dvorak.busy:  # queue it; sent when the current reply finishes
            if not quiet:
                panel.chat.append(['fisher', question])
            self.queued.append(question)
        elif self.dvorak.ask(question, self.telemetry(), m['title'], (panel.intel or {}).get('alarm') or 0):
            if not quiet:
                panel.chat.append(['fisher', question])
            panel.chat.append(['dvorak', ''])
            panel.dvorak_busy = True
            self.say('dvorak <-', question)
        panel.render()

    def pump_dvorak(self, mission):
        """Move streamed reply text into the terminal. DVORAK only speaks when Fisher asks (no automatic calls)."""
        panel, changed = self.panel, False
        panel.bond_info = (self.dvorak.stage_name(),) + self.dvorak.stage_level()
        while True:
            try:
                kind, text = self.dvorak.events.get_nowait()
            except Exception:
                break
            changed = True
            if kind == 'chunk' and panel.chat and panel.chat[-1][0] == 'dvorak':
                panel.chat[-1][1] += text
            elif kind == 'done':
                panel.dvorak_busy = False
                self.say('dvorak ->', panel.chat[-1][1][:200] if panel.chat else '')
                if self.queued:
                    q = self.queued.pop(0)
                    if self.dvorak.ask(q, self.telemetry(), (panel.mission() or {}).get('title', ''),
                                       (panel.intel or {}).get('alarm') or 0):
                        panel.chat.append(['dvorak', ''])
                        panel.dvorak_busy = True
                        self.say('dvorak <-', q)
            elif kind == 'memory':
                self.say('dvorak memory updated')
            elif kind == 'memory_error':
                self.say('dvorak memory error:', text)
            elif kind == 'error':
                panel.dvorak_busy = False
                self.queued = []
                if panel.chat and panel.chat[-1] == ['dvorak', '']:
                    panel.chat.pop()
                panel.chat.append(['sys', 'DVORAK error: ' + text])
                self.say('dvorak error:', text)
        on_terminal = panel.open and panel.TABS[panel.tab] == 'TERMINAL'
        if on_terminal and mission and not self.dvorak.online and not self.key_asked and not panel.typing:
            self.key_asked = True
            panel.chat.append(['sys', 'DVORAK: Link down, Fisher. I need an Anthropic API key to come online. '
                                      'Paste it (Ctrl+V) and press ENTER. ESC to skip.'])
            panel.key_mode = True
            panel.start_typing()
            changed = True
        if on_terminal and (changed or panel.dvorak_busy):
            panel.render()

    def track_triggers(self, mission, room, objs, intel):
        """Log what Sam just set off: objectives added/done/cancelled, alarms, rooms, guards alerted or down."""
        panel = self.panel
        sect = next((v for k, v in panel.titles.items() if k.startswith('p_' + (mission or '').lower())), {})
        snap = {
            'mission': mission, 'room': room,
            'objs': {o[0]: o[1] for o in objs},
            'alarm': (intel or {}).get('alarm'),
            'alert': sum(1 for g in (intel or {}).get('guards', []) if guard_mood(g)[0] == 'ALERT'),
            'down': sum(1 for g in (intel or {}).get('guards', []) if guard_mood(g)[0] in ('DEAD', 'OUT')),
        }
        old, self.trig = getattr(self, 'trig', None), snap
        if not old or old['mission'] != mission or not mission:
            return
        ev = []
        for key, st in snap['objs'].items():
            title = sect.get(key, key)
            if key not in old['objs'] and old['objs']:
                ev.append(('NEW OBJECTIVE: ' + title, AMBER))
            elif old['objs'].get(key) == 0 and st == 1:
                ev.append(('OBJECTIVE COMPLETE: ' + title, Opsat.GREEN))
            elif old['objs'].get(key) == 0 and st == 2:
                ev.append(('OBJECTIVE CANCELLED: ' + title, RED))
        if snap['alarm'] is not None and old['alarm'] is not None and snap['alarm'] != old['alarm']:
            ev.append(('ALARM STAGE %d' % snap['alarm'], RED if snap['alarm'] > old['alarm'] else Opsat.GREEN))
        if snap['alert'] > old['alert']:
            ev.append(('%d guard(s) went ALERT' % (snap['alert'] - old['alert']), RED))
        if snap['down'] > old['down']:
            ev.append(('%d guard(s) down' % (snap['down'] - old['down']), Opsat.MUTE))
        if room and old['room'] and room != old['room']:
            panel.events.append((time.time(), 'entered ' + room.replace('_', ' ')))
        recent = {e for t, e in panel.events if time.time() - t < 120}
        ev = [(t, c) for t, c in ev if t not in recent]
        for text, colour in ev:
            panel.events.append((time.time(), text))
            panel.chat.append(['event', text])
            self.say('trigger:', text)

    def threat_chip(self, intel):
        """Worst live threat from the guards' own AI state, or None when everyone is calm."""
        if not intel:
            return None
        if intel.get('alarm'):
            alarm = ('ALARM %d' % intel['alarm'], RED)
        else:
            alarm = None
        alert = [relative(intel['sam'], g['loc'])[0] for g in intel['guards'] if guard_mood(g)[0] == 'ALERT']
        if alert:
            return ('HOSTILE ALERT  %.0fm' % min(alert), RED)
        susp = [relative(intel['sam'], g['loc'])[0] for g in intel['guards'] if guard_mood(g)[0] == 'SUSPICIOUS']
        if susp:
            return ('GUARD SUSPICIOUS  %.0fm' % min(susp), AMBER)
        return alarm

    def draw(self, toast, threat=None, cheats=(False, False)):
        """HUD strip (bottom-left): GOD / INVISIBLE, the worst live threat and the one-time ready notice."""
        c = self.canvas
        c.delete('all')
        y = 0
        rows = [('GOD MODE', Opsat.GREEN)] * cheats[0] + [('INVISIBLE', Opsat.BLUE)] * cheats[1]
        if threat:
            rows.insert(0, threat)
        if toast:
            rows.append(('OPSAT V1.2   \u25b2 to open', Opsat.GREEN))
        for text, colour in rows:
            t = c.create_text(16, y + 16, text=text, fill=colour, anchor='w', font=(Opsat.UI, 14, 'bold'))
            x1 = c.bbox(t)[2] + 12
            c.create_rectangle(0, y, x1, y + 32, fill='#050805', outline='')
            c.create_line(0, y, 0, y + 32, fill=colour, width=3)
            for yy in (y, y + 31):
                c.create_line(0, yy, 10, yy, fill=colour)
                c.create_line(x1 - 10, yy, x1, yy, fill=colour)
            c.create_line(x1, y, x1, y + 32, fill=colour)
            c.tag_raise(t)
            y += 38
        self.hud_h = max(1, y - 6)

    def hide(self):
        if self.shown:
            self.root.withdraw()
            self.shown = False

    def detach(self):
        if self.mem:
            self.mem.close()
            self.say('game closed - waiting')
        self.mem = self.game = None
        self.dvorak.consolidate()  # game closed: save what was discussed
        self.last = self.hud = self.cur_mission = None
        self.panel.open = self.panel.closing = False  # a new game session starts with OPSAT closed
        self.game_fg = False
        self.hide()
        self.panel.place(None, False)

    def connect(self):
        """Returns True once attached; False while the game is absent or still loading."""
        if not self.mem:
            pid = find_pid()
            if not pid:
                return False
            self.mem = Mem(pid)
        try:
            self.game = Game(self.mem)
            self.say('attached to pid', self.mem.pid)
            return True
        except Exception as e:
            self.say('waiting for game:', e)
            if not self.mem.alive():
                self.detach()
            return False

    def tick(self):
        delay = POLL_MS
        try:
            delay = self.step()
        except Exception as e:
            import traceback
            self.say('error:', e, '|', ' / '.join(l.strip() for l in traceback.format_exc().splitlines()[-4:-1]))
            if self.mem and not self.mem.alive():
                self.detach()
            else:
                self.game = None  # re-resolve on next tick
        self.root.after(delay, self.tick)

    def step(self):
        if self.mem and not self.mem.alive():
            self.detach()
        if not self.game and not self.connect():
            self.hide()
            return POLL_MS if self.mem else IDLE_POLL_MS

        hwnd = game_window(self.mem.pid)
        self.game_fg = bool(hwnd) and u32.GetForegroundWindow() == hwnd
        mission, room, objs = self.game.mission_state()
        cheats = self.game.cheats()
        if cheats != self.panel.cheats:
            self.panel.cheats = cheats
            self.panel.render()
        self.panel.room_names = self.game.room_names
        self.panel.game = self.game
        if mission != self.cur_mission:
            if self.cur_mission:
                self.dvorak.consolidate()
            self.cur_mission, self.announced = mission, False
        if mission and room and not self.announced:
            self.announced = True
            self.toast_until = time.monotonic() + 6  # "OPSAT V1.2 - up arrow" once per mission
        intel = self.game.intel()
        self.panel.intel = intel
        self.track_triggers(mission, room, objs, intel)
        threat = self.threat_chip(intel)
        self.panel.game_hwnd = hwnd
        self.pump_dvorak(mission)
        if self.panel.open and self.panel.TABS[self.panel.tab] == 'RADAR':
            self.panel.render()  # live radar
        if self.panel.update_state(mission, room, objs):
            self.panel.render()
            self.say('mission', mission, '| level', self.game.level_name, '| room', room,
                     '| objectives', [(o[0], o[1]) for o in objs])
        self.panel.place(hwnd, self.game_fg or self.panel.typing)
        toast = time.monotonic() < self.toast_until
        if (toast, threat, cheats) != self.hud:
            self.hud = (toast, threat, cheats)
            self.draw(toast, threat, cheats)
            if threat:
                self.say('threat:', threat[0])
        want = (toast or threat or any(cheats)) and self.game_fg and not self.panel.open
        if want:
            dx, _, bottom = dock(hwnd)[:3]
            self.canvas.config(height=self.hud_h)
            self.root.geometry('320x%d+%d+%d' % (self.hud_h, dx, bottom - self.hud_h))
            if not self.shown:
                self.root.deiconify()
                self.root.attributes('-topmost', True)
                self.shown = True
        else:
            self.hide()
        return POLL_MS


if __name__ == '__main__':
    k32.CreateMutexW.restype = wt.HANDLE
    mutex = k32.CreateMutexW(None, False, 'Local\\SCCT_CheatOverlay')
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS - another copy is running
        sys.exit(0)
    ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)
    overlay = Overlay('--log' in sys.argv)
    overlay.root.mainloop()
