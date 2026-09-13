"""zip 中文文件名编码修复。

ZIP 规范（APPNOTE 4.4.17.4）：条目通用位标志（flag bits）第 11 位（0x800）
未设置时，文件名编码未知；Python zipfile 此时按 cp437 解码。Windows 上用
本地 GBK 打包的压缩包（如 Info-ZIP 3.0 在中文 Windows 打出的包）不设该标志，
Python 解压就会得到「Σ╗Äσ«ÿµû╣Φ┐üτº╗µòÖτ¿ï.txt」这类乱码——而 Linux 的 unzip、
文件管理器直接按字节透传/按 UTF-8 显示，看起来是正常的，于是出现
「压缩包里文件名正常、解压出来乱码」的现象。

修复思路：无标志位条目的 filename 是 zipfile 按 cp437 强解的结果，将
filename.encode("cp437") 可以无损还原打包时的原始字节，再按 utf-8 → gbk
顺序重试解码（utf-8 优先：跨平台 zip 的中文名大多本就是 UTF-8 字节；
gbk 兜底：Windows 中文环境本地打包的包）。两者都失败则保持原样。

使用：打开 ZipFile 后、读取 namelist()/解压之前调用 fix_zip_entry_names()
就地修补。zipfile 解压时按 ZipInfo.filename 直接落盘，修补后即落地正确文件名。
"""

import zipfile

__all__ = ["fix_zip_entry_names"]


def _fix_name(name: str) -> str:
    """尝试把 cp437 强解的单个条目名还原为正确文件名，失败原样返回。"""
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        # 含 cp437 之外的字符（已带 UTF-8 标志的条目一般可直接编回），
        # 无法还原字节，保持原样
        return name
    for encoding in ("utf-8", "gbk"):
        try:
            fixed = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if fixed != name:
            return fixed
        # 解回原串说明文件名本来就是纯 ASCII/cp437，无需处理
        return name
    return name


def fix_zip_entry_names(zf: zipfile.ZipFile) -> int:
    """就地修补 ZipFile 中无 UTF-8 标志位条目的文件名。

    必须在读取 namelist()/infolist()/解压之前调用。
    返回修补的条目数。
    """
    fixed_count = 0
    for info in zf.infolist():
        if info.flag_bits & 0x800:
            # 已声明 UTF-8，zipfile 解码正确
            continue
        fixed = _fix_name(info.filename)
        if fixed != info.filename:
            info.filename = fixed
            fixed_count += 1
    if fixed_count:
        # extract()/extractall()/read()/open() 都经 NameToInfo 按名查条目，
        # 改过 filename 后必须同步重建键映射，否则按新名查不到
        zf.NameToInfo = {info.filename: info for info in zf.infolist()}
    return fixed_count
