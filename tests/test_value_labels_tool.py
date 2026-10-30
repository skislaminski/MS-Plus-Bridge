"""The developer's tool that reads the text the pedal shows for each value of a knob from the effect
files (tools/value_labels): its C674x interpreter on listings of TI's disassembler kept here – of
display functions written for these tests and compiled with TI's compiler, and of the function the
custom builds use – the reading of an effect file, the setting of the table's "labels"; and, where
dis6x and the effect files are on this Mac, the whole run on the real files (into a copy of the
table). Nothing here talks to a pedal."""
import contextlib
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import effect_names
from tools.value_labels import c6x, generate, listing, zd2

# show.c, compiled with TI's cl6x 8.5.0 (-mv6740 --abi=eabi -O2) and linked with its run-time
# library, listed by dis6x -d (from its TEXT section to main; the two division routines only by
# their labels: the interpreter does those itself):
#
#   static const char names[5][8] = {"Off", "Slow", "Mid", "Fast", "Wild"};
#   static const unsigned scale[4] = {1, 2, 5, 10};
#   void show_name(unsigned value, char *text, int length)    a name from a table (LDDW, STNDW)
#   { const char *name = names[value > 4 ? 4 : value]; int i; for (i = 0; i < 8; i++) text[i] = name[i]; }
#   void show_offset(int value, char *text, int length)       -12 .. +12 (a division by 10 as MPY32US)
#   { int shown = value - 12, i = 0, tens;
#     if (shown < 0) { text[i++] = '-'; shown = -shown; } else if (shown > 0) text[i++] = '+';
#     tens = shown / 10; if (tens) text[i++] = '0' + tens; text[i++] = '0' + shown % 10; text[i] = 0; }
#   void show_ms(unsigned value, char *text, int length)      1 .. 600, 610 .. (SPLOOPW, SPLOOP, the stack)
#   { unsigned ms = value < 600 ? value + 1 : 610 + (value - 600) * 10; char digits[8]; int n = 0, i = 0;
#     do { digits[n++] = '0' + ms % 10; ms /= 10; } while (ms); while (n) text[i++] = digits[--n]; text[i] = 0; }
#   void show_ratio(unsigned value, char *text, int length)   the run-time library's __divu and __remu
#   { unsigned step = scale[value & 3], whole = value * 10 / step, rest = value * 10 % step;
#     text[0] = '0' + whole % 10; text[1] = ':'; text[2] = '0' + rest; text[3] = 0; }
#   void show_hz(unsigned value, char *text, int length)      single precision
#   { float hz = 40.0f + (float)value * 2.5f; int tenths = (int)(hz * 10.0f), whole = tenths / 10;
#     text[0] = '0' + whole / 100; text[1] = '0' + whole / 10 % 10; text[2] = '0' + whole % 10;
#     text[3] = '.'; text[4] = '0' + tenths % 10; text[5] = 'H'; text[6] = 'z'; text[7] = 0; }
COMPILED = """\
TEXT Section .text (Little Endian), 0xa40 bytes at 0x00008000
00008000            show_ratio:
00008000   02c73428           MVK.S1        0xffff8e68,A5
00008004   02800069           MVKH.S1       0x0000,A5
00008008   01906f58 ||        AND.L1        3,A4,A3
0000800c       74ec           LDW.D1T2      *A5[A3],B6
0000800e       663a           SHL.S1        A4,0x3,A3
00008010   028c9a40           ADDAH.D1      A3,A4,A5
00008014       15c6           MV.L1X        B3,A8
00008016       a247           MV.L2         B4,B5
00008018       8347           MV.L2         B6,B4
0000801a       82c6 ||        MV.L1         A5,A4
0000801c   ed003000           .fphead       n, l, W, BU, nobr, nosat, 1101000b
00008020   1000c012 ||        CALLP.S2      __divu (PC+1536 = 0x00008620),B3
00008024            $C$RL0:
00008024   01e666a8           MVK.S1        0xffffcccd,A3
00008028   01e66668           MVKH.S1       0xcccc0000,A3
0000802c   03106630           MPY32U.M1     A3,A4,A7:A6
00008030       8347           MV.L2         B6,B4
00008032       2c6e           NOP           2
00008034   019c69a1           SHRU.S1       A7,0x3,A3
00008038   0328a358 ||        MVK.L1        10,A6
0000803c   e2000000           .fphead       n, l, W, BU, nobr, nosat, 0010000b
00008040   01986f80           MPYU.M1       A3,A6,A3
00008044       0c6e           NOP           1
00008046       89b0           SUB.L1        A4,A3,A3
00008048       5a32 ||        MVK.S1        58,A4
0000804a       22c5           STB.D2T1      A4,*B5[1]
0000804c   01801850 ||        ADDK.S1       48,A3
00008050   1000ec13           CALLP.S2      __c6xabi_remu (PC+1888 = 0x000087a0),B3
00008054       82c6 ||        MV.L1         A5,A4
00008056       02b5 ||        STB.D2T1      A3,*B5[0]
00008058            $C$RL2:
00008058   00a05362           BNOP.S2X      A8,2
0000805c   e4c00428           .fphead       n, l, W, BU, nobr, nosat, 0100110b
00008060       0627           MVK.L2        0,B4
00008062       72c5           STB.D2T2      B4,*B5[3]
00008064   0190dec0 ||        ADDAD.D1      A4,0x6,A3
00008068   019442b4           STB.D2T1      A3,*+B5[2]
0000806c            show_offset:
0000806c       ab32           MVK.S1        45,A6
0000806e       b256 ||        MV.D1X        B4,A5
00008070   001188d8 ||        CMPGT.L1      12,A4,A0
00008074   c3140235    [ A0]  STB.D1T1      A6,*+A5[0]
00008078   033333a9 ||        MVK.S1        0x6667,A6
0000807c   e12000c2           .fphead       n, l, W, BU, nobr, nosat, 0001001b
00008080   00119adb ||        CMPLT.L2X     12,A4,B0
00008084   02128058 ||        SUB.L1        A4,0xc,A4
00008088   03333369           MVKH.S1       0x66660000,A6
0000808c   c21000d8 || [ A0]  NEG.L1        A4,A4
00008090   0310c670           MPY32US.M1    A6,A4,A7:A6
00008094   0213fda2           SHR.S2X       A4,0x1f,B4
00008098   d28015aa    [!A0]  MVK.S2        0x002b,B5
0000809c   c000a35a    [ A0]  MVK.L2        0,B0
000080a0       4fea           SHR.S1        A7,0x2,A6
000080a2       da70           SUB.L1X       A6,B4,A7
000080a4       25a6           MVK.L1        1,A3
000080a6       01ef ||        BNOP.S2       B3,0
000080a8   039d4c00 ||        MPY.M1        10,A7,A7
000080ac   22940237    [ B0]  STB.D1T2      B5,*+A5[0]
000080b0   0290d2fb ||        SUB.L2X       A6,B4,B5
000080b4       49e6 || [!A0]  MVK.L1        0,A3
000080b6       8bca           SUB.S1        A4,A7,A4
000080b8   02801853 ||        ADDK.S2       48,B5
000080bc   e460080c           .fphead       n, l, W, BU, nobr, nosat, 0100011b
000080c0       d668 ||        CMPEQ.L1X     A6,B4,A0
000080c2       a9f6 || [ B0]  MVK.D1        1,A3
000080c4   02001851           ADDK.S1       48,A4
000080c8   d28caa37 || [!A0]  STB.D1T2      B5,*+A3[A5]
000080cc   d18c2058 || [!A0]  ADD.L1        1,A3,A3
000080d0   020caa35           STB.D1T1      A4,*+A3[A5]
000080d4       25c0 ||        ADD.L1        A3,1,A4
000080d6       0192 ||        MVK.S1        0,A3
000080d8   0190aa34           STB.D1T1      A3,*+A4[A5]
000080dc   e4200401           .fphead       n, l, W, BU, nobr, nosat, 0100001b
000080e0            show_name:
000080e0   00108bd9           CMPLTU.L1     0x4,A4,A0
000080e4   02c72029 ||        MVK.S1        0xffff8e40,A5
000080e8   01808040 ||        MVK.D1        4,A3
000080ec   d1900fd9    [!A0]  MV.L1         A4,A3
000080f0   02800068 ||        MVKH.S1       0x0000,A5
000080f4       81ef           BNOP.S2       B3,4
000080f6       64cc ||        LDDW.D1T1     *A5[A3],A5:A4
000080f8   029003f4           STNDW.D2T1    A5:A4,*+B4[0]
000080fc   e4040400           .fphead       n, l, DW/NDW, W, nobr, nosat, 0100000b
00008100            show_ms:
00008100   0004a359           MVK.L1        1,A0
00008104   01906ca1 ||        SHL.S1        A4,0x3,A3
00008108   02812c2a ||        MVK.S2        0x0258,B5
0000810c   01902059           ADD.L1        1,A4,A3
00008110   0010b9fb ||        CMPGTU.L2X    B5,A4,B0
00008114   020c9a41 ||        ADDAH.D1      A3,A4,A4
00008118   03757929 ||        MVK.S1        0xffffeaf2,A6
0000811c   00000000 ||        NOP
00008120            $C$L1:
00008120   c283e001    [ A0]  SPLOOPW       6
00008124   02e666a9 ||        MVK.S1        0xffffcccd,A5
00008128   31988840 || [!B0]  ADD.D1        A6,A4,A3
0000812c            $C$L2:
0000812c       2d67           SPMASK        L1,S1
0000812e       c1c6 ||^       MV.L1         A3,A6
00008130   02e66668 ||^       MVKH.S1       0xcccc0000,A5
00008134   0818a630           MPY32U.M1     A5,A6,A17:A16
00008138       0c6e           NOP           1
0000813a       ac66           SPMASK        D2
0000813c   e90020c0           .fphead       n, l, W, BU, nobr, nosat, 1001000b
00008140       8cf7 ||^       SUBAW.D2      B15,0x4,B15
00008142       2d66           SPMASK        S1
00008144   04001828 ||^       MVK.S1        0x0030,A8
00008148       6ee7           SPMASK        L1,L2,S2,D1
0000814a       c24f ||^       MV.S2         B4,B6
0000814c   04c469a1 ||        SHRU.S1       A17,0x3,A9
00008150   03bd1059 ||^       ADD.L1X       8,B15,A7
00008154   02014041 ||^       MVK.D1        10,A4
00008158       0627 ||^       MVK.L2        0,B4
0000815a       c486           MV.L1         A9,A6
0000815c   e8a02032           .fphead       n, l, W, BU, nobr, nosat, 1000101b
00008160   04912f80 ||        MPYU.M1       A9,A4,A9
00008164   00180fd8           MV.L1         A6,A0
00008168   01a460f8           SUB.L1        A3,A9,A3
0000816c   048d0079           ADD.L1        A8,A3,A9
00008170   019806a0 ||        MV.S1         A6,A3
00008174   049c3634           STB.D1T1      A9,*A7++[1]
00008178       1c66           SPKERNEL      0,0
0000817a       2641 ||        ADD.L2        B4,1,B4
0000817c   e8001000           .fphead       n, l, W, BU, nobr, nosat, 1000000b
00008180            $C$L3:
00008180       0247           MV.L2         B4,B0
00008182       a34f ||        MV.S2         B6,B5
00008184   03800042 ||        MVK.D2        0,B7
00008188   3012a121    [!B0]  BNOP.S1       $C$L6 (PC+36 = 0x000081a4),5
0000818c   268003a2 || [ B0]  MVC.S2        B0,ILC
00008190            $C$L4:
00008190       0c66           SPLOOP        1
00008192       83ce ||        MV.S1         A7,A4
00008194            $C$L5:
00008194       4e3c           LDB.D1T1      *--A4[1],A3
00008196       2e66           SPMASK        S2
00008198       e04f ||^       MV.S2         B0,B7
0000819a       91c7 ||        MV.L2X        A3,B4
0000819c   ee311903           .fphead       p, l, W, B, nobr, nosat, 1110001b
000081a0       dc66           SPKERNEL      6,0
000081a2       1ec5 ||        STB.D2T2      B4,*B5++[1]
000081a4            $C$L6:
000081a4            $C$L7:
000081a4       61ef           BNOP.S2       B3,3
000081a6       05a6           MVK.L1        0,A3
000081a8       8c77 ||        ADDAW.D2      B15,0x4,B15
000081aa       c7b5           STB.D2T1      A3,*B7[B6]
000081ac            show_hz:
000081ac   01900938           INTSPU.L1     A4,A3
000081b0   0200a358           MVK.L1        0,A4
000081b4   02201068           MVKH.S1       0x40200000,A4
000081b8   0280a35a           MVK.L2        0,B5
000081bc   e0e00009           .fphead       n, l, W, BU, nobr, nosat, 0000111b
000081c0   018c8e00           MPYSP.M1      A4,A3,A3
000081c4   02a1106a           MVKH.S2       0x42200000,B5
000081c8   02b333a8           MVK.S1        0x6667,A5
000081cc   02b33368           MVKH.S1       0x66660000,A5
000081d0   00000000           NOP
000081d4   030cb21a           ADDSP.L2X     B5,A3,B6
000081d8   0280a35a           MVK.L2        0,B5
000081dc   02a0906a           MVKH.S2       0x41200000,B5
000081e0   00000000           NOP
000081e4   0298ae02           MPYSP.M2      B5,B6,B5
000081e8   00004000           NOP           3
000081ec   0394017a           SPTRUNC.L2    B5,B7
000081f0   00006000           NOP           4
000081f4   031cb670           MPY32US.M1X   A5,B7,A7:A6
000081f8   029feda2           SHR.S2        B7,0x1f,B5
000081fc   00002000           NOP           2
00008200       4fba           SHR.S1        A7,0x2,A3
00008202       7ac0           SUB.L1X       A3,B5,A4
00008204   0193eda0           SHR.S1        A4,0x1f,A3
00008208   03146b01           MPY32SU.M1    A3,A5,A7:A6
0000820c   0280a35a ||        MVK.L2        0,B5
00008210   0810bb03           MPY32SU.M2X   B5,A4,B17:B16
00008214   0410a630 ||        MPY32U.M1     A5,A4,A9:A8
00008218   0413fda2           SHR.S2X       A4,0x1f,B8
0000821c   e0200000           .fphead       n, l, W, BU, nobr, nosat, 0000001b
00008220   02c28faa           MVK.S2        0xffff851f,B5
00008224   02a8f5ea           MVKH.S2       0x51eb0000,B5
00008228   031a107a           ADD.L2X       B16,A6,B6
0000822c   0324d07a           ADD.L2X       B6,A9,B6
00008230       4723           SHR.S2        B6,0x2,B6
00008232       05a6           MVK.L1        0,A3
00008234   090cb673 ||        MPY32US.M2X   B5,A3,B19:B18
00008238   0320c0fa ||        SUB.L2        B6,B8,B6
0000823c   e2000200           .fphead       n, l, W, BU, nobr, nosat, 0010000b
00008240   0810b633           MPY32U.M2X    B5,A4,B17:B16
00008244   03106b00 ||        MPY32SU.M1    A3,A4,A7:A6
00008248   0418b670           MPY32US.M1X   A5,B6,A9:A8
0000824c   049beda2           SHR.S2        B6,0x1f,B9
00008250   02802428           MVK.S1        0x0048,A5
00008254   01c8d078           ADD.L1X       A6,B18,A3
00008258   03a44da1           SHR.S1        A9,0x2,A7
0000825c   03447078 ||        ADD.L1X       A3,B17,A6
00008260       a255           STB.D2T1      A5,*B4[5]
00008262       cab3 ||        MVK.S2        46,B5
00008264   01914c01 ||        MPY.M1        10,A4,A3
00008268   02a4f0f9 ||        SUB.L1X       A7,B9,A5
0000826c       a722 ||        SHR.S1        A6,0x5,A6
0000826e       7255           STB.D2T2      B5,*B4[3]
00008270   02994c03 ||        MPY.M2        10,B6,B5
00008274   0320d0f9 ||        SUB.L1X       A6,B8,A6
00008278   04803d2a ||        MVK.S2        0x007a,B9
0000827c   e1200083           .fphead       n, l, W, BU, nobr, nosat, 0001001b
00008280   02954c01           MPY.M1        10,A5,A5
00008284   0490c2b7 ||        STB.D2T2      B9,*+B4[6]
00008288   018cf2f9 ||        SUB.L1X       B7,A3,A3
0000828c   03001851 ||        ADDK.S1       48,A6
00008290       01ef ||        BNOP.S2       B3,0
00008292       0265           STB.D2T1      A6,*B4[0]
00008294   01801850 ||        ADDK.S1       48,A3
00008298       8235           STB.D2T1      A3,*B4[4]
0000829a       9ab0 ||        SUB.L1X       A4,B5,A3
0000829c   ea003200           .fphead       n, l, W, BU, nobr, nosat, 1010000b
000082a0       07a7 ||        MVK.L2        0,B7
000082a2       dad1           SUB.L2X       B6,A5,B5
000082a4   01801851 ||        ADDK.S1       48,A3
000082a8       f275 ||        STB.D2T2      B7,*B4[7]
000082aa       4235           STB.D2T1      A3,*B4[2]
000082ac   02801852 ||        ADDK.S2       48,B5
000082b0   029022b6           STB.D2T2      B5,*+B4[1]
00008620            __divu:
00008620            __c6xabi_divu:
000087a0            __c6xabi_remu:
000087a0            __remu:
"""
COMPILED_CONST = zd2.Segment(".const", 0x8E40, b"".join(name.ljust(8, b"\0") for name in (b"Off", b"Slow", b"Mid", b"Fast", b"Wild"))
                             + struct.pack("<4I", 1, 2, 5, 10), True, False)

