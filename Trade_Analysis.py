#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析.py

Purpose: Read the exported CSV files (from the screenshots) which include a small
metadata header area and then a data table with columns like 年度, 月度, 指数, 值, 贸易方式 etc.

This script provides:
- automatic detection of the data header row
- encoding fallback (utf-8, gbk)
- functions to load many CSVs from a directory and produce comparison plots of the '值' time series

Usage examples (in README):
	python 分析.py --data-dir /path/to/csvs --product-filter 动植物油 --group-by 伙伴国 --outdir ./plots

"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import logging
from typing import List, Tuple

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib import font_manager as fm
import matplotlib as mpl

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def ensure_chinese_font() -> None:
	"""Ensure matplotlib has a Chinese-capable font set (best-effort).
	This tries common macOS font names and file locations, then falls back to
	scanning available fonts. It sets plt.rcParams['font.family'] and
	plt.rcParams['axes.unicode_minus'] = False.
	"""
	plt.rcParams['axes.unicode_minus'] = False
	# Common macOS Chinese fonts
	font_candidates = ['PingFang SC', 'Heiti SC', 'STHeiti', 'STHeiti Medium', 'Songti SC', 'SimHei']
	names = [f.name for f in fm.fontManager.ttflist]
	for name in font_candidates:
		if name in names:
			plt.rcParams['font.family'] = name
			logging.info('Using system font for Chinese: %s', name)
			return

	# Try to add known font files (macOS locations)
	font_files = [
		'/System/Library/Fonts/PingFang.ttc',
		'/System/Library/Fonts/STHeiti Medium.ttc',
		'/System/Library/Fonts/STHeiti.ttf',
		'/System/Library/Fonts/Supplemental/Songti.ttc',
		'/Library/Fonts/SimHei.ttf',
	]
	for p in font_files:
		if os.path.exists(p):
			try:
				fm.fontManager.addfont(p)
				# refresh names
				names = [f.name for f in fm.fontManager.ttflist]
				# pick the first new font we added that's Chinese-looking
				for n in font_candidates:
					if n in names:
						plt.rcParams['font.family'] = n
						logging.info('Registered and using font from file: %s', p)
						return
			except Exception:
				continue

	# As a last resort, pick any font that declares a CJK range by checking names
	for f in fm.fontManager.ttflist:
		if any(part in f.name for part in ['Hei', 'Song', 'Fang', 'Sim']):
			plt.rcParams['font.family'] = f.name
			logging.warning('Falling back to font %s for Chinese labels', f.name)
			return

	logging.warning('No Chinese-capable font found; Chinese labels may not render correctly')


def find_csv_files(data_dir: str, pattern: str = '**/*.csv', recursive: bool = True) -> List[str]:
	"""Find CSV files under data_dir.

	By default this searches recursively (pattern '**/*.csv').
	"""
	path = os.path.join(data_dir, pattern)
	files = sorted(glob.glob(path, recursive=recursive))
	logging.info('Found %d files in %s (pattern=%s, recursive=%s)', len(files), data_dir, pattern, recursive)
	return files


def detect_header_row(path: str, encodings=('utf-8', 'gb18030', 'gbk', 'latin1')) -> Tuple[int, str]:
	"""Detect the header row index (0-based) by scanning the first 40 lines
	and finding a row that contains both '年度' and '值'. Returns (skiprows, encoding).
	If none found, returns (0, encoding).
	"""
	for enc in encodings:
		try:
			with open(path, 'r', encoding=enc, errors='replace') as f:
				for i in range(40):
					line = f.readline()
					if not line:
						break
					if '年度' in line and '值' in line:
						# header is this line (0-based index i), so skiprows = i
						logging.debug('Detected header at line %d using encoding %s for %s', i, enc, path)
						return i, enc
		except Exception:
			continue
	# Fallback: try to read first row as header
	logging.debug('No explicit header row detected for %s; defaulting to 0/utf-8', path)
	return 0, 'utf-8'


