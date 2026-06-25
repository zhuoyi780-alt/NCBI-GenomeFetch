# NCBI-GenomeFetch

NCBI-GenomeFetch 是一个面向批量基因组下载的 Python CLI 工具。项目的 Python 包名是 `taxonomy_downloader`，安装后的命令行入口是 `ncbi-genomefetch`。

当前版本：`1.0.1`

它封装 NCBI `datasets` 命令行工具，使用 dehydrate -> extract -> rehydrate -> organize 工作流下载和整理基因组文件，并提供以下能力：

- 按物种名、TaxID 或 split 输出文件批量下载 taxon 数据
- 按 accession 列表批量下载，支持批次处理、断点续传和可信 MD5 清单
- 交互式拆分大 taxon 下载任务，生成可直接作为后续输入的任务文件
- 校验 `md5sum.txt`，生成失败文件列表和报告
- 对 MD5 失败文件执行自动补全/重下载，支持可恢复状态、锁清理和失败重试
- 下载过程支持限速、并发、磁盘空间动态退避、超时控制和结构化日志

## 运行要求

- Python `3.8+`
- NCBI `datasets` CLI `16.0.0+`
- Python 运行依赖：`requests>=2.28.0`
- 本发布包不包含测试和开发工具依赖

安装或确认 NCBI datasets：

```bash
datasets --version
```

如果 `datasets` 不在 `PATH` 中，可以在运行时通过 `--datasets-exe /path/to/datasets` 指定可执行文件路径。

## 安装

在项目根目录安装依赖并注册 CLI：

```bash
pip install -r requirements.txt
pip install -e .
```

确认命令可用：

```bash
ncbi-genomefetch --help
```

NCBI API key 可通过参数传入，也可以放到环境变量中。程序会按顺序自动识别：

```bash
export NCBI_API_KEY="your_api_key"
export DATASETS_API_KEY="your_api_key"
export NCBI_DATASETS_API_KEY="your_api_key"
```

有 API key 时默认请求速率会从每秒 3 次提高到每秒 10 次。

## 快速开始

按 taxon 下载：

```bash
ncbi-genomefetch -i taxa.txt -o genomes/
```

按 accession 下载：

```bash
ncbi-genomefetch -a accessions.txt -o genomes/ -b 100
```

拆分大型 taxon 任务：

```bash
ncbi-genomefetch -s Archaea -o split_output/
```

校验已有下载目录：

```bash
ncbi-genomefetch --md5sum genomes/
```

校验并自动修复失败文件：

```bash
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix
```

直接根据失败列表修复：

```bash
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt
```

## 输入文件格式

### Taxon 输入文件

`-i/--input` 接收一行一个 taxon 名称或 TaxID：

```text
Escherichia coli
562
Bacillus subtilis
```

也支持 split 模式生成的 `name<TAB>tax_id` 格式。读取时会跳过空行和以 `#` 开头的注释行；如果一行包含 tab，程序会使用第二列 TaxID：

```text
# Species: Escherichia coli (562)
Escherichia coli	562
Escherichia coli O157:H7	83334
```

### Accession 输入文件

`-a/--accession` 接收一行一个 accession：

```text
GCF_000005845.2
GCA_000006765.1
GCF_000009045.1
```

程序会跳过空行并按首次出现顺序去重。注意：accession 输入不会把 `#` 行当作注释处理，因此不要在 accession 列表里写注释行。

## 工作模式

### 1. Taxon 下载模式

Taxon 模式通过 `-i/--input` 指定 taxon 列表，通过 `-o/--output` 指定输出目录。

```bash
ncbi-genomefetch -i taxa.txt -o genomes/
```

指定 API key、并发和下载内容：

```bash
ncbi-genomefetch \
  -i taxa.txt \
  -o genomes/ \
  -k YOUR_API_KEY \
  -w 4 \
  --include genome,protein,gff3 \
  --assembly-source refseq
```

Taxon 模式会为每个 taxon 执行：

1. 调用 `datasets download taxonomy taxon ... --dehydrated`
2. 解压 dehydrated package
3. 调用 `datasets rehydrate`
4. 整理目标文件到独立 taxon 目录
5. 合并并重写 `md5sum.txt`
6. 将完成/失败状态写入 `.progress_state.json`

典型输出结构：

```text
genomes/
  Escherichia_coli/
    GCF_000005845.2.fna
    GCF_000005845.2.faa
    GCF_000005845.2.gff
    md5sum.txt
  Bacillus_subtilis/
    ...
  .progress_state.json
```

如果任务被中断，下一次使用相同输出目录运行会读取 `.progress_state.json`，跳过已完成 taxon。

### 2. Accession 下载模式

