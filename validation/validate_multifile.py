#!/usr/bin/env python3
"""Multi-file distribution validation for larnd-sim output.

Ingests one or more simulation HDF5 files (paths and/or glob patterns) and,
for each file, plots distributions of the packets dataset (filtered to
packet_type==0) and prints descriptive statistics for dataword, timestamp,
linearized pixel ID, and inter-packet timestamp differences.

Two modes:
  default            each file is one code version (discrete color palette)
  --same-commit      each file is one seed draw of the same code; all series
                     share one color with alpha (overlap -> density band)

With --stat-tests, a designated reference file (--reference, default: the
first file) is compared against every other file via two-sample KS,
Cramer-von Mises, and 1-D Wasserstein-1 tests.

Standalone: no imports from validate_dists.py / validate_sim.py.
"""

import argparse
import glob as _glob
import json
import os
import sys
import time

import h5py
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

STYLE_SAME_COMMIT = {
    # "color": "C0",
    "alpha": 0.5,
    "histtype": "step",
    "lw": 1.0,
    "legend_note": "same-commit replicates (per-seed)",
    "palette": "default_cycle",   # or any matplotlib colormap name, e.g. "tab20"
}
STYLE_DIFF_COMMIT = {
    "alpha": 0.8,
    "histtype": "step",
    "lw": 1.0,
    "palette": "default_cycle",   # or any matplotlib colormap name, e.g. "tab20"
}


def style_color(style, i):
    """Per-series color: the style's fixed "color" when present, otherwise
    entry i of the style's palette. Makes the two style dicts
    interchangeable in every plot function."""
    if "color" in style:
        return style["color"]
    name = style.get("palette", "default_cycle")
    if name == "default_cycle":
        return f"C{i}" if i < 10 else plt.cm.tab20(i / 20)
    cmap = plt.get_cmap(name)
    return cmap(i / max(style.get("palette_n", 20) - 1, 1))


QUANTITIES = ["dataword", "pixel_id", "timestamp", "timestamp_dt"]

STAT_NAMES = ["count", "min", "max", "mean", "median", "std", "q01", "q25", "q75", "q99"]
_trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")  # numpy 2.x renamed trapz


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def expand_files(args):
    """CLI positional args -> sorted, deduplicated list of existing paths."""
    paths = []
    for arg in args:
        if any(c in arg for c in "*?["):
            matches = sorted(_glob.glob(arg))
            if not matches:
                print(f"Error: glob pattern matched nothing: {arg}", file=sys.stderr)
                sys.exit(1)
            paths.extend(matches)
        else:
            paths.append(arg)

    seen = set()
    files = []
    for p in paths:
        key = os.path.normpath(os.path.abspath(p))
        if key in seen:
            print(f"Warning: duplicate file ignored: {p}", file=sys.stderr)
            continue
        if not os.path.isfile(p):
            print(f"Error: not a file: {p}", file=sys.stderr)
            sys.exit(1)
        seen.add(key)
        files.append(p)
    return files


def pixel_id_rank(io_group, io_channel, chip_id, channel_id):
    """Rank-encode each field over its observed unique values, then combine
    into one compact integer: no gaps from unused hardware IDs, range
    ~= n_group * n_channel * n_chip * n_channel (<= 1.6M here)."""
    def ranks(v):
        _, inv = np.unique(v, return_inverse=True)
        return inv.astype(np.int64)
    r_ig = ranks(io_group)
    r_ic = ranks(io_channel)
    r_ci = ranks(chip_id)
    r_ch = ranks(channel_id)
    n_ic = int(r_ic.max()) + 1
    n_ci = int(r_ci.max()) + 1
    n_ch = int(r_ch.max()) + 1
    return ((r_ig * n_ic + r_ic) * n_ci + r_ci) * n_ch + r_ch


def pixel_id_tile(io_group, io_channel, chip_id, channel_id):
    """Tile-block encoding: keeps electronics-group locality (consecutive
    values = one tile / one chip). io_group and io_channel pair to a tile;
    chip_id / channel_id use their raw hardware offsets."""
    tile = (io_group - 1) * 10 + (io_channel - 1) // 4 + 1
    return ((tile - 1) * 25600
            + (chip_id - chip_id.min()) * 64
            + channel_id)


