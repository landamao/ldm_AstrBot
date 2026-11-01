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
set "OUT=Q:\_tmp\ldmbot.zip"
set "SEVENZIP=C:\Program Files\7-Zip\7z.exe"

set "RC=0"

echo ========== 1. 检查 7-Zip ==========
if not exist "%SEVENZIP%" (
    echo [错误] 找不到 7-Zip：%SEVENZIP%
    echo         请修改本脚本里的 SEVENZIP 变量
    set "RC=1"
    goto :finish
)
echo [完成] %SEVENZIP%

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
echo ========== 6. 用 7-Zip 打包 ==========
if not exist "%DST%" (
    echo [错误] 目标目录不存在，无法打包: %DST%
    set "RC=1"
    goto :finish
)
if exist "%OUT%" del /q "%OUT%"

rem -tzip 生成标准 zip；-mx=5 平衡速度与体积；-mmt=on 多线程
"%SEVENZIP%" a -tzip -mx=5 -mmt=on -bso0 -bsp0 "%OUT%" "%DST%"

if errorlevel 1 (
    echo [错误] 7-Zip 打包失败，退出码 !errorlevel!
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
"%SEVENZIP%" t -bso0 -bsp0 "%OUT%"
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