# The display function of a custom build (stomphacks' generator; DrumBox's Pattern, as dis6x lists
# DRUMBOX.ZD2): copies 8 bytes from a table of texts in .const, the value capped at the highest.
CUSTOM = """\
TEXT Section .text (Little Endian), 0x3e40 bytes at 0x00000000
000039b4   00000828           MVK.S1        0x0010,A0
000039b8   008089f8           CMPGTU.L1     A4,A0,A1
000039bc   0285302a           MVK.S2        0x0a60,B5
000039c0   02c0006a           MVKH.S2       0x80000000,B5
000039c4   82000fd8    [ A1]  MV.L1         A0,A4
000039c8   02106ca0           SHL.S1        A4,0x3,A4
000039cc   0290b07a           ADD.L2X       B5,A4,B5
000039d0   029402e4           LDW.D2T1      *+B5[0],A5
000039d4   039422e4           LDW.D2T1      *+B5[1],A7
000039d8   00008000           NOP           5
000039dc   029002f4           STW.D2T1      A5,*+B4[0]
000039e0   039022f4           STW.D2T1      A7,*+B4[1]
000039e4   008ca362           BNOP.S2       B3,5
"""
PATTERNS = ["Rock", "Rock2", "HalfT", "Disco", "House", "Funk", "Funk2", "Shufl", "1Drop", "Metal", "Boom",
            "Slow", "Bossa", "Punk", "Waltz", "Jazz", "Click"]
