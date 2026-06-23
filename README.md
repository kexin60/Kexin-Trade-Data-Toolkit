# 分析脚本

这个小脚本用于读取一批从“全球贸易与产业增长实验室”导出的 CSV 文件（带有前置的元数据行），自动检测数据表头并将时间序列列 '值' 按指定分组画出对比折线图。

使用示例：

    python 分析.py --data-dir /Users/jessica/Desktop/分析/csvs --group-by 伙伴国 --top-n 8 --outdir ./plots

在 macOS 上，推荐先创建并激活一个 virtualenv，然后安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

脚本会尝试自动检测 CSV 的编码（utf-8, gbk），并在遇到常见列名时进行处理。若 CSV 的结构不同，请打开 `分析.py` 根据实际列名调整。
