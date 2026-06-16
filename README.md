# wcn3990-regdomain

Fix the WiFi regulatory-domain lock on Qualcomm **WCN3990** (ath10k_snoc / SDM660-class)
postmarketOS devices and generate corrected `board-2.bin` files for any country.

Built for **xiaomi-jasmine_sprout** (Mi A2) and **xiaomi-tulip** (Redmi Note 6 Pro), but the
tool works for any WCN3990 device that uses the upstream ath10k `WCN3990/hw1.0/board-2.bin`.

---

## 1. The problem

These phones ship the generic upstream ath10k `board-2.bin`. ath10k matches the per-board
calibration image by name; with no device-specific entry the chip falls back to
`bus=snoc,qmi-board-id=ff`, whose regulatory-domain field is **`0x809C`**:

```
0x809C = COUNTRY_ERD_FLAG(0x8000) | 0x9C(156 = China, ISO-3166 numeric for CN)
```

ath10k reports this as `eeprom_rd=0x809c`. Because the `COUNTRY_ERD` flag is set,
`drivers/net/wireless/ath/regd.c:is_wwr_sku()` returns false, so ath10k applies the **CN**
regulatory domain directly. Result: `iw reg get` shows `phy#0` country **CN** (DFS-FCC),
`iw reg set IT` does **not** move `phy#0`, and the EU 5 GHz DFS channels 100-140 are
forbidden — the phone is pinned to congested 2.4 GHz on most EU 5 GHz APs.

The fix is to give the chip a **world-roaming** regulatory domain so it honours the
system-set country.

---

## 2. board-2.bin / bdwlan format

`board-2.bin` is a container (the qca-swiss-army-knife `ath10k-bdencoder` format): a 20-byte
signature `QCA-ATH10K-BOARD\0` + `0x6d` padding, then a list of board images. Each image has
one or more **name** IEs (e.g. `bus=snoc,qmi-board-id=ff`) and one **data** IE — the raw
19152-byte **bdwlan** calibration blob.

Inside each bdwlan blob (verified on real WCN3990 hardware and across the 25 generic + 25
genuine device-extracted blobs):

| field        | offset | type      | meaning |
|--------------|--------|-----------|---------|
| format       | 0x00   | u32 LE    | constant `0x04040001` |
| length       | 0x08   | u16 LE    | blob length (`0x4AD0` = 19152) |
| **checksum** | 0x0A   | u16 LE    | integrity word (see below) |
| **regdomain**| 0x14   | u32 LE    | regulatory domain (the field we change) |

### Integrity = 16-bit XOR (recomputable, NOT a signature)

The firmware validates a **whole-blob XOR invariant**: the XOR of every u16 LE word over the
entire 19152-byte blob equals **`0xFFFF`**. The word at `0x0A` is tuned to satisfy it. This
held for all 50 blobs examined (25 generic + 25 genuine stock files extracted from a device).

Consequently a naive regdomain byte-patch is **rejected by the WLAN firmware** (it boots into
a QMI re-init loop and never associates) — *the checksum must be recomputed*. To change the
regdomain:

```
1. write the new regdomain u32 at 0x14
2. zero the checksum word at 0x0A
3. set checksum = (XOR of all u16 words) XOR 0xFFFF
```

This was confirmed live on hardware: byte-patching `0x809C -> 0x6C` *without* fixing the
checksum re-init-loops; *with* the checksum recomputed it boots and associates.