CUSTOM_CONST = zd2.Segment(".const", 0x80000A60, b"".join(text.encode().ljust(8, b"\0") for text in PATTERNS), True, False)

# Made by hand in dis6x's format: a function that writes the low byte of A10, which the call does
# not set, and one that writes nothing.
UNSET = """\
TEXT Section .text (Little Endian), 0x20 bytes at 0x00000000
00000000   05100274           STB.D2T1      A10,*+B4[0]
00000004   008ca362           BNOP.S2       B3,5
"""
NOTHING = """\
TEXT Section .text (Little Endian), 0x20 bytes at 0x00000000
00000000   008ca362           BNOP.S2       B3,5
"""


def show_hz(value: int) -> str:
    """show_hz in Python, rounding to single precision where the C674x does."""
    single = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
    tenths = int(single(single(40.0 + single(float(value) * 2.5)) * 10.0))
    return f"{tenths // 1000}{tenths // 100 % 10}{tenths // 10 % 10}.{tenths % 10}Hz"


class Interpreter(unittest.TestCase):
    def setUp(self):
        self.code = listing.Listing(COMPILED)

    def texts(self, function: str, highest: int) -> list:
        return c6x.texts(self.code, [COMPILED_CONST], self.code.address[function], highest)

    def test_the_custom_builds_function_copies_its_table_and_caps_the_value(self):
        code = listing.Listing(CUSTOM)
        self.assertEqual(c6x.texts(code, [CUSTOM_CONST], 0x39B4, 16), PATTERNS)
        self.assertEqual(c6x.CPU(code, [CUSTOM_CONST]).display(0x39B4, 40), "Click")

    def test_compiled_functions_give_what_their_c_gives(self):
        self.assertEqual(self.texts("show_name", 6), ["Off", "Slow", "Mid", "Fast", "Wild", "Wild", "Wild"])
        self.assertEqual(self.texts("show_offset", 24), [f"{value - 12:+d}".replace("+0", "0") for value in range(25)])
        steps = [1, 2, 5, 10]
        self.assertEqual(self.texts("show_ratio", 40), [f"{value * 10 // steps[value & 3] % 10}:{chr(48 + value * 10 % steps[value & 3])}"
                                                        for value in range(41)])
        self.assertEqual(self.texts("show_hz", 300), [show_hz(value) for value in range(301)])

    def test_software_pipelined_loops_and_the_stack(self):
        texts = self.texts("show_ms", 962)                 # as the Delay's Time: 1 ms steps, then 10 ms
        self.assertEqual(texts, [str(value + 1 if value < 600 else 610 + (value - 600) * 10) for value in range(963)])
        self.assertEqual(texts[599:601], ["600", "610"])

    def test_the_listing_is_read_into_execute_packets(self):
        packet = self.code.eps[self.code.at[self.code.address["show_name"]]]
        self.assertEqual([instruction.op for instruction in packet], ["CMPLTU", "MVK", "MVK"])     # in parallel
        load = next(i for ep in self.code.eps for i in ep if i.addr == 0x8194)
        self.assertEqual((load.op, load.unit, load.operands, load.protected), ("LDB", "D1T1", ["*--A4[1]", "A3"], True))
        branch = next(i for ep in self.code.eps for i in ep if i.addr == 0x8188)
        self.assertEqual((branch.predicate, branch.negated, branch.target), ("B0", True, 0x81A4))
        self.assertEqual(self.code.labels[0x8620], ["__divu", "__c6xabi_divu"])

    def test_what_is_not_modelled_is_refused(self):
        line = "SUB.L1        A4,0xc,A4"
        self.assertIn(line, COMPILED)
        code = listing.Listing(COMPILED.replace(line, "SADD.L1       A4,0xc,A4"))
        with self.assertRaisesRegex(c6x.NotModelled, "SADD"):
            c6x.texts(code, [COMPILED_CONST], code.address["show_offset"], 24)
        with self.assertRaisesRegex(c6x.NotModelled, "outside the stack and the text buffer"):   # a store into .const
            c6x.CPU(self.code, [COMPILED_CONST]).call(self.code.address["show_name"], {"A4": 0, "B4": 0x8E40, "A6": 16})
        for written in (COMPILED_CONST._replace(loaded=False), COMPILED_CONST._replace(writable=True)):   # .far, .fardata
            with self.assertRaisesRegex(c6x.NotModelled, "which the effect writes while it runs"):
                c6x.texts(self.code, [written], self.code.address["show_name"], 1)

    def test_a_text_that_depends_on_what_the_call_does_not_set_is_refused(self):
        with self.assertRaisesRegex(c6x.NotModelled, "depends on what the call does not set"):
            c6x.texts(listing.Listing(UNSET), [], 0, 0)
        with self.assertRaisesRegex(c6x.NotModelled, "no NUL"):
            c6x.texts(listing.Listing(NOTHING), [], 0, 0)


