#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
gen-bdf.py - WCN3990 ath10k board-2.bin regulatory-domain generator

Qualcomm WCN3990 ships a per-board "bdwlan" data blob inside the ath10k
board-2.bin container. Each blob carries a regulatory-domain word and a
16-bit integrity checksum:

  * regdomain : u32 LE at blob offset 0x14. Behaviour verified on WCN3990 hardware:
                COUNTRY_ERD = 0x8000 | ISO-3166-1 numeric (IT=380->0x817C, CN=156->0x809C);
                              locks the phy to that country (e.g. IT => DFS-ETSI, ch100-140).
                world       = 0x0000006C (ath world-roaming SKU, is_wwr_sku): phy lands on
                              'country 99 / DFS-UNSET', 5GHz passive-scan -- region-safe
                              everywhere and honors the userspace-set country. SHIPPED DEFAULT.
                NOTE: 0x00000000 is NOT "world" here -- ath maps it to US/DFS-FCC (active
                      ch100-140 at US power, illegal in the EU on UNII-3). Do not ship global.
  * checksum  : u16 LE at blob offset 0x0A, chosen so the XOR of every u16 LE
                word over the whole blob equals 0xFFFF (whole-blob XOR invariant).
                The WLAN firmware rejects the blob (re-init loop) if this breaks,
                which is why a naive regdomain byte-patch fails: the checksum must
                be recomputed. (Checksum algorithm family: testuser7/ath_bdf_tool.)

The upstream generic board-2.bin only contains a fallback image
'bus=snoc,qmi-board-id=ff' whose regdomain is 0x809C (COUNTRY_ERD|China). This
tool keeps that fallback intact and ADDS a device-specific image named
'bus=snoc,qmi-board-id=ff,qmi-chip-id=<id>,variant=<name>' with the regdomain of
your choice (checksum recomputed). ath10k selects it when the device-tree sets
  qcom,ath10k-calibration-variant = "<name>";

