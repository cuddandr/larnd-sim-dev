# validate_multifile.py — usage guide

Multi-file distribution validation for larnd-sim output. Ingests one or more
simulation HDF5 files (paths and/or glob patterns), filters the `packets`
dataset to `packet_type==0`, and for each file reports descriptive statistics
for four quantities, optional statistical tests against a designated
reference file, and a standard set of plots. Report-only: it never gates the
run (bad inputs exit non-zero).

## Run it

From the repo root:

```bash
# Same commit, 4 seeds: everything on one plot, overlapping histograms,
# statistical tests vs the first file
python validation/validate_multifile.py 'output/fsd/full_charge_only_short/*d7baafc*.hdf5' \
    --same-commit --stat-tests --json out.json --plot-dir plots/

# Different commits, one seed each: discrete colors per commit,
# explicit reference
python validation/validate_multifile.py 'output/fsd/full_charge_only_short/*_01.hdf5' \
    --stat-tests \
    --reference output/fsd/full_charge_only_short/fsd_runner_run_d7baafc_20261026_154406_01.hdf5

# Numbers only, no plots
python validation/validate_multifile.py 'output/fsd/full_charge_only_short/*_02.hdf5' \
    --stat-tests --text-only
```

Exit code `0` on success, `1` on bad input (e.g. `--reference` not in the
resolved file list, unmatched glob, nonexistent file). Duplicate paths are
dropped with a warning.

## Modes

The `--same-commit` flag switches the *interpretation* and the styling; the
file set and all statistics are identical.

| | default (diff-commit) | `--same-commit` |
|---|---|---|
| What each file is | one code version | one seed draw of the same code |
| Plot colors | one per file (discrete palette) | single color, overlapping steps |
| Legend | one entry per file | collapses to one entry when all labels match |
| Expected result | distributions **should differ** if the commits changed anything | distributions should be **statistically identical** up to seed noise |

Typical use of `--same-commit`: characterize the seed-to-seed noise floor
first. A diff-commit comparison whose test statistics are comfortably inside
that noise floor is consistent with "the commit changed nothing material."

## CLI reference

