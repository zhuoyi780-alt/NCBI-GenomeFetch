# Changelog

All notable changes to NCBI-GenomeFetch will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-11

### Added
- **MD5自动修复功能**: 完整的MD5验证失败自动修复流程
  - 自动识别MD5验证失败的文件（FAIL、MISSING、ERROR状态）
  - 从文件路径提取Accession标识符
  - 使用Accession模式自动重新下载失败的文件
  - 应用文件名简化规则，将文件整理到原始路径
  - 重新验证修复后的文件并生成详细报告
  - 支持三种使用方式：仅校验、仅修复、校验+修复
  
- **智能路径匹配机制**: 使用md5sum.txt所在目录上下文
  - 避免全局搜索导致的同名文件冲突
  - 时间复杂度从O(n)优化到O(1)
  
- **ZIP文件自动解压**: 处理NCBI Datasets CLI下载的压缩包
  - 自动检测临时目录中的.zip文件
  - 解压到对应的目录结构

- **Accession模式断点续传**: 完整的恢复能力实现
  - 每个批次完成后增量保存进度
  - 自动检测和跳过已下载的accessions
  - 文件验证确保现有下载完整性
  - 成功后自动清理状态文件
  - 增强的中断处理（SIGTERM/SIGINT）

- **菌株级基因组自动识别**: 自动检测和包含仅有菌株级基因组的物种
  - 物种没有直接基因组但有菌株级基因组时自动包含
  - 增强的输出格式，包含物种和后代信息
  - 统计区分物种数和基因组数

- **多文件类型支持**: 支持下载多种数据类型
  - genome, protein, rna, cds, gff3, gtf, gbff, seq-report
  - 文件名标准化：`{accession}.{extension}`
  - 自动文件类型检测和处理

- **磁盘空间动态回退**: 智能磁盘空间监控和自动并发调整
  - 四个空间级别：NORMAL, WARNING, CRITICAL, PAUSED
  - 低磁盘空间时自动减少工作线程
  - 防抖机制避免阈值附近的状态抖动
  - 动态检查间隔（正常30s，关键5s）

- **任务分割功能**: 大型下载任务的交互式分割工作流
  - 支持7个分类学级别（Kingdom/Phylum/Class/Order/Family/Genus/Species）
  - 基于序列长度（Gb）的智能分组
  - 可选的双名法过滤
  - 超大分类单元的自动二次分割

### Features
- 基于分类学的基因组下载（使用学名或TaxID）
- 脱水/再水化工作流程，高效处理大规模下载
- 多线程处理，可配置工作线程数（2-20个）
- 自动速率限制合规（无API密钥3次/秒，有API密钥10次/秒）
- 中断下载的恢复能力
- 跨平台兼容性（Windows和Linux）
- 全面的错误处理和分类错误类型
- MD5校验和保留与路径修正
- 进度跟踪和详细日志记录
- 原子文件操作防止部分下载

### Testing
- 发布烟测覆盖包导入、CLI help 和源码编译检查

### Documentation
- 完整的README，包含安装和使用说明
- 配置指南和使用案例示例
- 错误代码和故障排除指南
- 快速开始指南（QUICKSTART.md）
- 示例输入文件

### Technical Implementation
- 使用Hypothesis进行基于属性的测试
- 多个工作线程间的线程安全速率限制
- 平台感知的可执行文件检测
- MD5文件中的正斜杠标准化，实现跨平台兼容
- 瞬态错误的指数退避重试逻辑
- 结构化错误日志记录
- 任务分割的First Fit Decreasing装箱算法
- 基于等级的分层分类树

## 许可证

MIT License