def read_trade_csv(path: str) -> pd.DataFrame:
	"""Read a single trade CSV and return a DataFrame with a unified datetime column 'period'.
	The CSVs from the provider often contain some metadata rows before the real header.
	"""
	skiprows, encoding = detect_header_row(path)
	# pandas expects header row index relative to file start; pass header=0 after skipping
	try:
		df = pd.read_csv(path, header=0, skiprows=skiprows, encoding=encoding)
	except Exception as e:
		logging.warning('Failed to read %s with encoding %s: %s; trying engine python', path, encoding, e)
		df = pd.read_csv(path, header=0, skiprows=skiprows, encoding=encoding, engine='python')

	# Normalize column names by stripping spaces
	df.columns = [str(c).strip() for c in df.columns]

	# If 年度 and 月度 both exist, create a datetime-like period column
	if '年度' in df.columns and '月度' in df.columns:
		# Some 月度 values may be '0' or empty for annual data
		def to_period(row):
			try:
				year = int(row['年度'])
				month = int(row['月度']) if pd.notnull(row['月度']) and str(row['月度']).strip() != '' else 1
				return pd.Timestamp(year=year, month=max(1, month), day=1)
			except Exception:
				return pd.NaT

		df['period'] = df.apply(to_period, axis=1)
	else:
		# fallback: try to find a column that looks like a time column
		for col in df.columns:
			if '期' in col or '时间' in col or 'year' in col.lower():
				try:
					df['period'] = pd.to_datetime(df[col], errors='coerce')
					break
				except Exception:
					continue

	# Normalize value column name (common label is '值' or 'value')
	value_col = None
	for candidate in ['值', 'value', 'Value', 'VAL']:
		if candidate in df.columns:
			value_col = candidate
			break
	if value_col is None:
		# try to find numeric column besides common metadata
		numeric_cols = df.select_dtypes(include='number').columns.tolist()
		if numeric_cols:
			value_col = numeric_cols[0]

	if value_col is not None:
		df['value'] = pd.to_numeric(df[value_col], errors='coerce')
	else:
		df['value'] = pd.NA

	# attach source filename for tracing
	df['__source_file__'] = os.path.basename(path)
	# attach source dir name
	df['__source_dir__'] = os.path.basename(os.path.dirname(path))

	# try to infer province from file path or dirname
	known_provinces = ['福建', '广东', '江苏', '山东', '浙江', '北京', '上海', '天津', '重庆', '安徽', '湖南', '湖北', '河北', '河南', '辽宁', '吉林', '黑龙江', '广西', '四川', '云南', '贵州', '陕西', '甘肃', '海南', '河南']
	def infer_province(p: str) -> str:
		for prov in known_provinces:
			if prov in p:
				return prov
		return ''

	df['__source_province__'] = infer_province(path) or infer_province(df['__source_dir__'].astype(str).iloc[0])

	return df


def load_many_csvs(files: List[str], max_files: int = 50) -> pd.DataFrame:
	dfs = []
	for i, p in enumerate(files):
		if i >= max_files:
			logging.info('Max files reached (%d); stopping load', max_files)
			break
		try:
			d = read_trade_csv(p)
			dfs.append(d)
		except Exception as e:
			logging.warning('Failed to load %s: %s', p, e)
	if not dfs:
		return pd.DataFrame()
	combined = pd.concat(dfs, ignore_index=True, sort=False)
	return combined


def aggregate_and_plot(df: pd.DataFrame, group_by: str = '伙伴国', outdir: str = './plots', top_n: int = 6, show: bool = False):
	os.makedirs(outdir, exist_ok=True)
	if df.empty:
		logging.error('Empty dataframe; nothing to plot')
		return

	# Use group_by column if present
	if group_by not in df.columns:
		logging.warning('group_by column %s not present; using __source_file__', group_by)
		group_by = '__source_file__'

	# Ensure period exists
	if 'period' not in df.columns:
		logging.error('No period column found; cannot time-series plot')
		return

	df2 = df.dropna(subset=['period', 'value'])
	if df2.empty:
		logging.error('No numeric time series data found after dropping NA')
		return

	# compute total by group
	totals = df2.groupby(group_by)['value'].sum().sort_values(ascending=False)
	top_groups = totals.head(top_n).index.tolist()

	plot_df = df2[df2[group_by].isin(top_groups)].copy()
	# resample to monthly (period already at month start) and sum
	plot_df.set_index('period', inplace=True)
	# pandas 3.x no longer supports 'M' offset alias; use 'ME' for month end grouping
	ts = plot_df.groupby([pd.Grouper(freq='ME'), group_by])['value'].sum().reset_index()

	plt.figure(figsize=(10, 6))
	sns.lineplot(data=ts, x='period', y='value', hue=group_by, marker='o')
	plt.title(f'前{top_n} {group_by} 值 趋势')
	plt.ylabel('值')
	plt.xlabel('时间')
	plt.tight_layout()
	outpath = os.path.join(outdir, f'top_{top_n}_{group_by}.png')
	plt.savefig(outpath)
	logging.info('Saved plot to %s', outpath)
	if show:
		plt.show()
	plt.close()


