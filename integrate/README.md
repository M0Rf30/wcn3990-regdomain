# pmaports firmware integration

How to wire the world-roaming (0x6C) board-2.bin into the two firmware aports
(`firmware-xiaomi-jasmine_sprout`, `firmware-xiaomi-tulip`). **Not yet applied to pmaports** —
apply after the on-device test is accepted.

Both approaches below derive the blob in-build from the fetched vendor `board-2.bin` (nothing
binary lands in git) using tools already present: `qca-swiss-army-knife` is already in
`makedepends`, plus `python3`. **`sha512sums` is unchanged** (no `source=` added/modified).
Only bump `pkgrel` (jasmine 1->2, tulip 0->1).

The in-build python (identical in both modes) asserts the source is still CN (`0x809c` @0x14)
and integrity-valid (whole-blob u16 XOR == `0xffff`), then sets the regdomain and recomputes
the XOR checksum word @0x0a. *It fails the build loudly if the format ever changes* — never
silently ships a bad blob. Both sequences were validated to produce blobs byte-identical to
the corresponding `pregenerated/` files.

---

## Option 1 — FALLBACK mode (recommended for the aport)

Rewrites the `bus=snoc,qmi-board-id=ff` image in place. **Works regardless of the kernel DT**
(ath10k always lands on this fallback), so the firmware fix is self-contained and does not
depend on the `qcom,ath10k-calibration-variant` DT change having shipped. The firmware package
is device-specific, so overriding *this device's* CN fallback is correct.

`firmware-xiaomi-jasmine_sprout` — bump `pkgrel`, replace the
`install -Dm644 $pkgname-$_commit/board-2.bin ...` line with:

```sh
	# WiFi regulatory fix (fallback): rewrite the qmi-board-id=ff image's regdomain
	# CN(0x809c)->world(0x6c) at bdwlan offset 0x14 and recompute the 16-bit XOR
	# checksum (whole-blob u16 XOR == 0xffff, word@0x0a). Works on any kernel; no DT
	# change needed. A bare byte-patch without the checksum is rejected (re-init loop).
	cp "$pkgname-$_commit/board-2.bin" board-2.bin
	ath10k-bdencoder -e board-2.bin
	python3 -c 'import struct
f="bus=snoc,qmi-board-id=ff.bin"
d=bytearray(open(f,"rb").read())
def xorall(b):
    x=0
    for i in range(0,len(b)//2*2,2): x^=b[i]|(b[i+1]<<8)
    return x
assert struct.unpack_from("<I",d,0x14)[0]==0x0000809c, "regdomain not CN (0x809c); bdwlan layout changed"
assert xorall(d)==0xffff, "unexpected board-data integrity (not the known XOR format)"
struct.pack_into("<I",d,0x14,0x0000006c)
d[0xa]=0; d[0xb]=0
struct.pack_into("<H",d,0xa, xorall(d)^0xffff)
open(f,"wb").write(d)'
	ath10k-bdencoder -c board-2.json -o board-2.bin
	install -Dm644 board-2.bin -t "$pkgdir/$_fwdir/ath10k/WCN3990/hw1.0/"
```

`firmware-xiaomi-tulip` — identical, except the vendor blob path:
`cp "ath10k/WCN3990/hw1.0/board-2.bin" board-2.bin`.

---

## Option 2 — VARIANT mode (upstream-clean, pairs with the DT change)

Keeps the CN fallback untouched and adds `bus=snoc,qmi-board-id=ff,qmi-chip-id=140,variant=<V>`.
Only takes effect once the kernel DT sets `qcom,ath10k-calibration-variant = "<V>"`. Use this
if the device's kernel aport ships that DT property.

`firmware-xiaomi-jasmine_sprout` — bump `pkgrel`, replace the board-2.bin install line with:

```sh
	# WiFi regulatory fix (variant): keep the CN ff fallback, ADD a device image
	# (selected by DT qcom,ath10k-calibration-variant="xiaomi-jasmine") with regdomain
	# 0x6c (world-roaming) and the XOR checksum (word@0x0a, whole-blob XOR==0xffff)
	# recomputed -- a bare byte-patch without the checksum is rejected (re-init loop).
	cp "$pkgname-$_commit/board-2.bin" board-2.bin
	ath10k-bdencoder -e board-2.bin
	_ff="bus=snoc,qmi-board-id=ff.bin"
	python3 -c 'import sys,struct
f=sys.argv[1]
d=bytearray(open(f,"rb").read())
def xorall(b):
    x=0
    for i in range(0,len(b)//2*2,2): x^=b[i]|(b[i+1]<<8)
    return x
assert struct.unpack_from("<I",d,0x14)[0]==0x0000809c, "regdomain not CN (0x809c); bdwlan layout changed"
assert xorall(d)==0xffff, "unexpected board-data integrity (not the known XOR format)"
struct.pack_into("<I",d,0x14,0x0000006c)
d[0xa]=0; d[0xb]=0
struct.pack_into("<H",d,0xa, xorall(d)^0xffff)
open(f,"wb").write(d)' "$_ff"
	ath10k-bdencoder -a board-2.bin "$_ff" "bus=snoc,qmi-board-id=ff,qmi-chip-id=140,variant=xiaomi-jasmine"
	install -Dm644 board-2.bin -t "$pkgdir/$_fwdir/ath10k/WCN3990/hw1.0/"
```

`firmware-xiaomi-tulip` — same, with `cp "ath10k/WCN3990/hw1.0/board-2.bin" board-2.bin` and
the `-a` name ending in `variant=xiaomi-tulip`.

---

## Alternatives
- **gen-bdf.py as a source**: add `gen-bdf.py` to `source=`, run
  `gen-bdf.py generate --base <vendor board-2.bin> --mode fallback --regdomain world -o board-2.bin`
  in `package()`, add its `sha512sum` (`abuild checksum`).
- **Vendored blob**: commit `pregenerated/fallback/board-2.bin.<dev>.world`, add to `source=`
  + `sha512sums`, `install` directly. Binary in git — least preferred.

Build (firmware package only):
`pmbootstrap build firmware-xiaomi-jasmine_sprout firmware-xiaomi-tulip --force`
