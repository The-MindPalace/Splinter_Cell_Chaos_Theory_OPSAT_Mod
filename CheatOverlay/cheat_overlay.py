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
from types import SimpleNamespace

import hud
from layered import LayeredWindow, InputBox

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
        self.stress_seen = {}   # controller -> [baseline rank, time the stress rose above it or None]

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
            stress = enum('StressSteps', self._byte(pat + P('EPattern', 'Stress_ReactionStep'))) if pat else 'S_Casual'
            guards.append({
                'id': c,  # AI controller address: stable for the guard's life (lets the bot follow one guard)
                'name': self.oname(self.m.u32(pawn + O_CLASS)) or '?',
                'loc': pose[0], 'yaw': pose[1],
                'health': self.m.u32(pawn + P('Pawn', 'Health')) or 0,
                'state': self._state_name(c) or '',
                'goal': enum('GoalType', self._byte(c + P('EAIController', 'LastGoalType'))),
                'event': enum('AIEventType', self._byte(pat + P('EPattern', 'CurrentEventType'))) if pat else 'AI_NONE',
                'stress': stress, 'stress_up': self._stress_up(c, stress),
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
        if alarm is not None and not 0 <= alarm <= 16:  # half-built level while loading: not a real stage
            alarm = None
        return {'sam': sam_pose, 'guards': guards, 'sensors': sensors, 'alarm': alarm,
                'objectives': self.objective_markers()}

    def _stress_up(self, ctrl, stress):
        """Has this guard's stress risen above where it started, in the last 45 s? Levels start guards at
        different steps (Penthouse: 3 in S_NormalA and 3 in S_NormalB at load, no reaction yet), so a stress
        name alone says nothing; a rise does. After 45 s the higher step is that guard's new normal."""
        rank = STRESS_RANK.get(stress, 0)
        seen = self.stress_seen.get(ctrl)
        now = time.monotonic()
        if seen is None:
            if len(self.stress_seen) > 400:  # controllers from earlier levels
                self.stress_seen.clear()
            self.stress_seen[ctrl] = [rank, None]
            return False
        if rank <= seen[0]:
            seen[0], seen[1] = rank, None
            return False
        if seen[1] is None:
            seen[1] = now
        if now - seen[1] > 45:
            seen[0], seen[1] = rank, None
            return False
        return True

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
                            approx = bool(centre and math.hypot(loc[0] - centre[0], loc[1] - centre[1]) > 15 * UU_PER_M)
                            if approx:  # only the area is reliable for this one, not the exact spot
                                loc = centre
                            out.append({'name': bid or oid, 'objective': oid, 'done': done, 'loc': loc,
                                        'room': room, 'approx': approx})
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

    def stance(self):
        """(crouched, luminosity) of Sam: Pawn.bIsCrouched (pawn+696, mask 2), Actor.LuminosityFactor (pawn+612;
        0-5 shadow, 35+ lit). For the run recorder."""
        try:
            pawn = self.m.u32(self.players[0] + self.pawn_off)
            return bool(self.m.u32(pawn + 696) & 2), round(self._f32(pawn + 612), 1)
        except Exception:
            return None, None

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


VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_INSERT = 0x25, 0x26, 0x27, 0x28, 0x2D


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


def ease(t, x1=.2, y1=.8, x2=.2, y2=1.0):
    """CSS cubic-bezier(.2, .8, .2, 1) - the spec's motion curve."""
    if t <= 0 or t >= 1:
        return min(1.0, max(0.0, t))
    u = t
    for _ in range(8):  # solve x(u) = t (Newton)
        x = 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3 - t
        dx = 3 * (1 - u) ** 2 * x1 + 6 * (1 - u) * u * (x2 - x1) + 3 * u * u * (1 - x2)
        if abs(dx) < 1e-6:
            break
        u = min(1.0, max(0.0, u - x / dx))
    return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3


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


STRESS_RANK = {'S_Casual': 0, 'S_NormalA': 1, 'S_NormalB': 2, 'S_Repetition': 3}


def guard_mood(g):
    """(label, colour) from the guard's own AI state machine."""
    st = g['state']
    if st == 's_Dead' or g['health'] <= 0:
        return 'DEAD', GREY
    if st in ('s_Unconscious', 's_Stunned', 's_Groggy', 's_Grabbed', 's_Carried'):
        return 'OUT', GREY
    if g['goal'] in ('GOAL_Shoot', 'GOAL_MoveAndShoot', 'GOAL_ThrowGrenade') or g['stress'].startswith('S_HighStress'):
        return 'ALERT', RED
    # Suspicious = stress just rose above that guard's own starting step (see Game._stress_up), or reacting to
    # an event right now. A stress name alone is not enough: levels start guards in S_NormalA/S_NormalB.
    if g.get('stress_up') or g['event'] not in ('AI_NONE', ''):
        return 'SUSPICIOUS', AMBER
    return 'CALM', '#9fe08a'


def pretty(name):
    """'AuthorizeA' -> 'AUTHORIZE A', 'RoofBeacon' -> 'ROOF'."""
    import re
    name = re.sub(r'Beacon$', '', name or '?') or name
    return re.sub(r'(?<=[a-z])(?=[A-Z0-9])', ' ', name).upper()


def short_title(title, limit=34):
    """'Authorize the vault access from three officers' panels.' -> 'Authorize the vault access from three…'"""
    title = title.strip().rstrip('.')
    if len(title) <= limit:
        return title
    return title[:limit].rsplit(' ', 1)[0].rstrip(',;:') + '…'


def merge_markers(obs, name=pretty):
    """Pending beacons, with ones at the same spot (within 3 m) merged: 'INFO / MONEY'."""
    out = []
    for ob in obs:
        if ob['done']:
            continue
        for o in out:
            if math.dist(o['loc'], ob['loc']) < 3 * UU_PER_M:
                o['extra'] = o.get('extra', 0) + 1  # 'Steal the bonds  +1' rather than two long titles
                o['label'] = o['base'] + '  +%d' % o['extra']
                break
        else:
            out.append(dict(ob, label=name(ob), base=name(ob)))
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
    """OPSAT V1.2 panel (HUD spec round 3): docked bottom-left, three tabs DVORAK - RADAR - INTEL.

    Up opens it, always on RADAR; Down closes; Left/Right move one tab (no wrap); Insert opens DVORAK and
    focuses the input. Drawing lives in hud.py; this class holds state, keys and motion."""
    INK, SOFT, MUTE, GREEN, BLUE = hud.INK, hud.SOFT, hud.MUTE, hud.GREEN, hud.BLUE  # HUD strip colours
    TABS = ('DVORAK', 'RADAR', 'INTEL')
    SLIDE_S, RESIZE_S = 0.34, 0.32   # spec motion: slide 340 ms, height 320 ms, cubic-bezier(.2, .8, .2, 1)
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
        self.fns = SimpleNamespace(relative=relative, guard_mood=guard_mood, facing=facing, range_m=RANGE_M,
                                   uu_per_m=UU_PER_M)
        self.entry_spec = None   # hud's description of the input box while typing
        self.input = InputBox(root, lambda text: self.submit(), lambda: self.stop_typing())
        self.entry = self.input.entry
        self.open = self.shown = False
        self.tab = 1  # RADAR
        self.mission_id = self.room = None
        self.objs, self.room_names = [], []
        self.intel = None
        self.cheats = (False, False)
        self.chat = []          # [who, text]: who in fisher / dvorak / sys / event
        self.typing = self.key_mode = False
        self.dvorak_online = self.dvorak_busy = False
        self.bond_info = None
        self.on_submit = None
        self.on_error = None    # logger for drawing errors (a paint bug must never take memory reading down)
        self.game = self.game_hwnd = None
        self.anchor, self.size, self.scale = None, (560, 640), 1.0
        self.img = self.full = None   # frame on screen / last paint at the tab's full height
        self.slide = (0.0, 0.0, 0.0)  # (start time, from, to) of the open/close animation
        self.resize = None            # (start time, from height, to height) when switching tabs
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
        """Beacons of objectives the game has given and that are still pending (beacon ObjID == objective ID).
        Beacons of objectives not given yet only carry internal names ("IND_DVORAK", "BUMP OBJECTIVE") and give
        the plot away, so they never show. Extraction only once the primaries are done."""
        rows = self.objective_rows()
        primaries_left = not rows or any(st == 0 and ty in (0, 3) for _, st, ty in rows)
        ids = {o[2]: (o[1], o[4]) for o in self.objs}
        given = [ob for ob in (self.intel or {}).get('objectives') or []
                 if ids.get(ob['objective'], (None,))[0] == 0
                 or (ob['objective'] not in ids and 'extract' in (ob['objective'] + ob['name']).lower()
                     and not primaries_left)]
        out = []
        for ob in merge_markers(given, self.marker_name):  # filter first: merging must not count hidden ones
            if not ('extract' in ob['label'].lower() and primaries_left):
                ob['primary'] = ids.get(ob['objective'], (0, 0))[1] in (0, 3)
                out.append(ob)
        return out

    def objective_key(self, ob):
        return next((o[0] for o in self.objs if o[2] == ob.get('objective')), None)

    def marker_name(self, ob):
        """Short radar name ('Vault panels') from walkthrough.json, else the objective's own title shortened -
        never the beacon's internal id ('Objectif0')."""
        key = self.objective_key(ob)
        note = ((self.walkthrough() or {}).get('objectives') or {}).get(key) if key else None
        if note and note.get('tag'):
            return note['tag']
        sect = self.section()
        return short_title(sect[key]) if key and sect.get(key) else pretty(ob.get('name'))

    def objective_title(self, ob, limit=60):
        """Full objective title for the NEXT line (game text), '+N' when markers share the spot."""
        key = self.objective_key(ob)
        title = self.section().get(key) if key else None
        base = short_title(title, limit) if title else ob.get('base', ob['label'])
        return base + ('  +%d' % ob['extra'] if ob.get('extra') else '')

    def next_objective(self):
        """Story order (the game lists objectives as they unlock), nearest beacon breaks ties."""
        marks = self.visible_markers() if self.intel and self.objs else []  # nothing to steer to in the briefing
        order = {o[2]: i for i, o in enumerate(self.objs)}
        sam = (self.intel or {}).get('sam')
        return min(marks, key=lambda ob: (not ob['primary'], order.get(ob['objective'], 99),
                                          relative(sam, ob['loc'])[0])) if marks else None

    def in_area(self, ob):
        """Sam is already in the area of a marker whose exact spot is unknown (no distance/direction to show)."""
        return bool(ob and ob.get('approx') and ob.get('room') and ob.get('room') == self.room)

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

    def here_note(self):
        """Room notes (hints.json) for the 3D-map area Sam is in: {'next', 'ways'}."""
        return ((self.mission() or {}).get('rooms') or {}).get(self.room or '')

    def ways_for(self, nxt=None):
        return (self.note_for(nxt) or {}).get('ways', [])

    def next_moves(self):
        """(where/progress line, next line, moves, fail rule) - the live 'what now', no AI involved."""
        must = [r for r in self.objective_rows() if r[2] in (0, 3)]
        done = sum(1 for r in must if r[1] == 1)
        now = '%s' % (self.room or 'unknown').replace('_', ' ')
        nxt = self.next_objective()
        if nxt:
            path = self.route_to(nxt) or []
            via = ('  via ' + ' › '.join(r.replace('_', ' ') for r in path[1:3])) if len(path) > 1 else ''
            dist = 'in this area' if self.in_area(nxt) else '%.0fm' % relative(self.intel['sam'], nxt['loc'])[0]
            nxt_line = '%s   %s%s' % (self.objective_title(nxt), dist, via)
        else:  # no map marker: name the first pending primary instead
            pending = [r[0] for r in must if r[1] == 0]
            nxt_line = 'Get to extraction' if must and done == len(must) else (pending[0] if pending else '')
        moves = self.ways_for(nxt) or (self.room_ways(nxt.get('room')) if nxt else [])[:3] or self.room_ways(self.room)[:3]
        fail =self.rules.get((self.mission_id or '').lower(), {}).get('fail', [])
        return now, (done, len(must)), nxt_line, moves, (fail[0] if fail else None)

    # --- keys ----------------------------------------------------------------
    def show_panel(self, tab='RADAR'):
        if not self.open or self.closing:
            self.tab = self.TABS.index(tab)  # opening always lands on RADAR (Insert lands on DVORAK)
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
            tab = max(0, min(len(self.TABS) - 1, self.tab + d))  # stops at the ends
            if tab != self.tab:
                self.tab = tab
                self.start_resize()
                self.render()

    def target_height(self):
        # RADAR 560 x 616 so the game stays visible; DVORAK and INTEL 560 x 770 (at 1080p)
        return int((hud.H_RADAR if self.TABS[self.tab] == 'RADAR' else hud.H_FULL) * self.scale)

    def start_resize(self):
        if not self.shown:
            return
        h1 = self.target_height()
        if h1 == self.size[1]:
            self.resize = None
        elif not (self.resize and self.resize[2] == h1):
            self.resize = (time.monotonic(), self.size[1], h1)

    def start_typing(self):
        if not self.open or self.TABS[self.tab] != 'DVORAK' or self.typing:
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
        r = wt.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))  # docked to the frame's bottom-left corner, grows upward
        x, bottom, sc = r.left, r.bottom, max(0.8, (r.bottom - r.top) / 1080) * hud.UI_SCALE
        w = int(hud.WIDTH * sc)
        if self.shown and (x, bottom) == self.anchor and w == self.size[0]:
            self.start_resize()  # same spot: glide to the tab's height instead of jumping
            return
        self.scale = sc
        size = (w, self.target_height())
        if (x, bottom) != self.anchor or size != self.size or not self.shown:
            if not self.shown and not self.closing:
                self.slide = (time.monotonic(), 0.0, 1.0)  # first frame on screen: start the slide here
            self.anchor, self.size, self.scale, self.shown = (x, bottom), size, sc, True
            self.render()

    def progress(self):
        t0, a, b = self.slide
        t = min(1.0, (time.monotonic() - t0) / self.SLIDE_S)
        return a + (b - a) * ease(t), t >= 1.0

    def animate(self):
        """Called every frame while sliding or gliding; returns True while an animation is still running."""
        resizing = False
        if self.resize:
            t0, h0, h1 = self.resize
            t = min(1.0, (time.monotonic() - t0) / self.RESIZE_S)
            self.size = (self.size[0], int(round(h0 + (h1 - h0) * ease(t))))
            self.resize = None if t >= 1 else self.resize
            resizing = self.resize is not None
        p, finished = self.progress()
        if self.full is not None and self.shown:
            self.compose()
        if finished and self.closing:
            self.open = self.closing = False
            self.lw.hide()
            self.input.hide()
            self.shown = False
        return resizing or not finished

    # --- drawing (hud.py) -----------------------------------------------------
    def render(self):
        if not (self.open and self.shown and self.anchor):
            return
        # Mid-glide, paint the final height once; compose() folds it to each in-between height (60 fps even on
        # INTEL, whose paint is too slow to redo every frame).
        try:
            self.full, self.entry_spec = hud.paint(self, self.fns, self.resize[2] if self.resize else None)
            self.compose()
        except Exception as e:
            if self.on_error:
                self.on_error(e)

    def compose(self):
        """Put the last paint on screen at the current height and slide offset, with the input over its plate."""
        W, H = self.size
        x, bottom = self.anchor
        img = hud.fold(self.full, H, self.scale)
        spec = self.entry_spec if self.typing else None
        if spec:  # cut the input area out of the panel so the Tk entry underneath shows at full strength
            rx, ry, rw, rh = spec.rect
            ry += H - self.full.height  # the body is pinned to the bottom while gliding
            img.paste((0, 0, 0, 0), (rx, ry, rx + rw, ry + rh))
        p, _ = self.progress()
        top = bottom - int(H * p)
        self.img = img
        self.lw.show(img, x, top)
        if spec:
            first = not self.input.win.winfo_viewable()
            self.input.style(spec.bg, spec.fg, spec.caret, spec.font, spec.masked)
            self.input.show(x + rx, top + ry, rw, rh)
            if first:  # the input line just appeared: give it the keyboard
                force_foreground(self.input.hwnd())
                self.entry.focus_force()
        elif self.input.placed:
            self.input.hide()