def plot_top_bars(df: pd.DataFrame, outdir: str = './plots', top_n: int = 10, show: bool = False):
	"""Plot two bar charts:
	- 前 top_n 伙伴国（按 value 总和）
	- 前 top_n 商品品类（按 value 总和，使用 列 '编码解释' 作为商品描述）
	This function sets macOS Chinese font before plotting as requested.
	"""
	os.makedirs(outdir, exist_ok=True)
	if df.empty:
		logging.error('Empty dataframe; nothing to plot')
		return

	# Ensure value present
	if 'value' not in df.columns:
		logging.error('No value column found; cannot aggregate')
		return

	df2 = df.dropna(subset=['value'])
	if df2.empty:
		logging.error('No numeric values to plot')
		return

	# Ensure a Chinese-capable font is registered and set for matplotlib
	ensure_chinese_font()

	# Top partners
	if '伙伴国' in df2.columns:
		partners = df2.groupby('伙伴国')['value'].sum().sort_values(ascending=False).head(top_n)
		plt.figure(figsize=(10, 6))
		sns.barplot(x=partners.values, y=partners.index, palette='viridis')
		plt.xlabel('出口额 (USD)')
		plt.title(f'前{top_n} 伙伴国（按出口额）')
		plt.tight_layout()
		outpath1 = os.path.join(outdir, f'top_{top_n}_伙伴国_bar.png')
		plt.savefig(outpath1)
		logging.info('Saved partners bar plot to %s', outpath1)
		if show:
			plt.show()
		plt.close()
	else:
		logging.warning('No 伙伴国 column to compute top partners')

	# Top products (use 编码解释 if present)
	prod_col = None
	for c in ['编码解释', '商品名', '商品类别', '商品']:
		if c in df2.columns:
			prod_col = c
			break
	if prod_col is None and '商品编码' in df2.columns:
		prod_col = '商品编码'

	if prod_col is not None:
		prods = df2.groupby(prod_col)['value'].sum().sort_values(ascending=False).head(top_n)
		plt.figure(figsize=(10, 6))
		sns.barplot(x=prods.values, y=prods.index, palette='magma')
		plt.xlabel('出口额 (USD)')
		plt.title(f'前{top_n} 商品品类（按出口额）')
		plt.tight_layout()
		outpath2 = os.path.join(outdir, f'top_{top_n}_商品品类_bar.png')
		plt.savefig(outpath2)
		logging.info('Saved products bar plot to %s', outpath2)
		if show:
			plt.show()
		plt.close()
	else:
		logging.warning('No product description column found to compute top products')


def plot_province_structure_bar(df: pd.DataFrame, provinces: List[str], outdir: str = './plots', top_n: int = 8, show: bool = False):
	"""Grouped bar chart comparing export structure (by product category) across the provided provinces.
	We pick the top_n product categories by national export amount and show grouped bars for each province.
	"""
	os.makedirs(outdir, exist_ok=True)
	# determine product column
	prod_col = None
	for c in ['编码解释', '商品名', '商品类别', '商品']:
		if c in df.columns:
			prod_col = c
			break
	if prod_col is None:
		prod_col = '商品编码' if '商品编码' in df.columns else None
	if prod_col is None:
		logging.error('No product column found for province structure plot')
		return

	# ensure province info
	if '__source_province__' not in df.columns:
		logging.error('No inferred province info; cannot plot province comparisons')
		return

	# compute national top categories
	national_top = df.groupby(prod_col)['value'].sum().sort_values(ascending=False).head(top_n).index.tolist()

	# build dataframe of values for each province and each category
	data = []
	for prov in provinces:
		sub = df[df['__source_province__'] == prov]
		sums = sub.groupby(prod_col)['value'].sum().reindex(national_top).fillna(0)
		for cat, val in sums.items():
			data.append({'province': prov, 'category': cat, 'value': val})
	plot_df = pd.DataFrame(data)

	ensure_chinese_font()
	plt.figure(figsize=(12, 6))
	sns.barplot(data=plot_df, x='category', y='value', hue='province')
	plt.xticks(rotation=45, ha='right')
	plt.ylabel('出口额 (USD)')
	plt.xlabel('商品品类')
	plt.title('五省出口结构对比（按商品品类）')
	plt.tight_layout()
	outpath = os.path.join(outdir, 'five_province_structure_bar.png')
	plt.savefig(outpath)
	logging.info('Saved province structure bar to %s', outpath)
	if show:
		plt.show()
	plt.close()