def entry(name: str, highest: int, words: dict = None, last: bool = False) -> bytes:
    """One entry of a parameter table: name, highest value, the words given, the flag of the last."""
    data = [0] * 14
    data[zd2.HIGHEST] = highest
    for at, value in (words or {}).items():
        data[at] = value
    if last:
        data[zd2.FLAGS] |= zd2.LAST
    return name.encode().ljust(12, b"\0") + struct.pack("<11I", *data[3:])


def elf(sections) -> bytes:
    """A little-endian ELF32 with those sections [(name, type, flags, address, bytes)] and .shstrtab
    (of a NOBITS section the bytes give only its size)."""
    names = b"\0" + b"".join(name.encode() + b"\0" for name, *_ in sections) + b".shstrtab\0"
    body = bytearray(52)
    offsets = []
    for _, kind, _, _, data in sections:
        offsets.append(len(body))
        body += data if kind != zd2.NOBITS else b""
    names_at = len(body)
    body += names
    headers, name_at = [bytes(40)], 1
    for (name, kind, flags, addr, data), offset in zip(sections, offsets):
        headers.append(struct.pack("<10I", name_at, kind, flags, addr, offset, len(data), 0, 0, 4, 0))
        name_at += len(name) + 1
    headers.append(struct.pack("<10I", name_at, 3, 0, 0, names_at, len(names), 0, 0, 1, 0))
    body[:4] = b"\x7fELF"
    struct.pack_into("<I", body, 0x20, len(body))
    struct.pack_into("<HHH", body, 0x2E, 40, len(headers), len(headers) - 1)
    return bytes(body) + b"".join(headers)


