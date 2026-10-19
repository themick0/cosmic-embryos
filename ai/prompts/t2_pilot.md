# T2 embryo calibration pilot

This assignment requires exactly one selected prediction for
`T2:embryo:val_interp`, at E7.5. Only the three embryo measurements enumerated in
`context.json` are permitted. Do not access heart data or external measurements.
This is a bounded calibration experiment, not a broad method search.

## Frozen software requirement

Before this run is prepared, the operator must install the official local scorer
from `https://github.com/aristoteleo/veckit.git` at commit
`cc3859ab2a2305efc4544815d3f44f660efa77d4` (package version `0.1.1`, MIT licence)
in the same Python environment as the harness. The exact installation requirement
is `veckit @ git+https://github.com/aristoteleo/veckit.git@cc3859ab2a2305efc4544815d3f44f660efa77d4`.
Its declared dependencies are NumPy, SciPy, AnnData and scikit-learn. The operator
must resolve and retain their exact installed versions before preparation and
exercise the scorer on synthetic data. No dependency installation is permitted
inside this locked, network-disabled run.

At startup verify that `importlib.metadata.distribution("veckit")` reports
version `0.1.1`, its `direct_url.json` records the exact Git commit above, and
`from veckit import score` succeeds. If this contract is absent or different,
return `blocked`; do not silently replace the evaluator. Record this provenance
and the installed dependency versions in a reproduction file.