PIXEL_ENC = "rank"   # "rank" (default) or "tile"
PIXEL_ENCODINGS = {"rank": pixel_id_rank, "tile": pixel_id_tile}

def load_file(path, pixel_enc="rank", drop_saturated=False):
    """Read one HDF5 file. Returns dict of value arrays after the
    packet_type==0 filter (and the dataword==255 filter when
    drop_saturated is set)."""
    with h5py.File(path, "r") as hf:
        arr = hf["packets"][:]
    arr = arr[arr["packet_type"] == 0]
    n_after_type = len(arr)
    if drop_saturated:
        arr = arr[arr["dataword"] != 255]

    io_group = arr["io_group"].astype(np.int64)
    io_channel = arr["io_channel"].astype(np.int64)
    chip_id = arr["chip_id"].astype(np.int64)
    channel_id = arr["channel_id"].astype(np.int64)
    pixel_id = PIXEL_ENCODINGS[pixel_enc](io_group, io_channel,
                                          chip_id, channel_id)

    ts = arr["timestamp"].astype(np.float64)
    ts_dt = np.diff(np.sort(ts))

    return {
        "n_packets": len(arr),
        "n_dropped_saturated": n_after_type - len(arr),
        "dataword": arr["dataword"].astype(np.float64),
        "pixel_id": pixel_id.astype(np.float64),
        "timestamp": ts,
        "timestamp_dt": ts_dt,
    }


def summarize(v):
    """Descriptive statistics of a 1-D array."""
    return {
        "count": len(v),
        "min": v.min(),
        "max": v.max(),
        "mean": float(np.mean(v)),
        "median": float(np.median(v)),
        "std": float(np.std(v)),
        "q01": float(np.percentile(v, 1)),
        "q25": float(np.percentile(v, 25)),
        "q75": float(np.percentile(v, 75)),
        "q99": float(np.percentile(v, 99)),
    }


def ks_pvalue(D, n_eff):
    """Asymptotic two-sample KS p-value (Stephens correction)."""
    if D == 0:
        return 1.0
    lam = (np.sqrt(n_eff) + 0.12 + 0.11 / np.sqrt(n_eff)) * D
    if lam < 1.18:
        s = sum(np.exp(-((2 * k - 1) ** 2) * np.pi**2 / (8.0 * lam * lam))
                for k in range(1, 101))
        F = np.sqrt(2.0 * np.pi) / lam * s
    else:
        s = sum((-1) ** (k - 1) * np.exp(-2.0 * k * k * lam * lam)
                for k in range(1, 101))
        F = 1.0 - 2.0 * s
    return float(min(1.0, max(0.0, 1.0 - F)))


def ks_2samp(a, b):
    """Two-sample KS: statistic D and asymptotic p-value. Pure numpy."""
    a = np.sort(a)
    b = np.sort(b)
    allv = np.sort(np.concatenate([a, b]))
    cdf_a = np.searchsorted(a, allv, side="right") / len(a)
    cdf_b = np.searchsorted(b, allv, side="right") / len(b)
    gaps = np.abs(cdf_a - cdf_b)
    D = float(gaps[int(np.argmax(gaps))])
    n_eff = len(a) * len(b) / (len(a) + len(b))
    return D, ks_pvalue(D, n_eff)


def cvm_2samp(a, b):
    """Two-sample Cramer-von Mises test via scipy (T statistic and
    p-value)."""
    from scipy.stats import cramervonmises_2samp
    res = cramervonmises_2samp(a, b, method="auto")
    return float(res.statistic), float(res.pvalue)


def wasserstein1d(a, b):
    a = np.sort(a)
    b = np.sort(b)
    allv = np.sort(np.concatenate([a, b]))
    cdf_a = np.searchsorted(a, allv, side="right") / len(a)
    cdf_b = np.searchsorted(b, allv, side="right") / len(b)
    return float(_trapz(np.abs(cdf_a - cdf_b), allv))


