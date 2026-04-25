#!/usr/bin/env python3
"""Generate two reference antenna patterns in MSI Planet format.

The MSI Planet (.msi) format is a widely-supported text format used by
radio-planning tools (Atoll, Mentum Planet, Pathloss, ICS Telecom). Each
file declares the peak gain and a 360-row horizontal cut + 360-row vertical
cut of relative attenuation in dB. Row 0 = main lobe boresight.

Patterns produced:
  * generic-omni-vertical-6dbi-868mhz.msi
      - 360 deg azimuth uniform (true omni)
      - vertical cut: classic dipole-ish sin(theta) lobe
  * generic-yagi-3el-7dbi-150mhz.msi
      - 60 deg HPBW horizontal cardioid
      - 70 deg HPBW vertical
      - ~15 dB front-to-back

The patterns are idealized but realistic enough to exercise the full
antenna-gain pipeline (Stage 7, polarization mismatch §4.5, applicable_bands
checks §3.2). Real deployments swap in vendor-supplied .msi files.
"""
import math
import hashlib
import os

OUT = os.path.join(os.path.dirname(__file__), "antenna_patterns")


def cut_omni_horizontal():
    return [(deg, 0.0) for deg in range(360)]


def cut_dipole_vertical():
    # Classic half-wave dipole: gain proportional to sin(theta) from horizon.
    # Translate to "attenuation from peak in dB". Floor at 30 dB.
    rows = []
    for deg in range(360):
        theta_from_horizon = abs(((deg + 180) % 360) - 180)  # 0 at horizon
        # pattern peak at horizon (theta_from_horizon = 90 in standard convention)
        # but MSI vertical row 0 = boresight which for an omni dipole = horizon.
        # We reorient: row 0 = horizon (peak), row 90 / 270 = zenith/nadir (null).
        elev = abs(((deg + 180) % 360) - 180)  # 0..180, 0 at row 0
        # treat row angle directly: 0 = horizon (peak), 90 = zenith (null)
        x = math.cos(math.radians(deg if deg <= 180 else 360 - deg))
        # cos(0)=1 (peak), cos(90)=0 (null). att = -20 log10(|x|).
        if abs(x) < 1e-3:
            att = 30.0
        else:
            att = min(30.0, -20.0 * math.log10(abs(x)))
        rows.append((deg, round(att, 2)))
    return rows


def cut_yagi_horizontal_3el(hpbw_deg=60.0, fb_db=15.0):
    rows = []
    for deg in range(360):
        # boresight at deg=0, back at deg=180
        theta = (deg + 180) % 360 - 180  # -180..+180
        # main lobe Gaussian-ish with HPBW
        sigma = hpbw_deg / 2.355
        if abs(theta) <= 120:
            att = 12.0 * (theta / sigma) ** 2 / 2.0
            att = min(att, fb_db)
        else:
            # back half: ~front-to-back floor with mild ripple
            att = fb_db - 1.5 * math.cos(math.radians((abs(theta) - 180) * 4))
        rows.append((deg, round(max(0.0, att), 2)))
    return rows


def cut_yagi_vertical(hpbw_deg=70.0):
    rows = []
    sigma = hpbw_deg / 2.355
    for deg in range(360):
        theta = (deg + 180) % 360 - 180
        att = 12.0 * (theta / sigma) ** 2 / 2.0
        att = min(att, 25.0)
        rows.append((deg, round(max(0.0, att), 2)))
    return rows


def write_msi(path, header, h_cut, v_cut):
    lines = []
    for k, v in header:
        lines.append(f"{k}\t{v}")
    lines.append("HORIZONTAL 360")
    for deg, att in h_cut:
        lines.append(f"{deg}\t{att:.2f}")
    lines.append("VERTICAL 360")
    for deg, att in v_cut:
        lines.append(f"{deg}\t{att:.2f}")
    body = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return sha, len(body.encode("utf-8"))


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = []

    # 1. 6 dBi omni at 868 MHz
    omni_path = os.path.join(OUT, "generic-omni-vertical-6dbi-868mhz.msi")
    sha, sz = write_msi(
        omni_path,
        header=[
            ("NAME", "generic-omni-vertical-6dbi-868mhz"),
            ("MAKE", "RfAnalyzer reference"),
            ("FREQUENCY", "868"),
            ("H_WIDTH", "360"),
            ("V_WIDTH", "30"),
            ("FRONT_TO_BACK", "0"),
            ("GAIN", "6 dBi"),
            ("TILT", "MECHANICAL"),
            ("POLARIZATION", "VERTICAL"),
            ("COMMENT", "Idealized half-wave dipole; vertical cut sin(theta)"),
        ],
        h_cut=cut_omni_horizontal(),
        v_cut=cut_dipole_vertical(),
    )
    manifest.append((os.path.basename(omni_path), sha, sz))

    # 2. 3-element Yagi at 150 MHz
    yagi_path = os.path.join(OUT, "generic-yagi-3el-7dbi-150mhz.msi")
    sha, sz = write_msi(
        yagi_path,
        header=[
            ("NAME", "generic-yagi-3el-7dbi-150mhz"),
            ("MAKE", "RfAnalyzer reference"),
            ("FREQUENCY", "150"),
            ("H_WIDTH", "60"),
            ("V_WIDTH", "70"),
            ("FRONT_TO_BACK", "15"),
            ("GAIN", "7 dBi"),
            ("TILT", "MECHANICAL"),
            ("POLARIZATION", "HORIZONTAL"),
            ("COMMENT", "Idealized 3-element Yagi-Uda; HPBW 60 deg az / 70 deg el"),
        ],
        h_cut=cut_yagi_horizontal_3el(),
        v_cut=cut_yagi_vertical(),
    )
    manifest.append((os.path.basename(yagi_path), sha, sz))

    # Write manifest
    with open(os.path.join(OUT, "MANIFEST.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# Bundled antenna pattern assets\n")
        f.write("# filename\tsha256\tsize_bytes\n")
        for name, sha, sz in manifest:
            f.write(f"{name}\t{sha}\t{sz}\n")
    for name, sha, sz in manifest:
        print(f"{name}\tsha256:{sha}\t{sz} bytes")


if __name__ == "__main__":
    main()