(Checksum algorithm family / prior art: <https://github.com/testuser7/ath_bdf_tool>.)

---

## 3. Regulatory-domain values (hardware-verified on tulip)

| regdomain    | meaning                              | on-device phy behaviour |
|--------------|--------------------------------------|-------------------------|
| `0x809C`     | `COUNTRY_ERD | 156` = **CN** (stock) | country CN / DFS-FCC; EU 5 GHz DFS blocked — the bug |
| **`0x6C`**   | ath **world-roaming SKU** (`is_wwr_sku`) | **country 99 / DFS-UNSET**, 5 GHz passive-scan; region-safe everywhere, honours userspace country. **SHIPPED GLOBAL DEFAULT.** |
| `0x817C`     | `COUNTRY_ERD | 380` = **IT**         | clean IT / DFS-ETSI, active scan, ch100-140 — best for a known EU region |
| `0x00000000` | `CTRY_DEFAULT`                       | **ath maps this to US / DFS-FCC** (active ch100-140 at US power, illegal in the EU on UNII-3). **NOT a world value — do not ship as global.** |

`0x6C` is shipped as the global default: it is the least-restrictive *region-safe* value and
lets the OS-set regulatory country govern. For a device known to live in one region, the
matching `COUNTRY_ERD` blob (e.g. `IT`) gives active scan and full local channels.

### Country -> regdomain formula

```
regdomain = 0x8000 | <ISO-3166-1 numeric country code>
```

Verified against the kernel ath `CountryCode` enum + `allCountries[]` table
(`drivers/net/wireless/ath/regd.h`, `regd_common.h`). Only codes present there are accepted
by the ath regulatory core. Common ones:

| alpha2 | ISO num | regdomain |
|--------|---------|-----------|
| IT     | 380     | `0x817C`  |
| DE     | 276     | `0x8114`  |
| FR     | 250     | `0x80FA`  |
| GB     | 826     | `0x833A`  |
| ES     | 724     | `0x82D4`  |
| US     | 840     | `0x8348`  |
| CN     | 156     | `0x809C`  |

`./gen-bdf.py list-countries` prints all 131 supported alpha2 codes.

---

## 4. How ath10k picks the variant

ath10k builds the board-image lookup name (`drivers/net/wireless/ath/ath10k/core.c
:ath10k_core_create_board_name`) for snoc/QMI devices as:

```
bus=snoc,qmi-board-id=<id>,qmi-chip-id=<chip>,variant=<V>   (tried first)
bus=snoc,qmi-board-id=<id>,qmi-chip-id=<chip>               (then)
bus=snoc,qmi-board-id=<id>                                  (last)
```

`<V>` comes from the device-tree property `qcom,ath10k-calibration-variant`. For WCN3990 here
`qmi-board-id=ff`, `qmi-chip-id=140`. So with the DT set to `variant=xiaomi-jasmine`, the
chip looks for `bus=snoc,qmi-board-id=ff,qmi-chip-id=140,variant=xiaomi-jasmine` first and
uses our corrected image; the original `bus=snoc,qmi-board-id=ff` fallback stays untouched for
every other device.

| device                    | DT variant       | board entry added |
|---------------------------|------------------|-------------------|
| xiaomi-jasmine_sprout     | `xiaomi-jasmine` | `bus=snoc,qmi-board-id=ff,qmi-chip-id=140,variant=xiaomi-jasmine` |
| xiaomi-tulip              | `xiaomi-tulip`   | `bus=snoc,qmi-board-id=ff,qmi-chip-id=140,variant=xiaomi-tulip` |

This firmware fix is paired with the kernel DT change that sets the matching
`qcom,ath10k-calibration-variant`.

---

## 5. gen-bdf.py

Self-contained (Python 3 stdlib only). Output is byte-identical to qca-swiss-army-knife
`ath10k-bdencoder`.

```
# FALLBACK mode: patch the ff entry in place. Works on ANY current kernel,
# no DT change. This is what most users want right now.
./gen-bdf.py generate --base base/board-2.bin --mode fallback --regdomain world \
    -o board-2.bin

# VARIANT mode (default): add a DT-selected image; needs the kernel DT
# qcom,ath10k-calibration-variant set. Upstream-clean, keeps the CN fallback.
./gen-bdf.py generate --base base/board-2.bin --mode variant --variant xiaomi-jasmine \
    --regdomain world -o board-2.bin

# inspect any board-2.bin (regdomain + integrity of every entry)
./gen-bdf.py discover --base board-2.bin

# decode a raw regdomain value, or grep a dmesg
./gen-bdf.py decode 0x809c
./gen-bdf.py discover --dmesg dmesg.txt

# list every supported country / world token
./gen-bdf.py list-countries
```

`--mode fallback` patches `bus=snoc,qmi-board-id=ff` in place (25 entries, **no DT needed**);
`--mode variant` (default) appends `...,variant=<name>` and keeps the CN fallback (26 entries,
**needs the DT** property). `--regdomain` accepts an ISO alpha2 (`IT`), a world token (`world`
= 0x6C global default, `world-zero` = 0x0 [US, avoid]), or `0xHEX`. `--chip-id` defaults to
`140` (WCN3990), `--board-id` to `ff`. Both modes recompute the XOR checksum and are idempotent.

---

## 6. pregenerated/

Ready-to-flash `board-2.bin`, `world` (0x6C) default + common EU countries + US, in **both
modes** (`SHA256SUMS` in each subdir):

```
pregenerated/fallback/board-2.bin.<dev>.<world|IT|DE|FR|GB|ES|US>   # drop-in, works NOW
pregenerated/variant/board-2.bin.<dev>.<world|IT|DE|FR|GB|ES|US>    # needs the DT patch
```

`<dev>` = `xiaomi-jasmine_sprout` or `xiaomi-tulip`. Most users want the **fallback `world`**
blob (see install). The fallback blobs derive from the shared generic base, so the jasmine and
tulip fallback files are byte-identical — the per-device name is only for convenience.

---

## 7. Install — two modes, pick one

### Mode A — fallback patch (drop-in, works on your CURRENT kernel) — recommended for users
Overwrites the `bus=snoc,qmi-board-id=ff` image inside board-2.bin with your regdomain. ath10k
always lands on it, so **no kernel/DT change is needed**. It overrides the generic CN fallback
for *this device's* board-2.bin (fine — it is this device's own firmware).
```
sudo cp pregenerated/fallback/board-2.bin.<dev>.world \
    /lib/firmware/ath10k/WCN3990/hw1.0/board-2.bin
sudo modprobe -r ath10k_snoc && sudo modprobe ath10k_snoc   # or just reboot
```