def plot_province_market_heatmap(df: pd.DataFrame, provinces: List[str], outdir: str = './plots', top_partners: int = 12, show: bool = False):
	"""Heatmap of export markets: rows = provinces, columns = top partners (by national export), values = export amount."""
	os.makedirs(outdir, exist_ok=True)
	if '__source_province__' not in df.columns:
		logging.error('No inferred province info; cannot plot heatmap')
		return
	if '伙伴国' not in df.columns:
		logging.error('No 伙伴国 column present; cannot compute markets')
		return

	# top partners nationally
	top_partners_list = df.groupby('伙伴国')['value'].sum().sort_values(ascending=False).head(top_partners).index.tolist()

	matrix = []
	for prov in provinces:
		row = []
		sub = df[df['__source_province__'] == prov]
		sums = sub.groupby('伙伴国')['value'].sum()
		for partner in top_partners_list:
			row.append(sums.get(partner, 0.0))
		matrix.append(row)

	import numpy as np
	mat = np.array(matrix)

	ensure_chinese_font()
	plt.figure(figsize=(max(8, len(top_partners_list)*0.6), max(4, len(provinces)*0.6)))
	sns.heatmap(mat, xticklabels=top_partners_list, yticklabels=provinces, cmap='YlGnBu', annot=True, fmt='.0f')
	plt.xlabel('伙伴国')
	plt.ylabel('省份')
	plt.title('五省出口市场比较热力图')
	plt.tight_layout()
	outpath = os.path.join(outdir, 'five_province_market_heatmap.png')
	plt.savefig(outpath)
	logging.info('Saved province market heatmap to %s', outpath)
	if show:
		plt.show()
	plt.close()


def plot_zhejiang_vs_national_category_share(df: pd.DataFrame, outdir: str = './plots', top_n: int = 10, show: bool = False):
	"""Horizontal bar chart comparing Zhejiang vs National category share."""
	os.makedirs(outdir, exist_ok=True)
	prod_col = None
	for c in ['编码解释', '商品名', '商品类别', '商品']:
		if c in df.columns:
			prod_col = c
			break
	if prod_col is None and '商品编码' in df.columns:
		prod_col = '商品编码'
	if prod_col is None:
		logging.error('No product column for Zhejiang vs national plot')
		return

	# total national
	national = df.groupby(prod_col)['value'].sum()
	top_cats = national.sort_values(ascending=False).head(top_n).index.tolist()

	zhejiang = df[df['__source_province__'] == '浙江'].groupby(prod_col)['value'].sum()
	data = []
	for cat in top_cats:
		nat_val = national.get(cat, 0.0)
		zj_val = zhejiang.get(cat, 0.0)
		nat_share = nat_val / national.sum() if national.sum() > 0 else 0
		zj_share = zj_val / zhejiang.sum() if zhejiang.sum() > 0 else 0
		data.append({'category': cat, 'national_share': nat_share, 'zj_share': zj_share})

	plot_df = pd.DataFrame(data).set_index('category')
	plot_df = plot_df.sort_values('national_share')

	ensure_chinese_font()
	plt.figure(figsize=(8, max(4, top_n*0.4)))
	plot_df[['zj_share', 'national_share']].plot(kind='barh')
	plt.xlabel('占比')
	plt.title('浙江 vs 全国 商品品类占比（前{}）'.format(top_n))
	plt.tight_layout()
	outpath = os.path.join(outdir, 'zhejiang_vs_national_category_share.png')
	plt.savefig(outpath)
	logging.info('Saved Zhejiang vs national category share to %s', outpath)
	if show:
		plt.show()
	plt.close()