Accession 模式通过 `-a/--accession` 指定 accession 列表，通过 `-o/--output` 指定输出目录。

```bash
ncbi-genomefetch -a accessions.txt -o genomes/
```

常用批处理参数：

```bash
ncbi-genomefetch \
  -a accessions.txt \
  -o genomes/ \
  -b 50 \
  -w 4 \
  --include genome,protein,gff3 \
  --download-timeout 1800 \
  --rehydrate-timeout 7200
```

Accession 模式会：

- 将 accession 按 `-b/--batch` 分批下载
- 在输出目录下维护 `.accession_progress_state.json`
- 在输出目录下维护 `.accession_manifest.json`
- 生成统一的 `md5sum.txt`
- 使用 manifest 校验已有输出文件，避免把残缺文件误判为已完成
- 成功完成全部 accession 后清理进度状态文件

典型输出结构：

```text
genomes/
  GCF_000005845.2.fna
  GCF_000005845.2.faa
  GCF_000005845.2.gff
  GCA_000006765.1.fna
  md5sum.txt
  .accession_manifest.json
```

如果运行失败或被中断，保留的 `.accession_progress_state.json` 会用于续跑。默认续跑会验证已完成文件是否存在并且是否有可信 manifest 记录。若只想信任进度文件而不检查输出文件，可使用：

```bash
ncbi-genomefetch -a accessions.txt -o genomes/ --no-validate-resume-files
```

### 3. Split 任务拆分模式

Split 模式用于把大型 taxon 下载任务拆成多个较小任务文件。

```bash
ncbi-genomefetch -s Archaea
```

或指定输出目录：

```bash
ncbi-genomefetch -s Archaea -o split_output/
```

`-s/--split` 可以是 taxon 名称，也可以是已有 taxon 数据目录路径：

```bash
ncbi-genomefetch -s ./genomes/Archaea -o split_output/
```

Split 模式是交互式流程，会提示选择：

- taxonomy level
- 是否启用 binomial name filter
- 数据源：`refseq`、`genbank` 或 `all`
- 拆分阈值，单位是 Gb，即 gigabases，不是磁盘 GB

输出文件包括：

```text
split_output/
  group1.txt
  group2.txt
  exceeded.txt
  TaxonName_1.txt
  TaxonName_2.txt
  README.md
```

`group*.txt` 可直接作为 taxon 模式输入：

```bash
ncbi-genomefetch -i split_output/group1.txt -o genomes_group1/
```

`TaxonName_*.txt` 是 accession 列表，可直接作为 accession 模式输入：

```bash
ncbi-genomefetch -a split_output/TaxonName_1.txt -o genomes_taxon_part1/
```

### 4. MD5 校验模式

MD5 模式会递归查找目录中的 `md5sum.txt`，并按其中记录校验文件。

```bash
ncbi-genomefetch --md5sum genomes/
```

输出：

- 控制台概要报告
- `genomes/md5_verification_report.txt`
- 如果存在失败、缺失或错误文件，默认在当前工作目录写出 `md5_failed_files.txt`

失败列表格式：

```text
STATUS | FILE_PATH | EXPECTED_HASH | COMPUTED_HASH | ERROR_MESSAGE
```

`FILE_PATH` 是相对被校验目录的路径。

### 5. MD5 自动修复模式

校验并自动修复：

```bash
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix
```

指定失败列表输出路径，并在同一次运行中用于 auto-fix：

```bash
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix failed_files.txt
```

不重新校验，直接读取已有失败列表：

```bash
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt
```

如果省略失败列表，standalone auto-fix 会读取当前目录下的 `md5_failed_files.txt`：

```bash
ncbi-genomefetch --md5sum-auto-fix
```

Auto-fix 会从失败路径中提取 accession，批量重下载缺失或损坏文件，重新组织到原验证目录并再次校验。当前版本支持断点续传，状态目录位于：

```text
genomes/
  .md5_autofix_state/
    autofix_state.json
    reports/
      redownload_report.<run_id>.txt
```

常用 auto-fix 控制参数：

```bash
# 忽略已有 auto-fix 状态，从头执行
ncbi-genomefetch --md5sum-auto-fix --md5sum-auto-fix-no-resume

# 为相同验证目录创建新的 run id
ncbi-genomefetch --md5sum-auto-fix --md5sum-auto-fix-new-run

# 重试之前标记为 failed 的任务
ncbi-genomefetch --md5sum-auto-fix --md5sum-auto-fix-retry-failed

# 成功后保留 auto-fix 下载缓存
ncbi-genomefetch --md5sum-auto-fix --md5sum-auto-fix-keep-cache

# 运行前清理已有 auto-fix 状态
ncbi-genomefetch --md5sum-auto-fix --md5sum-auto-fix-clear-state

# 清理过期锁后再运行
ncbi-genomefetch --md5sum-auto-fix --md5sum-auto-fix-clear-lock
```

