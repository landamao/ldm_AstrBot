@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

rem ============================================================
rem  在 Windows 侧复制并打包 ldmbot 源码
rem
rem  用法：
rem    双击运行            → 结束时按任意键退出，方便看结果
rem    WSL 调用            → 传 /nopause，不暂停（否则会卡住）
rem    例：打包源码.bat /nopause
rem
rem  路径按需修改下面几个 set 变量。
rem ============================================================

set "NOPAUSE=0"
if /i "%~1"=="/nopause" set "NOPAUSE=1"

set "SRC=D:\code\ldm-bot\ldmbot"
set "DST=Q:\_tmp\ldmbot"
set "DSTNAME=ldmbot"
set "OUT=Q:\_tmp\ldmbot.zip"
set "TAR=C:\Windows\System32\tar.exe"

set "RC=0"

echo ========== 1. 检查 tar.exe ==========
if not exist "%TAR%" (
    echo [错误] 找不到 tar.exe：%TAR%
    echo         Windows 10 1803+ 自带；缺失请升级系统或改用其他打包工具
    set "RC=1"
    goto :finish
)
echo [完成] %TAR%

echo.
echo ========== 2. 清理源目录 __pycache__ ==========
for %%p in (astrbot scripts) do (
    if exist "%SRC%\%%p" (
        for /d /r "%SRC%\%%p" %%d in (__pycache__) do (
            if exist "%%d" (
                echo 删除 "%%d"
                rd /s /q "%%d"
            )
        )
    ) else (
        echo [跳过] 源目录不存在: %SRC%\%%p
    )
)

echo.
echo ========== 3. 删除目标目录 ==========
if not exist "%DST%" (
    echo [跳过] 目标目录原本就不存在: %DST%
) else (
    echo 目标目录存在，正在删除: %DST%
    rd /s /q "%DST%"
    if exist "%DST%" (
        echo [错误] 删除失败，可能被占用，请关闭相关程序后重试
        set "RC=1"
        goto :finish
    ) else (
        echo [完成] 目标目录已删除
    )
)

echo.
echo ========== 4. 复制目录 ==========
if not exist "%DST%" mkdir "%DST%" >nul 2>nul
for %%d in (changelogs dashboard readme_image samples scripts tests "详细变更日志" astrbot) do (
    if exist "%SRC%\%%~d" (
        robocopy "%SRC%\%%~d" "%DST%\%%~d" /E /NFL /NDL /NJH /NJS /NP >nul
        if errorlevel 8 (
            echo [失败] %%~d
            set "RC=1"
        ) else (
            echo [完成] %%~d
        )
    ) else (
        echo [跳过] 源不存在: %%~d
    )
)
if not "%RC%"=="0" (
    echo [错误] 有目录复制失败，已中止
    goto :finish
)

echo.
echo ========== 5. 复制文件 ==========
for %%f in (.env .gitignore .zcodeignore CHANGELOG.md LICENSE main.py NOTICE pyproject.toml README.md requirements.txt runtime_bootstrap.py uv.lock "从官方迁移教程.txt") do (
    if exist "%SRC%\%%~f" (
        copy /Y "%SRC%\%%~f" "%DST%\%%~f" >nul
        echo [完成] %%~f
    ) else (
        echo [跳过] 源不存在: %%~f
    )
)

echo.
echo ========== 6. 用 bsdtar 打包 ==========
if not exist "%DST%" (
    echo [错误] 目标目录不存在，无法打包: %DST%
    set "RC=1"
    goto :finish
)
if exist "%OUT%" del /q "%OUT%"
if exist "%OUT%" (
    echo [警告] 旧压缩包删不掉，可能正被 7-Zip 文件管理器等程序打开: %OUT%
    echo         请关闭占用该文件的程序后重试
    set "RC=1"
    goto :finish
)

rem 用 Windows 自带 bsdtar 打包，不要用 7-Zip。
rem 原因：7-Zip 给中文文件名打上 0x800(UTF-8) 标志位，
rem       Linux 的 Info-ZIP unzip 6.00 读 local header 时会按本地编码
rem       重新解码，与 central directory 比对不上，逐条报
rem       "mismatching local filename"，最终 unzip 退出码为 1，
rem       导致安装脚本判定解压失败。
rem       bsdtar 写原始 UTF-8 字节且不设 0x800，Linux unzip 正常。
rem
rem 必须 cd 到父目录后用相对名打包：
rem   传绝对路径 D:\...\ldmbot 时 tar 会报 "Removing leading drive letter"
rem   并把成员名存成 _tmp/ldmbot/...，丢掉顶层 ldmbot 目录，
rem   解压后结构就不对了。
pushd "%DST%\.." >nul
"%TAR%" -a -c -f "%OUT%" "%DSTNAME%"
set "TARRC=%errorlevel%"
popd >nul
if not "%TARRC%"=="0" (
    echo [错误] tar 打包失败，退出码 %TARRC%
    set "RC=1"
    goto :finish
)
if not exist "%OUT%" (
    echo [错误] 打包后未找到输出文件: %OUT%
    set "RC=1"
    goto :finish
)
echo [完成] 已生成 %OUT%

echo.
echo ========== 7. 校验压缩包 ==========
"%TAR%" -t -f "%OUT%" >nul 2>&1
if errorlevel 1 (
    echo [错误] 压缩包完整性校验失败: %OUT%
    set "RC=1"
    goto :finish
)
echo [完成] 完整性校验通过

:finish
echo.
if "%RC%"=="0" (
    echo ========== 全部完成 ==========
    for %%A in ("%OUT%") do echo 输出: %%~fA   大小: %%~zA 字节
) else (
    echo ========== 执行失败，退出码 %RC% ==========
)

rem 双击运行时暂停，避免窗口一闪而过；WSL 调用传 /nopause 时跳过
if "%NOPAUSE%"=="0" pause

endlocal & exit /b %RC%
