# 快速开始 - NCBI-GenomeFetch v1.0.0

5 分钟上手 NCBI-GenomeFetch。

## 版本亮点

✅ **MD5自动修复**: MD5验证失败后自动重新下载并修复  
✅ **智能路径匹配**: 避免同名文件冲突  
✅ **断点续传**: Accession 模式支持自动恢复  
✅ **多文件类型**: 支持 genome, protein, gff3, cds 等  
✅ **发布烟测**: 支持基础导入、CLI help 和源码编译检查  

## 环境准备

```bash
# 1. 安装 NCBI datasets 工具
# Windows
curl -o datasets.exe https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/win64/datasets.exe

# Linux
curl -o datasets https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/linux-amd64/datasets
chmod +x datasets

# macOS
curl -o datasets https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/mac/datasets
chmod +x datasets

# 2. 安装 Python 包
pip install .

# 3. 验证安装
datasets --version    # 需要 16.0.0+
python --version      # 需要 3.8+
```

## 三种工作模式

### 模式 1：按分类下载（Taxon 模式）

**适用场景**：按物种、属、科等分类单元下载基因组

```bash
# 创建输入文件（推荐使用 TaxID）
cat > taxa.txt << EOF
562      # Escherichia coli
1423     # Bacillus subtilis
EOF

# 基本下载
ncbi-genomefetch -i taxa.txt -o genomes/

# 推荐配置（API 密钥 + 多线程）
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY -w 8

# 下载多种文件类型
ncbi-genomefetch -i taxa.txt -o genomes/ --include genome,protein,gff3
```

**输出结构**：
```
genomes/
├── Escherichia_coli_562/
│   ├── GCF_000005845.2.fna
│   ├── GCF_000005845.2.faa      # 如果 --include protein
│   └── GCF_000005845.2.gff      # 如果 --include gff3
├── Bacillus_subtilis_1423/
│   └── ...
├── md5sum.txt
└── taxonomy_summary.tsv
```

### 模式 2：按编号下载（Accession 模式）⭐ 支持断点续传

**适用场景**：已知具体 accession 号，需要批量下载

```bash
# 创建输入文件
cat > accessions.txt << EOF
GCF_000005845.2
GCF_000009045.1
GCA_000001405.29
EOF

# 基本下载（自动断点续传）
ncbi-genomefetch -a accessions.txt -o genomes/

# 推荐配置
ncbi-genomefetch -a accessions.txt -o genomes/ -b 100 -w 8 -k YOUR_API_KEY
```

**断点续传**：
```bash
# 如果下载中断（Ctrl+C 或网络问题），直接重新运行相同命令
ncbi-genomefetch -a accessions.txt -o genomes/

# 工具会自动：
# 1. 检测 .accession_progress_state.json
# 2. 跳过已完成的 accessions
# 3. 继续下载剩余的 accessions
# 4. 完成后自动清理状态文件

# 查看进度
cat genomes/.accession_progress_state.json
```

**输出结构**：
```
genomes/
├── GCF_000005845.2.fna           # 标准化文件名
├── GCF_000009045.1.fna
├── GCA_000001405.29.fna
├── md5sum.txt                    # 合并的 MD5 文件
└── .accession_progress_state.json  # 进度状态（未完成时）
```

### 模式 3：任务分割（Split 模式）

**适用场景**：处理大型数据集（如 Bacteria），需要分割成多个子任务

**前置要求**：准备 `{taxon}/` 文件夹
```
Bacteria/
├── Bacteria.tsv              # 基因组组装报告
└── taxonomy_report.jsonl     # 分类学报告
```

**运行分割**：
```bash
ncbi-genomefetch -s Bacteria -o Bacteria_split/
```

**交互式配置**：
1. 选择分类学水平（推荐 Genus 或 Species）
2. 是否过滤非正式命名（y/n）
3. 数据库来源（refseq/genbank/all）
4. 每组大小（Gb）

**功能特性**：
- ✅ 自动识别菌株级基因组（即使物种本身没有基因组）
- ✅ 输出文件包含物种及其所有子节点（菌株、亚种等）
- ✅ 统计更准确：物种数统计物种级节点，基因组数包含所有节点

**输出文件格式**（新格式）：
```
# Species: Escherichia coli (562)
Escherichia coli	562
Escherichia coli K-12	83333
Escherichia coli O157:H7	83334

# Species: Salmonella enterica (28901)
Salmonella enterica	28901
Salmonella enterica subsp. enterica	59201
```