def create_provinces_top_partners_table(df: pd.DataFrame, provinces: List[str], outdir: str = './plots', top_n: int = 10) -> None:
	"""Create CSV table: for each province, the top_n partners by export value.
	Output columns: province, rank, partner, value
	"""
	os.makedirs(outdir, exist_ok=True)
	rows = []
	for prov in provinces:
		sub = df[df['__source_province__'] == prov]
		if sub.empty:
			continue
		partners = sub.groupby('伙伴国')['value'].sum().sort_values(ascending=False).head(top_n)
		for i, (partner, val) in enumerate(partners.items(), start=1):
			rows.append({'province': prov, 'rank': i, 'partner': partner, 'value': float(val)})
	out_df = pd.DataFrame(rows)
	outpath = os.path.join(outdir, 'five_provinces_top_partners.csv')
	out_df.to_csv(outpath, index=False)
	logging.info('Saved provinces top partners table to %s', outpath)
	# also export styled PNG table
	png_path = os.path.join(outdir, 'five_provinces_top_partners.png')
	df_to_png_table(out_df, png_path, title='五省前10出口市场', value_cols=['value'])
	logging.info('Saved provinces top partners PNG table to %s', png_path)
	# also export pivot-style table: rows = rank, cols = provinces, cells = partner name
	pivot_png = os.path.join(outdir, 'five_provinces_top_partners_pivot.png')
	create_provinces_pivot_png(df, provinces, outpath=pivot_png, top_n=top_n, kind='partner')
	logging.info('Saved provinces top partners pivot PNG to %s', pivot_png)


def create_provinces_top_products_table(df: pd.DataFrame, provinces: List[str], outdir: str = './plots', top_n: int = 10) -> None:
	"""Create CSV table: for each province, the top_n products by export value.
	Output columns: province, rank, product, value
	"""
	os.makedirs(outdir, exist_ok=True)
	prod_col = None
	for c in ['编码解释', '商品名', '商品类别', '商品']:
		if c in df.columns:
			prod_col = c
			break
	if prod_col is None and '商品编码' in df.columns:
		prod_col = '商品编码'
	if prod_col is None:
		logging.error('No product column found; cannot create products table')
		return

	rows = []
	for prov in provinces:
		sub = df[df['__source_province__'] == prov]
		if sub.empty:
			continue
		prods = sub.groupby(prod_col)['value'].sum().sort_values(ascending=False).head(top_n)
		for i, (prod, val) in enumerate(prods.items(), start=1):
			rows.append({'province': prov, 'rank': i, 'product': prod, 'value': float(val)})
	out_df = pd.DataFrame(rows)
	outpath = os.path.join(outdir, 'five_provinces_top_products.csv')
	out_df.to_csv(outpath, index=False)
	logging.info('Saved provinces top products table to %s', outpath)
	png_path = os.path.join(outdir, 'five_provinces_top_products.png')
	df_to_png_table(out_df, png_path, title='五省前10出口商品', wrap_cols=['product'], value_cols=['value'])
	logging.info('Saved provinces top products PNG table to %s', png_path)
	pivot_png = os.path.join(outdir, 'five_provinces_top_products_pivot.png')
	create_provinces_pivot_png(df, provinces, outpath=pivot_png, top_n=top_n, kind='product')
	logging.info('Saved provinces top products pivot PNG to %s', pivot_png)


def create_zhejiang_vs_national_table(df: pd.DataFrame, outdir: str = './plots', top_n: int = 10) -> None:
	"""Create CSV comparing Zhejiang vs national export structure for top_n categories.
	Output columns: category, national_value, national_share, zj_value, zj_share
	"""
	os.makedirs(outdir, exist_ok=True)
	prod_col = None
	for c in ['编码解释', '商品名', '商品类别', '商品']:
		if c in df.columns:
			prod_col = c
			break
	if prod_col is None and '商品编码' in df.columns:
		prod_col = '商品编码'
	if prod_col is None:
		logging.error('No product column found; cannot create Zhejiang vs national table')
		return

	national = df.groupby(prod_col)['value'].sum()
	top_cats = national.sort_values(ascending=False).head(top_n).index.tolist()
	zhejiang = df[df['__source_province__'] == '浙江'].groupby(prod_col)['value'].sum()

	rows = []
	total_national = national.sum() if national.sum() > 0 else 1.0
	total_zj = zhejiang.sum() if zhejiang.sum() > 0 else 1.0
	for cat in top_cats:
		nat_val = float(national.get(cat, 0.0))
		zj_val = float(zhejiang.get(cat, 0.0))
		rows.append({
			'category': cat,
			'national_value': nat_val,
			'national_share': nat_val / total_national,
			'zj_value': zj_val,
			'zj_share': zj_val / total_zj,
		})
	out_df = pd.DataFrame(rows)
	outpath = os.path.join(outdir, 'zhejiang_vs_national_table.csv')
	out_df.to_csv(outpath, index=False)
	logging.info('Saved Zhejiang vs national table to %s', outpath)
	png_path = os.path.join(outdir, 'zhejiang_vs_national_table.png')
	# format share columns as percentages in display
	df_display = out_df.copy()
	# present columns in order: category, zj_share, national_share, with readable names
	df_display['national_share'] = df_display['national_share'].map(lambda x: f"{x:.1%}")
	df_display['zj_share'] = df_display['zj_share'].map(lambda x: f"{x:.1%}")
	df_display['national_value'] = df_display['national_value'].map(lambda x: f"{x:,.2f}")
	df_display['zj_value'] = df_display['zj_value'].map(lambda x: f"{x:,.2f}")
	df_display = df_display[['category', 'zj_value', 'zj_share', 'national_value', 'national_share']]
	df_display = df_display.rename(columns={
		'category': '商品类别',
		'zj_value': '浙江值',
		'zj_share': '浙江占比',
		'national_value': '全国值',
		'national_share': '全国占比'
	})
	df_to_png_table(df_display, png_path, title='浙江 vs 全国 商品品类占比（前{}）'.format(top_n), wrap_cols=['商品类别'])
	logging.info('Saved Zhejiang vs national PNG table to %s', png_path)