### 6. Accession MD5 重建模式

如果已有 dehydrated zip package，需要从包内可信 MD5 信息重建输出目录的 accession manifest 和 `md5sum.txt`，可以使用：

```bash
ncbi-genomefetch \
  --rebuild-md5 \
  -a accessions.txt \
  -o genomes/ \
  --dehy download.zip
```

该模式要求同时提供：

- `--rebuild-md5`
- `-a/--accession`
- `-o/--output`
- `--dehy`

## 参数参考

### 模式参数

| 参数 | 说明 |
| --- | --- |
| `-i, --input FILE` | Taxon 模式输入文件 |
| `-a, --accession FILE` | Accession 模式输入文件 |
| `-s, --split TAXON_OR_DIR` | 进入交互式 split 模式 |
| `--md5sum DIRECTORY` | 校验目录中的 `md5sum.txt` |
| `--md5sum-auto-fix [FILE]` | 开启 MD5 auto-fix；可选指定失败列表 |
| `--rebuild-md5` | 从 dehydrated package 重建 accession MD5 manifest |
| `--dehy ZIP` | `--rebuild-md5` 使用的 dehydrated zip 包 |

### 下载与输出参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-o, --output DIR` | 无 | 输出目录，taxon/accession/rebuild 模式必需 |
| `-b, --batch N` | `100` | Accession 批大小，范围 `1..1000` |
| `-w, --workers N` | `2` | 并发 worker 数，范围 `1..20` |
| `-k, --api-key KEY` | 自动从环境变量读取 | NCBI API key |
| `--temp-dir DIR` | 系统临时目录或输出目录 | 临时文件目录 |
| `--datasets-exe PATH` | `datasets` 或 `datasets.exe` | NCBI datasets 可执行文件 |
| `--include LIST` | `genome` | 逗号分隔的数据类型 |
| `--assembly-source SOURCE` | `all` | `refseq`、`genbank` 或 `all` |
| `--additional-params TEXT` | 无 | 额外 datasets 参数，格式 `key=value,key2=value2` |
| `--download-timeout SECONDS` | `1800` | dehydrate 下载超时 |
| `--rehydrate-timeout SECONDS` | `7200` | rehydrate 超时 |
| `--keep-failed-temp` | 关闭 | 保留失败批次临时目录，便于排查 |
| `--no-validate-resume-files` | 关闭 | 续跑 accession 时不验证已有输出文件 |

### 可下载数据类型

`--include` 支持逗号分隔的多类型组合：

| 类型 | 典型输出 |
| --- | --- |
| `genome` | `.fna` |
| `protein` | `.faa` |
| `rna` | `.rna.fna` |
| `cds` | `.cds` |
| `gff3` | `.gff` |
| `gtf` | `.gtf` |
| `gbff` | `.gbff` |
| `seq-report` | `.jsonl` |
| `none` | 不下载数据文件，主要用于 datasets 元数据流程 |

示例：

```bash
ncbi-genomefetch \
  -a accessions.txt \
  -o genomes/ \
  --include genome,protein,gff3,gbff \
  --assembly-source refseq
```

### 磁盘空间参数

程序默认启用磁盘空间动态退避。空间不足时会降低并发或暂停新任务，以减少临时文件和输出文件同时占用磁盘造成的失败。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--disk-warning-threshold FLOAT` | `0.20` | 可用比例低于该值进入 warning |
| `--disk-critical-threshold FLOAT` | `0.10` | 可用比例低于该值进入 critical |
| `--disk-minimum-threshold FLOAT` | `0.05` | 可用比例低于该值暂停新任务 |
| `--disk-warning-bytes BYTES` | `10737418240` | warning 最小可用字节，默认 10 GB |
| `--disk-critical-bytes BYTES` | `5368709120` | critical 最小可用字节，默认 5 GB |
| `--disk-minimum-bytes BYTES` | `1073741824` | minimum 最小可用字节，默认 1 GB |
| `--disk-check-interval SECONDS` | `30` | 磁盘检查间隔 |
| `--disable-disk-backoff` | 关闭 | 禁用自动磁盘退避，不推荐 |

## 断点续传与状态文件

| 场景 | 状态文件 | 行为 |
| --- | --- | --- |
| Taxon 下载 | `.progress_state.json` | 记录完成和失败 taxon，重新运行时跳过已完成项 |
| Accession 下载 | `.accession_progress_state.json` | 每个成功批次后增量保存；全部成功后清理 |
| Accession manifest | `.accession_manifest.json` | 保存可信输出文件与 MD5，辅助续跑验证 |
| MD5 auto-fix | `.md5_autofix_state/autofix_state.json` | 记录 run id、任务状态、已修复/失败/跳过文件 |

建议长期保留 `.accession_manifest.json` 和各目录的 `md5sum.txt`。如果需要完整复现或排错，可以同时保留 `.md5_autofix_state/reports/` 下的报告。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 任务成功，或没有需要处理的失败文件 |
| `1` | 参数错误、下载失败、校验失败或 auto-fix 后仍有失败/跳过文件 |
| `130` | 用户中断 accession 下载流程 |

## 常见使用场景

### 下载 RefSeq genome + protein

```bash
ncbi-genomefetch \
  -i taxa.txt \
  -o genomes_refseq/ \
  --include genome,protein \
  --assembly-source refseq