**使用分割结果**：
```bash
# 直接使用分组文件（自动识别新旧格式）
ncbi-genomefetch -i Bacteria_split/group1.txt -o downloads/group1/
ncbi-genomefetch -i Bacteria_split/group2.txt -o downloads/group2/
```

## 参数速查

### 模式选择（三选一）

| 参数 | 说明 | 示例 |
|------|------|------|
| `-i FILE` | Taxon 模式 | `-i taxa.txt` |
| `-a FILE` | Accession 模式（支持断点续传） | `-a accessions.txt` |
| `-s TAXON` | 分割模式 | `-s Bacteria` |

### 常用参数

| 参数 | 说明 | 默认值 | 推荐值 |
|------|------|--------|--------|
| `-o DIR` | 输出目录 | 必需 | - |
| `-k KEY` | API 密钥 | 无 | 强烈推荐 |
| `-w N` | 线程数 | 2 | 4-8 |
| `-b N` | 批次大小（Accession） | 100 | 100-200 |
| `--include` | 数据类型 | genome | genome,protein,gff3 |
| `--assembly-source` | 来源过滤 | all | all/refseq/genbank |
| `--datasets-exe` | datasets 路径 | datasets | - |
| `--temp-dir` | 临时目录 | 系统默认 | - |
| `--disable-disk-backoff` | 禁用磁盘退避 | false | false |

### 数据类型 (`--include`)

```bash
# 仅基因组（默认）
--include genome

# 基因组 + 蛋白质
--include genome,protein

# 基因组 + 蛋白质 + 注释
--include genome,protein,gff3

# 所有类型
--include genome,protein,rna,cds,gff3,gtf,gbff,seq-report

# 可选值：
# - genome: 基因组序列 (.fna)
# - protein: 蛋白质序列 (.faa)
# - rna: RNA 序列 (.fna)
# - cds: CDS 序列 (.cds.fna)
# - gff3: GFF3 注释 (.gff)
# - gtf: GTF 注释 (.gtf)
# - gbff: GenBank 格式 (.gbff)
# - seq-report: 序列报告 (.txt)
# - none: 仅元数据
```

### 过滤参数 (`--additional-params`)

```bash
# 仅参考基因组
--additional-params reference=true

# 仅完整装配的注释基因组
--additional-params assembly-level=complete,annotated=true

# 组合过滤
--additional-params reference=true,annotated=true,assembly-level=complete

# 可用参数：
# - reference: true/false
# - annotated: true/false
# - assembly-level: complete/chromosome/scaffold/contig
# - assembly-version: latest/all
```

## 实用示例

### 示例 1：下载 E. coli 参考基因组

```bash
echo "562" > ecoli.txt
ncbi-genomefetch -i ecoli.txt -o ecoli_genomes/ \
  --additional-params reference=true \
  -k YOUR_API_KEY
```

### 示例 2：批量下载特定 accessions（支持断点续传）

```bash
# 创建 accession 列表
cat > my_accessions.txt << EOF
GCF_000005845.2
GCF_000009045.1
GCF_000195955.2
GCF_000006945.2
EOF

# 下载（如果中断，重新运行相同命令即可恢复）
ncbi-genomefetch -a my_accessions.txt -o genomes/ \
  -b 100 -w 8 -k YOUR_API_KEY
```

### 示例 3：下载基因组 + 蛋白质 + 注释

```bash
echo "562" > taxa.txt
ncbi-genomefetch -i taxa.txt -o genomes/ \
  --include genome,protein,gff3 \
  -k YOUR_API_KEY -w 8
```

### 示例 4：仅下载完整装配的参考基因组

```bash
echo "562" > taxa.txt
ncbi-genomefetch -i taxa.txt -o genomes/ \
  --additional-params reference=true,assembly-level=complete \
  -k YOUR_API_KEY
```

### 示例 5：分割 Bacteria 并分布式下载

```bash
# 步骤 1：分割
ncbi-genomefetch -s Bacteria -o Bacteria_split/

# 步骤 2：在不同机器上下载不同分组
# 机器 1
ncbi-genomefetch -i Bacteria_split/group1.txt -o downloads/group1/ -k API_KEY -w 8

# 机器 2
ncbi-genomefetch -i Bacteria_split/group2.txt -o downloads/group2/ -k API_KEY -w 8

# 机器 3
ncbi-genomefetch -i Bacteria_split/group3.txt -o downloads/group3/ -k API_KEY -w 8
```

