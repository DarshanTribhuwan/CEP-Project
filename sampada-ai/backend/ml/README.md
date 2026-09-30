# `ml/` — reserved, currently empty

This directory was part of the original scaffold. **Nothing is implemented in it
yet**, and there is deliberately no placeholder model here.

Where the model logic actually lives today:

| Concern | Location |
|---|---|
| Soil evidence fusion | `app/services/soil.py` |
| Price estimation (naive baseline) | `app/services/price.py` |
| Crop suitability scoring | `app/services/suitability.py` |
| Community / oversupply signal | `app/services/community.py` |
| Reference tables used by the above | `app/data/*.json` |

These are versioned through the `[ml]` section of `config.toml`
(`soil_model_version`, `price_model_version`, …), which every recommendation
stores alongside its result so a later outcome can be traced to the model that
produced it.

## What belongs here later

Trained artefacts and their training code, once there is data to train on:

- a soil classification model, which requires a labelled image dataset that does
  not exist in this repository — do not add a model here and claim an accuracy
  figure without a held-out evaluation;
- a price forecasting model, which requires real historical mandi data and a
  time-based train/test split;
- the candidate-vs-incumbent evaluation harness the design called for, so that
  no model is promoted to production without beating the current one on unseen
  data.

Until then this stays empty on purpose: an empty directory is an honest signal,
whereas a stub model with an invented accuracy number is not.