def chi2_2samp(hist_a, hist_b):
    """Two-sample chi-square on binned counts. Bins with zero expected
    counts in both samples are excluded; expected counts come from the
    pooled (sum) histogram scaled to each sample's total. Returns
    (chi2, p, ndf); p via scipy's chi2 survival function."""
    from scipy.stats import chi2 as _chi2
    a = hist_a.astype(np.float64)
    b = hist_b.astype(np.float64)
    tot_a, tot_b = a.sum(), b.sum()
    if tot_a == 0 or tot_b == 0:
        return float("nan"), float("nan"), 0
    exp = (a + b) * (tot_a / (tot_a + tot_b))
    use = exp > 0
    chi2 = float((((a - exp)[use] ** 2) / exp[use]).sum()
                 + ((((b - (a + b - exp))[use]) ** 2) / (a + b - exp)[use]).sum())
    ndf = int(use.sum()) - 1  # 2 samples, 1 constraint (equal totals)
    return chi2, float(_chi2.sf(chi2, ndf)), ndf


def make_edges(all_values, bins):
    """Common histogram bin edges. Integer-valued data over a small range
    gets unit-width bins; otherwise evenly spaced bins."""
    lo, hi = all_values.min(), all_values.max()
    if np.all(all_values == np.floor(all_values)) and (hi - lo) <= 1024:
        if hi == lo:
            return np.array([lo - 0.5, lo + 0.5])
        return np.arange(lo - 0.5, hi + 1.5, 1.0)
    return np.histogram_bin_edges(all_values, bins=bins)


def to_py(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"unserializable type {type(o)}")


def file_label(path):
    """Git short hash parsed from fsd_runner_run_<ref>_... filenames;
    fallback to the basename without .hdf5."""
    for token in os.path.basename(path).removesuffix(".hdf5").split("_"):
        if len(token) == 7 and all(c in "0123456789abcdef" for c in token.lower()):
            return token
    return os.path.basename(path).removesuffix(".hdf5")


def palette_colors(n, style):
    name = style.get("palette", "default_cycle")
    if name == "default_cycle":
        if n <= 10:
            return [f"C{i}" for i in range(n)]
        cmap = plt.cm.tab20
    else:
        cmap = plt.get_cmap(name)
    return [cmap(i / max(n - 1, 1)) for i in range(n)]


def plot_quantity(out_path, name, per_file, labels, edges, colors, style,
                  density, log_scale=False):
    fig, ax = plt.subplots(figsize=(9, 5.5))
    same_commit = "color" in style
    for i, (fname, vals) in enumerate(per_file.items()):
        ax.hist(vals, bins=edges, density=density,
                color=style_color(style, i),
                alpha=style["alpha"], histtype=style["histtype"],
                lw=style["lw"], label=labels[fname])

    if log_scale:
        ax.set_xscale("log")
        ax.set_xlim(edges[1], edges[-1])
        xlabel = f"{name} (log scale)"
    else:
        ax.set_xlim(edges[0], edges[-1])
        xlabel = name
    ax.set_xlabel(xlabel)
    ax.set_ylabel("density" if density else "packets")
    n_files = len(per_file)
    title = f"{name}  ({n_files} files, packet_type==0"
    if "legend_note" in style:
        title += f", {style['legend_note']}"
    ax.set_title(title + ")", fontsize=11)

    distinct = set(labels.values())
    if "legend_note" in style and len(distinct) == 1:
        print(f"  note: {n_files} files share label '{next(iter(distinct))}' "
              f"-> replicates; single legend entry")
        handles = [plt.Rectangle((0, 0), 1, 1,
                                 color=style_color(style, 0),
                                 alpha=style["alpha"])]
        ax.legend(handles, [f"{next(iter(distinct))} ({n_files} files)"])
    else:
        ax.legend(fontsize=8)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_violin(out_path, per_file, labels, colors, style):
    """One violin per file for the dataword distribution."""
    names = list(per_file)
    fig, ax = plt.subplots(figsize=(9, 5.5))
    parts = ax.violinplot([per_file[n] for n in names],
                          showmedians=True, showmeans=True, showextrema=False)
    for i, body in enumerate(parts["bodies"]):
        color = style_color(style, i)
        body.set_facecolor(color)
        body.set_edgecolor(color)
        # body.set_alpha(style["alpha"])
    parts["cmedians"].set_color("k")
    parts["cmedians"].set_linewidth(0.5)
    parts["cmedians"].set_linestyle('-')
    parts["cmeans"].set_color("k")
    parts["cmeans"].set_linewidth(0.5)
    parts["cmeans"].set_linestyle(':')

    median_line = matplotlib.lines.Line2D([0], [0], color='black', linestyle='-', label='Median')
    mean_line = matplotlib.lines.Line2D([0], [0], color='black', linestyle=':', label='Mean')

    ax.legend(
        handles=[median_line, mean_line], 
    )

    ax.set_xticks(range(1, len(names) + 1))
    ax.set_xticklabels([labels[n] for n in names], fontsize=9)
    ax.set_ylabel("dataword")
    n_files = len(names)
    title = f"dataword  ({n_files} files, packet_type==0"
    if "legend_note" in style:
        title += f", {style['legend_note']}"
    ax.set_title(title + ")", fontsize=11)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_boxen(out_path, per_file, labels, colors, style):
    """One boxenplot per file for the dataword distribution."""
    try:
        import seaborn as sns
    except ImportError:
        print(f"  skipping {out_path}: seaborn not installed")
        return False

    names = list(per_file)
    fig, ax = plt.subplots(figsize=(9, 5.5))
    data_list = [per_file[n] for n in names]

    sns.boxenplot(data=data_list, palette=colors, ax=ax)

    ax.set_xticks(range(0, len(names)))
    ax.set_xticklabels([labels[n] for n in names], fontsize=9)
    ax.set_ylabel("dataword")
    n_files = len(names)
    title = f"dataword  ({n_files} files, packet_type==0"
    if "legend_note" in style:
        title += f", {style['legend_note']}"
    ax.set_title(title + ")", fontsize=11)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return True