### Mode B — variant + DT (upstream-clean, needs a kernel/DTB rebuild)
Keeps the CN fallback untouched and adds a device image selected by the device-tree. A plain
board-2.bin swap is NOT enough — you must flash a kernel/DTB that sets the variant.
1. Kernel DT (device wcnss/wifi node):
```
qcom,ath10k-calibration-variant = "xiaomi-jasmine";   /* or "xiaomi-tulip" */
```
2. Firmware: install `pregenerated/variant/board-2.bin.<dev>.world` to
   `/lib/firmware/ath10k/WCN3990/hw1.0/board-2.bin`, or use the aport change in
   [`integrate/`](integrate/).

**Which to pick:** stock/unpatched kernel → **Mode A**. Building/flashing the kernel anyway
(device port, pmaports) → **Mode B** (cleaner, leaves the shared CN fallback intact).

### Verify on device (after install + reboot)
```
dmesg | grep -i "board name"        # Mode B: shows ...,variant=<name>
dmesg | grep -i "Country alpha2"    # should no longer say CN
iw reg get                          # phy#0 follows the system country (not pinned CN)
iw dev wlan0 scan | grep -iE "DFS|MHz"   # 5GHz ch100-140 present
```

### Expected impact (honest)
This fixes the regulatory *capability* — it removes the CN lock so the chip can use the EU
5 GHz band (incl. DFS ch100-140) and honour the system-set country. It does **not** by itself
make WiFi faster:
- The visible benefit needs a **5 GHz AP in range**. On a 2.4 GHz-only network throughput is
  unchanged — the fix only stops 5 GHz from being forbidden.
- `power_save`, AP band-steering and channel width are independent throughput/latency factors;
  if measuring, check `iw dev wlan0 get power_save`.
- With `world` (0x6C) the 5 GHz channels come up **passive-scan** (region-safe): the device can
  join a 5 GHz AP but won't actively probe those channels. A specific country (e.g. `IT`) gives
  active scan + full local power if you prefer that over the region-agnostic default.

---

## 8. Credits / references

- Checksum algorithm family: <https://github.com/testuser7/ath_bdf_tool> (Paweł Owoc, MIT).
- Container format: qca-swiss-army-knife `ath10k-bdencoder`
  (<https://github.com/qca/qca-swiss-army-knife>).
- Regulatory semantics: Linux `drivers/net/wireless/ath/regd.c`, `regd_common.h`, `regd.h`.

## License

MIT (see `gen-bdf.py` header).