class Overlay:
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
        self.root.withdraw()  # never shown: everything on screen is a layered window
        self.strip = LayeredWindow(self.root)  # closed-panel HUD: cheats, worst threat, the 'press up' notice
        self.strip_img, self.strip_ver, self.strip_at = None, 0, None
        self.shown = False
        self.panel = Opsat(self.root)
        from dvorak import Dvorak, mission_briefing, save_key
        self.dvorak, self.mission_briefing, self.dvorak_save_key = Dvorak(), mission_briefing, save_key
        self.panel.on_submit = self.ask_dvorak
        self.panel.on_error = self.panel_error
        self.panel.dvorak_online = self.dvorak.online
        self.queued = []        # questions typed while DVORAK was still answering
        self.retry_q = None     # the question a rejected key swallowed: asked again once a new key is saved
        self.chat_mission = None
        from runlog import RunLog
        self.runlog = RunLog()
        self.key_asked = False  # asked for the API key this run
        self.keys_down = set()
        self.game_fg = False
        self.announced, self.toast_until, self.hud = False, 0.0, None
        self.cur_mission = None
        self.root.after(0, self.tick)
        self.root.after(0, self.poll_keys)
        self.root.after(0, self.animate)

    def animate(self):
        """Slide and glide frames (~60 fps only while moving). Always reschedules, even after an error."""
        busy = False
        try:
            busy = self.panel.animate() if (self.panel.open and self.panel.shown) else False
        except Exception as e:
            self.panel_error(e)
        self.root.after(15 if busy else 60, self.animate)

    def panel_error(self, e):
        import traceback
        self.say('panel error:', e, '|', ' / '.join(l.strip() for l in traceback.format_exc().splitlines()[-4:-1]))

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
        if panel.open and not panel.closing:
            panel.tab = panel.TABS.index('DVORAK')
            panel.start_resize()
        else:
            panel.show_panel('DVORAK')
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
            for ob in panel.visible_markers():  # given and pending only: no internal names, no plot spoilers
                if not ob['done']:
                    dist, bearing, dz = relative(intel['sam'], ob['loc'])
                    clock = int(round(bearing / 30)) % 12 or 12
                    if ob.get('approx'):
                        beacons.append((dist, '  %s: somewhere in %s (exact spot unknown)' % (panel.marker_name(ob), ob['room'])))
                        continue
                    beacons.append((dist, '  %s: ~%.0fm at %d o\'clock%s' % (
                        panel.marker_name(ob), dist, clock, '' if abs(dz) < 2.5 else ' (%.0fm %s)' % (abs(dz), 'up' if dz > 0 else 'down'))))
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
            q, self.retry_q = self.retry_q, None
            if q and self.dvorak.online:  # Fisher asked this; the old key ate it
                self.ask_dvorak(q, quiet=True)
                return
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
                if 'key' in text.lower() and ('rejected' in text.lower() or 'permission' in text.lower()):
                    self.retry_q = next((t for w, t in reversed(panel.chat) if w == 'fisher'), None)
                    panel.chat.append(['sys', 'DVORAK: That key does not work. Paste a working Anthropic API key '
                                              '(Ctrl+V) and press ENTER. ESC to skip.'])
                    panel.tab = panel.TABS.index('DVORAK')
                    panel.start_resize()
                    panel.key_mode = True
                    panel.start_typing()
        on_terminal = panel.open and panel.TABS[panel.tab] == 'DVORAK'
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
            'objs': {o[0]: o[1] for o in objs if o[0]},
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
            n = snap['alert'] - old['alert']
            ev.append(('%d guard%s went alert' % (n, '' if n == 1 else 's'), RED))
        if snap['down'] > old['down']:
            n = snap['down'] - old['down']
            ev.append(('%d guard%s down' % (n, '' if n == 1 else 's'), Opsat.MUTE))
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
        near = [(guard_mood(g)[0],) + relative(intel['sam'], g['loc']) for g in intel['guards']]
        alert = [d for mood, d, _, dz in near if mood == 'ALERT' and d <= 40]
        if alert:
            return ('HOSTILE ALERT  %.0fm' % min(alert), RED)
        susp = [d for mood, d, _, dz in near if mood == 'SUSPICIOUS' and d <= RANGE_M and abs(dz) < 4]
        if susp:
            return ('GUARD SUSPICIOUS  %.0fm' % min(susp), AMBER)
        return alarm

    def draw(self, toast, threat=None, cheats=(False, False), sc=1.0):
        """HUD strip (bottom-left, panel closed): the worst live threat, GOD / INVISIBLE, the one-time notice."""
        rows = [('GOD MODE', hud.GREEN)] * cheats[0] + [('INVISIBLE', hud.BLUE)] * cheats[1]
        if threat:
            rows.insert(0, threat)
        if toast:
            rows.append(('OPSAT V1.2   \u25b2 TO OPEN', hud.GREEN))
        self.strip_img = hud.strip(rows, sc)
        self.strip_ver += 1

    def hide(self):
        if self.shown:
            self.strip.hide()
            self.shown, self.strip_at = False, None

    def detach(self):
        if self.mem:
            self.mem.close()
            self.say('game closed - waiting')
        self.mem = self.game = None
        self.dvorak.consolidate()  # game closed: save what was discussed
        self.runlog.close()
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
            m = self.panel.hints.get((mission or '').lower())
            if m and mission != self.chat_mission:  # a new mission starts a clean thread (reloads keep it)
                self.chat_mission = mission
                self.panel.chat[:] = [['event', 'Mission: ' + m['title'].split(' (')[0]]]
                self.queued, self.retry_q = [], None
        if mission and room and not self.announced:
            self.announced = True
            self.toast_until = time.monotonic() + 6  # "OPSAT V1.2 - up arrow" once per mission
        intel = self.game.intel()
        self.panel.intel = intel
        self.track_triggers(mission, room, objs, intel)
        self.runlog.tick(mission, room, objs, intel, guard_mood, relative,  # the training set (runs/)
                         cheats=cheats, stance=self.game.stance())
        threat = self.threat_chip(intel)
        self.panel.game_hwnd = hwnd
        self.pump_dvorak(mission)
        if self.panel.open and self.panel.TABS[self.panel.tab] == 'RADAR':
            self.panel.render()  # live radar
        if self.panel.update_state(mission, room, objs):
            self.panel.render()
            self.say('mission', mission, '| level', self.game.level_name, '| room', room,
                     '| objectives', [(o[0], o[1]) for o in objs])
            if intel:
                live = [g for g in intel['guards'] if guard_mood(g)[0] not in ('DEAD', 'OUT')]
                count = lambda key: sorted(__import__('collections').Counter(g[key] for g in live).items())
                self.say('guards | stress', count('stress'), '| events', count('event'))
        self.panel.place(hwnd, self.game_fg or self.panel.typing)
        toast = time.monotonic() < self.toast_until
        sc = (dock(hwnd)[5] if hwnd else 1.0) * hud.UI_SCALE
        if (toast, threat, cheats, sc) != self.hud:
            self.hud = (toast, threat, cheats, sc)
            self.draw(toast, threat, cheats, sc)
            if threat:
                self.say('threat:', threat[0])
        want = self.strip_img is not None and self.game_fg and not self.panel.open
        if want:
            dx, _, bottom = dock(hwnd)[:3]
            at = (self.strip_ver, dx, bottom)
            if at != self.strip_at:  # only push pixels when the strip or the game window changed
                self.strip_at = at
                self.strip.show(self.strip_img, dx, bottom - self.strip_img.height)
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