def plot_pulls(out_path, name, per_file_hists, ref_path, ref_label, labels,
               colors, style):
    """Pull plot: per-bin deviation of each non-reference file's histogram
    from the reference histogram, in units of the Poisson sigma
    sqrt(ref + file) of the difference."""
    ref_h = per_file_hists[ref_path].astype(np.float64)
    others = [f for f in per_file_hists if f != ref_path]
    nbins = len(ref_h)
    width = 0.8 / max(len(others), 1)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for i, f in enumerate(others):
        h = per_file_hists[f].astype(np.float64)
        sigma = np.sqrt(ref_h + h)
        use = sigma > 0
        pull = np.where(use, (h - ref_h) / np.where(use, sigma, 1.0), 0.0)
        xs = np.arange(nbins)[use] + 0.5
        ax.bar(xs + (i - (len(others) - 1) / 2) * width,
               pull[use], width=width,
               color=style_color(style, i),
               label=labels[f])

    ax.axhline(0, color="k", lw=0.8)
    for y in (-2, 2):
        ax.axhline(y, color="r", lw=0.8, ls="--")
    ax.set_xlabel(f"{name} bin")
    ax.set_ylabel("pull  (file - ref) / sqrt(ref + file)")
    ax.set_title(f"{name} pulls vs reference {ref_label}", fontsize=11)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def pull_bias_metrics(pull, use):
    """Quantitative bias metrics from the per-bin pulls.

    offset_z : sum(pulls) / sqrt(n_used)  -- sensitivity to a global
        (normalization-like) bias, in sigma; the pulls are already signed,
        so a coherent same-sign offset adds in quadrature to this.
    slope_z_per_bin : least-squares slope of pulls vs bin center, in sigma
        per bin -- sensitivity to a ramp/gradient bias (e.g. gain drift
        across the range).
    max_abs_pull / argmax_bin : single-bin worst deviation and where it
        sits (bin index within the used bins).
    """
    x = np.arange(len(pull))[use].astype(np.float64)
    y = pull[use]
    n = len(y)
    if n == 0:
        return {"offset_z": float("nan"), "slope_z_per_bin": float("nan"),
                "max_abs_pull": float("nan"), "argmax_bin": -1}
    offset_z = float(y.sum() / np.sqrt(n))
    if n > 1:
        xm, ym = x.mean(), y.mean()
        denom = ((x - xm) ** 2).sum()
        slope = float(((x - xm) * (y - ym)).sum() / denom) if denom > 0 else float("nan")
    else:
        slope = float("nan")
    imax = int(np.argmax(np.abs(y)))
    return {"offset_z": offset_z,
            "slope_z_per_bin": slope,
            "max_abs_pull": float(np.abs(y[imax])),
            "argmax_bin": int(x[imax])}