| Flag | Purpose |
|---|---|
| `FILE…` | HDF5 paths or glob patterns (expanded, sorted, deduplicated) |
| `--same-commit` | seed-replicate mode (see table above) |
| `--stat-tests` | run KS + Cramér–von Mises + Wasserstein-1 + chi² + pull bias metrics vs the reference |
| `--reference PATH` | reference file for tests (default: first resolved file; must be in the list) |
| `--text-only` | skip all plotting |
| `--plot-dir DIR` | output directory for plots (default `./multifile_plots`) |
| `--json PATH` | machine-readable summary |
| `--density` | normalize histograms to density (default: raw packet counts) |
| `--drop-saturated` | remove `dataword == 255` (ADC saturation) before everything |
| `--pixel-encoding {rank,tile}` | linearized pixel ID scheme (default `rank`) |
| `--log-timestamp-dt` | log-scale x axis for the `timestamp_dt` histogram |
| `--bins N` | histogram bins (default 100; dataword's small integer range uses unit bins regardless) |

## The four quantities

All are computed after the `packet_type==0` filter (and `--drop-saturated`
when set):

| Quantity | Definition | What it diagnoses |
|---|---|---|
| `dataword` | raw ADC value | gain/quantization/noise changes |
| `pixel_id` | linearized pixel ID from `io_group, io_channel, chip_id, channel_id` | channel/tile efficiency changes |
| `timestamp` | raw packet timestamp | event rate / windowing changes |
| `timestamp_dt` | `np.diff(np.sort(timestamp))` — inter-packet gap | pileup, sparsification, timing |

### Pixel-ID encodings

Two schemes map the four hardware fields onto one axis:

- **`rank` (default)** — each field is rank-encoded over its observed unique
  values, then combined: `((r_ig*n_ic + r_ic)*n_ci + r_ci)*n_ch + r_ch`.
  Compact (≤ ~1.6M span for this data), no gaps from unused hardware IDs.
  Use for the default distribution-overall check.
- **`tile`** — electronics-group locality: `(io_group-1)*10 +
  (io_channel-1)//4 + 1` tiles, blocks of 64 = one chip. Use when you want
  structure by tile/chip to line up on the axis (e.g. chasing a specific
  electronics group).

Rank-encoding is monotone, so KS D is unchanged by the choice; Wasserstein
units differ (rank space vs raw-ID space) — rank space is the more meaningful
of the two, since raw-ID gaps carry no physical meaning.

## Statistics

Per file, for each quantity: count, min, max, mean, median, std, and the
1/25/75/99 percentiles.

## Statistical tests (`--stat-tests`)

Every non-reference file is compared to the reference on each quantity:

```
    dataword    : KS D=      0.0022  p=        1  W1=      0.0317663  CvM T=     0.03002  p=    0.976  chi2=          78/105  p=    0.978
                  bias: offset=  +0.872 sigma  slope=  -0.0013 sigma/bin  max|pull|=  1.976 @ bin 17
```

| Test | Answers | Notes |
|---|---|---|
| **KS D + p** | how surprising is the max CDF gap? | unbinned, distribution-free; most sensitive near the median; p-values saturate at large N |
| **Cramér–von Mises T + p** | integrated squared CDF difference | also unbinned; balances bulk and tails better than KS; scipy (`method="auto"`, exact for small samples) |
| **Wasserstein-1** | how big is the discrepancy in field units? | no p-value, but the physically interpretable number; use to rank candidates against a common reference |
| **chi² / ndf + p** | omnibus binned agreement | uses the same pooled binning as the plots; zero-pool bins excluded; ndf = used bins − 1 |
| **pull bias metrics** | *where* and *in what shape* do they differ? | from the per-bin pulls (see below) |

Reading guidance:

- With ~45k packets/file, KS/CvM p-values are extremely powerful: expect
  p ≈ 0 or 1 and little discrimination in between. W1 and the bias metrics
  carry the ranking information.
- KS/CvM are *unbinned*; chi² and pulls are *binned*. Agreement between the
  two families is a consistency check; disagreement usually means binning is
  diluting a real feature (try more `--bins`).
- chi² p ≈ 1 with thousands of counts per bin means the distributions are
  agreement to well below statistical precision.

### Pull bias metrics

The pulls are per-bin signed deviations
`(file − ref) / sqrt(ref + file)` (Poisson σ of the bin-count difference).
Three numbers summarize their structure:

- **`offset` (σ)** — `sum(pulls)/sqrt(n_used)`: sensitivity to a global
  yield/normalization bias. |offset| ≳ 2–3 means coherent same-sign excess
  or deficit; a −5% yield drop reads ≈ −4.6σ on this data.
- **`slope` (σ/bin)** — least-squares slope of pulls vs bin center:
  sensitivity to a ramp/gradient bias (gain drift). Read it together with
  offset: a mild ramp also produces a nonzero offset; slope separates the
  *shape* (null ≈ 0.000–0.001 σ/bin here, ~10× less than an injected ramp).
- **`max|pull|` @ bin** — worst single bin. A large value with modest
  offset/slope flags a localized feature (e.g. a distribution edge shifted
  across a bin boundary); a lone 3σ bin among hundreds can be a fluctuation —
  check whether the same bin is hot in all files.

## Plots

Written once per run (not per file) to `--plot-dir`:

| File | Content |
|---|---|
| `<quantity>_hist.png` | all files overlaid, common pooled bin edges |
| `<quantity>_pulls.png` | per-bin pulls of every non-reference file vs the reference, grouped bars, ±2σ guides |
| `dataword_violin.png` | one violin per file (mean dotted, median solid) |

Bin edges are computed **once per quantity from all files pooled** and shared
by the histograms, the chi² test, and the pulls — the plot and the numbers
always describe the same binning. Linear x-range is clipped to the edge
range (no dead axis beyond the last bin); `--log-timestamp-dt` switches the
`timestamp_dt` histogram to log-spaced edges (zeros captured in a leading
bin).

Pull-plot pattern cheatsheet:

| Pattern | Diagnosis |
|---|---|
| dipole (− below x₀, + above) | mean/offset bias (e.g. gain shift) |
| center −, tails + (or mirror) | width/scale change |
| all same sign, similar size | yield/normalization mismatch |
| localized same-sign cluster | localized distortion (pileup, channel excess) |
| same-sign runs, each < 2σ | correlated trend — the thing neither KS nor CvM localizes |

## JSON summary

`--json PATH` writes mode, reference basename, bins, density flag, and a
flat per-file dict:

```json
{
  "mode": "diff_commit",
  "stat_tests": true,
  "reference": "fsd_runner_run_d7baafc_20261026_154406_01.hdf5",
  "bins": 100,
  "density": false,
  "files": {
    "<basename>": {
      "label": "8398363",
      "n_packets": 45807,
      "stats": {
        "dataword":     {"count": …, "min": …, "max": …, "mean": …,
                         "median": …, "std": …, "q01": …, "q25": …,
                         "q75": …, "q99": …},
        "pixel_id":     {…}, "timestamp": {…}, "timestamp_dt": {…}
      },
      "tests_vs_reference": {
        "dataword": {"ks_stat": …, "ks_pvalue": …, "wasserstein": …,
                     "cvm_stat": …, "cvm_pvalue": …,
                     "chi2": …, "chi2_pvalue": …, "chi2_ndf": …,
                     "pull_offset_z": …, "pull_slope_z_per_bin": …,
                     "pull_max_abs": …, "pull_argmax_bin": …},
        "pixel_id": {…}, "timestamp": {…}, "timestamp_dt": {…}
      }
    }
  }
}
```

`tests_vs_reference` is present only with `--stat-tests` and only for
non-reference files. Labels are the git short hash parsed from
`fsd_runner_run_<ref>_<date>_<time>_<seed>.hdf5` filenames (fallback: the
basename without `.hdf5`).

## Styling

The `STYLE_SAME_COMMIT` / `STYLE_DIFF_COMMIT` dicts at the top of the script
are the single styling knob. They are interchangeable: any plot function
takes a style dict and uses its fixed `"color"` when present, otherwise
entry *i* of its `"palette"` (`"default_cycle"`, or any matplotlib colormap
name). `alpha`, `histtype`, `lw`, and `legend_note` are honored wherever they
appear.

## Relationship to the other validators

- `validate_dists.py` — two files, unbinned KS + W1, per-field deltas. This
  script copies its stat/edge/KS/W1 implementations verbatim so numbers agree
  to float precision for the same file pair.
- `validate_sim.py` — per-entry verdicts; answers "which entries differ".
- `validate_multifile.py` — N files, adds CvM/chi²/pulls, binned view, and
  the reference-based screening workflow. Run it as the screening layer;
  drill into a flagged pair with `validate_dists.py`.