def effect_file(effect_id: int, name: str, knobs, program: bytes = b"") -> bytes:
    """An effect file: header with id and name, chunk DATA with the ELF, then the knobs' table as
    effect_names.read_zd2 finds it (after the ELF, as in the real files)."""
    header = bytes(88) + struct.pack("<I", effect_id) + name.encode().ljust(11, b"\0")
    data = b"ZDLF" + struct.pack("<I", len(header)) + header + b"DATA" + struct.pack("<I", len(program)) + program
    table = entry("OnOff", 1) + entry(name, 0xFFFFFFFF)
    table += b"".join(entry(knob, highest, last=index == len(knobs) - 1) for index, (knob, highest) in enumerate(knobs))
    return data + table + bytes(56)


class EffectFile(unittest.TestCase):
    def test_the_parameter_table_ends_at_the_entry_flagged_last(self):
        table = (entry("OnOff", 1) + entry("HoldDelay", 0xFFFFFFFF) + entry("Time", 962, {zd2.DISPLAY: 0x1234})
                 + entry("Dummy", 0, last=True) + entry("2", 20))           # what follows is not a knob
        program = elf([(".text", zd2.PROGBITS, 6, 0, bytes(64)), (".const", zd2.PROGBITS, 2, 0x80000000, table + bytes(8)),
                       (".far", zd2.NOBITS, 3, 0x80001000, bytes(16))])
        parsed = zd2.elf_of(effect_file(0x0A000070, "HoldDelay", [], program))
        self.assertEqual([(found.name, found.words[zd2.HIGHEST], found.words[zd2.DISPLAY]) for found in zd2.parameter_table(parsed)],
                         [("OnOff", 1, 0), ("HoldDelay", 0xFFFFFFFF, 0), ("Time", 962, 0x1234), ("Dummy", 0, 0)])
        self.assertEqual([(segment.name, segment.addr, segment.loaded, segment.writable) for segment in parsed.segments()],
                         [(".text", 0, True, False), (".const", 0x80000000, True, False), (".far", 0x80001000, False, True)])
        self.assertIsNone(zd2.elf_of(b"ZDLF" + bytes(200)))

    def test_dis6x_is_taken_as_given_else_from_the_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder, "dis6x")
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o755)
            with mock.patch.dict(os.environ, {"TI_CGT_DIS6X": str(tool)}):
                self.assertEqual(listing.dis6x_path(), str(tool))
                self.assertIsNone(listing.dis6x_path(str(Path(folder, "missing"))))    # given: not looked past
            with mock.patch.dict(os.environ, {"TI_CGT_DIS6X": str(Path(folder, "missing"))}):
                self.assertIsNone(listing.dis6x_path())


