# trade-data-toolkit

A Python toolkit for processing and analyzing Chinese customs trade data.

This project supports automatic CSV parsing, provincial trade comparison, export structure analysis, market concentration analysis, and report-ready visualization generation.

## Features

* Automatic CSV header detection
* Encoding fallback support (UTF-8 / GBK / GB18030)
* Batch processing of multiple trade datasets
* Provincial export structure comparison
* Export market analysis
* Zhejiang vs. National structure comparison
* Heatmap and bar chart generation
* PNG table export for policy reports

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Generate trade analysis charts:

```bash
python Trade_Analysis.py \
    --data-dir ./csvs \
    --group-by 伙伴国 \
    --top-n 8 \
    --outdir ./plots
```

Generate five-province comparison tables:

```bash
python Trade_Analysis.py \
    --data-dir ./csvs \
    --five-provinces 福建,广东,江苏,山东,浙江 \
    --tables
```

Generate Zhejiang vs. National comparison:

```bash
python Trade_Analysis.py \
    --data-dir ./csvs \
    --zj-vs-national \
    --tables
```

## Data

Trade datasets are not included in this repository due to licensing and data-sharing restrictions.

Users should obtain the original datasets from authorized sources.

## Dependencies

* pandas
* matplotlib
* seaborn

## License

For academic and research purposes.
