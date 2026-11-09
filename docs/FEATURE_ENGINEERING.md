# Feature engineering — first implementation for Issue #1

**Status:** Implemented as a first building block. This is **not** the promised Isolation Forest model; that remains future development.

## Command

```powershell
.\.venv\Scripts\rigpulse.exe features --db rigpulse.db --out reports/features.csv --bucket-minutes 60 --window 12
```

The command creates a CSV with one row per rig and UTC time bucket. There is no training and it never uses synthetic event labels.

## Causality and semantics

* A bucket is a half-open UTC interval `[bucket_start, bucket_end)` and is **labeled by its end time**, when its observations are fully available. For example, a reading at 00:00 belongs to the bucket ending 01:00. A reading at exactly 01:00 belongs to the *next* bucket.
* If multiple observations of the same metric occur in a bucket, the latest is retained. This makes the export a snapshot at the bucket end, **not** the original measurement stream.
* Each rig is processed separately. Entirely empty buckets between its first and last observation are present with missing indicators. No interpolation, backward fill, or cross-rig borrowing occurs.
* The `*_past_median`, `*_past_std`, `*_history_count`, and `*_delta_1` features use **only preceding buckets**. The `*_delta_1` feature is absent if the immediately prior bucket is missing, instead of compressing time gaps.
* Current-bucket ratios are available only if both inputs are present and the denominator is nonzero. `vibration_flow_ratio_delta_1` uses the preceding bucket's ratio, not a future ratio.
* If sensor measurements are delayed in real equipment, this data model would need `ingested_at`/availability timestamps before an online causality guarantee could be made. The simulator currently assumes observation time matches availability time.

## Export columns

- `rig_id`, `bucket_end_utc`, `present_metrics`
- For each metric (temperature, pressure, vibration, flow): `metric`, `metric_missing`, `metric_history_count`, `metric_past_median`, `metric_past_std`, `metric_delta_1`
- `pressure_flow_ratio`, `vibration_flow_ratio`, `vibration_flow_ratio_delta_1`

Missing numerical values are empty CSV cells. `_missing` is 1 if the metric is absent in the completed bucket, otherwise 0. `_history_count` is the number of nonmissing readings within the preceding `window` buckets; `_past_std` is the population standard deviation, left empty when fewer than 2 samples are present. No scaling is performed: downstream ML must fit normalizers on training history only.

## What remains in Issue #1

1. Add an explicit training-only time split and fit an Isolation Forest on the feature matrix, including a strategy for missing values.
2. Compare an ML detector to the existing operating-limit / robust-z baselines, reporting alert rate and errors as well as F1.
3. Add independent holdout scenarios and validation across rigs, avoiding synthetic-label leakage.
4. Add stable ML artifact serialization, model versioning, and integration with the dashboard and alert APIs.

This prototype uses synthetic data only. Do not infer suitability for equipment safety systems from the demo evaluation.
