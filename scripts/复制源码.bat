@echo off
setlocal
chcp 65001 >nul

set "SRC=D:\code\ldm-bot\ldmbot"
set "DST=Q:\_tmp\ldmbot"

echo ========== 1. 清理 __pycache__ ==========
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
echo ========== 2. 删除目标目录 ==========
if not exist "%DST%" (
    echo [跳过] 目标目录原本就不存在: %DST%
) else (
    echo 目标目录存在，正在删除: %DST%
    rd /s /q "%DST%"
    if exist "%DST%" (
        echo [错误] 删除失败，可能被占用，请关闭相关程序后重试
        pause
        exit /b 1
    ) else (
        echo [完成] 目标目录已删除
    )
)

echo.
echo ========== 3. 复制目录 ==========
if not exist "%DST%" mkdir "%DST%" >nul 2>nul
for %%d in (changelogs dashboard readme_image samples scripts tests "详细变更日志" astrbot) do (
    if exist "%SRC%\%%~d" (
        robocopy "%SRC%\%%~d" "%DST%\%%~d" /E /NFL /NDL /NJH /NJS /NP >nul
        if errorlevel 8 (
            echo [失败] %%~d
        ) else (
            echo [完成] %%~d
        )
    ) else (
        echo [跳过] 源不存在: %%~d
    )
)

echo.
echo ========== 4. 复制文件 ==========
for %%f in (.env.example .gitignore .zcodeignore CHANGELOG.md LICENSE main.py NOTICE pyproject.toml README.md requirements.txt runtime_bootstrap.py uv.lock "从官方迁移教程.txt") do (
    if exist "%SRC%\%%~f" (
        copy /Y "%SRC%\%%~f" "%DST%\%%~f" >nul
        echo [完成] %%~f
    ) else (
        echo [跳过] 源不存在: %%~f
    )
)

echo.
echo ========== 全部完成 ==========
pause
endlocal