def make_plots(data, files, labels, args, ref_path, ref_label):
    os.makedirs(args.plot_dir, exist_ok=True)
    style = STYLE_SAME_COMMIT if args.same_commit else STYLE_DIFF_COMMIT
    colors = palette_colors(len(files), style)

    for name in QUANTITIES:
        all_vals = np.concatenate([data[f][name] for f in files])
        if name == "timestamp_dt" and args.log_timestamp_dt:
            # log-spaced edges over positive values; exact zeros captured
            # by a leading [0, lo) bin
            pos = all_vals[all_vals > 0]
            edges = np.concatenate(
                [[0.0],
                 np.logspace(np.log10(pos.min()), np.log10(pos.max()),
                             args.bins + 1)])
            log_scale = True
        else:
            edges = make_edges(all_vals, args.bins)
            log_scale = False
        out = os.path.join(args.plot_dir, f"{name}_hist.png")
        plot_quantity(out, name, {f: data[f][name] for f in files},
                      labels, edges, colors, style, args.density,
                      log_scale=log_scale)
        print(f"  wrote {out}")

        out = os.path.join(args.plot_dir, f"{name}_pulls.png")
        plot_pulls(out, name, {f: data[f]["_hists"][name] for f in files},
                   ref_path, ref_label, labels, colors, style)
        print(f"  wrote {out}")

    out = os.path.join(args.plot_dir, "dataword_violin.png")
    plot_violin(out, {f: data[f]["dataword"] for f in files},
                labels, colors, style)
    print(f"  wrote {out}")

    out = os.path.join(args.plot_dir, "dataword_boxen.png")
    if plot_boxen(out, {f: data[f]["dataword"] for f in files},
                  labels, colors, style):
        print(f"  wrote {out}")


def print_stats_block(name, s):
    print(f"  {name:<12}: count={s['count']}  min={s['min']:.6g}  max={s['max']:.6g}  "
          f"mean={s['mean']:.6g}  median={s['median']:.6g}  std={s['std']:.6g}")
    print(f"  {'':<12}  q01={s['q01']:.6g}  q25={s['q25']:.6g}  "
          f"q75={s['q75']:.6g}  q99={s['q99']:.6g}")