class Labels(unittest.TestCase):
    def test_only_knobs_that_show_text_get_it_without_padding_and_with_the_note_characters(self):
        self.assertEqual(generate.labels_of({
            2: [" Rock", "Rock2 "],                 # padded as the code writes it
            3: ["  0", " 1", "2"],                  # the number: no labels
            4: ["\x19 3", "\x18.", "\x19x2"],       # 1/8T, 1/8., 1/4x2 as the pedal's characters
            5: ["1", "2"]}),                        # a number, but not the value
            {"2": ["Rock", "Rock2"], "4": ["\x19 3", "\x18.", "\x19x2"], "5": ["1", "2"]})

    def test_of_several_files_the_tables_build_else_the_newest_and_no_intermediates(self):
        with tempfile.TemporaryDirectory() as folder:
            files = {"a/DRUMBOX.ZD2": (b"x" * 10, 1), "b/DrumBox.zd2": (b"x" * 20, 3), "b/DRUMBOX_REBUILT.ZD2": (b"x", 5),
                     "b/drumbox_template.ZD2": (b"x", 5)}
            for name, (data, when) in files.items():
                path = Path(folder, name)
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(data)
                os.utime(path, (when, when))
            found = generate.effect_files([folder])
            self.assertEqual(list(found), ["DRUMBOX.ZD2"])
            self.assertEqual([path.parent.name for path in found["DRUMBOX.ZD2"]], ["b", "a"])     # newest first
            self.assertEqual(generate.choose(found["DRUMBOX.ZD2"], 10).parent.name, "a")
            self.assertEqual(generate.choose(found["DRUMBOX.ZD2"], 99).parent.name, "b")

    def run_update(self, table: dict, files: dict, texts: dict) -> tuple:
        """update() over effect files {name: (id, effect, knobs)} whose functions give texts
        {effect: (texts, failed)} (instead of running dis6x and the interpreter)."""
        def value_texts(data, dis6x):
            read = effect_names.read_zd2(data)
            return (read["knobs"],) + texts[read["name"]]
        said = []
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(generate, "value_texts", value_texts):
            for name, (effect_id, effect, knobs) in files.items():
                Path(folder, name).write_bytes(effect_file(effect_id, effect, knobs))
            done = generate.update(table, [folder], "dis6x", said.append)
        return done, said

    def test_the_table_gets_the_texts_of_its_effects_files(self):
        table = {
            "09000F8A": {"name": "DrumBox", "knobs": [["Pattern", 1], ["Level", 2], ["Fill", 1], ["Swing", 1]], "file": "DRUMBOX.ZD2"},
            "0A000010": {"name": "Delay", "knobs": [["Time", 2]], "file": "DELAY_M.ZD2", "labels": {"2": ["1", "2", "3"]}},
            "0A000020": {"name": "AnalogDly", "knobs": [["Time", 2]], "file": "ANDLY_M.ZD2", "labels": {"2": ["a", "b", "c"]}},
            "01000010": {"name": "DYN Comp", "knobs": [["Sense", 1]], "file": "DCOMP.ZD2", "labels": {"2": ["x", "y"]}}}
        files = {"DRUMBOX.ZD2": (0x09000F8A, "DrumBox", [("Pattern", 1), ("Level", 2), ("Fill", 1), ("Swing", 1)]),
                 "DELAY_M.ZD2": (0x0A000010, "Delay", [("Time", 2), ("Mode", 2)]),        # another build: other knobs
                 "DCOMP.ZD2": (0x01000010, "DYN Comp", [("Sense", 1)])}
        texts = {"DrumBox": ({2: ["Rock ", " Funk"], 3: ["0", " 1", "2"], 5: ["1", "2"]}, {4: "instruction SADD is not modelled"}),
                 "DYN Comp": ({2: ["0", "1"]}, {})}
        done, said = self.run_update(table, files, texts)
        self.assertEqual(table["09000F8A"]["labels"], {"2": ["Rock", "Funk"], "5": ["1", "2"]})   # Fill failed: none
        self.assertEqual(table["0A000010"]["labels"], {"2": ["1", "2", "3"]})        # other knobs: as it was
        self.assertEqual(table["0A000020"]["labels"], {"2": ["a", "b", "c"]})        # its file is not there: as it was
        self.assertNotIn("labels", table["01000010"])                                # the number after all
        self.assertEqual((done["effects"], done["labelled"], done["knobs"], done["missing"]), (2, 1, 2, 1))
        self.assertEqual(done["failed"], [("09000F8A DrumBox (DRUMBOX.ZD2)", "Fill", "instruction SADD is not modelled")])
        self.assertEqual([effect for effect, _ in done["skipped"]], ["0A000010 Delay (DELAY_M.ZD2)"])
        self.assertTrue(any("Fill" in line for line in said) and any("other knobs" in line for line in said))

    def test_the_command_writes_the_table_unless_it_is_a_dry_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "zoom_effects.json")
            path.write_text(json.dumps({"09000F8A": {"name": "DrumBox", "knobs": [["Pattern", 1]], "file": "DRUMBOX.ZD2"}}))
            Path(folder, "DRUMBOX.ZD2").write_bytes(effect_file(0x09000F8A, "DrumBox", [("Pattern", 1)]))
            value_texts = mock.Mock(return_value=([["Pattern", 1]], {2: ["Rock", "Funk"]}, {}))
            with mock.patch.object(generate, "value_texts", value_texts), \
                    mock.patch.object(listing, "dis6x_path", return_value="dis6x"), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(generate.main(["--dry-run", "--table", str(path), folder]), 0)
                self.assertNotIn("labels", path.read_text())
                self.assertEqual(generate.main(["--table", str(path), folder]), 0)
            self.assertIn("1 knobs of 1 effects show text", out.getvalue())
            self.assertEqual(json.loads(path.read_text())["09000F8A"]["labels"], {"2": ["Rock", "Funk"]})
            with mock.patch.object(listing, "dis6x_path", return_value=None), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(generate.main(["--table", str(path), folder]), 1)