def create_provinces_pivot_png(df: pd.DataFrame, provinces: List[str], outpath: str, top_n: int = 10, kind: str = 'product') -> None:
	"""Create a pivot PNG where rows are rank (1..top_n) and columns are provinces.
	kind: 'product' or 'partner'
	Cells contain the product name or partner name (auto-wrapped).
	"""
	# determine source column
	if kind == 'product':
		prod_col = None
		for c in ['编码解释', '商品名', '商品类别', '商品']:
			if c in df.columns:
				prod_col = c
				break
		if prod_col is None and '商品编码' in df.columns:
			prod_col = '商品编码'
		if prod_col is None:
			logging.error('No product column for pivot; skipping %s', outpath)
			return
		source_col = prod_col
	else:
		if '伙伴国' not in df.columns:
			logging.error('No 伙伴国 column for pivot partners; skipping %s', outpath)
			return
		source_col = '伙伴国'

	# collect top lists per province
	rank_lists = {}
	for prov in provinces:
		sub = df[df['__source_province__'] == prov]
		grouped = sub.groupby(source_col)['value'].sum().sort_values(ascending=False).head(top_n)
		# ensure length top_n by padding with empty strings
		items = [str(x) for x in grouped.index.tolist()]
		items += [''] * (top_n - len(items))
		rank_lists[prov] = items

	# build DataFrame: index 1..top_n, columns = provinces
	idx = list(range(1, top_n+1))
	pivot_df = pd.DataFrame({prov: rank_lists.get(prov, ['']*top_n) for prov in provinces}, index=idx)
	pivot_df.index.name = '排名'
	display_df = pivot_df.reset_index()

	# rename columns to Chinese province names if needed (already Chinese)
	# wrap long text in province columns
	wrap_cols = provinces
	df_to_png_table(display_df, outpath, title=f'表: 五省前{top_n}（{"商品" if kind=="product" else "市场"}）比较', wrap_cols=wrap_cols)