def main(argv=None):
    t_start = time.monotonic()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+",
                    help="HDF5 paths or glob patterns")
    ap.add_argument("--same-commit", action="store_true",
                    help="files are seed replicates of the same commit "
                         "(single color, alpha; default: discrete palette)")
    ap.add_argument("--stat-tests", action="store_true",
                    help="run KS + Cramer-von Mises + Wasserstein-1 of each "
                         "file vs the reference")
    ap.add_argument("--reference",
                    help="reference file for stat tests (default: first file)")
    ap.add_argument("--text-only", action="store_true",
                    help="skip all plotting")
    ap.add_argument("--plot-dir", default="./multifile_plots",
                    help="output directory for plots (default: ./multifile_plots)")
    ap.add_argument("--json", help="save results JSON to PATH")
    ap.add_argument("--density", action="store_true",
                    help="normalize histograms to density (default: raw counts)")
    ap.add_argument("--drop-saturated", action="store_true",
                    help="remove packets with dataword == 255 (ADC "
                         "saturation) before computing stats/plots")
    ap.add_argument("--pixel-encoding", choices=sorted(PIXEL_ENCODINGS), default=PIXEL_ENC,
                    help="linearized pixel ID scheme (default: %(default)s; "
                         "'rank' = compact rank encode, 'tile' = tile-block "
                         "keeping electronics-group locality)")
    ap.add_argument("--log-timestamp-dt", action="store_true",
                    help="log-scale x axis for the timestamp_dt histogram "
                         "(inter-packet dt is heavily right-skewed)")
    ap.add_argument("--bins", type=int, default=100,
                    help="histogram bins (default: 100)")
    args = ap.parse_args(argv)

    files = expand_files(args.files)

    ref_path = None
    if args.reference:
        ref_key = os.path.normpath(os.path.abspath(args.reference))
        for f in files:
            if os.path.normpath(os.path.abspath(f)) == ref_key:
                ref_path = f
                break
        if ref_path is None:
            print(f"Error: --reference {args.reference} is not in the resolved "
                  f"file list", file=sys.stderr)
            return 1
    else:
        ref_path = files[0]

    labels = {f: file_label(f) for f in files}
    print(f"mode       : {'same_commit' if args.same_commit else 'diff_commit'}")
    print(f"files      : {len(files)}")
    print(f"reference  : {os.path.basename(ref_path)} (label {labels[ref_path]})")

    data = {}
    for f in files:
        data[f] = load_file(f, pixel_enc=args.pixel_encoding,
                            drop_saturated=args.drop_saturated)
        data[f]["_stats"] = {q: summarize(data[f][q]) for q in QUANTITIES}

    # Shared histograms: one binning per quantity from all files pooled,
    # reused by the chi-square test and the pull plots
    pooled_edges = {}
    for q in QUANTITIES:
        all_vals = np.concatenate([data[f][q] for f in files])
        pooled_edges[q] = make_edges(all_vals, args.bins)
    for f in files:
        data[f]["_hists"] = {
            q: np.histogram(data[f][q], bins=pooled_edges[q])[0]
            for q in QUANTITIES
        }

    for f in files:
        print(f"\n== {os.path.basename(f)} ==")
        print(f"  label      : {labels[f]}")
        drop_note = (f"  (dropped {data[f]['n_dropped_saturated']} "
                     f"dataword==255 packets)"
                     if args.drop_saturated else "")
        print(f"  n_packets  : {data[f]['n_packets']}  "
              f"(after packet_type==0 filter){drop_note}")
        for q in QUANTITIES:
            print_stats_block(q, data[f]["_stats"][q])

        if args.stat_tests and f != ref_path:
            print(f"  vs reference {labels[ref_path]}:")
            tests = {}
            for q in QUANTITIES:
                D, p = ks_2samp(data[f][q], data[ref_path][q])
                w1 = wasserstein1d(data[f][q], data[ref_path][q])
                T, cp = cvm_2samp(data[f][q], data[ref_path][q])
                c2, c2p, c2ndf = chi2_2samp(data[f]["_hists"][q],
                                            data[ref_path]["_hists"][q])
                V = np.sqrt(c2 / (len(data[f][q]) * (c2ndf - 1)))
                h, href = data[f]["_hists"][q], data[ref_path]["_hists"][q]
                sigma = np.sqrt(href.astype(np.float64) + h.astype(np.float64))
                use = sigma > 0
                pull = np.where(use, (h - href) / np.where(use, sigma, 1.0), 0.0)
                bias = pull_bias_metrics(pull, use)
                tests[q] = {"ks_stat": D, "ks_pvalue": p, "wasserstein": w1,
                            "cvm_stat": T, "cvm_pvalue": cp,
                            "chi2": c2, "chi2_pvalue": c2p, "chi2_ndf": c2ndf,
                            "pull_offset_z": bias["offset_z"],
                            "pull_slope_z_per_bin": bias["slope_z_per_bin"],
                            "pull_max_abs": bias["max_abs_pull"],
                            "pull_argmax_bin": bias["argmax_bin"]}
                print(f"    {q:<12}: KS D={D:>12.4g}  p={p:>9.3g}  "
                      f"W1={w1:>12.4g}  CvM T={T:>12.4g}  p={cp:>9.3g}  "
                      f"chi2={c2:>9.3g}/{c2ndf}  p={c2p:>9.3g}  "
                      f"CvM V={V:>9.3g}")
                print(f"    {'':<12}  bias: offset={bias['offset_z']:>+8.3f} sigma  "
                      f"slope={bias['slope_z_per_bin']:>+9.4f} sigma/bin  "
                      f"max|pull|={bias['max_abs_pull']:>7.3f} @ bin {bias['argmax_bin']}")
            data[f]["_tests"] = tests

        print()

    if not args.text_only:
        make_plots(data, files, labels, args, ref_path, labels[ref_path])

    if args.json:
        result = {
            "mode": "same_commit" if args.same_commit else "diff_commit",
            "stat_tests": bool(args.stat_tests),
            "reference": os.path.basename(ref_path),
            "bins": args.bins,
            "density": bool(args.density),
            "files": {
                os.path.basename(f): {
                    "label": labels[f],
                    "n_packets": data[f]["n_packets"],
                    "stats": data[f]["_stats"],
                    **({"tests_vs_reference": data[f]["_tests"]}
                       if "_tests" in data[f] else {}),
                }
                for f in files
            },
        }
        with open(args.json, "w") as fh:
            json.dump(json.loads(json.dumps(result, default=to_py)), fh, indent=2)
        print(f"\nwrote {args.json}")

    print(f"\nprocessing time: {time.monotonic() - t_start:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