Create temporary and cache directories inside this run's `work/`. For numerical
subprocesses, set `TMPDIR`, `JOBLIB_TEMP_FOLDER`, `XDG_CACHE_HOME` and
`MPLCONFIGDIR` to those directories before importing scientific libraries;
the operating-system temporary directory and home are not writable by research
tools. Set `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and
`NUMEXPR_NUM_THREADS` to `1` to avoid nested thread pools competing for the
container's three CPUs. Record these settings in the reproduction command.

## Development protocol

Withhold E7.25 from constructing the two development predictions. Fit only to
E6.75 and E8.0, with development target time 7.25. Read the withheld expression,
coordinates and labels only in evaluation code after both candidate files have
been generated, or after the second candidate's declared time limit fails.
Fitting code must not receive the E7.25 file. The fixed scorer may use the
withheld data internally to define its evaluation space; do not reuse that
space, labels or fitted probe in prediction construction.

Use the frozen board panel and its exact gene order for every scorer input,
including local copies of reference and target. The current panel has 498 genes;
the frozen panel remains authoritative. Preserve expression preprocessing if
inputs are already non-negative and log-normalised. Do not use withheld data to
choose genes, expression transformations, coordinate registration or scales.

Generate exactly 1,000 cells per candidate and for the final prediction, provided
this remains within the frozen board bounds; otherwise return `blocked`.
Set construction and evaluator seeds to 42. Draw cells without replacement
within each source when enough exist, with replacement only when needed; report
the duplicated fraction. Keep each selected cell's expression and position
together. Never pair cells from separate stages by row index.

Construct only these two comparators:

1. **Nearest stage:** choose the allowed fitting stage nearest to the requested
   time, breaking ties toward the earlier stage, and resample its joint cells.
   Preserve that stage's coordinates and expression. For development this is
   E6.75. This is a nearest-stage comparator, not a claim to reproduce the
   official board's `copy_last` floor.
2. **Time-weighted mixture:** use the two fitting stages bracketing the requested
   time. Allocate cells in proportion to linear time weights, with the earlier
   allocation rounded to the nearest integer and the remainder assigned later.
   For E7.25 the weights are 0.6 for E6.75 and 0.4 for E8.0. Preserve each sampled
   cell's expression. Centre each fitting cloud by its own centroid, estimate its
   own PCA axes and RMS radius, and express coordinates in that canonical frame.
   Choose PCA signs deterministically from the fitting cloud alone, require a
   proper rotation, and document the convention and remaining alignment
   ambiguity. Restore a common radius given by linear interpolation of the two
   fitting RMS radii. No target-dependent registration, cell matching or scale
   adjustment is allowed. Do not search alternative alignment conventions.

## Evaluation and selection

Use `veckit.score(task="T2", setting="embryo", input=prediction_path,
target=withheld_E7_25_panel_path, reference=E6_75_panel_path, seed=42)` in a fresh
bounded subprocess for each candidate. Always pass the real E6.75 reference
explicitly; letting the scorer default its reference to each prediction changes
the question. Preserve complete returned JSON and subprocess logs. The scorer's
`setting` argument labels metadata; it does not load or discover target data.

The official evaluation page and the frozen panel's anchors identify these
eight ranking components. The pinned scorer returns additional diagnostics;
retain those but do not add them to selection.

| Metric key | Preferred value |
| --- | --- |
| `de_score` | Higher |
| `de_direction` | Higher |
| `mmd_u` | Lower |
| `variogram` | Lower |
| `d2_shape` | Lower |
| `occupancy_dice` | Higher |
| `scale_log_ratio` | Smaller absolute value |
| `neighborhood_mmd` | Lower |

Use the mean of ranks over these eight metrics as the fixed local selection
heuristic: rank 1 is better, rank 2 is worse, and exact ties receive 1.5. A missing
or non-finite component loses to a finite component; two unavailable components
tie and remain explicitly reported. Select the lower mean rank, breaking a tie
toward nearest stage. If both candidates have no finite ranking components,
return `blocked` because evaluation failed. If the second candidate cannot be
constructed or evaluated in its fixed budget, retain the valid, successfully
evaluated nearest-stage baseline and disclose the failure.

Mean rank is a local heuristic, not the official weighted skill score. The
published E7.5 floor/ceiling anchors do not calibrate an E7.25 pseudo-target;
do not reuse them to claim an official score. Selection on E7.25 makes these
development results, not an independent estimate of hidden E7.5 accuracy.

## Final generation and time budget

Total wall time is 1,800 seconds, including model interaction and numerical
work. Reserve the last 450 seconds for final generation, validation, reports
and the final JSON response. Do not start a numerical subprocess unless its
timeout leaves that reserve intact. Give each candidate's construction and
evaluation at most 300 seconds combined; stop new comparator work at elapsed
1,350 seconds. These inner limits are agent responsibilities; the harness's
outer timeout is not a successful finalisation procedure.

After selection, make all three permitted stages available to the fixed method
and generate E7.5. Nearest stage now selects E7.25. The mixture uses bracketing
E7.25 and E8.0 with weights 2/3 and 1/3, applying the same fitting-only coordinate
procedure. E6.75 remains an allowed input and development reference; do not force
it into the final mixture or change the method to make every input contribute.
Do not perform another method-selection loop after this refit. If the official
phase changes during execution, complete this frozen E7.5 assignment; eligibility
of any later upload belongs to the external submission operator.

Validate the selected prediction using the frozen harness and retain the
validation result. Include both development outputs, the full metric results,
selection calculation, experiment journal, implementation, exact regeneration
commands, resource observations and limitations in the declared reproduction
files. Record elapsed time and peak process memory where measurable; do not
invent unavailable token or subscription-usage measurements. Return the final
JSON required by the shared prompt. No official upload is part of this run.

## Source references

- Scorer implementation: https://github.com/aristoteleo/veckit/tree/cc3859ab2a2305efc4544815d3f44f660efa77d4
- Metric outputs: https://github.com/aristoteleo/veckit/blob/cc3859ab2a2305efc4544815d3f44f660efa77d4/T2/metrics_v2.py
- CLI and Python argument semantics: https://github.com/aristoteleo/veckit/blob/cc3859ab2a2305efc4544815d3f44f660efa77d4/score_h5ad.py
- Current official ranking components: https://virtualembryo.ai/challenge/evaluation?section=scoring&task=2

These references document the frozen protocol; they do not authorise browsing
inside the locked run. Include software provenance and licences in `sources`.