## 性能优化建议

### 1. 获取 API 密钥（强烈推荐）

```bash
# 注册 NCBI 账号并获取 API 密钥
# https://www.ncbi.nlm.nih.gov/account/

# 使用 API 密钥可提高速率限制 10 倍
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY
```

### 2. 根据数据量调整参数

| 数据量 | 推荐配置 |
|--------|----------|
| 小型（< 10 个） | `-w 2 -b 50` |
| 中型（10-100 个） | `-w 4 -b 100 -k API_KEY` |
| 大型（100-1000 个） | `-w 8 -b 150 -k API_KEY` |
| 超大型（> 1000 个） | 使用分割模式 + 分布式下载 |

### 3. 监控磁盘空间

```bash
# 设置磁盘空间阈值
ncbi-genomefetch -i taxa.txt -o genomes/ --disk-warning-bytes 20GB

# 检查磁盘空间
df -h
```

## 常见问题

### Q1: 如何获取 NCBI API 密钥？

访问 https://www.ncbi.nlm.nih.gov/account/ 注册账号并生成 API 密钥。

### Q2: 下载中断了怎么办？

**Accession 模式**：直接重新运行相同命令，自动恢复
```bash
ncbi-genomefetch -a accessions.txt -o genomes/
```

**Taxon 模式**：直接重新运行相同命令，自动恢复
```bash
ncbi-genomefetch -i taxa.txt -o genomes/
```

### Q3: 如何查看下载进度？

**Accession 模式**：
```bash
cat genomes/.accession_progress_state.json
```

**Taxon 模式**：
```bash
cat genomes/.progress_state.json
```

### Q4: 文件名格式是什么？

当前版本使用标准化格式：`{accession}.{extension}`

示例：
- 基因组：`GCF_000005845.2.fna`
- 蛋白质：`GCF_000005845.2.faa`
- 注释：`GCF_000005845.2.gff`
- CDS：`GCF_000005845.2.cds.fna`

### Q5: 如何验证下载完整性？

使用 MD5 校验和：
```bash
# Linux/macOS
md5sum -c genomes/md5sum.txt

# Windows (PowerShell)
Get-FileHash genomes/*.fna -Algorithm MD5
```

### Q6: datasets 工具未找到怎么办？

```bash
# 方案 1：添加到 PATH
export PATH=$PATH:/path/to/datasets

# 方案 2：指定完整路径
ncbi-genomefetch -i taxa.txt -o genomes/ --datasets-exe /path/to/datasets
```

### Q7: 如何只下载参考基因组？

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ \
  --additional-params reference=true
```

### Q8: 如何下载特定数据库的基因组？

```bash
# 仅 RefSeq
ncbi-genomefetch -i taxa.txt -o genomes/ --assembly-source refseq

# 仅 GenBank
ncbi-genomefetch -i taxa.txt -o genomes/ --assembly-source genbank

# 两者都要（默认）
ncbi-genomefetch -i taxa.txt -o genomes/ --assembly-source all
```

## 故障排除

### 问题 1：下载速度慢

**原因**：未使用 API 密钥

**解决**：
```bash
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY -w 8
```

### 问题 2：磁盘空间不足

**解决**：
```bash
# 清理磁盘或使用其他目录
ncbi-genomefetch -i taxa.txt -o /other/path/genomes/

# 或降低最低保留空间阈值
ncbi-genomefetch -i taxa.txt -o genomes/ --disk-minimum-bytes 5GB
```

### 问题 3：进度未保存

**检查**：
```bash
# 确保输出目录可写
ls -la genomes/

# 检查状态文件
cat genomes/.accession_progress_state.json
```

**解决**：
- 确保输出目录有写权限
- 确保磁盘空间充足
- 更新到最新版本

## 下一步

- 查看完整文档：[README.md](README.md)
- 了解版本历史：[CHANGELOG.md](CHANGELOG.md)
- 报告问题：[GitHub Issues](https://github.com/zhuoyi780-alt/NCBI-GenomeFetch/issues)

## 版本信息

当前版本：v1.0.0
发布日期：2026-03-11
测试状态：包含基础发布烟测
