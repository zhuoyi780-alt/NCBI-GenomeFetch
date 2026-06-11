# NCBI-GenomeFetch v1.0.0

从 NCBI 批量下载基因组数据的命令行工具。支持三种工作模式：
- **Taxon 模式**：按分类名称或 TaxID 下载基因组
- **Accession 模式**：按 Accession 号批量下载基因组（支持断点续传）
- **分割模式**：将大型下载任务智能分割为子任务（支持菌株级基因组统计）

## 版本亮点 (v1.0.0)

✅ **MD5自动修复**: MD5验证失败后自动重新下载并修复文件  
✅ **智能路径匹配**: 使用md5sum目录上下文，避免同名文件冲突  
✅ **文件名标准化**: 自动应用文件名简化规则，保持一致性  
✅ **发布烟测**: 提供基础导入、CLI help 和源码编译检查  
✅ **断点续传**: Accession 模式支持自动恢复  

## 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [工作模式](#工作模式)
- [命令行参数](#命令行参数)
- [断点续传](#断点续传)
- [输入文件格式](#输入文件格式)
- [输出结构](#输出结构)
- [性能优化](#性能优化)
- [故障排除](#故障排除)
  - [手动补充失败的物种](#7-手动补充失败的物种)

## 安装

### 系统要求

- Python 3.8+
- NCBI datasets 工具 16.0.0+

### 安装 NCBI datasets 工具

```bash
# Windows
curl -o datasets.exe https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/win64/datasets.exe

# Linux
curl -o datasets https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/linux-amd64/datasets
chmod +x datasets

# macOS
curl -o datasets https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/mac/datasets
chmod +x datasets

# 或使用 conda
conda install -c conda-forge ncbi-datasets-cli
```

### 安装 Python 包

```bash
# 从源码目录安装
pip install .
```

### 验证安装

```bash
datasets --version    # 需要 16.0.0+
python --version      # 需要 3.8+
```

## 快速开始

### Taxon 模式（按分类下载）

```bash
# 创建输入文件（推荐使用 TaxID）
echo "562" > taxa.txt      # E. coli
echo "1423" >> taxa.txt    # B. subtilis

# 基本下载
ncbi-genomefetch -i taxa.txt -o genomes/

# 推荐配置（API 密钥 + 多线程）
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY -w 8

# 或使用安装后的命令
ncbi-genomefetch -i taxa.txt -o genomes/

# 下载多种文件类型
ncbi-genomefetch -i taxa.txt -o genomes/ --include genome,protein,gff3
```

### Accession 模式（按编号下载，支持断点续传）

```bash
# 创建 accession 文件
echo "GCF_000005845.2" > accessions.txt
echo "GCF_000009045.1" >> accessions.txt

# 批量下载（自动断点续传）
ncbi-genomefetch -a accessions.txt -o genomes/

# 自定义批次大小
ncbi-genomefetch -a accessions.txt -o genomes/ -b 50 -w 4

# 中断后恢复：直接重新运行相同命令
# 工具会自动检测已完成的 accessions 并跳过
ncbi-genomefetch -a accessions.txt -o genomes/
```

### 分割模式（处理大型数据集）

```bash
# 分割 Bacteria 数据集
ncbi-genomefetch -s Bacteria -o Bacteria_split/

# 使用分割结果下载
ncbi-genomefetch -i Bacteria_split/group1.txt -o downloads/group1/
```

## 工作模式

### 1. Taxon 模式 (`-i/--input`)

按分类名称或 TaxID 下载基因组，使用 NCBI datasets 的脱水/再水化工作流程。

**特点**：
- ✅ 支持分类名称和 TaxID
- ✅ 自动包含菌株级基因组
- ✅ 支持多种文件类型（genome, protein, gff3, cds 等）
- ✅ 进度跟踪和断点续传

**工作流程**：
1. 下载脱水包（dehydrated package）
2. 再水化（rehydrate）获取实际数据
3. 组织文件到输出目录
4. 生成 MD5 校验和

**示例**：
```bash
# 使用 TaxID（推荐）
ncbi-genomefetch -i taxa.txt -o genomes/

# 下载多种文件类型
ncbi-genomefetch -i taxa.txt -o genomes/ --include genome,protein,gff3

# 仅下载参考基因组
ncbi-genomefetch -i taxa.txt -o genomes/ --additional-params reference=true
```

### 2. Accession 模式 (`-a/--accession-file`)

按 Accession 号批量下载基因组，支持自动断点续传。

**特点**：
- ✅ 批量处理 accessions
- ✅ 自动断点续传（中断后恢复）
- ✅ 并行下载（可配置线程数）
- ✅ 自动去重
- ✅ 文件名标准化（`{accession}.{extension}`）

**断点续传机制**：
- 进度保存在 `.accession_progress_state.json`
- 每个批次完成后自动保存进度
- 中断后重新运行相同命令即可恢复
- 自动跳过已完成的 accessions
- 如需仅信任进度 JSON、不检查输出文件是否存在，可添加 `--no-validate-resume-files`
- 完成后自动清理状态文件

**工作流程**：
1. 读取 accession 列表
2. 检查已完成的 accessions（如果有）
3. 分批下载剩余 accessions
4. 每批完成后保存进度
5. 生成合并的 MD5 文件

**示例**：
```bash
# 基本下载
ncbi-genomefetch -a accessions.txt -o genomes/

# 自定义批次大小和线程数
ncbi-genomefetch -a accessions.txt -o genomes/ -b 100 -w 8

# 中断后恢复（直接重新运行）
ncbi-genomefetch -a accessions.txt -o genomes/

# 仅根据 .accession_progress_state.json 续传，不验证输出文件
ncbi-genomefetch -a accessions.txt -o genomes/ --no-validate-resume-files

# 查看进度状态
cat genomes/.accession_progress_state.json
```

### 3. 分割模式 (`-s/--split`)

将大型分类（如 Bacteria）智能分割为多个子任务，便于分布式下载。

**功能特性**：
- ✅ 自动识别菌株级基因组（即使物种本身没有基因组）
- ✅ 输出文件包含物种及其所有子节点（菌株、亚种等）
- ✅ 统计更准确：物种数统计物种级节点，基因组数包含所有节点

**前置要求**：
准备 `{taxon}/` 文件夹，包含：
- `{taxon}.tsv` - 基因组组装报告
- `taxonomy_report.jsonl` - 分类学报告

**示例**：
```bash
# 分割 Bacteria
ncbi-genomefetch -s Bacteria -o Bacteria_split/

# 交互式配置
# 1. 选择分类学水平（推荐 Genus 或 Species）
# 2. 是否过滤非正式命名（y/n）
# 3. 数据库来源（refseq/genbank/all）
# 4. 每组大小（Gb）

# 使用分割结果
ncbi-genomefetch -i Bacteria_split/group1.txt -o downloads/group1/
```

## 命令行参数

### 模式选择（三选一）

| 参数 | 说明 | 示例 |
|------|------|------|
| `-i FILE` | Taxon 模式：按分类下载 | `-i taxa.txt` |
| `-a FILE` | Accession 模式：按编号下载 | `-a accessions.txt` |
| `-s TAXON` | 分割模式：分割大型数据集 | `-s Bacteria` |

### 基本参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-o DIR` | 输出目录 | 必需 |
| `-k KEY` | NCBI API 密钥 | 无 |
| `-w N` | 并行线程数 | 2 |
| `-b N` | 批次大小（Accession 模式） | 100 |
| `--datasets-exe PATH` | datasets 工具路径 | `datasets` |
| `--temp-dir DIR` | 临时文件目录 | 系统默认 |
| `--md5sum DIR` | 对指定目录执行MD5校验，失败文件保存到当前目录 | - |
| `--md5sum-auto-fix [FILE]` | 自动修复失败文件。可独立使用（读取md5_failed_files.txt）或与--md5sum联合使用 | `md5_failed_files.txt` |

### 数据类型参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--include TYPE` | 下载的文件类型 | `genome` |
| `--assembly-source SRC` | 数据库来源 | `all` |
| `--additional-params PARAMS` | 额外过滤参数 | 无 |

**`--include` 可选值**：
- `genome` - 基因组序列（默认）
- `protein` - 蛋白质序列
- `rna` - RNA 序列
- `cds` - CDS 序列（使用 `.cds.fna` 扩展名）
- `gff3` - GFF3 注释
- `gtf` - GTF 注释
- `gbff` - GenBank 格式
- `seq-report` - 序列报告
- `none` - 仅元数据

**组合使用**：
```bash
# 下载基因组 + 蛋白质 + 注释
--include genome,protein,gff3

# 下载所有类型
--include genome,protein,rna,cds,gff3,gtf,gbff,seq-report
```

**`--assembly-source` 可选值**：
- `all` - RefSeq 和 GenBank（默认）
- `refseq` - 仅 RefSeq
- `genbank` - 仅 GenBank

**`--additional-params` 示例**：
```bash
# 仅参考基因组
--additional-params reference=true

# 仅完整装配的注释基因组
--additional-params assembly-level=complete,annotated=true

# 组合过滤
--additional-params reference=true,annotated=true,assembly-level=complete
```

### 性能参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--disable-disk-backoff` | 禁用磁盘空间动态回退 | false |
| `--disk-warning-threshold PERCENT` | 磁盘空间警告比例阈值 | 0.20 |
| `--disk-critical-threshold PERCENT` | 磁盘空间严重比例阈值 | 0.10 |
| `--disk-minimum-threshold PERCENT` | 暂停新任务的最低比例阈值 | 0.05 |
| `--disk-warning-bytes SIZE` | 磁盘空间警告绝对阈值 | 10GB |
| `--disk-critical-bytes SIZE` | 磁盘空间严重绝对阈值 | 5GB |
| `--disk-minimum-bytes SIZE` | 暂停新任务的最低绝对阈值 | 1GB |

## 断点续传

### MD5校验与自动修复

**三种使用方式**：

#### 1. 仅MD5校验（生成失败文件列表）
```bash
# 校验指定目录，失败文件信息保存到当前目录的 md5_failed_files.txt
ncbi-genomefetch --md5sum genomes/

# 输出文件：
# - genomes/md5_verification_report.txt  # 完整校验报告
# - ./md5_failed_files.txt               # 失败文件列表（在当前运行目录）
```

#### 2. 仅自动修复（读取失败文件列表）
```bash
# 从默认位置读取失败文件列表并修复（当前目录的 md5_failed_files.txt）
ncbi-genomefetch --md5sum-auto-fix

# 从自定义位置读取失败文件列表
ncbi-genomefetch --md5sum-auto-fix /path/to/custom_failed_files.txt

# 工具会自动：
# 1. 读取失败文件列表
# 2. 提取Accession标识符
# 3. 重新下载失败的文件
# 4. 应用文件名简化规则
# 5. 整理到原始路径
# 6. 重新验证并生成报告
```

#### 3. 一站式校验和修复
```bash
# 校验并自动修复，失败文件列表保存到默认位置
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix

# 校验并自动修复，失败文件列表保存到自定义位置
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix custom_failed.txt
```

**生成的文件**：
- `md5_failed_files.txt` - 失败文件的详细信息列表（默认在当前运行目录）
- `md5_verification_report.txt` - 完整的MD5校验报告（在校验目录）
- `redownload_report.txt` - 详细的修复报告（在校验目录，仅auto-fix时生成）

**失败文件列表格式**：
```
# MD5 Verification Failed Files
# Generated: 2024-03-05 10:30:00
# Total failed files: 5
#
# Format: STATUS | FILE_PATH | EXPECTED_HASH | COMPUTED_HASH | ERROR_MESSAGE
#================================================================================

FAIL | Bacteria/Escherichia_coli/GCF_000005845.2/genome.fna | abc123... | def456... | 
MISSING | Bacteria/Salmonella/GCF_000006945.2/protein.faa | xyz789... | N/A | File not found
ERROR | Archaea/Methanococcus/GCF_000007845.1/genome.fna | 123abc... | N/A | Permission denied
```

**修复报告示例**：
```
MD5 Auto-Fix Report
===================
Total failed files: 5
Successfully fixed: 4
Still failed: 1
Skipped: 0
Processing time: 120.5 seconds

Successfully Fixed Files:
- GCA_000302455.1.fna
- GCA_000302456.1.fna
- GCA_000302457.1.fna
- GCA_000302458.1.fna

Still Failed Files:
- GCA_000302459.1.fna (MD5 mismatch after redownload)
```

### Accession 模式断点续传

**自动断点续传**：
```bash
# 开始下载
ncbi-genomefetch -a accessions.txt -o genomes/

# 如果中断（Ctrl+C 或 SIGTERM），直接重新运行相同命令
ncbi-genomefetch -a accessions.txt -o genomes/
# 工具会自动：
# 1. 检测 .accession_progress_state.json
# 2. 跳过已完成的 accessions
# 3. 继续下载剩余的 accessions
```

**进度状态文件**：
- 位置：`{output_dir}/.accession_progress_state.json`
- 内容：已完成的 accessions 列表
- 自动管理：完成后自动删除

**查看进度**：
```bash
# 查看状态文件
cat genomes/.accession_progress_state.json

# 示例输出
{
  "completed_taxa": ["GCF_000005845.2", "GCF_000009045.1"],
  "last_update": "2026-01-21T10:30:00"
}
```

**中断处理**：
- ✅ 支持 Ctrl+C (SIGINT)
- ✅ 支持 SIGTERM（容器环境）
- ✅ 线程安全的中断处理
- ✅ 优雅关闭，不丢失进度

### Taxon 模式断点续传

Taxon 模式也支持断点续传，状态文件为 `.progress_state.json`。

```bash
# 中断后恢复
ncbi-genomefetch -i taxa.txt -o genomes/

# 仅根据 .progress_state.json 续传，不验证输出目录中的文件
ncbi-genomefetch -i taxa.txt -o genomes/ --no-validate-resume-files
```

## 输入文件格式

### Taxon 模式输入文件

支持三种格式：

**格式 1：仅 TaxID（推荐）**
```
562
1423
```

**格式 2：名称 + TaxID（制表符分隔）**
```
Escherichia coli	562
Bacillus subtilis	1423
```

**格式 3：分割模式输出（包含子节点）**
```
# Species: Escherichia coli (562)
Escherichia coli	562
Escherichia coli K-12	83333
Escherichia coli O157:H7	83334
```

**注释和空行**：
- 以 `#` 开头的行为注释，会被忽略
- 空行会被忽略
- 支持混合格式

### Accession 模式输入文件

每行一个 accession 号：
```
GCF_000005845.2
GCF_000009045.1
GCA_000001405.29
```

**注意**：
- 自动去重
- 保留原始顺序
- 支持版本号（如 `.2`）

## 输出结构

### Taxon 模式输出

```
genomes/
├── Escherichia_coli_562/
│   ├── GCF_000005845.2.fna          # 基因组序列
│   ├── GCF_000005845.2.faa          # 蛋白质序列（如果 --include protein）
│   ├── GCF_000005845.2.gff          # 注释文件（如果 --include gff3）
│   └── ...
├── Bacillus_subtilis_1423/
│   └── ...
├── md5sum.txt                        # MD5 校验和
├── taxonomy_summary.tsv              # 分类学摘要
├── taxonomy_report.jsonl             # 详细分类学报告
└── .progress_state.json              # 进度状态（未完成时）
```

### Accession 模式输出

```
genomes/
├── GCF_000005845.2.fna               # 标准化文件名
├── GCF_000009045.1.fna
├── GCA_000001405.29.fna
├── md5sum.txt                        # 合并的 MD5 文件
└── .accession_progress_state.json    # 进度状态（未完成时）
```

**文件名标准化**：
- 格式：`{accession}.{extension}`
- 示例：`GCF_000005845.2.fna`
- 扩展名：`.fna` (genome), `.faa` (protein), `.gff` (gff3), `.cds.fna` (cds)

### MD5 校验和文件

```
# MD5 checksums
a1b2c3d4e5f6...  GCF_000005845.2.fna
b2c3d4e5f6a7...  GCF_000009045.1.fna
```

## 性能优化

### 1. 使用 API 密钥

获取 NCBI API 密钥可提高速率限制 10 倍：

```bash
# 注册获取 API 密钥：https://www.ncbi.nlm.nih.gov/account/

# 使用 API 密钥
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY
```

### 2. 调整并行参数

```bash
# 增加线程数（推荐 4-8）
ncbi-genomefetch -a accessions.txt -o genomes/ -w 8

# 增加批次大小（Accession 模式，推荐 100-200）
ncbi-genomefetch -a accessions.txt -o genomes/ -b 150 -w 8
```

### 3. 磁盘空间管理

```bash
# 设置磁盘空间阈值
ncbi-genomefetch -i taxa.txt -o genomes/ --disk-warning-bytes 20GB

# 禁用磁盘空间检查（不推荐）
ncbi-genomefetch -i taxa.txt -o genomes/ --disable-disk-backoff
```

### 4. 性能建议

| 场景 | 推荐配置 |
|------|----------|
| 小型下载（< 10 个） | `-w 2 -b 50` |
| 中型下载（10-100 个） | `-w 4 -b 100 -k API_KEY` |
| 大型下载（> 100 个） | `-w 8 -b 150 -k API_KEY` |
| 超大型下载（> 1000 个） | 使用分割模式 + 分布式下载 |

## 故障排除

### 1. datasets 工具未找到

**错误**：`datasets: command not found`

**解决方案**：
```bash
# 方案 1：添加到 PATH
export PATH=$PATH:/path/to/datasets

# 方案 2：指定完整路径
ncbi-genomefetch -i taxa.txt -o genomes/ --datasets-exe /path/to/datasets
```

### 2. 下载速度慢

**原因**：未使用 API 密钥，受速率限制

**解决方案**：
```bash
# 获取并使用 API 密钥
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY -w 8
```

### 3. 磁盘空间不足

**错误**：`Insufficient disk space`

**解决方案**：
```bash
# 清理磁盘空间或使用其他目录
ncbi-genomefetch -i taxa.txt -o /other/path/genomes/

# 或降低最低保留空间阈值
ncbi-genomefetch -i taxa.txt -o genomes/ --disk-minimum-bytes 5GB
```

### 4. 下载中断

**Accession 模式**：
```bash
# 直接重新运行相同命令，自动恢复
ncbi-genomefetch -a accessions.txt -o genomes/
```

**Taxon 模式**：
```bash
# 直接重新运行相同命令，自动恢复
ncbi-genomefetch -i taxa.txt -o genomes/
```

### 5. 进度未保存

**检查**：
```bash
# 确保输出目录可写
ls -la genomes/

# 检查状态文件
cat genomes/.accession_progress_state.json
```

**解决方案**：
- 确保输出目录有写权限
- 确保磁盘空间充足
- 更新到最新版本

### 6. 文件名不匹配

**问题**：期望旧格式文件名（如 `GCF_000005845.2_genomic.fna`）

**说明**：当前版本使用标准化文件名格式 `{accession}.{extension}`

**示例**：
- 旧格式：`GCF_000005845.2_genomic.fna`
- 新格式：`GCF_000005845.2.fna`

### 7. 手动补充失败的物种

**场景**：程序运行时部分物种下载失败，你已手动下载并补水这些物种

**解决方案**：使用手动组织工具将文件补充到输出目录

**单个物种**：
```bash
# 1. 手动下载并补水
datasets download genome taxon "Escherichia coli" --filename temp/ecoli.zip --dehydrated
unzip temp/ecoli.zip -d temp/ecoli_extracted
datasets rehydrate --directory temp/ecoli_extracted

# 2. 使用同一输入文件重新运行工具，或按输出命名规则手动放入对应目录
ncbi-genomefetch -i taxa.txt -o output/
```

**批量处理多个物种**：
```bash
# 1. 创建配置文件 (species_to_organize.txt)
# 格式: 临时目录<TAB>物种名称
temp/ecoli_extracted	Escherichia coli
temp/salmonella_extracted	Salmonella enterica

# 2. 使用同一批输入重新运行，已完成任务会按进度状态跳过
ncbi-genomefetch -i taxa.txt -o output/
```

**说明**：如果需要直接合并手工下载的数据，请先核对文件命名、目录结构和 `md5sum.txt`。

## 版本历史

### v1.0.0 (2026-03-11)
- ✅ MD5自动修复：验证失败后自动重新下载并修复文件
- ✅ 智能路径匹配：使用md5sum目录上下文，避免同名文件冲突
- ✅ ZIP文件自动解压：处理NCBI下载的压缩包
- ✅ 增加发布烟测：导入、CLI help 和源码编译检查
- ✅ Accession 模式断点续传
- ✅ 增强中断处理（SIGTERM/SIGINT）
- ✅ 菌株级基因组自动识别
- ✅ 多文件类型支持（protein, gff3, cds 等）
- ✅ 文件名标准化
- ✅ 磁盘空间动态回退

## 许可证

MIT License

## 支持

- 文档：[README.md](README.md)
- 快速开始：[QUICKSTART.md](QUICKSTART.md)
- 问题反馈：[GitHub Issues](https://github.com/zhuoyi780-alt/NCBI-GenomeFetch/issues)

## 致谢

感谢 NCBI 提供 datasets 工具和 API 服务。