Container (board-2.bin) format mirrors qca-swiss-army-knife ath10k-bdencoder.
"""
import sys, struct, argparse, os

SIGNATURE = b"QCA-ATH10K-BOARD"
SIG_TOTAL = 20
PADDING_MAGIC = 0x6d
IE_BOARD, IE_BOARD_EXT = 0, 1
IE_NAME, IE_DATA = 0, 1
REGDOMAIN_OFFSET = 0x14      # u32 LE inside each bdwlan blob
CHECKSUM_OFFSET  = 0x0a      # u16 LE checksum word inside each bdwlan blob
XOR_INVARIANT    = 0xffff    # XOR of all u16 LE words over the whole blob
COUNTRY_ERD_FLAG = 0x8000
DEFAULT_BOARD_ID = "ff"
DEFAULT_CHIP_ID  = "140"

WORLD_TOKENS = {
    "world":      0x0000006c,   # GLOBAL DEFAULT: ath world-roaming SKU 0x6C (is_wwr_sku);
                                #   phy 'country 99 / DFS-UNSET', region-safe, honors userspace country
    "world-roam": 0x0000006c,
    "world-6c":   0x0000006c,
    "world-zero": 0x00000000,   # CTRY_DEFAULT; WARNING: ath maps this to US/DFS-FCC, NOT world
}

# ISO-3166-1 alpha2 -> numeric, extracted from the kernel ath CountryCode enum
# + allCountries[] table (drivers/net/wireless/ath/regd*.h). Only these are
# accepted by the ath regulatory core, so only these yield a valid COUNTRY_ERD.
COUNTRIES = {
    'AE': 784,
    'AL': 8,
    'AM': 51,
    'AN': 530,
    'AR': 32,
    'AT': 40,
    'AU': 36,
    'AW': 533,
    'AZ': 31,
    'BA': 70,
    'BB': 52,
    'BD': 50,
    'BE': 56,
    'BG': 100,
    'BH': 48,
    'BL': 5002,
    'BM': 60,
    'BN': 96,
    'BO': 68,
    'BR': 76,
    'BS': 44,
    'BY': 112,
    'BZ': 84,
    'CA': 124,
    'CH': 756,
    'CL': 152,
    'CN': 156,
    'CO': 170,
    'CR': 188,
    'CS': 891,
    'CY': 196,
    'CZ': 203,
    'DE': 276,
    'DK': 208,
    'DO': 214,
    'DZ': 12,
    'EC': 218,
    'EE': 233,
    'EG': 818,
    'ES': 724,
    'FI': 246,
    'FR': 250,
    'GB': 826,
    'GD': 308,
    'GE': 268,
    'GL': 304,
    'GR': 300,
    'GT': 320,
    'GU': 316,
    'HK': 344,
    'HN': 340,
    'HR': 191,
    'HT': 332,
    'HU': 348,
    'ID': 360,
    'IE': 372,
    'IL': 376,
    'IN': 356,
    'IR': 364,
    'IS': 352,
    'IT': 380,
    'JM': 388,
    'JO': 400,
    'JP': 392,
    'K2': 411,
    'K3': 412,
    'K4': 413,
    'KE': 404,
    'KH': 116,
    'KP': 408,
    'KR': 410,
    'KW': 414,
    'KZ': 398,
    'LB': 422,
    'LI': 438,
    'LK': 144,
    'LT': 440,
    'LU': 442,
    'LV': 428,
    'MA': 504,
    'MC': 492,
    'ME': 499,
    'MK': 807,
    'MO': 446,
    'MT': 470,
    'MU': 480,
    'MX': 484,
    'MY': 458,
    'NI': 558,
    'NL': 528,
    'NO': 578,
    'NP': 524,
    'NZ': 554,
    'OM': 512,
    'PA': 591,
    'PE': 604,
    'PG': 598,
    'PH': 608,
    'PK': 586,
    'PL': 616,
    'PR': 630,
    'PS': 842,
    'PT': 620,
    'PY': 600,
    'QA': 634,
    'RO': 642,
    'RS': 688,
    'RU': 643,
    'SA': 682,
    'SE': 752,
    'SG': 702,
    'SI': 705,
    'SK': 703,
    'SV': 222,
    'SY': 760,
    'TH': 764,
    'TN': 788,
    'TR': 792,
    'TT': 780,
    'TW': 158,
    'TZ': 834,
    'UA': 804,
    'UG': 800,
    'US': 840,
    'UY': 858,
    'UZ': 860,
    'VE': 862,
    'VN': 704,
    'YE': 887,
    'ZA': 710,
    'ZW': 716,
}

def pad(n):
    return (4 - n % 4) % 4

def xor_words(b):
    x = 0
    for i in range(0, len(b) - (len(b) & 1), 2):
        x ^= b[i] | (b[i + 1] << 8)
    return x

def read_board2(path):
    buf = open(path, "rb").read()
    if buf[:len(SIGNATURE)] != SIGNATURE or buf[len(SIGNATURE)] != 0:
        sys.exit("%s: not a valid ath10k board-2.bin (bad signature)" % path)
    boards = []
    off, n = SIG_TOTAL, len(buf)
    while off < n:
        ie_id, ie_len = struct.unpack_from("<2i", buf, off); off += 8
        if ie_id in (IE_BOARD, IE_BOARD_EXT):
            names, data, p, end = [], None, off, off + ie_len
            while p < end:
                sid, slen = struct.unpack_from("<2i", buf, p); p += 8
                val = buf[p:p + slen]
                if sid == IE_NAME: names.append(val.decode())
                elif sid == IE_DATA: data = bytes(val)
                p += slen + pad(slen)
            boards.append([names, data])
        off += ie_len + pad(ie_len)
    return boards

def _subie(sid, val):
    return struct.pack("<2i", sid, len(val)) + val + bytes([PADDING_MAGIC]) * pad(len(val))

def write_board2(path, boards):
    out = bytearray(SIGNATURE + b"\0" + bytes([PADDING_MAGIC]) * (SIG_TOTAL - len(SIGNATURE) - 1))
    for names, data in boards:
        body = b"".join(_subie(IE_NAME, nm.encode()) for nm in names) + _subie(IE_DATA, data)
        ie_id = IE_BOARD_EXT if any("bmi-eboard-id" in nm for nm in names) else IE_BOARD
        out += struct.pack("<2i", ie_id, len(body)) + body
    open(path, "wb").write(bytes(out))

def find_board(boards, name):
    for names, data in boards:
        if name in names:
            return [names, data]
    return None

def regdomain_for(token):
    t = token.strip().lower()
    if t in WORLD_TOKENS:
        return WORLD_TOKENS[t]
    if t.startswith("0x"):
        return int(t, 16)
    u = token.strip().upper()
    if u in COUNTRIES:
        return COUNTRY_ERD_FLAG | COUNTRIES[u]
    sys.exit("unknown regdomain %r: use an ISO alpha2 (e.g. IT), a world token %s, or 0xHEX" %
             (token, sorted(WORLD_TOKENS)))

def describe_regdomain(rd):
    lo = rd & 0xffff
    if lo == 0:
        return "CTRY_DEFAULT (ath->US/DFS-FCC, NOT world)"
    if lo & COUNTRY_ERD_FLAG:
        cc = lo & ~COUNTRY_ERD_FLAG
        a2 = next((k for k, v in COUNTRIES.items() if v == cc), "?")
        return "COUNTRY_ERD | %d (%s)" % (cc, a2)
    if (lo & 0x00f0) == 0x0060:
        return "world-roaming SKU 0x%02x" % lo
    return "raw 0x%04x" % lo

def set_regdomain(blob, rd_u32):
    if len(blob) < REGDOMAIN_OFFSET + 4:
        sys.exit("blob too small")
    b = bytearray(blob)
    struct.pack_into("<I", b, REGDOMAIN_OFFSET, rd_u32)
    struct.pack_into("<H", b, CHECKSUM_OFFSET, 0)
    struct.pack_into("<H", b, CHECKSUM_OFFSET, xor_words(b) ^ XOR_INVARIANT)
    if xor_words(b) != XOR_INVARIANT:
        sys.exit("internal error: checksum recompute failed")
    return bytes(b)

def cmd_generate(args):
    boards = read_board2(args.base)
    src_name = "bus=snoc,qmi-board-id=%s" % args.board_id
    src = None
    for b in boards:
        if src_name in b[0]:
            src = b
            break
    if src is None:
        sys.exit("base has no %r entry to derive the blob from" % src_name)
    if xor_words(src[1]) != XOR_INVARIANT:
        sys.exit("source blob fails the XOR integrity invariant; unexpected format")
    rd = regdomain_for(args.regdomain)
    new_blob = set_regdomain(src[1], rd)
    if args.mode == "fallback":
        # Patch the fallback image IN PLACE: no new entry, no DT change. Works on
        # ANY current kernel because ath10k always lands on this entry. It does
        # override the generic CN fallback for THIS device's board-2.bin.
        src[1] = new_blob
        out = args.output or ("board-2.bin.%s.fallback" % args.board_id)
        write_board2(out, boards)
        print("wrote %s (%d entries, %d bytes) [fallback mode]" % (out, len(boards), os.path.getsize(out)))
        print("  patched in place: %s" % src_name)
        print("  regdom: %s -> 0x%08x (%s)" % (args.regdomain, rd, describe_regdomain(rd)))
        print("  cksum : word@0x%02x recomputed, whole-blob XOR == 0x%04x" % (CHECKSUM_OFFSET, XOR_INVARIANT))
        print("  works on a STOCK kernel (no DT calibration-variant needed)")
        return
    if not args.variant:
        sys.exit("variant mode requires --variant NAME (or use --mode fallback)")
    variant_name = "bus=snoc,qmi-board-id=%s,qmi-chip-id=%s,variant=%s" % (
        args.board_id, args.chip_id, args.variant)
    boards = [b for b in boards if variant_name not in b[0]]   # idempotent
    boards.append([[variant_name], new_blob])
    out = args.output or ("board-2.bin.%s" % args.variant)
    write_board2(out, boards)
    print("wrote %s (%d entries, %d bytes) [variant mode]" % (out, len(boards), os.path.getsize(out)))
    print("  added : %s" % variant_name)
    print("  regdom: %s -> 0x%08x (%s)" % (args.regdomain, rd, describe_regdomain(rd)))
    print("  cksum : word@0x%02x recomputed, whole-blob XOR == 0x%04x" % (CHECKSUM_OFFSET, XOR_INVARIANT))
    print("  kept  : original %s fallback (unchanged)" % src_name)
    print("  REQUIRES DT: qcom,ath10k-calibration-variant = \"%s\"" % args.variant)

def cmd_discover(args):
    if args.dmesg:
        import re
        txt = open(args.dmesg, "r", errors="replace").read()
        pats = ("alpha2", "regulatory", "reg_notifier", "eeprom", "regdomain", "country")
        hits = [ln for ln in txt.splitlines() if any(p in ln.lower() for p in pats)]
        print("dmesg regulatory lines (%d):" % len(hits))
        for ln in hits:
            print("  " + ln.strip())
        return
    boards = read_board2(args.base)
    print("%s: %d board image(s)" % (args.base, len(boards)))
    for names, data in boards:
        if not data or len(data) < REGDOMAIN_OFFSET + 4:
            print("  %-60s (no/short data)" % names[0]); continue
        rd = struct.unpack_from("<I", data, REGDOMAIN_OFFSET)[0]
        ck = struct.unpack_from("<H", data, CHECKSUM_OFFSET)[0]
        inv = xor_words(data)
        print("  %-62s rd=0x%08x (%-26s) ck=0x%04x xor=0x%04x %s" %
              (names[0], rd, describe_regdomain(rd), ck, inv,
               "OK" if inv == XOR_INVARIANT else "BAD-INTEGRITY"))

def cmd_decode(args):
    rd = int(args.value, 0)
    print("0x%08x -> %s" % (rd, describe_regdomain(rd)))

def cmd_list(args):
    for t, v in sorted(WORLD_TOKENS.items()):
        print("%-12s regdomain 0x%08x  (%s)" % (t, v, describe_regdomain(v)))
    for a2 in sorted(COUNTRIES):
        rd = COUNTRY_ERD_FLAG | COUNTRIES[a2]
        print("%-12s regdomain 0x%08x  (ISO %d)" % (a2, rd, COUNTRIES[a2]))

def main():
    ap = argparse.ArgumentParser(description="WCN3990 ath10k board-2.bin regulatory-domain generator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate a board-2.bin with a chosen regdomain (fallback or variant mode)")
    g.add_argument("--base", required=True, help="base board-2.bin to derive from")
    g.add_argument("--mode", choices=["fallback", "variant"], default="variant",
                   help="fallback=patch the ff entry in place (works on any kernel, no DT); "
                        "variant=add a DT-selected image (needs qcom,ath10k-calibration-variant). default: variant")
    g.add_argument("--variant", help="variant name for variant mode (matches DT qcom,ath10k-calibration-variant)")
    g.add_argument("--regdomain", required=True, help="ISO alpha2 (IT), world token, or 0xHEX")
    g.add_argument("--chip-id", default=DEFAULT_CHIP_ID, help="qmi-chip-id hex (default 140 = WCN3990)")
    g.add_argument("--board-id", default=DEFAULT_BOARD_ID, help="qmi-board-id hex to derive from (default ff)")
    g.add_argument("-o", "--output", help="output board-2.bin path")
    g.set_defaults(func=cmd_generate)
    d = sub.add_parser("discover", help="report regdomains in a board-2.bin (or grep a dmesg)")
    d.add_argument("--base", help="board-2.bin to inspect")
    d.add_argument("--dmesg", help="dmesg text file to grep for regulatory lines")
    d.set_defaults(func=cmd_discover)
    de = sub.add_parser("decode", help="decode a regdomain value")
    de.add_argument("value", help="regdomain (0xHEX or decimal)")
    de.set_defaults(func=cmd_decode)
    li = sub.add_parser("list-countries", help="list supported regdomains")
    li.set_defaults(func=cmd_list)
    args = ap.parse_args()
    if getattr(args, "cmd", None) == "discover" and not args.base and not args.dmesg:
        ap.error("discover needs --base or --dmesg")
    return args.func(args)

if __name__ == "__main__":
    main()
