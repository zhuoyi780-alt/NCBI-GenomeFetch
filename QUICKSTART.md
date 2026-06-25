# 快速开始 - NCBI-GenomeFetch v1.0.1

本文件面向 `release/NCBI-GenomeFetch-v1.0.1` 最简发布包。发布包只包含核心代码、安装配置和必要文档，不包含测试、示例数据、历史 release 或 `datasets.exe`。

## 1. 准备环境

要求：

- Python `3.8+`
- NCBI `datasets` CLI `16.0.0+`
- 可访问 NCBI 下载服务

确认外部工具：

```bash
python --version
datasets --version
```

如果 `datasets` 不在 `PATH` 中，运行命令时使用 `--datasets-exe /path/to/datasets` 指定。

## 2. 安装

在发布包目录内执行：

```bash
pip install -r requirements.txt
pip install -e .
```

确认 CLI 已注册：

```bash
ncbi-genomefetch --help
```

可选配置 NCBI API key：

```bash
export NCBI_API_KEY="your_api_key"
```

程序也会识别 `DATASETS_API_KEY` 和 `NCBI_DATASETS_API_KEY`。

## 3. Taxon 下载

创建 taxon 输入文件，一行一个 taxon 名称或 TaxID：

```bash
cat > taxa.txt << EOF
562
Bacillus subtilis
EOF
```

下载默认 genome 数据：

```bash
ncbi-genomefetch -i taxa.txt -o genomes/
```

下载多种文件类型：

```bash
ncbi-genomefetch \
  -i taxa.txt \
  -o genomes/ \
  --include genome,protein,gff3 \
  --assembly-source refseq \
  -w 4
```

Taxon 模式会在输出目录维护 `.progress_state.json`，重新运行相同命令时会跳过已完成 taxon。

## 4. Accession 下载

创建 accession 输入文件，一行一个 accession：

```bash
cat > accessions.txt << EOF
GCF_000005845.2
GCF_000009045.1
GCA_000006765.1
EOF
```

运行批量下载：

```bash
ncbi-genomefetch -a accessions.txt -o genomes/ -b 100 -w 4
```

Accession 模式支持断点续传：

```bash
# 中断后直接重新运行
ncbi-genomefetch -a accessions.txt -o genomes/ -b 100 -w 4
```

默认续传会使用 `.accession_manifest.json` 和已有输出文件做完整性判断。若只想信任进度文件：

```bash
ncbi-genomefetch -a accessions.txt -o genomes/ --no-validate-resume-files
```

典型输出：

```text
genomes/
  GCF_000005845.2.fna
  GCF_000009045.1.fna
  md5sum.txt
  .accession_manifest.json
```

## 5. Split 任务拆分

Split 模式用于将大型 taxon 任务拆成多个较小输入文件：

```bash
ncbi-genomefetch -s Archaea -o split_output/
```

也可以传入已有 taxon 数据目录：

```bash
ncbi-genomefetch -s ./Archaea -o split_output/
```

该模式是交互式流程，会提示选择 taxonomy level、数据源和拆分阈值。拆分阈值单位为 Gb，即 gigabases，不是磁盘 GB。

`group*.txt` 可作为 taxon 模式输入：

```bash
ncbi-genomefetch -i split_output/group1.txt -o genomes_group1/
```

二次拆分生成的 `TaxonName_*.txt` 是 accession 列表，可作为 accession 模式输入：

```bash
ncbi-genomefetch -a split_output/TaxonName_1.txt -o genomes_part1/
```

## 6. MD5 校验和自动修复

校验已有下载目录：

```bash
ncbi-genomefetch --md5sum genomes/
```

输出：

- `genomes/md5_verification_report.txt`
- 当前工作目录下的 `md5_failed_files.txt`，仅在存在失败、缺失或错误文件时生成

校验后自动修复：

```bash
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix
```

直接使用失败列表修复：

```bash
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt
```

Auto-fix 状态保存在被校验目录下：

```text
genomes/.md5_autofix_state/autofix_state.json
genomes/.md5_autofix_state/reports/redownload_report.<run_id>.txt
```

常用恢复控制：

```bash
# 重试之前失败的修复任务
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt --md5sum-auto-fix-retry-failed

# 清理异常退出留下的锁
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt --md5sum-auto-fix-clear-lock

# 忽略旧状态重新执行
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt --md5sum-auto-fix-no-resume
```

## 7. 常用参数

| 参数 | 说明 |
| --- | --- |
| `-i FILE` | Taxon 模式输入文件 |
| `-a FILE` | Accession 模式输入文件 |
| `-s TAXON_OR_DIR` | Split 模式 |
| `-o DIR` | 输出目录 |
| `-b N` | Accession 批大小，默认 `100` |
| `-w N` | 并发 worker 数，默认 `2` |
| `-k KEY` | NCBI API key |
| `--include LIST` | 数据类型，默认 `genome` |
| `--assembly-source SOURCE` | `refseq`、`genbank` 或 `all` |
| `--temp-dir DIR` | 临时目录 |
| `--datasets-exe PATH` | datasets 可执行文件路径 |

支持的数据类型：

```text
genome, protein, rna, cds, gff3, gtf, gbff, seq-report, none
```

## 8. 磁盘空间建议

默认启用磁盘空间动态退避。空间紧张时建议降低并发和批大小，或把临时目录放到更大的磁盘：

```bash
ncbi-genomefetch \
  -a accessions.txt \
  -o /data/genomes/ \
  --temp-dir /scratch/genomefetch_tmp \
  -b 50 \
  -w 2
```

可调整磁盘阈值：

```bash
ncbi-genomefetch \
  -a accessions.txt \
  -o genomes/ \
  --disk-warning-threshold 0.20 \
  --disk-critical-threshold 0.10 \
  --disk-minimum-threshold 0.05
```

## 9. 版本信息

- 发布包：`NCBI-GenomeFetch-v1.0.1`
- 包名：`taxonomy_downloader`
- CLI：`ncbi-genomefetch`
- 版本：`1.0.1`
- 日期：`2026-06-23`
