#!/usr/bin/env python3
"""
Setup script for taxonomy-downloader package.
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read the README file
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text(encoding='utf-8')

# Read runtime requirements
requirements = []
with open('requirements.txt', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            requirements.append(line)

setup(
    name="ncbi-genomefetch",
    version="1.0.0",
    description="A command-line tool for batch downloading genome data from NCBI using taxonomy names",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="NCBI-GenomeFetch Team",
    url="https://github.com/zhuoyi780-alt/NCBI-GenomeFetch",
    packages=find_packages(),
    include_package_data=True,
    install_requires=requirements,
    entry_points={
        'console_scripts': [
            'ncbi-genomefetch=taxonomy_downloader.cli:main',
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Scientific/Engineering :: Bio-Informatics",
    ],
    python_requires=">=3.8",
    keywords="bioinformatics, genomics, ncbi, taxonomy, genome-download",
    project_urls={
        "Bug Reports": "https://github.com/zhuoyi780-alt/NCBI-GenomeFetch/issues",
        "Source": "https://github.com/zhuoyi780-alt/NCBI-GenomeFetch",
        "Documentation": "https://github.com/zhuoyi780-alt/NCBI-GenomeFetch/blob/main/README.md",
    },
)