PROTOTYP = Path.home() / "Desktop" / "PROTOTYP"
FOLDERS = [PROTOTYP / "programme" / "installer" / "daten" / "Backups", PROTOTYP / "programme" / "installer" / "daten" / "Adapted",
           PROTOTYP / "referenz" / "MS EFFECTS", PROTOTYP / "prototypes" / "install_all"]
NOTES = {chr(code) for code in range(0x16, 0x1B)}         # the pedal's note characters 1/32 .. 1/2


@unittest.skipUnless(listing.dis6x_path() and all(folder.is_dir() for folder in FOLDERS),
                     "needs TI's dis6x and the effect files in ~/Desktop/PROTOTYP")
class RealFiles(unittest.TestCase):
    """The tool on the real effect files (read only), into a copy of zoom_effects.json."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.path = Path(cls.folder.name, "zoom_effects.json")
        shutil.copyfile(effect_names.TABLE, cls.path)
        with mock.patch.object(effect_names, "write_table", wraps=effect_names.write_table) as written, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            cls.code = generate.main(["--table", str(cls.path)] + [str(folder) for folder in FOLDERS])
        cls.written = [call.args[1] for call in written.call_args_list]
        cls.output = out.getvalue()
        cls.table = json.loads(cls.path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def by_file(self, name: str) -> dict:
        return next(entry for entry in self.table.values() if entry.get("file") == name)

    def test_it_writes_the_copy_only_and_no_knob_fails(self):
        self.assertEqual((self.code, self.written), (0, [self.path]))
        self.assertIn(" 0 knobs failed", self.output)
        self.assertNotIn("failed:", self.output)

    def test_drumbox(self):
        labels = self.by_file("DRUMBOX.ZD2")["labels"]
        self.assertEqual(labels["2"], PATTERNS)
        self.assertEqual(labels["12"], ["Off", "4Bar", "8Bar", "16Bar"])
        self.assertEqual([labels["3"][0]] + labels["3"][-2:], ["40", "250", "Sync"])

    def test_the_delays_time_in_ms_then_note_values(self):
        delay = self.table["0A000010"]["labels"]
        time = delay["2"]
        self.assertEqual((len(time), time[0], time[599], time[600], time[939]), (963, "1", "600", "610", "4000"))
        self.assertEqual((time[940], time[941], time[962]), ("\x17", "\x19 3", "\x19x16"))     # 1/16, 1/8T, 1/4×16
        self.assertEqual(delay["3"], ["SHORT", "LONG", "BPM SYNC"])

    def test_floating_point_and_offsets(self):
        solid = self.table["04000060"]["labels"]                        # SolidPre: Hz with floating point
        self.assertEqual((solid["5"][0], solid["5"][-1], solid["7"][0], solid["7"][-1]), ("40Hz", "600Hz", "1.5kHz", "22.0kHz"))
        self.assertEqual(self.by_file("BA_GEQ1U.ZD2")["labels"]["2"][::24], ["-12.0", "0.0", "12.0"])

    def test_every_list_has_a_text_for_each_value_as_the_pedal_shows_it(self):
        lists = [(key, param, texts) for key, entry in self.table.items() for param, texts in entry.get("labels", {}).items()]
        self.assertGreater(len(lists), 200)
        for key, param, texts in lists:
            highest = self.table[key]["knobs"][int(param) - effect_names.FIRST_KNOB][1]
            self.assertEqual(len(texts), highest + 1, (key, param))
            self.assertNotEqual(texts, [str(value) for value in range(highest + 1)], (key, param))
            for text in texts:
                self.assertEqual(text, text.strip(" "), (key, param))
                self.assertTrue(all(" " <= char <= "~" or char in NOTES for char in text), (key, param, text))


if __name__ == "__main__":
    unittest.main()