def df_to_png_table(df: pd.DataFrame, outpath: str, title: str = '', wrap_cols: List[str] = None, value_cols: List[str] = None, dpi: int = 300) -> None:
	"""Render a DataFrame to a PNG file with table styling suitable for reports.

	Requirements handled:
	- PNG output
	- Research-report-like table style with borders
	- Chinese centered
	- Title above the table
	- Auto-wrap long text in specified columns
	- 300 dpi
	"""
	os.makedirs(os.path.dirname(outpath), exist_ok=True)
	ensure_chinese_font()

	wrap_cols = wrap_cols or []
	value_cols = value_cols or []

	def wrap_text(s: str, width: int = 20) -> str:
		if not isinstance(s, str):
			s = str(s)
		# simple wrap by characters (works for Chinese and ascii)
		s = s.strip()
		if len(s) <= width:
			return s
		parts = [s[i:i+width] for i in range(0, len(s), width)]
		return '\n'.join(parts)

	df2 = df.copy()
	for c in wrap_cols:
		if c in df2.columns:
			df2[c] = df2[c].apply(lambda x: wrap_text(str(x), width=18))

	# format numeric columns for display unless already formatted
	for c in value_cols:
		if c in df2.columns:
			df2[c] = df2[c].apply(lambda x: f"{float(x):,.2f}" if pd.notnull(x) and x != '' else '')

	# table size
	nrows, ncols = df2.shape
	col_labels = list(df2.columns)

	# figsize heuristics
	fig_w = max(6, ncols * 3)
	fig_h = max(2.5, 0.5 + nrows * 0.35)
	fig, ax = plt.subplots(figsize=(fig_w, fig_h))
	ax.axis('off')

	# build cell text
	cell_text = df2.values.tolist()

	table = ax.table(cellText=cell_text, colLabels=col_labels, cellLoc='center', loc='center')
	table.auto_set_font_size(False)
	# set font size
	base_fontsize = 10
	table.set_fontsize(base_fontsize)

	# style header
	for (row, col), cell in table.get_celld().items():
		cell.set_edgecolor('black')
		cell.set_linewidth(0.8)
		if row == 0:
			cell.set_facecolor('#f2f2f2')
			cell.set_text_props(weight='bold')
		# center alignment already set via cellLoc

	# Title above table
	if title:
		plt.title(title, y=1.02, fontsize=12)

	plt.tight_layout()
	plt.savefig(outpath, dpi=dpi, bbox_inches='tight', pad_inches=0.3)
	plt.close()


def main(argv=None):
	p = argparse.ArgumentParser(description='读取并绘制贸易CSV对比图')
	p.add_argument('--data-dir', '-d', required=True, help='CSV 文件所在目录')
	p.add_argument('--pattern', default='**/*.csv', help='glob 模式, 默认 **/*.csv (递归)')
	p.add_argument('--group-by', default='伙伴国', help='用于对比的列名 (例如 伙伴国 或 地区/省州)')
	p.add_argument('--top-n', type=int, default=6, help='绘制前 N 个分组')
	p.add_argument('--outdir', default='./plots', help='保存图像的位置')
	p.add_argument('--max-files', type=int, default=200, help='最多加载多少文件')
	p.add_argument('--show', action='store_true', help='运行结束显示图像')
	p.add_argument('--list-only', action='store_true', help='只列出找到的 CSV 文件并退出')
	p.add_argument('--recursive', action='store_true', help='与 --pattern 一起使用以启用递归 glob (默认已递归)')
	p.add_argument('--bars', action='store_true', help='生成前10伙伴国和前10商品品类的柱状图（替代时间序列）')
	p.add_argument('--five-provinces', help='逗号分隔的五个省名，用于生成五省对比图，示例: 福建,广东,江苏,山东,浙江')
	p.add_argument('--zj-vs-national', action='store_true', help='生成浙江 vs 全国 比较品类占比图')
	p.add_argument('--tables', action='store_true', help='生成表格而不是绘图：五省与浙江/全国对比')
	args = p.parse_args(argv)

	files = find_csv_files(args.data_dir, args.pattern, recursive=not getattr(args, 'recursive', True) is False)
	if not files:
		logging.error('No files found. 请检查 --data-dir 和 --pattern')
		sys.exit(1)

	if args.list_only:
		print('Found files:')
		for f in files:
			print(f)
		print(f'Total: {len(files)}')
		return

	df = load_many_csvs(files, max_files=args.max_files)
	if args.bars:
		plot_top_bars(df, outdir=args.outdir, top_n=10, show=args.show)
	if args.five_provinces:
		provinces = [p.strip() for p in args.five_provinces.split(',') if p.strip()]
		if provinces:
			if args.tables:
				# generate tables instead of plots
				create_provinces_top_partners_table(df, provinces, outdir=args.outdir, top_n=10)
				create_provinces_top_products_table(df, provinces, outdir=args.outdir, top_n=10)
			else:
				plot_province_structure_bar(df, provinces, outdir=args.outdir, top_n=8, show=args.show)
				plot_province_market_heatmap(df, provinces, outdir=args.outdir, top_partners=12, show=args.show)
	if args.zj_vs_national:
		if args.tables:
			create_zhejiang_vs_national_table(df, outdir=args.outdir, top_n=10)
		else:
			plot_zhejiang_vs_national_category_share(df, outdir=args.outdir, top_n=10, show=args.show)
	else:
		aggregate_and_plot(df, group_by=args.group_by, outdir=args.outdir, top_n=args.top_n, show=args.show)


if __name__ == '__main__':
	main()

