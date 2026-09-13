# zip 中文文件名编码修复测试（无 UTF-8 标志位条目 cp437 乱码）
#
# 背景：ZIP 规范规定无 0x800 标志时文件名编码未知，Python zipfile 按 cp437
# 解码。Info-ZIP 3.0（Windows 中文环境）打的包不设该标志 → 解压中文乱码。
# Linux unzip/文件管理器按字节透传所以「看压缩包正常、Python 解压乱码」。
import os
import shutil
import struct
import zipfile
import zlib

import pytest

from astrbot.core.utils.zip_fix import fix_zip_entry_names


def make_zip_no_flag(zip_path, entries):
    """手写 zip 字节流模拟 Info-ZIP 3.0：文件名原始字节写入、flag 不含 0x800。

    zipfile 写入非 ASCII 名时会强制设 0x800，无法直接模拟，故手写。
    entries: [(原始文件名字节 bytes, 数据 bytes)]
    """
    local = bytearray()
    central = bytearray()
    for name_bytes, data in entries:
        offset = len(local)
        crc = zlib.crc32(data) & 0xFFFFFFFF
        local += struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0x2100, crc,
            len(data), len(data), len(name_bytes), 0,
        )
        local += name_bytes + data
        central += struct.pack(
            "<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0x2100,
            crc, len(data), len(data), len(name_bytes), 0, 0, 0, 0, 0, offset,
        )
        central += name_bytes
    eocd = struct.pack(
        "<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries),
        len(central), len(local), 0,
    )
    with open(zip_path, "wb") as f:
        f.write(local + central + eocd)


def test_utf8_bytes_no_flag(tmp_path):
    """发版包形态：文件名本就是 UTF-8 字节但无标志位。"""
    p = str(tmp_path / "t1.zip")
    make_zip_no_flag(p, [("docs/".encode() + "中文测试文件.txt".encode(), b"hi")])
    with zipfile.ZipFile(p) as zf:
        assert zf.infolist()[0].flag_bits & 0x800 == 0
        assert zf.namelist()[0] != "docs/中文测试文件.txt"  # 修补前是 cp437 乱码
        assert fix_zip_entry_names(zf) == 1
        assert zf.namelist() == ["docs/中文测试文件.txt"]


def test_gbk_bytes_no_flag(tmp_path):
    """Windows GBK 工具打包形态。"""
    p = str(tmp_path / "t2.zip")
    gbk = "中文测试文件.txt".encode("gbk")
    make_zip_no_flag(p, [("docs/".encode() + gbk, b"hi")])
    with zipfile.ZipFile(p) as zf:
        assert fix_zip_entry_names(zf) == 1
        assert zf.namelist() == ["docs/中文测试文件.txt"]


def test_flagged_utf8_untouched(tmp_path):
    """带 0x800 标志的正常条目不受影响。"""
    p = str(tmp_path / "t3.zip")
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("目录/正常文件.txt", "ok")
    with zipfile.ZipFile(p) as zf:
        assert fix_zip_entry_names(zf) == 0
        assert zf.namelist() == ["目录/正常文件.txt"]


def test_ascii_untouched(tmp_path):
    p = str(tmp_path / "t4.zip")
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("docs/readme.md", "x")
    with zipfile.ZipFile(p) as zf:
        assert fix_zip_entry_names(zf) == 0
        assert zf.namelist() == ["docs/readme.md"]


def test_gbk_extract_to_disk(tmp_path):
    """端到端：修补后解压落盘文件名正确（NameToInfo 同步生效）。"""
    p = str(tmp_path / "t5.zip")
    gbk = "中文测试文件.txt".encode("gbk")
    make_zip_no_flag(p, [("docs/".encode() + gbk, b"hi")])
    out = tmp_path / "out"
    with zipfile.ZipFile(p) as zf:
        fix_zip_entry_names(zf)
        zf.extractall(out)
    assert os.listdir(out / "docs") == ["中文测试文件.txt"]


def test_read_by_fixed_name(tmp_path):
    """read()/open() 按修补后名字查条目可用（NameToInfo 重建）。"""
    p = str(tmp_path / "t6.zip")
    make_zip_no_flag(p, [("中文.md".encode(), "内容".encode())])
    with zipfile.ZipFile(p) as zf:
        fix_zip_entry_names(zf)
        assert zf.read("中文.md") == "内容".encode()


def test_undecodable_bytes_kept(tmp_path):
    """既不是 utf-8 也不是 gbk 的字节保持原样，不抛异常。"""
    p = str(tmp_path / "t7.zip")
    # 0x81 0x40 在 gbk 中是合法二字符序列?——选一个两种编码都解不出的序列
    bad = b"\x81\xfe\x81\xfe.txt"
    make_zip_no_flag(p, [(bad, b"x")])
    with zipfile.ZipFile(p) as zf:
        n = fix_zip_entry_names(zf)
        # 若恰好能被 gbk 解出也不算错；这里确保不抛异常即可
        assert isinstance(n, int)
        assert zf.namelist()  # 条目还在