```

### 大 accession 列表稳定下载

```bash
ncbi-genomefetch \
  -a accessions.txt \
  -o genomes_accession/ \
  -b 50 \
  -w 4 \
  --temp-dir /data/tmp/genomefetch \
  --download-timeout 3600 \
  --rehydrate-timeout 10800
```

### 校验后修复并重试失败任务

```bash
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt --md5sum-auto-fix-retry-failed
```

### 清理 stale auto-fix lock

如果上次运行异常退出后留下锁文件，可以先清理锁再续跑：

```bash
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt --md5sum-auto-fix-clear-lock
```

## 故障排查

### 找不到 datasets

确认 `datasets` 在 `PATH` 中：

```bash
which datasets
datasets --version
```

或显式指定：

```bash
ncbi-genomefetch -a accessions.txt -o genomes/ --datasets-exe /opt/ncbi/datasets
```

### datasets 版本过低

项目要求 `datasets` CLI `16.0.0+`。如果版本过低，请升级 NCBI datasets 后重试。

### 下载速度慢或被限流

优先配置 NCBI API key，并适当降低并发或批大小：

```bash
export NCBI_API_KEY="your_api_key"
ncbi-genomefetch -a accessions.txt -o genomes/ -b 50 -w 2
```

### 磁盘空间不足

降低 `--workers`，减小 `--batch`，或把 `--temp-dir` 放到空间更大的磁盘：

```bash
ncbi-genomefetch \
  -a accessions.txt \
  -o /data/genomes/ \
  --temp-dir /scratch/genomefetch_tmp \
  -b 25 \
  -w 2
```

### 续跑时已有文件被判定无效

Accession 模式默认会用 manifest 验证已有文件。如果你确认输出目录可信，但 manifest 缺失或需要跳过检查，可以使用：

```bash
ncbi-genomefetch -a accessions.txt -o genomes/ --no-validate-resume-files
```

更推荐的做法是保留 `.accession_manifest.json`，或使用 `--rebuild-md5` 从 dehydrated 包重建可信记录。

### MD5 auto-fix 仍有失败

查看报告：

```text
genomes/.md5_autofix_state/reports/redownload_report.<run_id>.txt
```

然后重试失败项：

```bash
ncbi-genomefetch --md5sum-auto-fix md5_failed_files.txt --md5sum-auto-fix-retry-failed
```

## 发布包自检

该最简发布包不包含测试目录。安装后可用以下命令确认 CLI 和版本可用：

```bash
python -c "import taxonomy_downloader; print(taxonomy_downloader.__version__)"
ncbi-genomefetch --help
```

期望版本输出为：

```text
1.0.1
```

## 项目结构

```text
taxonomy_downloader/
  cli.py                          # CLI 入口和模式分发
  config.py                       # 参数解析和配置校验
  download_orchestrator.py         # Taxon 下载并发调度
  taxon_processor.py               # dehydrate/rehydrate/organize
  datasets_interface.py            # NCBI datasets subprocess 封装
  accession_downloader.py          # Accession 下载主流程
  accession_batch_processor.py     # Accession 批次处理
  split_workflow.py                # 交互式任务拆分
  md5_verifier.py                  # MD5 校验
  md5_autofix_coordinator.py       # MD5 自动修复协调器
  progress_manager.py              # 进度状态管理
  disk_space_monitor.py            # 磁盘空间监控
```

## 注意事项

- `--datasets-exe` 应传入可执行文件路径，不要传入包含 shell 参数的命令字符串。
- 大规模下载建议使用 API key，并先通过 split 模式估算任务规模。
- split 模式中的 Gb 表示 gigabases，即碱基数量，不等同于磁盘 GB。
- `md5_failed_files.txt` 默认写到当前工作目录，而不是被校验目录。
- 长时间任务建议保留日志、manifest、`md5sum.txt` 和 auto-fix 报告，便于续跑和审计。
